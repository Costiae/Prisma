"""Scenario and Ren'Py operations shared by the WebUI and MCP."""

import base64
from dataclasses import asdict
from hashlib import sha256
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
import re
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import urllib.error
import urllib.parse
import urllib.request

from prisma import (CHAPTER_ID, LINE_CUES, anchor_scenario, build_project, decode_mapping, draft_scenario, parse_scenario,
                    render_script, scenario_document)
from llm_credentials import LocalLlmCredentials
from api_providers import ApiProviders, api_request


def require_text(data, key):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ValueError(f'{key} 必须是文本。')
    return value


def resolve_llm_settings(config, operation):
    """Resolve task settings from the project LLM, unless that task overrides it."""
    if not isinstance(config, dict):
        return {}
    global_settings = config.get('llm', {})
    if not isinstance(global_settings, dict):
        raise ValueError('项目全局 LLM 配置须为对象。')
    overrides = config.get('llm_overrides', {})
    if not isinstance(overrides, dict):
        raise ValueError('项目 LLM Override 配置须为对象。')
    override = overrides.get(operation, {})
    if not isinstance(override, dict):
        raise ValueError(f'{operation} LLM Override 配置须为对象。')
    if not override.get('enabled', False):
        return dict(global_settings)
    settings = override.get('settings', {})
    if not isinstance(settings, dict):
        raise ValueError(f'{operation} LLM Override 设置须为对象。')
    effective = {**global_settings, **settings}
    if settings.get('base_url') and 'provider_id' not in settings:
        effective['provider_id'] = ''
    elif 'provider_id' in settings and 'base_url' not in settings:
        effective['base_url'] = ''
    return effective


def draft(data):
    speakers = data.get('speakers', {})
    if not isinstance(speakers, dict):
        raise ValueError('说话人映射须为 JSON 对象。')
    line_ids = data.get('line_ids', False)
    if type(line_ids) is not bool:
        raise ValueError('line_ids 须为布尔值。')
    config = data.get('config') or {}
    llm = resolve_llm_settings(config, 'scenario')
    if llm.get('mode', 'rules') == 'openai-compatible':
        llm = dict(llm, _api_key=data.get('_llm_api_key', ''))
        scenario, pending = draft_scenario_llm(data, speakers, llm)
        return {'scenario': scenario, 'pending': pending, 'engine': 'openai-compatible'}
    if llm.get('mode', 'rules') != 'rules':
        raise ValueError('未知的 Scenario 生成模式。')
    scenario, pending = draft_scenario(require_text(data, 'raw'), speakers, require_text(data, 'heading').strip() or None)
    return {'scenario': scenario, 'pending': pending, 'engine': 'rules'}


def llm_api_request(data, credentials):
    """Fetch model metadata or make a small live connection check."""
    if not isinstance(data, dict):
        raise ValueError('LLM API 请求须为 JSON 对象。')
    base_url = data.get('base_url', '')
    action = data.get('action', '')
    if action not in ('models', 'test') or not isinstance(base_url, str) or len(base_url) > 512:
        raise ValueError('请提供有效的 API 地址和操作。')
    parsed = urllib.parse.urlparse(base_url.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('API 地址须为有效的 HTTP 或 HTTPS 地址，且不可包含账号密码。')
    if parsed.query or parsed.fragment:
        raise ValueError('API 地址不可包含查询参数或片段。')
    if parsed.hostname.lower() not in ('localhost', '127.0.0.1', '::1') and parsed.scheme != 'https':
        raise ValueError('远程 API 必须使用 HTTPS。')
    model = data.get('model', '')
    if action == 'test' and (not isinstance(model, str) or not model.strip() or len(model) > 160):
        raise ValueError('连接测试需要有效的模型名称。')
    key = credentials.get(base_url)
    if not key and parsed.hostname.lower() not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('请先在 API 设置中保存此服务商的密钥。')
    endpoint = base_url.strip().rstrip('/')
    if not endpoint.endswith('/v1') and not endpoint.endswith('/v1beta/openai'):
        endpoint += '/v1'
    if action == 'models':
        url = endpoint + '/models'
        body = None
    else:
        url = endpoint + '/chat/completions'
        body = json.dumps({'model': model.strip(), 'max_tokens': 8,
                           'messages': [{'role': 'user', 'content': 'Reply with OK.'}]},
                          separators=(',', ':')).encode('utf-8')
    headers = {'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if key:
        headers['Authorization'] = f'Bearer {key}'
    request = urllib.request.Request(url, data=body, headers=headers, method='GET' if body is None else 'POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(2_000_001))
    except urllib.error.HTTPError as error:
        detail = error.read(800).decode('utf-8', errors='replace')
        raise ValueError(f'LLM API 返回 HTTP {error.code}：{detail[:500]}') from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError(f'LLM API 连接失败：{str(error)[:400]}') from error
    if not isinstance(result, dict):
        raise ValueError('LLM API 返回了无法识别的 JSON。')
    if action == 'models':
        items = result.get('data')
        if not isinstance(items, list):
            raise ValueError('模型接口未返回 OpenAI 兼容的 data 列表。')
        models = []
        for item in items[:1000]:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                continue
            models.append(item)
        return {'models': models, 'count': len(models)}
    choices = result.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError('请求已送达，但响应缺少模型输出。')
    message = choices[0].get('message')
    response_text = message.get('content', '') if isinstance(message, dict) else ''
    return {'connected': True, 'model': model.strip(),
            'response': str(response_text)[:100]}


SCENARIO_RESPONSE_FORMAT = (
    'Return the Scenario document itself. Its top-level keys must be "version" (number 1) and '
    '"chapters" (a non-empty array). Do not use a wrapper, a flat scene list, or an empty chapters array. '
    'Use chapter and scene IDs as JSON strings: chapter "1", scene "1:1"; next scene "1:2"; next chapter "2". '
    'Each chapter needs a non-empty title and scenes array. Each scene needs a non-empty lines array. '
    'Example shape: {"version":1,"chapters":[{"id":"1","title":"Chapter title",'
    '"scenes":[{"id":"1:1","lines":[{"text":"Original text","speaker":null,'
    '"source":{"kind":"raw","line":1}}]}]}]}. The text and title in this example are placeholders.'
)


def model_scenario_document(value, event):
    def chapter_like(item):
        return isinstance(item, dict) and 'scenes' in item

    def chapter_list(item):
        return isinstance(item, list) and bool(item) and all(chapter_like(chapter) for chapter in item)

    current = value
    keys = ('scenario', 'result', 'data', 'output')
    while isinstance(current, dict) and 'chapters' not in current and not chapter_like(current):
        wrapped = [current[key] for key in keys if isinstance(current.get(key), (dict, list))]
        if len(wrapped) != 1:
            break
        current = wrapped[0]

    converted = current is not value
    if chapter_list(current):
        document = {'version': 1, 'chapters': current}
        converted = True
    elif chapter_like(current):
        chapter = {key: field for key, field in current.items() if key != 'version'}
        document = {'version': current.get('version', 1), 'chapters': [chapter]}
        converted = True
    elif isinstance(current, dict) and chapter_like(current.get('chapter')) and 'chapters' not in current:
        document = {'version': current.get('version', 1), 'chapters': [current['chapter']]}
        converted = True
    elif isinstance(current, dict):
        document = dict(current)
        chapters = document.get('chapters')
        if isinstance(chapters, dict) and chapter_list(list(chapters.values())):
            document['chapters'] = list(chapters.values())
            event('已将模型返回的章节对象按原字段顺序转为列表。')
    else:
        document = current
    if converted and isinstance(document, dict) and 'chapters' in document:
        if current is not value and isinstance(value, dict):
            if not isinstance(current, dict) or 'version' not in current:
                document['version'] = value.get('version', 1)
        event('已整理模型返回的外层结构。')
    if isinstance(document, dict) and 'chapters' not in document:
        fields = ', '.join(str(key)[:30] for key in list(document)[:8]) or '无'
        raise ValueError(f'缺少顶层 chapters。实际字段：{fields}。可展开日志中的模型响应。')
    if isinstance(document, dict) and ('version' not in document or document['version'] == '1'):
        document['version'] = 1
        event('已将模型输出的版本字段规范为数字 1。')
    return document


def normalize_model_scenario(document, event):
    """Repair generated container IDs before the standard Scenario validator."""
    if not isinstance(document, dict):
        raise ValueError('返回的 Scenario 须为 JSON 对象。')
    chapters = document.get('chapters')
    if not isinstance(chapters, list) or not chapters:
        shape = '空数组' if chapters == [] else '空值' if chapters is None else type(chapters).__name__
        raise ValueError(f'chapters 须为非空数组，模型返回了{shape}。')
    normalize_ids = False
    seen_chapters, seen_scenes = set(), set()
    for position, chapter in enumerate(chapters, 1):
        path = f'第 {position} 章'
        if not isinstance(chapter, dict):
            raise ValueError(f'{path}须为对象。')
        title = chapter.get('title')
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f'{path}缺少非空的 title。')
        scenes = chapter.get('scenes')
        if not isinstance(scenes, list) or not scenes:
            raise ValueError(f'{path}缺少非空的 scenes 数组。')
        ident = chapter.get('id')
        valid = isinstance(ident, str) and CHAPTER_ID.fullmatch(ident) and ident not in seen_chapters
        if valid:
            seen_chapters.add(ident)
        else:
            normalize_ids = True
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict) or not isinstance(scene.get('lines'), list) or not scene['lines']:
                raise ValueError(f'{path}第 {index} 场须为带有非空 lines 数组的对象。')
            scene_id = scene.get('id')
            if (valid and isinstance(scene_id, str) and re.fullmatch(rf'{ident}:[1-9][0-9]*', scene_id)
                    and scene_id not in seen_scenes):
                seen_scenes.add(scene_id)
            else:
                normalize_ids = True
    if normalize_ids:
        for position, chapter in enumerate(chapters, 1):
            chapter['id'] = str(position)
            for index, scene in enumerate(chapter['scenes'], 1):
                scene['id'] = f'{position}:{index}'
        event('模型返回的章节或场景编号不符合格式，已按原顺序重新编号。')
    return document


def draft_scenario_llm(data, speakers, settings, event=lambda _message: None, save_failed_response=None):
    raw = require_text(data, 'raw')
    heading = require_text(data, 'heading').strip()
    base_url = settings.get('base_url', 'https://api.openai.com/v1')
    model = settings.get('model', '')
    temperature = settings.get('temperature', 0.2)
    max_tokens = settings.get('max_tokens', 4096)
    timeout = settings.get('timeout', 180)
    system = settings.get('system_prompt', '')
    if not isinstance(base_url, str) or not isinstance(model, str) or not model.strip():
        raise ValueError('请填写有效的 LLM API 地址和模型名称。')
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        raise ValueError('LLM Temperature 须为 0–2。') from None
    if not 0 <= temperature <= 2 or type(max_tokens) is not int or not 128 <= max_tokens <= 32768:
        raise ValueError('LLM Temperature 或最大输出 Token 超出范围。')
    if type(timeout) is not int or not 30 <= timeout <= 600 or not isinstance(system, str) or not system.strip():
        raise ValueError('LLM 请求超时或系统提示词无效。')
    system = f'{system.rstrip()}\n\n{SCENARIO_RESPONSE_FORMAT}'
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('LLM API 地址须以 http:// 或 https:// 开头。')
    key = settings.get('_api_key', '')
    user = json.dumps({'heading': heading, 'speaker_map_by_raw_line': speakers, 'raw_text': raw}, ensure_ascii=False)
    protocol = settings.get('_protocol', 'openai')
    payload = {'model': model.strip(), 'max_tokens': max_tokens, 'temperature': temperature,
               'messages': [{'role': 'user', 'content': user}]}
    if protocol == 'anthropic':
        if temperature > 1:
            raise ValueError('Anthropic Temperature 须为 0–1。')
        payload['system'] = system
    else:
        payload['messages'].insert(0, {'role': 'system', 'content': system})
        if parsed.hostname == 'api.openai.com' and model.strip().startswith(('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')):
            payload['max_completion_tokens'] = payload.pop('max_tokens')
            payload.pop('temperature')
    operation = 'messages' if protocol == 'anthropic' else 'chat/completions'
    event(f'已开始请求 POST /{operation}，等待响应（超时 {timeout} 秒）。')
    status, result = api_request({'base_url': base_url, 'api_key': key, 'protocol': protocol},
                                 operation, payload, timeout=timeout, limit=8_000_000, response_meta=True)
    event(f'收到 HTTP {status}，正在解析 Scenario JSON。')
    content = None
    try:
        if not isinstance(result, dict):
            raise ValueError('模型接口响应须为 JSON 对象。')
        if protocol == 'anthropic':
            content = ''.join(block['text'] for block in result['content']
                              if isinstance(block, dict) and block.get('type') == 'text'
                              and isinstance(block.get('text'), str))
            if result.get('stop_reason') == 'max_tokens':
                raise ValueError('已达到 Token 上限。请提高最大输出 Token，或缩短本次原文。')
        else:
            choice = result['choices'][0]
            if not isinstance(choice, dict):
                raise ValueError('模型接口响应缺少有效的 choices。')
            if isinstance(choice.get('message'), dict):
                content = choice['message'].get('content')
            if choice.get('finish_reason') == 'length':
                raise ValueError('已达到 Token 上限。请提高最大输出 Token，或缩短本次原文。')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('模型没有返回可解析的正文。')
        text = content.strip().lstrip('\ufeff')
        if text.startswith('```'):
            text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        parsed_document = model_scenario_document(json.loads(text), event)
        document = normalize_model_scenario(parsed_document, event)
        scenes = parse_scenario(document, allow_unresolved=True)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        if save_failed_response and isinstance(content, str) and content.strip():
            save_failed_response(content)
        raise ValueError(f'LLM 输出不符合 Scenario v1 结构：{error}') from None
    scenario = scenario_document(scenes)
    pending = [line.source for scene in scenes for line in scene.lines
               if line.speaker == '？' and line.source is not None]
    return scenario, pending


def inspect(data):
    scenes = parse_scenario(data.get('scenario'), allow_unresolved=True)
    return {'scenario': scenario_document(scenes), 'scenes': [asdict(scene) for scene in scenes]}


def read_cues(data):
    cues = data.get('cues', {})
    if isinstance(cues, dict):
        return cues
    if isinstance(cues, str):
        return decode_mapping(cues.strip() or '{}', 'cues.json')
    raise ValueError('演出动作须为 JSON 对象或 JSON 文本。')


def anchor(data):
    scenario, cues = anchor_scenario(data.get('scenario'), read_cues(data))
    return {'scenario': scenario, 'cues': cues}


ASSET_FRAMING = {
    'background': 'visual novel background, environmental storytelling, wide establishing shot, no foreground characters',
    'sprite': 'visual novel character sprite, readable silhouette, full figure with head and feet inside the frame, plain background',
    'cg': 'visual novel event illustration, deliberate composition and visible character interaction',
}


def asset_prompt(data):
    scenes = parse_scenario(data.get('scenario'), allow_unresolved=True)
    scene = next((scene for scene in scenes if scene.id == data.get('scene')), None)
    if scene is None:
        raise ValueError('找不到指定场景，请先解析 Scenario。')
    kind = require_text(data, 'kind')
    if kind not in ASSET_FRAMING:
        raise ValueError('素材类型须为 background、sprite 或 cg。')
    brief = require_text(data, 'brief').strip()
    trigger = require_text(data, 'trigger').strip()
    if len(brief) > 3000 or len(trigger) > 64:
        raise ValueError('画面说明最多 3000 字，LoRA 触发词最多 64 字。')
    line = data.get('line', 1)
    if type(line) is not int or not 1 <= line <= len(scene.lines):
        raise ValueError('行号超出场景范围。')
    context = [asdict(item) for item in scene.lines[max(0, line - 3):line + 2]]
    config = data.get('config') or {}
    prompts = config.get('prompts', {}) if isinstance(config, dict) else {}
    style = prompts.get('style', 'Japanese anime visual novel illustration, clean linework, vivid colors, crisp flat cel shading')
    negative = prompts.get('negative', 'text, watermark, frame')
    if not isinstance(style, str) or not isinstance(negative, str):
        raise ValueError('项目默认提示词须为文本。')
    if kind != 'background':
        negative += ', cropped head, extra limbs, animal ears, halo'
    return {'scene': scene.id, 'chapter': scene.chapter, 'scene_source_line': scene.lines[0].source,
            'source_line': scene.lines[line - 1].source, 'speaker': scene.lines[line - 1].speaker,
            'context': context, 'kind': kind,
            'prompt': ', '.join(part for part in (trigger, ASSET_FRAMING[kind], brief, style) if part),
            'negative': negative}


class StudioWorkflow:
    """Record the shared Scenario operations as workspace resources."""

    def __init__(self, resources):
        self.resources = resources
        self.credentials = LocalLlmCredentials(resources.library)
        self.providers = ApiProviders(resources.library)

    def draft(self, data):
        return self._record_run('scenario', data, self._draft)

    def speech(self, data):
        return self._record_run('voice-assets', data, self._speech)

    def generation_log(self, data):
        project, node = data.get('project_id') or '', data.get('node')
        ident = data.get('run_id')
        if node not in ('scenario', 'voice-assets'):
            raise ValueError('此节点没有请求日志。')
        include_response = data.get('include_response') is True
        with self.resources.library.connection() as db:
            row = db.execute("""SELECT id,state,events,response_text IS NOT NULL AS has_response,
                CASE WHEN ? AND state='failed' THEN response_text END AS response_text FROM generation_runs
                WHERE project=? AND node=? AND (? IS NULL OR id=?) ORDER BY rowid DESC LIMIT 1""",
                             (include_response, project, node, ident, ident)).fetchone()
        if not row:
            return {'run': None}
        payload = dict(row, events=json.loads(row['events']), has_response=bool(row['has_response']) and row['state']=='failed')
        if not include_response:
            payload.pop('response_text')
        return {'run': payload}

    def _record_run(self, node, data, operation):
        project = data.get('project_id') or ''
        if project:
            self.resources.project_record(project)
        ident = data.get('run_id') or uuid4().hex
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise ValueError('生成日志 ID 无效。')
        events, started = [], monotonic()
        library = self.resources.library
        with library.connection() as db:
            cursor = db.execute("""INSERT OR IGNORE INTO generation_runs (id,project,node,state,events)
                VALUES (?,?,?,'running','[]')""",
                                (ident, project, node))
            if not cursor.rowcount:
                raise ValueError('此运行已提交，请查看日志。')

        def event(message, state='running'):
            events.append({'at': datetime.now(timezone.utc).isoformat(), 'elapsed': round(monotonic() - started, 2),
                           'message': message})
            with library.connection() as db:
                db.execute('UPDATE generation_runs SET state=?,events=? WHERE id=?',
                           (state, json.dumps(events, ensure_ascii=False), ident))

        def save_failed_response(content):
            preview = content[:16000].encode('utf-8', errors='replace').decode('utf-8')
            if len(content) > 16000:
                preview += '\n\n[仅保存前 16,000 个字符]'
            with library.connection() as db:
                db.execute('UPDATE generation_runs SET response_text=? WHERE id=?', (preview, ident))

        event('服务端已接收请求。')
        try:
            result = operation(data, event, save_failed_response)
        except (ValueError, OSError) as error:
            event(f'失败：{str(error)[:450]}', 'failed')
            raise
        except Exception:
            event('失败：服务端内部错误。', 'failed')
            raise
        event('已完成，产物已登记。', 'done')
        return dict(result, run_id=ident)

    def _draft(self, data, event, save_failed_response):
        data = dict(data)
        project = data.get('project_id')
        if 'config' not in data and project:
            data['config'] = self.resources.load_project({'id': project})['config']
        settings = resolve_llm_settings(data.get('config') or {}, 'scenario')
        mode = data.get('mode', settings.get('mode', 'rules'))
        if mode not in ('rules', 'openai-compatible'):
            raise ValueError('请选择本地解析或 Provider。')
        parameters = data.get('parameters', {})
        if not isinstance(parameters, dict) or parameters.keys() - {'temperature', 'max_tokens', 'timeout', 'system_prompt'}:
            raise ValueError('Scenario 试跑参数格式无效。')
        settings.update(parameters)
        model_ref = None
        speakers = data.get('speakers', {})
        if mode == 'openai-compatible':
            if not isinstance(speakers, dict) or type(data.get('line_ids', False)) is not bool:
                raise ValueError('说话人映射或台词 ID 选项无效。')
            provider, declared = self.providers.resolve_node_model('text', data.get('model_override'))
            model = declared['id']
            settings.update(base_url=provider['base_url'], model=model, _api_key=provider['api_key'],
                            _protocol=provider['protocol'])
            event(f'使用 {provider["name"]} · {model}。')
            scenario, pending = draft_scenario_llm(data, speakers, settings, event, save_failed_response)
            result = {'scenario': scenario, 'pending': pending, 'engine': provider['protocol'] + '-compatible'}
            model_ref = {'provider_id': provider['id'], 'model': model, 'protocol': provider['protocol']}
        else:
            event('正在运行本地规则解析。')
            result = draft(dict(data, config={}))
        event(f'Scenario 已解析，{len(result["pending"])} 条对白待确认，正在登记。')
        source_id = self.resources.document('source', require_text(data, 'raw'), project=project)
        links = [('from_source', 'source', source_id)]
        generation = None
        if model_ref:
            generation = {**model_ref, **{key: settings[key] for key in
                          ('system_prompt', 'temperature', 'max_tokens', 'timeout') if key in settings}}
        scenario_id = self.resources.document('scenario', result['scenario'], links, project=project, generation=generation)
        return dict(result, source_id=source_id, scenario_id=scenario_id, **({'model_ref': model_ref} if model_ref else {}))

    def _speech(self, data, event, _save_failed_response):
        project = data.get('project_id')
        self.resources.project_record(project)
        text = require_text(data, 'text').strip()
        voice = require_text(data, 'voice').strip()
        line_id = require_text(data, 'line_id').strip()
        speed = data.get('speed', 1)
        if not 1 <= len(text) <= 4096 or not 1 <= len(voice) <= 100 or len(line_id) > 160:
            raise ValueError('请填写日语台词和声音名称。台词最多 4096 字。')
        if type(speed) not in (int, float) or not 0.25 <= speed <= 4:
            raise ValueError('语速须为 0.25–4。')
        provider, model = self.providers.resolve_node_model('audio', data.get('model_override'))
        event(f'使用 {provider["name"]} · {model["id"]}，开始请求 POST /audio/speech（超时 120 秒）。')
        status, blob = api_request(provider, 'audio/speech', {
            'model': model['id'], 'input': text, 'voice': voice, 'speed': speed, 'response_format': 'mp3',
        }, binary=True, limit=16_000_000, timeout=120, response_meta=True)
        event(f'收到 HTTP {status} · {len(blob):,} 字节，正在检查音频。')
        if not (blob.startswith(b'ID3') or len(blob) > 2 and blob[0] == 255 and blob[1] & 224 == 224):
            raise ValueError('音频 API 未返回 MP3。')
        params = {'provider_id': provider['id'], 'model': model['id'], 'voice': voice,
                  'speed': speed, 'language': 'ja', 'line_id': line_id, 'text': text}
        fingerprint = sha256(json.dumps(params, ensure_ascii=False).encode()).hexdigest()[:12]
        return self.resources.register_asset(f'voice-{fingerprint}.mp3', blob=blob, project=project, generation=params)

    def llm_status(self, data=None):
        data = data or {}
        return self.credentials.status(require_text(data, 'base_url'))

    def llm_api(self, data):
        return llm_api_request(data, self.credentials)

    def llm_credential(self, data):
        if not isinstance(data, dict):
            raise ValueError('API 凭据请求须为 JSON 对象。')
        base_url = require_text(data, 'base_url')
        action = require_text(data, 'action')
        if action == 'status':
            return self.credentials.status(base_url)
        if action == 'save':
            return self.credentials.save(base_url, require_text(data, 'api_key'))
        if action == 'clear':
            return self.credentials.clear(base_url)
        raise ValueError('未知的 API 凭据操作。')

    def anchor(self, data):
        result = anchor(data)
        project = data.get('project_id')
        state, _ = self.resources.snapshot(result, project=project)
        return dict(result, scenario_id=state['scenario_id'], cues_id=state['cues_id'])

    def background_prompt(self, data):
        project = self.resources.load_project({'id': data['project_id']})
        scene = self.resources.library.get('scene-design', require_text(data, 'id'))
        if scene['project_id'] != project['id']:
            raise ValueError('场景背景不属于当前项目。')
        meta = scene['metadata']
        prompt = ', '.join((ASSET_FRAMING['background'], meta['background_prompt']))
        negative = project['config']['prompts']['negative']
        context = {'kind': 'background', 'scene': meta['id'], 'scene_design_id': scene['id']}
        ident = self.resources.prompt(prompt, negative, context=context, project=project['id'])
        return {'prompt': prompt, 'negative': negative, 'prompt_id': ident}

    def asset_prompt(self, data):
        data = dict(data)
        project = data.get('project_id')
        if 'config' not in data and project:
            data['config'] = self.resources.load_project({'id': project})['config']
        result = asset_prompt(data)
        context = {key: result[key] for key in ('scene', 'chapter', 'kind', 'context')}
        kind = result['kind']
        if project and kind in ('background', 'cg') and result['scene_source_line']:
            with self.resources.library.connection() as db:
                design = db.execute("""SELECT id, metadata FROM records WHERE kind='scene-design' AND project_id=?
                    AND json_extract(metadata, '$.id')=? AND json_extract(metadata, '$.source_line')=?
                    ORDER BY updated DESC, id LIMIT 1""",
                    (project, result['scene'], result['scene_source_line'])).fetchone()
            if design:
                stage = json.loads(design['metadata'])
                authored = require_text(data, 'brief').strip()
                context['scene_design_id'] = design['id']
                if kind == 'background':
                    result['prompt'] = ', '.join(part for part in (ASSET_FRAMING[kind], stage['background_prompt'], authored) if part)
                elif cg_id := stage.get('cg_design_ids', {}).get(f'cg-{result["source_line"]}'):
                    cg = self.resources.library.get('cg-design', cg_id)['metadata']
                    trigger = require_text(data, 'trigger').strip()
                    result['prompt'] = ', '.join(part for part in (trigger, ASSET_FRAMING[kind], cg['prompt'], authored) if part)
                    context['cg_design_id'] = cg_id
        if project and kind == 'sprite':
            actor = require_text(data, 'actor').strip() or result['speaker']
            if actor:
                with self.resources.library.connection() as db:
                    person = db.execute("""SELECT id, metadata FROM records WHERE kind='character-design'
                        AND project_id=? AND label=? ORDER BY updated DESC, id LIMIT 1""", (project, actor)).fetchone()
                if person:
                    role = json.loads(person['metadata']).get('role')
                    if role in ('secondary', 'unreviewed'):
                        status = '次要角色' if role == 'secondary' else '待定角色'
                        raise ValueError(f'「{actor}」是{status}，未进入立绘制作名单。')
                    context['character_design_id'] = person['id']
                context['actor'] = actor
        scenario_id = self.resources.document('scenario', data.get('scenario'), project=project)
        prompt_id = self.resources.prompt(result['prompt'], result['negative'],
                                          source=scenario_id, context=context, project=project)
        return dict(result, prompt_id=prompt_id, scenario_id=scenario_id)

    def save_documents(self, data):
        inputs = dict(data, cues=read_cues(data))
        state, _ = self.resources.snapshot(inputs, project=data.get('project_id'))
        return {key: state[key] for key in ('source_id', 'scenario_id', 'cues_id')}

    def preview_script(self, data):
        scenes = parse_scenario(data.get('scenario'))
        registered = data.get('asset_ids', {})
        if not isinstance(registered, dict):
            raise ValueError('素材须为文件名映射。')
        used = {}
        paths = {}

        def resolve(name):
            if name in paths:
                return paths[name]
            upload_path(Path('.'), name)
            if name not in registered:
                raise ValueError(f'请添加素材「{name}」。')
            ident = registered[name]
            media = self.resources.asset(ident)
            if Path(name).suffix.lower() != Path(media['name']).suffix.lower():
                raise ValueError(f'素材「{name}」的扩展名与原文件不匹配。')
            used[name] = ident
            path = self.resources.asset_path(media)
            paths[name] = path
            return path

        font = require_text(data, 'font')
        if font:
            upload_path(Path('.'), font)
        config = data.get('config')
        project_title = data.get('project_title', 'Prisma')
        if data.get('project_id'):
            project = self.resources.load_project({'id': data['project_id']})
            if config is None:
                config = project['config']
            project_title = data.get('project_title', project['title'])
        points = []
        script, files = render_script(scenes, read_cues(data), Path('.'), Path(font) if font else None,
                                  asset_resolver=resolve, source_map=points, config=config,
                                  project_title=project_title)
        ids_by_path = {path: used[name] for name, path in paths.items()}
        return {'script': script, 'points': points, 'used_assets': used,
                'managed_assets': {name: ids_by_path[path] for name, path in files.items()}}

    def bind_script(self, data):
        # Store the insertion in cues so repeated compilation retains the choice.
        anchored = anchor(data)
        scenes = parse_scenario(anchored['scenario'])
        scene = next((scene for scene in scenes if scene.id == data.get('scene')), None)
        index = data.get('index')
        if scene is None or type(index) is not int or not 1 <= index <= len(scene.lines):
            raise ValueError('请选择脚本中的一条有效台词。')
        line = scene.lines[index - 1]
        if data.get('line_id') and data['line_id'] != line.id:
            raise ValueError('台词位置已变化，请重新预览脚本。')
        cue = data.get('cue')
        if not isinstance(cue, dict) or len(cue) != 1:
            raise ValueError('每次插入须提供一个演出动作。')
        commands = anchored['cues'].setdefault(LINE_CUES, {}).setdefault(line.id, [])
        if not isinstance(commands, list) or any(not isinstance(command, dict) for command in commands):
            raise ValueError('该台词的演出动作须为数组。')
        replace = data.get('replace')
        if replace is None:
            if next(iter(cue)) == 'voice':
                commands[:] = [command for command in commands if 'voice' not in command]
            commands.append(cue)
        else:
            if type(replace) is not int or not 0 <= replace < len(commands):
                raise ValueError('找不到要替换的演出动作。')
            commands[replace] = cue
        preview = self.preview_script(dict(data, **anchored))
        self.save_documents(dict(anchored, project_id=data.get('project_id')))
        return dict(anchored, **preview)


def upload_path(directory, name):
    if (
        not isinstance(name, str) or not name or name != name.strip(' .')
        or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
        or PureWindowsPath(name).is_reserved()
    ):
        raise ValueError('素材文件名须为有效的单个文件名。')
    return directory / name


def make_archive(data, root, resolve_image, *, resources=None, links=None, approved_script=None):
    scenes = parse_scenario(data.get('scenario'))
    uploads = data.get('files', {})
    references = data.get('image_assets', {})
    registered = data.get('asset_ids', {})
    if not all(isinstance(items, dict) for items in (uploads, references, registered)):
        raise ValueError('素材须为文件名映射。')
    if registered and resources is None:
        raise ValueError('没有用于读取已登记素材的工作目录。')
    names = [upload_path(root, name).name for name in (*uploads, *references, *registered)]
    if len(names) != len({name.casefold() for name in names}):
        raise ValueError('有重复的素材文件名。')
    assets = root / 'assets'
    assets.mkdir()
    total = 0
    for name, encoded in uploads.items():
        if not isinstance(encoded, str):
            raise ValueError('素材文件内容无效。')
        blob = base64.b64decode(encoded, validate=True)
        total += len(blob)
        if total > 45 * 1024 * 1024:
            raise ValueError('上传素材总计超过 45 MiB。')
        (assets / name).write_bytes(blob)
    for name, ref in references.items():
        if not isinstance(ref, dict):
            raise ValueError('生成素材须提供任务 ID 和文件名。')
        source = resolve_image(require_text(ref, 'job_id'), 'images', require_text(ref, 'name'))
        if Path(name).suffix.lower() != source.suffix.lower():
            raise ValueError('素材名须保留原图的扩展名。')
        shutil.copyfile(source, assets / name)
    for name, ident in registered.items():
        media = resources.asset(ident)
        if Path(name).suffix.lower() != Path(media['name']).suffix.lower():
            raise ValueError('素材名须保留原文件扩展名。')
        shutil.copyfile(resources.asset_path(media), assets / name)

    font_name = require_text(data, 'font')
    font = upload_path(assets, font_name) if font_name else None
    script, files = render_script(scenes, read_cues(data), assets, font, confine_assets=True,
                                  config=data.get('config'), project_title=data.get('project_title', 'Prisma'))
    if approved_script is not None and script != approved_script:
        raise ValueError('工程内容与已通过的脚本版本不同。请回到脚本生成，预览并通过当前版本。')
    project = root / 'project'
    build_project(project, script, files)
    if resources is not None and links is not None:
        used = {}
        for source in files.values():
            name = source.name
            if name in registered:
                used[name] = registered[name]
            elif name in references:
                used[name] = resources.register_asset(name, artifact=references[name], project=data.get('project_id'))['id']
            else:
                used[name] = resources.register_asset(name, blob=source.read_bytes(), project=data.get('project_id'))['id']
        inputs = dict(data, assets=used, cues=read_cues(data))
        _, provenance = resources.snapshot(inputs, project=data.get('project_id'))
        links.extend(provenance)
    return Path(shutil.make_archive(str(root / 'prisma-renpy'), 'zip', project))
