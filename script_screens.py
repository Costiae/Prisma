"""Portable Ren'Py screen definitions produced by the visual editor."""

import json
import math
from pathlib import PurePosixPath
import re

from prisma import quoted


SCREEN_FILE = 'prisma_ui.rpy'
HEADER = '# Prisma visual screens: '
VARIABLE_HEADER = '# Prisma visual variables: '
VARIABLE_FILE = 'prisma_variables.rpy'
NAME = re.compile(r'[A-Za-z_]\w*', flags=re.ASCII)
ACTION = {
    'return': 'Return()', 'start': 'Start()', 'quit': 'Quit()',
    'save': 'ShowMenu("save")', 'load': 'ShowMenu("load")', 'preferences': 'ShowMenu("preferences")',
}
TARGET_ACTION = {'jump': 'Jump', 'show': 'Show', 'hide': 'Hide', 'toggle': 'ToggleVariable', 'language': 'Language'}


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or len(value) > 64:
        raise ValueError('界面名或按钮目标须使用字母、数字和下划线，以字母或下划线起始。')
    return value


def _scalar(value):
    if type(value) is bool:
        return repr(value)
    if type(value) is int and abs(value) <= 10**15:
        return repr(value)
    if type(value) is float and math.isfinite(value) and abs(value) <= 10**15:
        return repr(value)
    if isinstance(value, str) and len(value) <= 240:
        return repr(value)
    raise ValueError('变量值须为布尔值、有限数字或不超过 240 字的文本。')


def _value(raw):
    if not isinstance(raw, str) or len(raw) > 240:
        raise ValueError('请输入不超过 240 字的值。')
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    return _scalar(value)


def render_screens(screens):
    if not isinstance(screens, dict) or len(screens) > 100:
        raise ValueError('工程最多可设计 100 个界面。')
    lines = [HEADER + json.dumps(screens, ensure_ascii=False, separators=(',', ':')), '']
    uses = {name: set() for name in screens}
    for name, controls in screens.items():
        _name(name)
        if not isinstance(controls, list) or len(controls) > 100:
            raise ValueError('每个界面最多包含 100 个控件。')
        lines.extend((f'screen {name}():', '    fixed:'))
        if not controls:
            lines.append('        null')
        for control in controls:
            if not isinstance(control, dict) or set(control) - {'argument', 'visible_when'} != {
                    'type', 'value', 'x', 'y', 'w', 'h', 'action', 'target'}:
                raise ValueError('控件参数不完整。')
            x, y, w, h = [control[key] for key in ('x', 'y', 'w', 'h')]
            if any(type(v) is not int for v in (x, y, w, h)) or not (
                    0 <= x < x + w <= 100 and 0 <= y < y + h <= 100):
                raise ValueError('请把控件完整放在画布内。')
            kind, value = control['type'], control['value']
            if not isinstance(value, str) or len(value) > 240 or any(ord(char) < 32 for char in value):
                raise ValueError('控件内容须为不超过 240 字的单行文本。')
            literal = quoted(value, dialogue=(kind in ('text', 'button')))
            indent = '        '
            if condition := control.get('visible_when'):
                lines.append(f'{indent}if {_name(condition)}:')
                indent += '    '
            if kind == 'text':
                statement = f'text {literal}'
            elif kind == 'variable':
                statement = f'text str({_name(value)})'
            elif kind == 'component':
                uses[name].add(_name(value))
                statement = 'fixed'
            elif kind == 'button':
                action = control['action']
                if not isinstance(action, str) or action not in {*ACTION, *TARGET_ACTION, 'set'}:
                    raise ValueError('请选择按钮动作。')
                if action in TARGET_ACTION:
                    handler = f'{TARGET_ACTION[action]}({quoted(_name(control["target"]))})'
                elif action == 'set':
                    handler = f'SetVariable({quoted(_name(control["target"]))}, {_value(control.get("argument", ""))})'
                else:
                    handler = ACTION[action]
                lines.append(f'{indent}textbutton {literal}:')
                lines.append(f'{indent}    action {handler}')
                statement = ''
            elif kind == 'image':
                path = PurePosixPath(value)
                if ('\\' in value or ':' in value or path.is_absolute() or '..' in path.parts
                        or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp')):
                    raise ValueError('请选择 game/ 下的相对图片路径。')
                statement = f'add {literal}'
            elif kind == 'panel':
                if not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
                    raise ValueError('色块请填写 #RRGGBB 或 #RRGGBBAA。')
                statement = f'add Solid({literal})'
            else:
                raise ValueError('请选择一种支持的控件。')
            if statement:
                lines.append(f'{indent}{statement}:')
            lines.extend(f'{indent}    {key} {value / 100:.2f}'
                         for key, value in (('xpos', x), ('ypos', y), ('xsize', w), ('ysize', h)))
            if kind == 'component':
                lines.append(f'{indent}    use {control["value"]}()')
        lines.append('')

    def visit(name, path):
        if name in path:
            raise ValueError('复用界面之间存在循环引用。')
        for component in uses.get(name, ()):
            if component not in completed:
                visit(component, path | {name})
        completed.add(name)

    completed = set()
    for name in screens:
        if name not in completed:
            visit(name, set())
    return '\n'.join(lines)


def screens_from_source(source):
    if not source:
        return {}
    if not source.startswith(HEADER):
        raise ValueError(f'{SCREEN_FILE} 含手写代码，请在源码中编辑。')
    try:
        screens = json.loads(source.split('\n', 1)[0][len(HEADER):])
    except (ValueError, RecursionError) as error:
        raise ValueError(f'{SCREEN_FILE} 的设计数据无法读取。') from error
    if render_screens(screens) != source:
        raise ValueError(f'{SCREEN_FILE} 自生成后已在源码中修改，请保留源码并在源码中继续编辑。')
    return screens


def render_variables(variables):
    if not isinstance(variables, dict) or len(variables) > 200:
        raise ValueError('工程最多包含 200 个游戏变量。')
    lines = [VARIABLE_HEADER + json.dumps(variables, ensure_ascii=False, separators=(',', ':')), '']
    lines.extend(f'default {_name(name)} = {_scalar(value)}' for name, value in variables.items())
    return '\n'.join([*lines, ''])


def variables_from_source(source):
    if not source:
        return {}
    if not source.startswith(VARIABLE_HEADER):
        raise ValueError(f'{VARIABLE_FILE} 含手写代码，请在源码中编辑。')
    try:
        variables = json.loads(source.split('\n', 1)[0][len(VARIABLE_HEADER):])
    except (ValueError, RecursionError) as error:
        raise ValueError(f'{VARIABLE_FILE} 的变量配置无法读取。') from error
    if render_variables(variables) != source:
        raise ValueError(f'{VARIABLE_FILE} 自生成后已修改，请在源码中继续编辑。')
    return variables
