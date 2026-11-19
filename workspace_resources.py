"""Project revisions and reusable media in the shared Prisma workspace."""

import base64
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import secrets
import tempfile
import threading

from image_library import artifact_id, timestamp


DOCUMENT_KINDS = ('source', 'scenario', 'cues', 'prompt', 'revision')
MEDIA_TYPES = {
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
    '.ogg': 'audio', '.opus': 'audio', '.mp3': 'audio', '.wav': 'audio', '.flac': 'audio',
    '.ttf': 'font', '.otf': 'font', '.ttc': 'font',
}
ID = re.compile(r'[0-9a-f]{16}|[0-9a-f]{64}')


def content_id(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text_title(text, fallback):
    return next((line.strip(' #\t')[:100] for line in text.splitlines() if line.strip(' #\t')), fallback)


def asset_name(name):
    if (not isinstance(name, str) or not 0 < len(name) <= 160 or name != name.strip(' .')
            or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
            or PureWindowsPath(name).is_reserved() or Path(name).suffix.lower() not in MEDIA_TYPES):
        raise ValueError('素材文件名或格式无效。请使用支持的图片、音频或字体。')
    return name


class WorkspaceResources:
    def __init__(self, library, artifact):
        self.library = library
        self.home = library.workspace
        self.records = self.home / 'records'
        self.media = self.home / 'media'
        self.artifact = artifact
        self.lock = threading.RLock()

    def record_path(self, kind, ident):
        if kind not in (*DOCUMENT_KINDS, 'project', 'asset') or not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ValueError('资源 ID 或类别无效。')
        return self.records / kind / f'{ident}.json'

    def _write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent, prefix='.record-') as temporary:
            staged = Path(temporary) / 'record.json'
            staged.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            staged.replace(path)

    def _index(self, record, path):
        entry = dict(record, path=path, updated=timestamp(path))
        if record['kind'] == 'asset':
            metadata = record['metadata']
            if 'external_path' in metadata:
                entry['path'] = Path(metadata['external_path'])
            else:
                try:
                    entry['path'] = self.asset_path(metadata)
                except FileNotFoundError:
                    ref = metadata.get('artifact')
                    entry['path'] = (self.home / 'jobs' / ref['job_id'] / 'images' / ref['name']
                                     if ref else self.media / metadata['sha256'])
        self.library.put('workspace', entry)

    def _save(self, record):
        path = self.record_path(record['kind'], record['id'])
        self._write(path, record)
        self._index(record, path)
        return record['id']

    def note(self, kind, label, metadata, links=()):
        base = dict(kind=kind, label=label, metadata=metadata, links=list(links))
        ident = content_id([kind, metadata])
        path = self.record_path(kind, ident)
        with self.lock:
            if path.is_file():
                existing = json.loads(path.read_text(encoding='utf-8'))
                merged = sorted({tuple(link) for link in (*existing['links'], *links)})
                if len(merged) == len(existing['links']):
                    self._index(existing, path)
                    return ident
                base = dict(existing, links=merged)
            return self._save(dict(base, id=ident))

    def document(self, kind, text, links=()):
        return self.note(kind, text_title(text, kind), {'text': text}, links)

    def prompt(self, positive, negative, *, source=None, context=None, derived_from=None):
        links = [('from_scenario', 'scenario', source)] if source else []
        if derived_from:
            links.append(('adapted_from', 'prompt', derived_from))
        metadata = dict(prompt=positive, negative=negative)
        if context:
            metadata['context'] = context
        return self.note('prompt', text_title(positive, '提示词'), metadata, links)

    def asset_path(self, metadata):
        if ref := metadata.get('artifact'):
            return self.artifact(ref['job_id'], 'images', ref['name'])
        digest = metadata.get('sha256', '')
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('素材校验值无效。')
        path = self.media / digest
        if not path.is_file() or not path.resolve().is_relative_to(self.media.resolve()):
            raise FileNotFoundError('素材文件不存在。')
        return path

    def asset(self, ident):
        path = self.record_path('asset', ident)
        metadata = json.loads(path.read_text(encoding='utf-8'))['metadata']
        if 'external_path' in metadata:
            raise ValueError('本机外部素材须先导入，才能通过 WebUI 或 MCP 复用。')
        return metadata

    def record_local_asset(self, source):
        path = source.resolve(strict=True)
        metadata = dict(name=path.name, media_type=MEDIA_TYPES[path.suffix.lower()],
                        external_path=path.as_posix(), size=path.stat().st_size)
        with path.open('rb') as stream:
            metadata['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return self.note('asset', path.name, metadata)

    def register_asset(self, name, *, blob=None, artifact=None, origin=None):
        name = asset_name(name)
        if (blob is None) == (artifact is None):
            raise ValueError('请提供一个素材文件或生成产物。')
        links = []
        metadata = dict(name=name, media_type=MEDIA_TYPES[Path(name).suffix.lower()])
        if artifact is not None:
            job, original = artifact.get('job_id'), artifact.get('name')
            path = self.artifact(job, 'images', original)
            if Path(name).suffix.lower() != path.suffix.lower():
                raise ValueError('素材名须保留原图扩展名。')
            metadata.update(artifact=dict(job_id=job, name=original), size=path.stat().st_size)
            links.append(('from_artifact', 'artifact', artifact_id(job, 'images', original)))
        else:
            if not blob or len(blob) > 45 * 1024 * 1024:
                raise ValueError('单个素材须大于 0 字节且不超过 45 MiB。')
            digest = hashlib.sha256(blob).hexdigest()
            metadata.update(sha256=digest, size=len(blob))
            self.media.mkdir(parents=True, exist_ok=True)
            path = self.media / digest
            with self.lock:
                if not path.is_file():
                    with tempfile.TemporaryDirectory(dir=self.media, prefix='.asset-') as directory:
                        staged = Path(directory) / 'data'
                        staged.write_bytes(blob)
                        staged.replace(path)
        ident = content_id(['asset', metadata])
        metadata['url'] = f'/api/workspace/assets/{ident}'
        if origin and origin != ident:
            links.append(('adapted_from', 'asset', origin))
        with self.lock:
            path = self.record_path('asset', ident)
            if path.is_file():
                previous = json.loads(path.read_text(encoding='utf-8'))
                links = sorted({tuple(link) for link in (*previous['links'], *links)})
            self._save(dict(kind='asset', id=ident, label=name, metadata=metadata, links=links))
        return {'id': ident, **metadata}

    def import_asset(self, data):
        blob = base64.b64decode(data['data'], validate=True) if isinstance(data.get('data'), str) else None
        reference = data.get('artifact')
        if reference is not None and (not isinstance(reference, dict)
                or not isinstance(reference.get('job_id'), str) or not isinstance(reference.get('name'), str)):
            raise ValueError('生成素材须包含任务 ID 和文件名。')
        return self.register_asset(data.get('name'), blob=blob, artifact=reference)

    def snapshot(self, data):
        raw, scenario = data.get('raw', ''), data.get('scenario', '')
        cues, assets = data.get('cues', {}), data.get('assets', {})
        font, heading, speakers = data.get('font', ''), data.get('heading', ''), data.get('speakers', {})
        if (not all(isinstance(value, str) for value in (raw, scenario, font, heading))
                or not all(isinstance(value, dict) for value in (cues, assets, speakers))):
            raise ValueError('项目文本、演出绑定或素材清单格式无效。')
        links = []
        source_id = self.document('source', raw) if raw.strip() else None
        if source_id:
            links.append(('uses_source', 'source', source_id))
        scenario_id = self.document('scenario', scenario,
                                    [('from_source', 'source', source_id)] if source_id else []) if scenario.strip() else None
        if scenario_id:
            links.append(('uses_scenario', 'scenario', scenario_id))
        cues_id = None
        if cues:
            cues_id = self.note('cues', text_title(scenario, '演出绑定'), {'cues': cues, 'scenario_id': scenario_id},
                                [('for_scenario', 'scenario', scenario_id)] if scenario_id else [])
            links.append(('uses_cues', 'cues', cues_id))
        for name, ident in assets.items():
            media = self.asset(ident)
            if Path(asset_name(name)).suffix.lower() != Path(media['name']).suffix.lower():
                raise ValueError('素材名须保留原文件扩展名。')
            links.append(('uses_asset', 'asset', ident))
        return dict(source_id=source_id, scenario_id=scenario_id, cues_id=cues_id,
                    assets=assets, font=font, heading=heading, speakers=speakers), links

    def save_project(self, data):
        title = data.get('title', '')
        if not isinstance(title, str) or not 0 < len(title.strip()) <= 128:
            raise ValueError('请填写 1–128 字的项目名称。')
        title = title.strip()
        ident = data.get('id') or secrets.token_hex(8)
        path = self.record_path('project', ident)
        with self.lock:
            previous = json.loads(path.read_text(encoding='utf-8'))['metadata']['revision'] if path.is_file() else None
            if data.get('id') and (previous is None or data.get('revision') != previous):
                raise ValueError('项目版本已变化，请重新打开后再保存。')
            state, links = self.snapshot(data)
            revision_data = dict(state, project_id=ident, title=title, previous=previous)
            if previous:
                old = self.library.get('revision', previous)['metadata']
                if all(old.get(key) == value for key, value in revision_data.items() if key != 'previous'):
                    return {'id': ident, 'title': title, 'revision': previous}
                links.append(('previous_revision', 'revision', previous))
            revision = self.note('revision', title, revision_data, links)
            self._save(dict(kind='project', id=ident, label=title, metadata={'revision': revision},
                            links=[('current_revision', 'revision', revision)]))
        return {'id': ident, 'title': title, 'revision': revision}

    def load_project(self, data):
        ident = data.get('id')
        self.record_path('project', ident)
        project = self.library.get('project', ident)
        revision = data.get('revision') or project['metadata']['revision']
        item = self.library.get('revision', revision)['metadata']
        if item.get('project_id') != ident:
            raise ValueError('版本不属于此项目。')
        content = {}
        for key, kind, field, fallback in [('raw', 'source', 'text', ''), ('scenario', 'scenario', 'text', ''),
                                            ('cues', 'cues', 'cues', {})]:
            doc = item.get(f'{kind}_id')
            content[key] = self.library.get(kind, doc)['metadata'][field] if doc else fallback
        return dict(item, **content, id=ident, revision=revision)

    def refresh(self):
        skipped = 0
        with self.lock:
            for kind in (*DOCUMENT_KINDS, 'project', 'asset'):
                for path in (self.records / kind).glob('*.json'):
                    if not path.resolve().is_relative_to(self.records.resolve()):
                        continue
                    try:
                        record = json.loads(path.read_text(encoding='utf-8'))
                        if record['kind'] != kind or record['id'] != path.stem:
                            raise ValueError('记录 ID 与路径不符。')
                        self._index(record, path)
                    except (OSError, ValueError, TypeError, KeyError):
                        skipped += 1
        return skipped
