"""SQLite catalog for file-backed Prisma records and their relationships."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import sqlite3


KINDS = ('project', 'revision', 'source', 'scenario', 'cues', 'prompt', 'asset',
         'tool', 'model', 'lora', 'dataset', 'job', 'artifact', 'export')

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    owner TEXT NOT NULL,
    label TEXT NOT NULL,
    path TEXT,
    state TEXT,
    updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    metadata TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS records_owner ON records(owner);
CREATE INDEX IF NOT EXISTS records_recency ON records(updated DESC, kind, id);
CREATE INDEX IF NOT EXISTS records_kind_recency ON records(kind, updated DESC, id);
CREATE TABLE IF NOT EXISTS links (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (kind, id, relation, target_kind, target_id),
    FOREIGN KEY (kind, id) REFERENCES records(kind, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS links_target ON links(target_kind, target_id);
"""


def timestamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class ImageLibrary:
    def __init__(self, workspace):
        self.workspace = workspace
        self.path = workspace / 'catalog.sqlite3'

    @contextmanager
    def connection(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript(f'BEGIN IMMEDIATE; {SCHEMA} PRAGMA user_version=1; COMMIT;')
            elif version != 1:
                raise ValueError(f'此 Prisma 版本无法读取目录数据库版本 {version}。')
            yield db

    def location(self, path):
        path = path.absolute()
        return path.relative_to(self.workspace).as_posix() if path.is_relative_to(self.workspace) else path.as_posix()

    def replace(self, owner, records):
        """Replace one job's projection or one managed inventory in a transaction."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {(row['kind'], row['id']) for row in
                        db.execute('SELECT kind, id FROM records WHERE owner = ?', (owner,))}
            for record in records:
                self._store(db, owner, record)
                existing.discard((record['kind'], record['id']))
            db.executemany('DELETE FROM records WHERE kind = ? AND id = ?', existing)

    def put(self, owner, record):
        with self.connection() as db:
            self._store(db, owner, record)

    def _store(self, db, owner, record):
        kind, ident = record['kind'], record['id']
        path = record.get('path')
        updated = record.get('updated') or datetime.now(timezone.utc).isoformat(
            timespec='milliseconds').replace('+00:00', 'Z')
        db.execute("""
            INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, id) DO UPDATE SET
              owner=excluded.owner, label=excluded.label, path=excluded.path,
              state=excluded.state, updated=excluded.updated, metadata=excluded.metadata
        """, (kind, ident, owner, record['label'], self.location(path) if path else None,
              record.get('state'), updated, json.dumps(record.get('metadata', {}), ensure_ascii=False)))
        db.execute('DELETE FROM links WHERE kind = ? AND id = ?', (kind, ident))
        db.executemany('INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?)',
                       ((kind, ident, *link) for link in record.get('links', ())))

    def owners(self, prefix):
        with self.connection() as db:
            return {row['owner']: json.loads(row['metadata']).get('stamp') for row in db.execute(
                'SELECT owner, metadata FROM records WHERE kind = ? AND owner LIKE ?', ('job', prefix + '%'))}

    def trained_loras(self):
        with self.connection() as db:
            rows = db.execute("""
                SELECT id, label, path, metadata FROM records
                WHERE kind = 'lora' AND owner LIKE 'job:%' ORDER BY updated DESC, id
            """).fetchall()
        return [dict(row, metadata=json.loads(row['metadata'])) for row in rows]

    def list(self, *, kind=None, query='', limit=30, offset=0):
        where = """WHERE (? IS NULL OR kind = ?) AND
                   (? = '' OR instr(lower(label || ' ' || id || ' ' || coalesce(path, '') || ' ' || metadata), lower(?)) > 0)"""
        params = (kind, kind, query, query)
        with self.connection() as db:
            total = db.execute(f'SELECT count(*) FROM records {where}', params).fetchone()[0]
            rows = db.execute(f"""SELECT kind, id, label, path, state, updated FROM records {where}
                ORDER BY updated DESC, kind, id LIMIT ? OFFSET ?""", (*params, limit, offset)).fetchall()
        return {'items': [self.describe(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def describe(self, row):
        data = dict(row)
        path = data['path']
        data['available'] = (self.workspace / path).exists() if path else None
        return data

    def get(self, kind, ident):
        key = (kind, ident)
        with self.connection() as db:
            row = db.execute('SELECT * FROM records WHERE kind = ? AND id = ?', key).fetchone()
            if row is None:
                raise FileNotFoundError('目录记录不存在。')
            outgoing = db.execute("""
                SELECT relation, l.target_kind AS kind, l.target_id AS id, r.label
                FROM links l LEFT JOIN records r ON (r.kind, r.id) = (l.target_kind, l.target_id)
                WHERE l.kind = ? AND l.id = ? ORDER BY relation, l.target_id LIMIT 100
            """, key).fetchall()
            incoming = db.execute("""
                SELECT relation, l.kind, l.id, r.label
                FROM links l JOIN records r ON (r.kind, r.id) = (l.kind, l.id)
                WHERE l.target_kind = ? AND l.target_id = ?
                ORDER BY r.updated DESC, l.kind, l.id, relation LIMIT 100
            """, key).fetchall()
        result = self.describe(row)
        result.pop('owner')
        result['metadata'] = json.loads(result['metadata'])
        result['metadata'].pop('stamp', None)
        result['links'] = [dict(link, direction='out') for link in outgoing]
        result['links'].extend(dict(link, direction='in') for link in incoming)
        return result


def artifact_id(job, category, name):
    return f'{job}/{category}/{name}'
