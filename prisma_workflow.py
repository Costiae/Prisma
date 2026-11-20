"""Scenario and Ren'Py operations shared by the WebUI and MCP."""

import base64
from dataclasses import asdict
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import urllib.error
import urllib.parse
import urllib.request

from prisma import (LINE_CUES, anchor_scenario, build_project, decode_mapping, draft_scenario, parse_scenario,
                    render_script, scenario_document)


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
    return {**global_settings, **settings}


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
        scenario, pending = draft_scenario_llm(data, speakers, llm)
        return {'scenario': scenario, 'pending': pending, 'engine': 'openai-compatible'}
    if llm.get('mode', 'rules') != 'rules':
        raise ValueError('未知的 Scenario 生成模式。')
    scenario, pending = draft_scenario(require_text(data, 'raw'), speakers, require_text(data, 'heading').strip() or None)
    return {'scenario': scenario, 'pending': pending, 'engine': 'rules'}


def llm_status(_data=None):
    if os.environ.get('PRISMA_LLM_API_KEY', '').strip():
        source = 'PRISMA_LLM_API_KEY'
    elif os.environ.get('OPENAI_API_KEY', '').strip():
        source = 'OPENAI_API_KEY'
    else:
        source = ''
    return {'configured': bool(source), 'source': source}


def draft_scenario_llm(data, speakers, settings):
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
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('LLM API 地址须以 http:// 或 https:// 开头。')
    endpoint = base_url.rstrip('/')
    if not endpoint.endswith('/chat/completions'):
        endpoint += '/chat/completions'
    key = os.environ.get('PRISMA_LLM_API_KEY', '').strip() or os.environ.get('OPENAI_API_KEY', '').strip()
    if not key and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('请在启动 WebUI 前设置 PRISMA_LLM_API_KEY 或 OPENAI_API_KEY。')
    user = json.dumps({'heading': heading, 'speaker_map_by_raw_line': speakers, 'raw_text': raw}, ensure_ascii=False)
    body = json.dumps({'model': model.strip(), 'temperature': temperature, 'max_tokens': max_tokens,
                       'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]},
                      ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    request = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read(1200).decode('utf-8', errors='replace')
        raise ValueError(f'LLM 请求失败（HTTP {error.code}）：{detail[:600]}') from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError(f'无法连接 LLM 服务：{error}') from None
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f'LLM 服务返回了无效响应：{error}') from None
    try:
        content = result['choices'][0]['message']['content']
        if not isinstance(content, str):
            raise TypeError('message content is not text')
        text = content.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        document = json.loads(text)
        scenes = parse_scenario(document, allow_unresolved=True)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
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
    return {'scene': scene.id, 'chapter': scene.chapter, 'context': context, 'kind': kind,
            'prompt': ', '.join(part for part in (trigger, ASSET_FRAMING[kind], brief, style) if part),
            'negative': negative}


class StudioWorkflow:
    """Record the shared Scenario operations as workspace resources."""

    def __init__(self, resources):
        self.resources = resources

    def draft(self, data):
        data = dict(data)
        project = data.get('project_id')
        if 'config' not in data and project:
            data['config'] = self.resources.load_project({'id': project})['config']
        result = draft(data)
        source_id = self.resources.document('source', require_text(data, 'raw'), project=project)
        links = [('from_source', 'source', source_id)]
        scenario_id = self.resources.document('scenario', result['scenario'], links, project=project)
        return dict(result, source_id=source_id, scenario_id=scenario_id)

    def llm_status(self, data=None):
        return llm_status(data)

    def anchor(self, data):
        result = anchor(data)
        project = data.get('project_id')
        state, _ = self.resources.snapshot(result, project=project)
        return dict(result, scenario_id=state['scenario_id'], cues_id=state['cues_id'])

    def asset_prompt(self, data):
        data = dict(data)
        project = data.get('project_id')
        if 'config' not in data and project:
            data['config'] = self.resources.load_project({'id': project})['config']
        result = asset_prompt(data)
        scenario_id = self.resources.document('scenario', data.get('scenario'), project=project)
        context = {key: result[key] for key in ('scene', 'chapter', 'kind', 'context')}
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
