"""Scenario and Ren'Py operations shared by the WebUI and MCP."""

import base64
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
import shutil

from prisma import anchor_scenario, build_project, decode_mapping, draft_scenario, parse_scenario, render_script


def require_text(data, key):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ValueError(f'{key} 必须是文本。')
    return value


def draft(data):
    speakers = data.get('speakers', {})
    if not isinstance(speakers, dict):
        raise ValueError('说话人映射须为 JSON 对象。')
    line_ids = data.get('line_ids', False)
    if type(line_ids) is not bool:
        raise ValueError('line_ids 须为布尔值。')
    scenario, pending = draft_scenario(require_text(data, 'raw'), speakers, require_text(data, 'heading').strip() or None)
    if line_ids:
        scenario, _ = anchor_scenario(scenario, {})
    return {'scenario': scenario, 'pending': pending}


def inspect(data):
    scenes = parse_scenario(require_text(data, 'scenario'), allow_unresolved=True)
    return {'scenes': [asdict(scene) for scene in scenes]}


def read_cues(data):
    cues = data.get('cues', {})
    if isinstance(cues, dict):
        return cues
    if isinstance(cues, str):
        return decode_mapping(cues.strip() or '{}', 'cues.json')
    raise ValueError('演出动作须为 JSON 对象或 JSON 文本。')


def anchor(data):
    scenario, cues = anchor_scenario(require_text(data, 'scenario'), read_cues(data))
    return {'scenario': scenario, 'cues': cues}


ASSET_FRAMING = {
    'background': 'visual novel background, environmental storytelling, wide establishing shot, no foreground characters',
    'sprite': 'visual novel character sprite, readable silhouette, full figure with head and feet inside the frame, plain background',
    'cg': 'visual novel event illustration, deliberate composition and visible character interaction',
}


def asset_prompt(data):
    scenes = parse_scenario(require_text(data, 'scenario'), allow_unresolved=True)
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
    style = 'Japanese anime visual novel illustration, clean linework, vivid colors, crisp flat cel shading'
    negative = 'text, watermark, frame'
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
        result = draft(data)
        source_id = self.resources.document('source', require_text(data, 'raw'))
        links = [('from_source', 'source', source_id)]
        scenario_id = self.resources.document('scenario', result['scenario'], links)
        return dict(result, source_id=source_id, scenario_id=scenario_id)

    def anchor(self, data):
        result = anchor(data)
        previous = self.resources.document('scenario', require_text(data, 'scenario'))
        scenario_id = self.resources.document('scenario', result['scenario'])
        if previous != scenario_id:
            self.resources.document('scenario', result['scenario'], [('previous_revision', 'scenario', previous)])
        state, _ = self.resources.snapshot(result)
        return dict(result, scenario_id=scenario_id, cues_id=state['cues_id'])

    def asset_prompt(self, data):
        result = asset_prompt(data)
        scenario_id = self.resources.document('scenario', require_text(data, 'scenario'))
        context = {key: result[key] for key in ('scene', 'chapter', 'kind', 'context')}
        prompt_id = self.resources.prompt(result['prompt'], result['negative'],
                                          source=scenario_id, context=context)
        return dict(result, prompt_id=prompt_id, scenario_id=scenario_id)

    def save_documents(self, data):
        inputs = dict(data, cues=read_cues(data))
        state, _ = self.resources.snapshot(inputs)
        return {key: state[key] for key in ('source_id', 'scenario_id', 'cues_id')}


def upload_path(directory, name):
    if (
        not isinstance(name, str) or not name or name != name.strip(' .')
        or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
        or PureWindowsPath(name).is_reserved()
    ):
        raise ValueError('素材文件名须为有效的单个文件名。')
    return directory / name


def make_archive(data, root, resolve_image, *, resources=None, links=None):
    scenes = parse_scenario(require_text(data, 'scenario'))
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
    script, files = render_script(scenes, read_cues(data), assets, font, confine_assets=True)
    project = root / 'project'
    build_project(project, script, files)
    if resources is not None and links is not None:
        used = {}
        for source in files.values():
            name = source.name
            if name in registered:
                used[name] = registered[name]
            elif name in references:
                used[name] = resources.register_asset(name, artifact=references[name])['id']
            else:
                used[name] = resources.register_asset(name, blob=source.read_bytes())['id']
        inputs = dict(data, assets=used, cues=read_cues(data))
        _, provenance = resources.snapshot(inputs)
        links.extend(provenance)
    return Path(shutil.make_archive(str(root / 'prisma-renpy'), 'zip', project))
