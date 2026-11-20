"""Project revisions and reusable media in the shared Prisma workspace."""

import base64
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import secrets
import tempfile
import threading
from urllib.parse import urlparse

from image_library import artifact_id, timestamp
from prisma import normalize_scenario


DOCUMENT_KINDS = ('source', 'scenario', 'cues', 'prompt', 'script', 'script-draft', 'revision', 'pipeline', 'node-version', 'handoff')
MEDIA_TYPES = {
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
    '.ogg': 'audio', '.opus': 'audio', '.mp3': 'audio', '.wav': 'audio', '.flac': 'audio',
    '.ttf': 'font', '.otf': 'font', '.ttc': 'font',
}
ID = re.compile(r'[0-9a-f]{16}|[0-9a-f]{64}')
DEFAULT_PROJECT_CONFIG = {
    'resolution': {'width': 1280, 'height': 720},
    'llm': {'mode': 'rules', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini',
            'temperature': 0.2, 'max_tokens': 4096, 'timeout': 180,
            'system_prompt': ('Convert the supplied novel text into Prisma Scenario JSON version 1. '
                             'Return only one JSON object with version and chapters. Each chapter has id, title, scenes; '
                             'each scene has id and lines; each line has text, speaker (string or null), and source '
                             '{"kind":"raw","line":N} when traceable to an input line. Preserve the original language, wording, '
                             'chapter and scene order. Do not summarize, rewrite, invent, omit, or merge distinct utterances. '
                             'Use the supplied speaker map for quoted dialogue; use speaker "？" for unmapped quoted dialogue '
                             'and null for narration. Keep valid sequential chapter IDs and scene IDs such as 1 and 1:1. '
                             'Optional line fields are viewpoint and direction. Do not output Markdown fences.')},
    'llm_overrides': {'scenario': {'enabled': False, 'settings': {}}},
    'generation': {'mode': 'local', 'base_model': '', 'lora': '', 'compare': 'none', 'weight': 0.35,
                   'size': '512x896', 'seed': None, 'sampler': 'euler_a', 'steps': 20, 'cfg': 6.5,
                   'count': 1, 'negative': 'worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, chromatic aberration, bad anatomy, extra limbs, text, watermark',
                   'online_model': '', 'online_size': '1024x1536', 'quality': 'medium'},
    'training': {'base_model': '', 'dataset': '', 'parent': '', 'name': 'prisma_style_next', 'steps': 200,
                 'save_every': 100, 'rank': 4, 'lr': 0.0001, 'repeats': 10, 'seed': 1065270805},
    'prompts': {'style': 'Japanese anime visual novel illustration, clean linework, vivid colors, crisp flat cel shading',
                'negative': 'text, watermark, frame'},
}


def validate_llm_settings(llm, label='LLM'):
    if not isinstance(llm, dict):
        raise ValueError(f'{label} 配置须为对象。')
    if llm.get('mode') not in ('rules', 'openai-compatible'):
        raise ValueError(f'{label} 模式须为规则解析或 OpenAI 兼容接口。')
    for key, limit in (('base_url', 512), ('model', 160)):
        if not isinstance(llm.get(key), str) or len(llm[key]) > limit:
            raise ValueError(f'{label}「{key}」格式无效。')
    if llm['mode'] == 'openai-compatible':
        endpoint = urlparse(llm['base_url'])
        if endpoint.scheme not in ('http', 'https') or not endpoint.netloc or endpoint.username or endpoint.password:
            raise ValueError(f'{label} API 地址须为不含账号密码的 HTTP(S) 地址。')
        if not llm['model'].strip():
            raise ValueError(f'请填写{label}模型名称。')
    temperature, tokens = llm.get('temperature'), llm.get('max_tokens')
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0 <= temperature <= 2
            or type(tokens) is not int or not 128 <= tokens <= 32768):
        raise ValueError(f'{label} Temperature 须为 0–2，最大输出须为 128–32768 Token。')
    if (type(llm.get('timeout')) is not int or not 30 <= llm['timeout'] <= 600
            or not isinstance(llm.get('system_prompt'), str) or not 1 <= len(llm['system_prompt']) <= 6000):
        raise ValueError(f'{label}请求超时须为 30–600 秒，系统提示词须为 1–6000 字。')


def content_id(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize_project_config(value):
    if not isinstance(value, dict):
        raise ValueError('项目配置格式无效。')
    config = json.loads(json.dumps(DEFAULT_PROJECT_CONFIG))
    for key in config:
        section = value.get(key, {})
        if not isinstance(section, dict):
            raise ValueError(f'项目配置「{key}」须为对象。')
        config[key].update(section)

    resolution = config['resolution']
    width, height = resolution.get('width'), resolution.get('height')
    if type(width) is not int or not 640 <= width <= 7680 or type(height) is not int or not 360 <= height <= 4320:
        raise ValueError('项目分辨率须为整数，宽度 640–7680，高度 360–4320。')

    llm = config['llm']
    validate_llm_settings(llm, '项目全局 LLM')
    overrides = config['llm_overrides']
    scenario_override = overrides.get('scenario', {})
    if not isinstance(scenario_override, dict) or type(scenario_override.get('enabled', False)) is not bool:
        raise ValueError('Scenario LLM Override 配置无效。')
    override_settings = scenario_override.get('settings', {})
    if not isinstance(override_settings, dict):
        raise ValueError('Scenario LLM Override 设置须为对象。')
    if scenario_override.get('enabled', False):
        validate_llm_settings({**llm, **override_settings}, 'Scenario LLM Override')
    config['llm_overrides'] = {'scenario': {
        'enabled': scenario_override.get('enabled', False), 'settings': override_settings,
    }}

    generation = config['generation']
    if generation.get('mode') not in ('local', 'online') or generation.get('base_model') not in ('', 'sd1', 'sdxl', 'anima'):
        raise ValueError('图像模式或本地底模无效。')
    if generation.get('sampler') not in ('euler_a', 'euler', 'dpm++2m', 'er_sde'):
        raise ValueError('图像采样器无效。')
    if generation.get('size') not in ('512x896', '832x1216', '896x512', '512x512', '768x768', '1024x1024'):
        raise ValueError('本地图像尺寸无效。')
    if generation.get('online_size') not in ('1024x1536', '1536x1024', '1024x1024'):
        raise ValueError('在线图像尺寸无效。')
    if generation.get('quality') not in ('low', 'medium', 'high'):
        raise ValueError('在线图像质量无效。')
    for key, limit in (('lora', 160), ('compare', 160), ('online_model', 160), ('negative', 4000)):
        if not isinstance(generation.get(key), str) or len(generation[key]) > limit:
            raise ValueError(f'图像设置「{key}」格式无效。')
    if (not isinstance(generation.get('weight'), (int, float)) or isinstance(generation.get('weight'), bool)
            or not math.isfinite(generation['weight']) or not 0.05 <= generation['weight'] <= 1.5
            or not isinstance(generation.get('cfg'), (int, float)) or isinstance(generation.get('cfg'), bool)
            or not math.isfinite(generation['cfg']) or not 1 <= generation['cfg'] <= 15):
        raise ValueError('LoRA 权重或 CFG 超出范围。')
    for key, low, high in (('steps', 1, 60), ('count', 1, 4)):
        if type(generation.get(key)) is not int or not low <= generation[key] <= high:
            raise ValueError(f'图像设置「{key}」超出范围。')
    seed = generation.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2_147_483_644):
        raise ValueError('图像 Seed 超出范围。')

    training = config['training']
    if training.get('base_model') not in ('', 'sd1', 'sdxl', 'iris3b', 'anima'):
        raise ValueError('训练底模无效。')
    for key, limit in (('dataset', 160), ('parent', 160), ('name', 48)):
        if not isinstance(training.get(key), str) or len(training[key]) > limit:
            raise ValueError(f'训练设置「{key}」格式无效。')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_\-]{0,47}', training['name']):
        raise ValueError('训练输出名格式无效。')
    for key, low, high in (('steps', 1, 5000), ('save_every', 1, 5000), ('rank', 2, 64),
                           ('repeats', 1, 50), ('seed', 0, 2_147_483_647)):
        if type(training.get(key)) is not int or not low <= training[key] <= high:
            raise ValueError(f'训练设置「{key}」超出范围。')
    lr = training.get('lr')
    if (isinstance(lr, bool) or not isinstance(lr, (int, float)) or not math.isfinite(lr)
            or not 0.000001 <= lr <= 0.001):
        raise ValueError('训练学习率须为 0.000001–0.001。')

    prompts = config['prompts']
    for key in ('style', 'negative'):
        if not isinstance(prompts.get(key), str) or len(prompts[key]) > 2000:
            raise ValueError(f'提示词设置「{key}」格式无效。')
    return config


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

    @staticmethod
    def ownership(project):
        return [('for_project', 'project', project)] if project else []

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

    def note(self, kind, label, metadata, links=(), *, project=None):
        if project:
            metadata = dict(metadata, project_id=project)
            links = [*links, *self.ownership(project)]
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

    def document(self, kind, text, links=(), *, project=None):
        if kind == 'scenario':
            data = normalize_scenario(text)
            return self.note(kind, data['chapters'][0]['title'], {'scenario': data}, links, project=project)
        return self.note(kind, text_title(text, kind), {'text': text}, links, project=project)

    def prompt(self, positive, negative, *, source=None, context=None, derived_from=None, project=None):
        links = [('from_scenario', 'scenario', source)] if source else []
        if derived_from:
            links.append(('adapted_from', 'prompt', derived_from))
        metadata = dict(prompt=positive, negative=negative)
        if context:
            metadata['context'] = context
        return self.note('prompt', text_title(positive, '提示词'), metadata, links, project=project)

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

    def register_asset(self, name, *, blob=None, artifact=None, origin=None, project=None):
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
        return self.save_asset(metadata, links, origin=origin, project=project)

    def save_asset(self, metadata, links=(), *, origin=None, project=None):
        metadata = {key: value for key, value in metadata.items() if key not in ('url', 'project_id')}
        links = [*links, *self.ownership(project)]
        if ref := metadata.get('artifact'):
            links.append(('from_artifact', 'artifact', artifact_id(ref['job_id'], 'images', ref['name'])))
        if project:
            metadata['project_id'] = project
        ident = content_id(['asset', metadata])
        metadata['url'] = f'/api/workspace/assets/{ident}'
        if origin and origin != ident:
            links.append(('adapted_from', 'asset', origin))
        with self.lock:
            path = self.record_path('asset', ident)
            if path.is_file():
                previous = json.loads(path.read_text(encoding='utf-8'))
                links = sorted({tuple(link) for link in (*previous['links'], *links)})
            self._save(dict(kind='asset', id=ident, label=metadata['name'], metadata=metadata, links=links))
        return {'id': ident, **metadata}

    def import_asset(self, data, *, project=None):
        blob = base64.b64decode(data['data'], validate=True) if isinstance(data.get('data'), str) else None
        reference = data.get('artifact')
        if reference is not None and (not isinstance(reference, dict)
                or not isinstance(reference.get('job_id'), str) or not isinstance(reference.get('name'), str)):
            raise ValueError('生成素材须包含任务 ID 和文件名。')
        return self.register_asset(data.get('name'), blob=blob, artifact=reference, project=project)

    def snapshot(self, data, *, project=None):
        raw, scenario = data.get('raw', ''), data.get('scenario', '')
        cues, assets = data.get('cues', {}), data.get('assets', {})
        font, heading, speakers = data.get('font', ''), data.get('heading', ''), data.get('speakers', {})
        config = normalize_project_config(data.get('config', {}))
        if (not all(isinstance(value, str) for value in (raw, font, heading))
                or not all(isinstance(value, dict) for value in (cues, assets, speakers))):
            raise ValueError('项目文本、演出绑定或素材清单格式无效。')
        links = []
        source_id = self.document('source', raw, project=project) if raw.strip() else None
        if source_id:
            links.append(('uses_source', 'source', source_id))
        scenario_present = scenario is not None and (not isinstance(scenario, str) or bool(scenario.strip()))
        scenario_id = self.document('scenario', scenario,
                                    [('from_source', 'source', source_id)] if source_id else [],
                                    project=project) if scenario_present else None
        if scenario_id:
            links.append(('uses_scenario', 'scenario', scenario_id))
        cues_id = None
        if cues:
            label = self.library.get('scenario', scenario_id)['label'] if scenario_id else '演出绑定'
            cues_id = self.note('cues', label, {'cues': cues, 'scenario_id': scenario_id},
                                [('for_scenario', 'scenario', scenario_id)] if scenario_id else [], project=project)
            links.append(('uses_cues', 'cues', cues_id))
        for name, ident in assets.items():
            media = self.asset(ident)
            if Path(asset_name(name)).suffix.lower() != Path(media['name']).suffix.lower():
                raise ValueError('素材名须保留原文件扩展名。')
            links.append(('uses_asset', 'asset', ident))
        return dict(source_id=source_id, scenario_id=scenario_id, cues_id=cues_id, config=config,
                    assets=assets, font=font, heading=heading, speakers=speakers), links

    def save_project(self, data):
        title = data.get('title', '')
        if not isinstance(title, str) or not 0 < len(title.strip()) <= 128:
            raise ValueError('请填写 1–128 字的项目名称。')
        title = title.strip()
        ident = data.get('id') or secrets.token_hex(8)
        path = self.record_path('project', ident)
        with self.lock:
            current = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
            if current and current.get('state') == 'trashed':
                raise ValueError('项目已在回收站，请先恢复。')
            previous = current['metadata']['revision'] if current else None
            if data.get('id') and (previous is None or data.get('revision') != previous):
                raise ValueError('项目版本已变化，请重新打开后再保存。')
            assets = data.get('assets', {})
            if not isinstance(assets, dict):
                raise ValueError('素材清单格式无效。')
            remapped = {}
            for name, asset_id in assets.items():
                media = self.asset(asset_id)
                remapped[name] = (asset_id if media.get('project_id') == ident
                                  else self.save_asset(media, origin=asset_id, project=ident)['id'])
            assets = remapped
            state, links = self.snapshot(dict(data, assets=assets), project=ident)
            revision_data = dict(state, project_id=ident, title=title, previous=previous, ownership_version=2)
            if previous:
                old = self.library.get('revision', previous)['metadata']
                if all(old.get(key) == value for key, value in revision_data.items() if key != 'previous'):
                    return {'id': ident, 'title': title, 'revision': previous, 'assets': assets,
                            'config': state['config']}
                links.append(('previous_revision', 'revision', previous))
            revision = self.note('revision', title, revision_data, links, project=ident)
            self._save(dict(kind='project', id=ident, label=title, metadata={'revision': revision},
                            links=[('current_revision', 'revision', revision)]))
        return {'id': ident, 'title': title, 'revision': revision, 'assets': assets,
                'config': state['config']}

    def project_record(self, ident):
        self.record_path('project', ident)
        project = self.library.get('project', ident)
        if project.get('state') == 'trashed':
            raise ValueError('项目已在回收站，请先恢复。')
        return project

    def list_projects(self, data):
        trash, offset = data.get('trash', False), data.get('offset', 0)
        if type(trash) is not bool or type(offset) is not int or not 0 <= offset <= 2_147_483_647:
            raise ValueError('项目列表参数无效。')
        return self.library.list(kind='project', limit=100, offset=offset, trash=trash)

    def set_project_trash(self, data, *, trash):
        ident = data.get('id')
        path = self.record_path('project', ident)
        with self.lock:
            record = json.loads(path.read_text(encoding='utf-8'))
            if (record.get('state') == 'trashed') != trash:
                if trash:
                    record['state'] = 'trashed'
                else:
                    record.pop('state', None)
                self._save(record)
        return {'id': ident, 'title': record['label'], 'trashed': trash}

    def load_project(self, data):
        ident = data.get('id')
        project = self.project_record(ident)
        revision = data.get('revision') or project['metadata']['revision']
        item = self.library.get('revision', revision)['metadata']
        if item.get('project_id') != ident:
            raise ValueError('版本不属于此项目。')
        content = {}
        for key, kind, field, fallback in [('raw', 'source', 'text', ''), ('scenario', 'scenario', 'scenario', None),
                                            ('cues', 'cues', 'cues', {})]:
            doc = item.get(f'{kind}_id')
            if not doc:
                content[key] = fallback
                continue
            metadata = self.library.get(kind, doc)['metadata']
            content[key] = (normalize_scenario(metadata.get('scenario', metadata.get('text')))
                            if kind == 'scenario' else metadata[field])
        return dict(item, config=normalize_project_config(item.get('config', {})),
                    **content, id=ident, revision=revision)

    def project_history(self, data):
        ident = data.get('id')
        project = self.project_record(ident)
        current = project['metadata']['revision']
        rows = []
        seen = set()
        while current and current not in seen and len(rows) < 100:
            seen.add(current)
            revision = self.library.get('revision', current)
            metadata = revision['metadata']
            rows.append({'revision': current, 'title': metadata.get('title', revision['label']),
                         'updated': revision.get('updated'), 'current': current == project['metadata']['revision']})
            current = metadata.get('previous')
        return {'id': ident, 'items': rows}

    def undo_project(self, data):
        ident = data.get('id')
        current = self.load_project({'id': ident})
        previous = current.get('previous')
        if not previous:
            raise ValueError('项目还没有可撤回的历史版本。')
        return self.restore_project_revision({'id': ident, 'revision': previous})

    def restore_project_revision(self, data):
        ident, target = data.get('id'), data.get('revision')
        current = self.load_project({'id': ident})
        restored = self.load_project({'id': ident, 'revision': target})
        payload = {key: restored[key] for key in ('title', 'raw', 'scenario', 'cues', 'assets', 'font',
                                                   'heading', 'speakers', 'config')}
        payload.update(id=ident, revision=current['revision'])
        return self.save_project(payload)

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
