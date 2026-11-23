"""SQLite catalog for file-backed Prisma records and their relationships."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import sqlite3


KINDS = ('project', 'revision', 'source', 'scenario', 'cues', 'prompt', 'script', 'script-draft', 'asset',
         'tool', 'model', 'lora', 'dataset', 'job', 'artifact', 'export', 'pipeline', 'node-version', 'handoff',
         'production-plan', 'production-default', 'production-checkpoint', 'character-design', 'scene-design', 'cg-design', 'work-style')

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
CREATE TABLE IF NOT EXISTS local_secrets (
    scope TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS model_cache (
    provider TEXT PRIMARY KEY,
    models TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS api_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL DEFAULT '',
    models TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
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
            if version < 10:
                db.execute('BEGIN IMMEDIATE')
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version == 0:
                    for statement in SCHEMA.split(';'):
                        if statement.strip():
                            db.execute(statement)
                if version < 2:
                    db.execute('ALTER TABLE records ADD COLUMN project_id TEXT')
                    db.execute('CREATE INDEX records_project ON records(project_id, kind, updated DESC, id)')
                    self._backfill_projects(db)
                    version = 2
                if version < 3:
                    db.execute("""CREATE TABLE IF NOT EXISTS local_secrets (
                        scope TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )""")
                    db.execute('PRAGMA user_version=3')
                    version = 3
                if version < 4:
                    db.execute("""CREATE TABLE IF NOT EXISTS model_cache (
                        provider TEXT PRIMARY KEY,
                        models TEXT NOT NULL,
                        updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )""")
                    db.execute('PRAGMA user_version=4')
                    version = 4
                if version < 5:
                    db.execute("""CREATE TABLE IF NOT EXISTS api_providers (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                        base_url TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '', models TEXT NOT NULL DEFAULT '[]',
                        created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                        updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )""")
                    db.execute('PRAGMA user_version=5')
                    version = 5
                if version < 6:
                    db.execute("ALTER TABLE api_providers ADD COLUMN protocol TEXT NOT NULL DEFAULT 'openai'")
                    db.execute("""INSERT OR IGNORE INTO api_providers (id,name,kind,base_url,api_key,models,protocol)
                        SELECT 'openai','OpenAI','text,image,audio','https://api.openai.com/v1',
                            coalesce((SELECT value FROM local_secrets WHERE scope='https://api.openai.com'), ''),
                            coalesce((SELECT models FROM model_cache WHERE provider='openai-images'), '[]'), 'openai'""")
                    db.execute("""CREATE TABLE api_model_defaults (
                        kind TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                        FOREIGN KEY (provider_id) REFERENCES api_providers(id) ON DELETE RESTRICT
                    )""")
                    db.execute('PRAGMA user_version=6')
                    version = 6
                if version < 7:
                    db.execute("""CREATE TABLE api_model_tests (
                        provider_id TEXT NOT NULL, model_id TEXT NOT NULL, started_at TEXT NOT NULL,
                        result TEXT NOT NULL, PRIMARY KEY (provider_id, model_id),
                        FOREIGN KEY (provider_id) REFERENCES api_providers(id) ON DELETE CASCADE
                    )""")
                    db.execute('PRAGMA user_version=7')
                    version = 7
                if version < 8:
                    db.execute("""CREATE TABLE generation_runs (
                        id TEXT PRIMARY KEY, project TEXT NOT NULL, node TEXT NOT NULL,
                        state TEXT NOT NULL, events TEXT NOT NULL)""")
                    db.execute('CREATE INDEX generation_runs_project_node ON generation_runs(project,node)')
                    db.execute('PRAGMA user_version=8')
                    version = 8
                if version < 9:
                    db.execute('ALTER TABLE generation_runs ADD COLUMN response_text TEXT')
                    version = 9
                if version < 10:
                    db.execute("""CREATE TABLE generation_requests (
                        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES generation_runs(id) ON DELETE CASCADE,
                        snapshot TEXT NOT NULL, evidence TEXT
                    )""")
                    db.execute('CREATE INDEX generation_requests_run ON generation_requests(run_id)')
                    db.execute('PRAGMA user_version=10')
                db.commit()
            elif version != 10:
                raise ValueError(f'此 Prisma 版本无法读取目录数据库版本 {version}。')
            yield db

    def api_providers(self):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM api_providers ORDER BY name COLLATE NOCASE, id').fetchall()
        return [{**{key: row[key] for key in row.keys() if key not in ('api_key', 'models')},
                 'models': json.loads(row['models']), 'configured': bool(row['api_key'])} for row in rows]

    def api_provider(self, ident):
        with self.connection() as db:
            row = db.execute('SELECT * FROM api_providers WHERE id = ?', (ident,)).fetchone()
        if row is None:
            raise FileNotFoundError('API Provider 不存在。')
        return dict(row, models=json.loads(row['models']), configured=bool(row['api_key']))

    def get_local_secret(self, scope):
        with self.connection() as db:
            row = db.execute('SELECT value FROM local_secrets WHERE scope = ?', (scope,)).fetchone()
        return row['value'] if row else ''

    def set_local_secret(self, scope, value):
        with self.connection() as db:
            db.execute("""INSERT INTO local_secrets (scope, value) VALUES (?, ?)
                ON CONFLICT(scope) DO UPDATE SET value=excluded.value,
                updated=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""", (scope, value))

    def clear_local_secret(self, scope):
        with self.connection() as db:
            db.execute('DELETE FROM local_secrets WHERE scope = ?', (scope,))

    def get_model_cache(self, provider):
        with self.connection() as db:
            row = db.execute('SELECT models FROM model_cache WHERE provider = ?', (provider,)).fetchone()
        return json.loads(row['models']) if row else None

    def set_model_cache(self, provider, models):
        with self.connection() as db:
            db.execute("""INSERT INTO model_cache (provider, models) VALUES (?, ?)
                ON CONFLICT(provider) DO UPDATE SET models=excluded.models,
                updated=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
                       (provider, json.dumps(models, ensure_ascii=False)))

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
        metadata = record.get('metadata', {})
        project = record.get('project_id') or metadata.get('project_id')
        if kind == 'project':
            project = ident
        if not project:
            project = next((target for relation, target_kind, target in record.get('links', ())
                            if relation == 'for_project' and target_kind == 'project'), None)
        db.execute("""
            INSERT INTO records (kind, id, owner, label, path, state, updated, metadata, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, id) DO UPDATE SET
              owner=excluded.owner, label=excluded.label, path=excluded.path,
              state=excluded.state, updated=excluded.updated, metadata=excluded.metadata,
              project_id=coalesce(excluded.project_id, records.project_id)
        """, (kind, ident, owner, record['label'], self.location(path) if path else None,
              record.get('state'), updated, json.dumps(metadata, ensure_ascii=False), project))
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

    def visible_ids(self, kind, project, *, include_shared=False):
        shared = 'project_id IS NULL'
        owned = 'project_id = ?'
        predicate = f'({owned} OR {shared})' if project and include_shared else owned if project else shared
        params = (kind, project) if project else (kind,)
        with self.connection() as db:
            return {row['id'] for row in db.execute(
                f'SELECT id FROM records WHERE kind = ? AND {predicate}', params)}

    def backfill_projects(self):
        with self.connection() as db:
            self._backfill_projects(db)

    @staticmethod
    def _backfill_projects(db):
        db.execute("""
            UPDATE records AS r SET project_id = CASE WHEN kind = 'project' THEN id ELSE
              coalesce(json_extract(metadata, '$.project_id'), (
                SELECT target_id FROM links
                WHERE (kind, id) = (r.kind, r.id) AND relation = 'for_project' AND target_kind = 'project'
                GROUP BY kind, id HAVING count(DISTINCT target_id) = 1
              )) END WHERE project_id IS NULL
        """)
        db.execute("""
            WITH ownership(kind, id, project_id) AS (
                SELECT l.target_kind, l.target_id, min(r.project_id)
                FROM links l JOIN records r ON (r.kind, r.id) = (l.kind, l.id)
                WHERE r.kind IN ('revision', 'export') AND r.project_id IS NOT NULL
                  AND coalesce(json_extract(r.metadata, '$.ownership_version'), 1) = 1
                  AND l.relation IN ('uses_source', 'uses_scenario', 'uses_cues', 'uses_asset')
                GROUP BY l.target_kind, l.target_id HAVING count(DISTINCT r.project_id) = 1
            )
            UPDATE records AS r SET project_id = o.project_id FROM ownership o
            WHERE r.project_id IS NULL AND (r.kind, r.id) = (o.kind, o.id)
        """)
        db.execute("""
            UPDATE records AS r SET project_id = parent.project_id
            FROM links l JOIN records parent ON (parent.kind, parent.id) = (l.target_kind, l.target_id)
            WHERE (l.kind, l.id) = (r.kind, r.id) AND r.project_id IS NULL
              AND l.relation IN ('produced_by', 'member_of') AND r.kind IN ('artifact', 'asset', 'lora')
              AND parent.project_id IS NOT NULL
        """)

    def list(self, *, kind=None, query='', limit=30, offset=0, project=None, shared=False, trash=False):
        where = """WHERE (? IS NULL OR kind = ?) AND
                   (? = '' OR instr(lower(label || ' ' || id || ' ' || coalesce(path, '') || ' ' || metadata), lower(?)) > 0)"""
        params = (kind, kind, query, query)
        if trash:
            where += " AND kind = 'project' AND state = 'trashed'"
        else:
            where += " AND (kind <> 'project' OR coalesce(state, '') <> 'trashed')"
        if project:
            where += ' AND project_id = ?'
            params += (project,)
        elif shared:
            where += ' AND project_id IS NULL'
        with self.connection() as db:
            total = db.execute(f'SELECT count(*) FROM records {where}', params).fetchone()[0]
            rows = db.execute(f"""SELECT kind, id, label, path, state, updated, project_id FROM records {where}
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
