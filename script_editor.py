"""Editable Ren'Py revisions and their portable project assets."""

from difflib import unified_diff
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil

from prisma import asset_destination, quoted, write_project
from script_screens import SCREEN_FILE, VARIABLE_FILE, render_screens, render_variables, screens_from_source, variables_from_source
from script_story import cut_project_story, edit_story, inspect_project_story, inspect_story


ASSET_LITERAL = re.compile(r'''["'](prisma_assets/[^"'\r\n]+)["']''')
LABEL = re.compile(r'^label\s+([A-Za-z_]\w*)\s*(?:\([^\n]*\))?\s*:', re.MULTILINE)
SOURCE_TYPES = frozenset({'.rpy', '.rpym', '.py', '.json', '.txt', '.md', '.csv', '.toml', '.xml'})
SCRIPT_TYPES = frozenset({'.rpy', '.rpym'})
VARIANT_FILE = 'prisma-variants.json'


def revision_sources(record):
    if 'files' in record:
        return record['files']
    return {'script.rpy': record['text']} if record.get('text') else {}


def require_project_start(record):
    for name, text in revision_sources(record).items():
        if PurePosixPath(name).suffix.lower() == '.rpy' and LABEL.search(text):
            if any(scene['name'] == 'start' for scene in inspect_story({'text': text})['scenes']):
                return
    raise ValueError('工程的 .rpy 文件中缺少入口 label start。')


def project_path(name):
    if not isinstance(name, str) or len(name) > 240:
        raise ValueError('工程文件名无效。')
    parts = name.split('/')
    if any(not part or part in ('.', '..') or part.endswith((' ', '.')) or part.startswith(' ')
           or re.search(r'[\\:<>"|?*\x00-\x1f]', part) or PureWindowsPath(part).is_reserved() for part in parts):
        raise ValueError(f'请使用 game/ 下的可移植相对路径「{name}」。')
    return PurePosixPath(name)


def check_paths(*groups):
    seen = {}
    for group in groups:
        for name in group:
            parts = project_path(name).parts
            for count in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:count])
                key = prefix.casefold()
                entry = (prefix, count == len(parts))
                if key in seen and (entry[1] or seen[key] != entry):
                    raise ValueError(f'工程路径冲突「{prefix}」。')
                seen[key] = entry


class ScriptEditor:
    def __init__(self, resources, pipeline):
        self.resources = resources
        self.pipeline = pipeline
        self.library = resources.library

    def open(self, data):
        project = data.get('project_id')
        with self.resources.lock:
            self.resources.project_record(project)
            try:
                head = self.library.get('script-draft', project)['metadata']['revision']
            except FileNotFoundError:
                head = ''
            ident = data.get('revision', head)
            if not ident:
                return {'project_id': project, 'id': '', 'text': '', 'files': {}, 'managed_assets': {},
                        'project_assets': {}, 'previous': ''}
            record = self.library.get('script', ident)
            if record['project_id'] != project or 'managed_assets' not in record['metadata']:
                raise ValueError('此脚本版本不属于当前项目的 IDE。')
            metadata = record['metadata']
            return dict(metadata, id=ident, files=revision_sources(metadata), project_assets=metadata.get('project_assets', {}))

    def _asset(self, project, ident):
        media = self.resources.asset(ident)
        if media.get('project_id') not in (None, project):
            raise ValueError('素材属于其他项目。')
        path = self.resources.asset_path(media)
        return media, path

    def _files(self, project, sources, assets):
        if not isinstance(assets, dict):
            raise ValueError('脚本素材须为导出路径到资源 ID 的映射。')
        files = {}
        managed = {match for text in sources.values() for match in ASSET_LITERAL.findall(text)}
        for destination in sorted(managed):
            if destination not in assets:
                raise ValueError(f'脚本引用了未登记的素材「{destination}」。请从资产栏重新插入。')
            media, path = self._asset(project, assets[destination])
            actual = asset_destination(path, Path(media['name']).suffix)
            if actual != destination:
                raise ValueError(f'素材版本与脚本路径不一致「{destination}」。')
            files[destination] = path
        return files

    def _project_assets(self, project, aliases):
        if not isinstance(aliases, dict) or len(aliases) > 2000:
            raise ValueError('工程素材须为路径到资源 ID 的映射，最多 2000 项。')
        files = {}
        for name, ident in aliases.items():
            suffix = project_path(name).suffix.lower()
            media, source = self._asset(project, ident)
            if suffix != Path(media['name']).suffix.lower():
                raise ValueError(f'工程素材须保留原文件扩展名「{name}」。')
            files[name] = source
        return files

    @staticmethod
    def _sources(data, current):
        if 'files' in data:
            sources = data['files']
        elif 'text' in data:
            sources = dict(current, **{'script.rpy': data['text']})
        else:
            raise ValueError('请提供工程的 files 或单文件 text。')
        if not isinstance(sources, dict) or not 1 <= len(sources) <= 500:
            raise ValueError('工程须包含 1–500 个源文件。')
        files = {}
        total = 0
        for name, content in sources.items():
            if project_path(name).suffix.lower() not in SOURCE_TYPES:
                raise ValueError(f'源文件类型不受支持「{name}」。')
            if not isinstance(content, str) or len(content) > 1_000_000 or '\0' in content:
                raise ValueError(f'源文件须为文本，最多 100 万字符「{name}」。')
            total += len(content)
            files[name] = content.replace('\r\n', '\n').replace('\r', '\n')
        if total > 4_000_000:
            raise ValueError('工程源码合计最多 400 万字符。')
        check_paths(files)
        return files

    def save(self, data):
        project = data.get('project_id')
        with self.resources.lock:
            current = self.open({'project_id': project})
            if data.get('base', '') != current['id']:
                raise ValueError('脚本已有较新版本。请重新打开后再保存。')
            sources = self._sources(data, current['files'])
            assets = data.get('managed_assets', current['managed_assets'])
            aliases = data.get('project_assets', current['project_assets'])
            managed = self._files(project, sources, assets)
            named_assets = self._project_assets(project, aliases)
            check_paths(sources, managed, named_assets)
            used = {name: assets[name] for name in managed}
            if (sources, used, aliases) == (current['files'], current['managed_assets'], current['project_assets']):
                return current
            metadata = {'text': sources.get('script.rpy', ''), 'files': sources, 'managed_assets': used,
                        'project_assets': aliases, 'previous': current['id']}
            links = [('uses_asset', 'asset', ident) for ident in sorted(set(used.values()) | set(aliases.values()))]
            if current['id']:
                links.append(('previous_script', 'script', current['id']))
            ident = self.resources.note('script', 'Ren’Py 工程源码', metadata, links, project=project)
            self.resources._save({'kind': 'script-draft', 'id': project, 'label': 'Ren’Py 编辑草稿',
                                  'metadata': {'project_id': project, 'revision': ident},
                                  'links': [*self.resources.ownership(project), ('current_script', 'script', ident)]})
            return dict(metadata, id=ident, project_id=project)

    def prepare_asset(self, data):
        project = data.get('project_id')
        ident = data.get('asset_id')
        items = self.pipeline.approved({'project_id': project})['items']
        if not any(ref['kind'] == 'asset' and ref['id'] == ident
                   for item in items if not item['needs_review'] for ref in item['references']):
            raise ValueError('请选择当前项目中已经通过且无需复核的资产。')
        media, source = self._asset(project, ident)
        path = asset_destination(source, Path(media['name']).suffix)
        kind = data.get('action')
        literal = quoted(path)
        if kind == 'path':
            snippet = literal
        elif kind == 'background' and media['media_type'] == 'image':
            snippet = f'show expression {literal} as prisma_background at prisma_backdrop zorder 0'
        elif kind == 'portrait' and media['media_type'] == 'image':
            snippet = f'show expression {literal} as prisma_actor at prisma_portrait zorder 10'
        elif kind in ('music', 'sound', 'voice') and media['media_type'] == 'audio':
            snippet = f'{"" if kind == "voice" else "play "}{kind} {literal}'
        else:
            raise ValueError('该插入动作与素材类型不匹配。')
        return {'snippet': snippet, 'path': path, 'asset_id': ident}

    def edit_story(self, data):
        targets = ()
        if 'files' in data:
            files = self._sources(data, {})
            name = data.get('file')
            if name not in files or not name.lower().endswith('.rpy'):
                raise ValueError('请选择要编辑的 .rpy 文件。')
            targets = {item['name'] for item in inspect_project_story(files)['scenes']}
            data = dict(data, text=files[name])
        return edit_story(data, self.prepare_asset, targets)

    def story(self, data):
        files = self._sources(data, {}) if data.get('files') else {}
        return inspect_project_story(files, data.get('file'), data.get('scene'))

    def localized_asset(self, data):
        project = data.get('project_id')
        self.resources.project_record(project)
        language = data.get('language')
        if not isinstance(language, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', language):
            raise ValueError('语言标识须以小写字母开头，可用小写字母、数字和下划线。')
        source = project_path(data.get('path'))
        if source.parts[0].lower() == 'tl':
            raise ValueError('请选择默认素材的 game/ 相对路径。')
        path = project_path(f'tl/{language}/{source}')
        ident = data.get('asset_id')
        self._project_assets(project, {str(path): ident})
        return {'path': str(path), 'asset_id': ident}

    @staticmethod
    def screens(data):
        source = data.get('text', '')
        if not isinstance(source, str) or len(source) > 1_000_000:
            raise ValueError('界面源码最多 100 万字符。')
        screens = screens_from_source(source)
        if 'screens' in data:
            screens = data['screens']
            source = render_screens(screens)
            if len(source) > 1_000_000:
                raise ValueError('界面源码最多 100 万字符。')
        return {'file': SCREEN_FILE, 'screens': screens, 'text': source}

    @staticmethod
    def variables(data):
        source = data.get('text', '')
        if not isinstance(source, str) or len(source) > 100_000:
            raise ValueError('变量源码最多 10 万字符。')
        variables = variables_from_source(source)
        if 'variables' in data:
            variables = data['variables']
            source = render_variables(variables)
        return {'file': VARIABLE_FILE, 'variables': variables, 'text': source}

    def variants(self, data):
        source = data.get('text', '')
        if 'project_id' in data:
            source = self.open(data)['files'].get(VARIANT_FILE, '')
        if not isinstance(source, str) or len(source) > 100_000:
            raise ValueError('发行版本配置最多 10 万字符。')
        try:
            variants = data['variants'] if 'variants' in data else json.loads(source or '{}')
        except (ValueError, RecursionError) as error:
            raise ValueError(f'{VARIANT_FILE} 必须是有效的 JSON 对象。') from error
        if not isinstance(variants, dict) or len(variants) > 30:
            raise ValueError('工程最多配置 30 个发行变体。')
        for name, config in variants.items():
            if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name) or name == 'main':
                raise ValueError('变体标识须以小写字母开头，可用小写字母、数字和下划线。main 由系统保留。')
            if not isinstance(config, dict) or set(config) != {'stop_at', 'exclude'}:
                raise ValueError('每个发行变体需要截止场景和排除路径列表。')
            stop = config['stop_at']
            if not isinstance(stop, str) or (stop and not re.fullmatch(r'[A-Za-z_]\w*', stop, re.ASCII)):
                raise ValueError('请选择有效的截止场景。')
            exclude = config['exclude']
            if not isinstance(exclude, list) or len(exclude) > 500:
                raise ValueError('排除路径最多 500 项。')
            for path in exclude:
                project_path(path)
        return {'file': VARIANT_FILE, 'variants': variants,
                'text': json.dumps(variants, ensure_ascii=False, indent=2) + '\n'}

    def variant(self, record, name):
        if name in (None, '', 'main'):
            return record
        if not isinstance(name, str):
            raise ValueError('请选择发行变体。')
        sources = revision_sources(record)
        variants = self.variants({'text': sources.get(VARIANT_FILE, '')})['variants']
        if name not in variants:
            raise ValueError('已通过的工程版本中没有此发行变体。')
        config = variants[name]
        excluded = set(config['exclude'])
        actual = sources.keys() | record['project_assets'].keys() | record['managed_assets'].keys()
        if missing := excluded - actual:
            raise ValueError(f'发行配置中的路径不存在「{min(missing)}」。')
        files = {path: text for path, text in sources.items() if path not in excluded}
        if config['stop_at']:
            files = cut_project_story(files, config['stop_at'])
        result = dict(record, files=files, **{
            key: {path: ident for path, ident in record[key].items() if path not in excluded}
            for key in ('project_assets', 'managed_assets')
        })
        require_project_start(result)
        return result

    @staticmethod
    def starter(_data):
        def control(kind, value, x, y, w, h, action='return'):
            return {'type': kind, 'value': value, 'x': x, 'y': y, 'w': w, 'h': h, 'action': action, 'target': ''}

        return {'files': {
            'script.rpy': 'label start:\n    "在剧情工作区写下第一幕。"\n    return\n',
            SCREEN_FILE: render_screens({'main_menu': [
                control('panel', '#172826', 0, 0, 100, 100),
                control('text', '新游戏', 10, 18, 70, 16),
                control('button', '开始游戏', 10, 44, 28, 10, 'start'),
                control('button', '退出', 10, 58, 28, 10, 'quit'),
            ]}),
        }}

    def history(self, data):
        project = data.get('project_id')
        cursor = data.get('cursor')
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ValueError('无效的历史游标。')
        current = self.open({'project_id': project, **({'revision': cursor} if cursor else {})})
        items = []
        while current['id'] and len(items) < 25:
            ident = current['id']
            record = self.library.get('script', ident)
            items.append({'id': ident, 'previous': current['previous'], 'updated': record['updated'],
                          'files': len(current['files'])})
            if not current['previous']:
                break
            current = self.open({'project_id': project, 'revision': current['previous']})
        return {'items': items, 'next': items[-1]['previous'] if items else ''}

    def diff(self, data):
        project = data.get('project_id')
        current = self.open({'project_id': project, 'revision': data.get('revision', '')})
        base = self.open({'project_id': project, 'revision': data.get('base', current['previous'])})
        before = base['files']
        after = current['files']
        files = [{'file': name, 'kind': 'added' if name not in before else 'deleted' if name not in after else 'changed'}
                 for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
        media_before = {**base['managed_assets'], **base['project_assets']}
        media_after = {**current['managed_assets'], **current['project_assets']}
        assets = [{'file': name, 'before': media_before.get(name), 'after': media_after.get(name)}
                  for name in sorted(media_before.keys() | media_after.keys())
                  if media_before.get(name) != media_after.get(name)]
        result = {'id': current['id'], 'base': base['id'], 'files': files, 'assets': assets}
        if name := data.get('file'):
            project_path(name)
            old, new = before.get(name, ''), after.get(name, '')
            if max(len(old), len(new)) > 150_000 or max(old.count('\n'), new.count('\n')) > 3000:
                result['too_large'] = True
            else:
                result['patch'] = '\n'.join(unified_diff(
                    old.splitlines(), new.splitlines(), fromfile=name, tofile=name, lineterm='',
                ))
        return result

    def merge(self, data):
        project = data.get('project_id')
        base_id = data.get('base', '')
        if not isinstance(base_id, str):
            raise ValueError('请提供草稿所基于的版本。')
        latest = self.open({'project_id': project})
        base = self.open({'project_id': project, 'revision': base_id})
        if data.get('expected_head', latest['id']) != latest['id']:
            raise ValueError('工程已再次更新，请重新合并。')
        resolutions = data.get('resolutions', {})
        if not isinstance(resolutions, dict) or any(side not in ('draft', 'latest') for side in resolutions.values()):
            raise ValueError('合并时请选择草稿或最新保存版。')
        draft = {'files': self._sources(data, base['files'])}
        for group in ('project_assets', 'managed_assets'):
            values = data.get(group, base[group])
            if not isinstance(values, dict) or any(not isinstance(ident, str) for ident in values.values()):
                raise ValueError('素材映射必须提供素材 ID。')
            for name in values:
                project_path(name)
            draft[group] = values
        conflicts = []
        result = {key: latest[key] for key in ('id', 'previous', 'project_id')}
        for group, listing in draft.items():
            original, head = base[group], latest[group]
            combined = {}
            for name in sorted(original.keys() | head.keys() | listing.keys()):
                before, saved, mine = original.get(name), head.get(name), listing.get(name)
                if saved == before or saved == mine:
                    change = mine
                elif mine == before:
                    change = saved
                else:
                    key = f'{group}:{name}'
                    side = resolutions.get(key)
                    if side is None:
                        conflicts.append({'key': key, 'file': name, 'kind': group, 'draft': mine, 'latest': saved})
                        continue
                    change = mine if side == 'draft' else saved
                if change is not None:
                    combined[name] = change
            result[group] = combined
        if conflicts:
            return {'id': latest['id'], 'conflicts': conflicts}
        checked = self._sources(result, {})
        managed = self._files(project, checked, result['managed_assets'])
        aliases = self._project_assets(project, result['project_assets'])
        check_paths(checked, managed, aliases)
        return dict(result, files=checked, conflicts=[])

    def check(self, data):
        project = data.get('project_id')
        self.resources.project_record(project)
        sources = self._sources(data, {})
        managed = self._files(project, sources, data.get('managed_assets', {}))
        aliases = self._project_assets(project, data.get('project_assets', {}))
        check_paths(sources, managed, aliases)
        labels = {}
        issues = []
        has_start = False
        for name, text in sources.items():
            if PurePosixPath(name).suffix.lower() not in SCRIPT_TYPES:
                continue
            for scene in inspect_story({'text': text})['scenes']:
                label = scene['name']
                line = scene['line']
                has_start |= label == 'start' and name.lower().endswith('.rpy')
                if label in labels:
                    issues.append({'file': name, 'line': line, 'message': f'标签 {label} 已出现于 {labels[label]}。'})
                else:
                    labels[label] = name
        if not has_start:
            issues.append({'file': '', 'line': 0, 'message': '工程的 .rpy 文件中缺少入口 label start。'})
        return {'files': len(sources), 'assets': len(managed) + len(aliases), 'issues': issues}

    def materialize(self, project, output, record):
        require_project_start(record)
        sources = revision_sources(record)
        managed = self._files(project, sources, record['managed_assets'])
        aliases = self._project_assets(project, record.get('project_assets', {}))
        check_paths(sources, managed, aliases)
        write_project(output, sources, {**managed, **aliases})

    def make_archive(self, project, root, record):
        self.materialize(project, root / 'project', record)
        return Path(shutil.make_archive(str(root / 'prisma-renpy'), 'zip', root / 'project'))
