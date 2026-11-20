"""A lossless story projection of the supported Ren'Py statements."""

import re

from prisma import quoted


NAME = r'[A-Za-z_]\w*'
LITERAL = r'"(?:\\.|[^"\\])*"'
LABEL = re.compile(rf'label\s+({NAME})(?:\s*\([^\n]*\))?\s*:\s*(?:#.*)?')
SAY = re.compile(rf'(?:(?P<speaker>{NAME})\s+)?(?P<literal>{LITERAL})')
CHOICE = re.compile(rf'({LITERAL}):')
JUMP = re.compile(rf'jump ({NAME})')
CHARACTER = re.compile(rf'define\s+({NAME})\s*=\s*Character\(({LITERAL})\)')
MEDIA = re.compile(rf'(show expression|play music|play sound|voice) "(prisma_assets/[a-f0-9]{{24}}\.\w+)"(.*)')
TOKENS = re.compile(
    r"#[^\n]*|(\"\"\"|''')(?:\\.|(?!\1)[\s\S])*?(?:\1|\Z)"
    r"""|"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'"""
)


def _literal_text(literal):
    value = literal[1:-1]
    escapes = {'\\': '\\', '"': '"', 'n': '\n', 'u0009': '\t', ' ': ' '}
    token = re.compile(r'\\(u0009|.)|([\[{〖%])\2|[\[{〖%]')
    result = []
    offset = 0
    for match in token.finditer(value):
        if match[1] is not None and match[1] in escapes:
            decoded = escapes[match[1]]
        elif match[2]:
            decoded = match[2]
        else:
            return None
        result.extend((value[offset:match.start()], decoded))
        offset = match.end()
    return ''.join((*result, value[offset:]))


def _protected_lines(text):
    # Ignore apparent labels inside Python or dialogue triple-quoted strings.
    protected = set()
    line = offset = 0
    for token in TOKENS.finditer(text):
        if '\n' in token[0]:
            line += text.count('\n', offset, token.start())
            last = line + token[0].count('\n')
            protected.update(range(line + 1, last + 1))
            line, offset = last, token.end()
    return protected


def _scenes(lines, protected):
    scenes = []
    scene = None
    for index, raw in enumerate(lines):
        if index in protected or not raw.strip() or raw[:1].isspace() or raw.startswith('#'):
            continue
        if scene:
            scene['end'] = index
            scene = None
        if match := LABEL.fullmatch(raw):
            scene = {'name': match[1], 'line': index + 1, 'end': len(lines)}
            scenes.append(scene)
    return scenes


def _choice_rows(lines, indent):
    meaningful = [line for line in lines if line.strip()]
    if not meaningful or len(meaningful) % 2:
        return None
    options = []
    indents = None
    for header, statement in zip(meaningful[::2], meaningful[1::2]):
        header_indent = len(header) - len(header.lstrip())
        jump_indent = len(statement) - len(statement.lstrip())
        caption = CHOICE.fullmatch(header.strip())
        jump = JUMP.fullmatch(statement.strip())
        content = _literal_text(caption[1]) if caption else None
        if (content is None or not jump or not indent < header_indent < jump_indent
                or indents is not None and indents != (header_indent, jump_indent)):
            return None
        indents = (header_indent, jump_indent)
        options.append({'content': content, 'target': jump[1]})
    return options


def _rows(lines, scene, protected):
    meaningful = [index for index in range(scene['line'], scene['end'])
                  if index not in protected and lines[index].strip() and not lines[index].lstrip().startswith('#')]
    if not meaningful:
        return [], '    '
    first = lines[meaningful[0]]
    indent = first[:len(first) - len(first.lstrip())]
    starts = [index for index in meaningful if lines[index].startswith(indent)
              and not lines[index][len(indent):][:1].isspace()]
    rows = []
    for start, following in zip(starts, [*starts[1:], scene['end']]):
        end = following
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith('#')):
            end -= 1
        value = lines[start].strip()
        data = {'kind': 'source', 'content': value}
        if value == 'menu:' and (options := _choice_rows(lines[start + 1:end], len(indent))):
            data = {'kind': 'menu', 'options': options}
        elif end == start + 1:
            if (match := SAY.fullmatch(value)) and (content := _literal_text(match['literal'])) is not None:
                if match['speaker'] not in ('voice', 'pause', 'scene', 'show', 'call', 'return', 'jump', 'queue',
                                             'hide', 'with', 'play', 'window', 'if', 'elif', 'while', 'extend'):
                    data = {'kind': 'dialogue', 'speaker': match['speaker'] or '', 'content': content}
            if match := JUMP.fullmatch(value):
                data = {'kind': 'jump', 'target': match[1]}
            elif value == 'return':
                data = {'kind': 'return'}
            elif value == 'stop music':
                data = {'kind': 'stop'}
            elif value in ('pass', 'with dissolve', 'with fade', 'with None'):
                data = {'kind': 'transition', 'content': value}
            elif match := MEDIA.fullmatch(value):
                action = match[1].split()[-1]
                if action == 'expression':
                    action = 'background' if ' as prisma_background' in match[3] else 'portrait'
                data = {'kind': 'media', 'action': action, 'path': match[2], 'content': value}
        rows.append({'line': start + 1, 'end': end, **data})
    return rows, indent


def _source(data):
    text = data.get('text', '')
    if not isinstance(text, str) or len(text) > 1_000_000 or '\0' in text:
        raise ValueError('脚本最多 100 万字符。')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    protected = _protected_lines(text)
    return text, lines, _scenes(lines, protected), protected


def inspect_story(data):
    _, lines, scenes, protected = _source(data)
    names = [scene['name'] for scene in scenes]
    selected = data.get('scene') or next((name for name in names if name != 'start'), names[0] if names else '')
    scene = next((item for item in scenes if item['name'] == selected), None)
    rows = _rows(lines, scene, protected)[0] if scene else []
    characters = {match[1]: _literal_text(match[2]) or match[1]
                  for index, line in enumerate(lines) if index not in protected and (match := CHARACTER.fullmatch(line))}
    diagnostics = []
    if names and 'start' not in names:
        diagnostics.append('缺少入口场景 start。')
    if len(names) != len(set(names)):
        diagnostics.append('存在同名场景，请在源码中确认。')
    for row in rows:
        targets = [row['target']] if row['kind'] == 'jump' else [item['target'] for item in row.get('options', [])]
        diagnostics.extend(f'第 {row["line"]} 行跳转的场景 {name} 不在脚本中。' for name in targets if name not in names)
    return {'scenes': [{'name': item['name'], 'line': item['line']} for item in scenes],
            'scene': scene['name'] if scene else '', 'rows': rows, 'characters': characters,
            'diagnostics': diagnostics}


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(NAME, value):
        raise ValueError('标签须以字母或下划线开头，随后可用字母、数字或下划线。')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 10_000:
        raise ValueError('请填写一段不超过 10,000 字的文本。')
    return quoted(value.replace('\r\n', '\n').replace('\r', '\n'), dialogue=True)


def _statement(row, indent, names):
    def target(value):
        if _name(value) not in names:
            raise ValueError(f'场景「{value}」不存在。')
        return value

    kind = row.get('kind')
    if kind == 'dialogue':
        speaker = row.get('speaker', '')
        return [indent + (_name(speaker) + ' ' if speaker else '') + _text(row.get('content'))]
    if kind == 'jump':
        return [indent + 'jump ' + target(row.get('target'))]
    if kind == 'menu':
        options = row.get('options')
        if not isinstance(options, list) or not 1 <= len(options) <= 12 or not all(isinstance(item, dict) for item in options):
            raise ValueError('选项菜单需要 1–12 个去向。')
        lines = [indent + 'menu:']
        for item in options:
            lines.append(indent + '    ' + _text(item.get('content')) + ':')
            lines.append(indent + '        jump ' + target(item.get('target')))
        return lines
    if kind in ('return', 'stop'):
        return [indent + ('stop music' if kind == 'stop' else 'return')]
    raise ValueError('这种语句请在源码视图编辑。')


def edit_story(data, prepare_asset):
    text, lines, scenes, protected = _source(data)
    operation = data.get('operation')
    names = [scene['name'] for scene in scenes]
    if operation == 'add_scene':
        name = _name(data.get('scene'))
        if name in names:
            raise ValueError('已有同名场景。')
        # Preserve existing source, including the whitespace at the end of the file.
        separator = '' if not text or text.endswith('\n\n') else '\n' if text.endswith('\n') else '\n\n'
        return {'text': text + separator + f'label {name}:\n    return\n', 'scene': name}
    if operation == 'add_character':
        character = data.get('row')
        if not isinstance(character, dict):
            raise ValueError('请填写角色标识和名字。')
        ident = _name(character.get('speaker'))
        if re.search(rf'^(?:define|default)\s+{re.escape(ident)}\s*=', text, re.MULTILINE):
            raise ValueError('角色标识已存在。')
        name = character.get('content')
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or '\n' in name or '\r' in name:
            raise ValueError('请填写 1–120 字的角色名。')
        return {'text': f'define {ident} = Character({quoted(name)})\n\n' + text, 'scene': data.get('scene', '')}
    name = data.get('scene')
    scene = next((item for item in scenes if item['name'] == name), None)
    if not scene or names.count(name) != 1:
        raise ValueError('请选择唯一的场景。')
    rows, indent = _rows(lines, scene, protected)
    row = next((entry for entry in rows if entry['line'] == data.get('line')), None)
    if operation not in ('insert', 'replace', 'delete') or (row is None and (operation != 'insert' or 'line' in data)):
        raise ValueError('请选择有效的操作与脚本行。')
    if row and operation in ('replace', 'delete') and row['kind'] == 'source':
        raise ValueError('这行请在源码视图编辑。')
    replacement = []
    assets = {}
    if operation != 'delete':
        insert = data.get('row')
        if not isinstance(insert, dict):
            raise ValueError('请提供要写入的剧情行。')
        if insert.get('kind') == 'media':
            if insert.get('action') == 'path':
                raise ValueError('路径请直接插入源码。')
            asset = prepare_asset({'project_id': data.get('project_id'), 'asset_id': insert.get('asset_id'),
                                   'action': insert.get('action')})
            snippet = asset['snippet']
            for transform in ('prisma_backdrop', 'prisma_portrait'):
                if not re.search(rf'^transform\s+{transform}\s*:', text, re.MULTILINE):
                    snippet = snippet.replace(f' at {transform}', '')
            replacement = [indent + snippet]
            assets[asset['path']] = asset['asset_id']
        else:
            replacement = _statement(insert, indent, names)
    if row:
        start = row['line'] - 1
        end = start if operation == 'insert' else row['end']
    else:
        start = end = rows[-1]['end'] if rows else scene['line']
    if not replacement and len(rows) == 1:
        replacement = [indent + 'pass']
    lines[start:end] = replacement
    return {'text': '\n'.join(lines), 'scene': name, 'managed_assets': assets, 'line': start + 1}
