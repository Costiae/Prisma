"""Scenario and Ren'Py operations shared by the WebUI and MCP."""

import base64
from dataclasses import asdict
import json
from pathlib import Path, PureWindowsPath
import shutil

from prisma import build_project, draft_scenario, parse_scenario, read_mapping, render_script


def require_text(data, key):
    value = data.get(key, '')
    if not isinstance(value, str):
        raise ValueError(f'{key} 必须是文本。')
    return value


def draft(data):
    speakers = data.get('speakers', {})
    if not isinstance(speakers, dict):
        raise ValueError('说话人映射须为 JSON 对象。')
    scenario, pending = draft_scenario(require_text(data, 'raw'), speakers, require_text(data, 'heading').strip() or None)
    return {'scenario': scenario, 'pending': pending}


def inspect(data):
    scenes = parse_scenario(require_text(data, 'scenario'), allow_unresolved=True)
    return {'scenes': [asdict(scene) for scene in scenes]}


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


def upload_path(directory, name):
    if (
        not isinstance(name, str) or not name or name != name.strip(' .')
        or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
        or PureWindowsPath(name).is_reserved()
    ):
        raise ValueError('素材文件名须为有效的单个文件名。')
    return directory / name


def make_archive(data, root, resolve_image):
    scenes = parse_scenario(require_text(data, 'scenario'))
    uploads = data.get('files', {})
    references = data.get('image_assets', {})
    if not isinstance(uploads, dict) or not isinstance(references, dict):
        raise ValueError('素材须为文件名映射。')
    names = [upload_path(root, name).name for name in (*uploads, *references)]
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

    font_name = require_text(data, 'font')
    font = upload_path(assets, font_name) if font_name else None
    mapping = root / 'cues.json'
    cues = data.get('cues', {})
    if not isinstance(cues, (dict, str)):
        raise ValueError('演出动作须为 JSON 对象或 JSON 文本。')
    mapping.write_text((cues.strip() or '{}') if isinstance(cues, str) else json.dumps(cues, ensure_ascii=False),
                       encoding='utf-8')
    script, files = render_script(scenes, read_mapping(mapping), assets, font, confine_assets=True)
    project = root / 'project'
    build_project(project, script, files)
    return Path(shutil.make_archive(str(root / 'prisma-renpy'), 'zip', project))
