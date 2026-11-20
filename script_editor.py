"""Editable Ren'Py revisions and their portable project assets."""

from pathlib import Path
import re
import shutil

from prisma import asset_destination, build_project, quoted
from script_story import edit_story


ASSET_LITERAL = re.compile(r'''["'](prisma_assets/[^"'\r\n]+)["']''')
LABEL = re.compile(r'^label\s+([A-Za-z_]\w*)\s*(?:\([^\n]*\))?\s*:', re.MULTILINE)


def require_start(text):
    if 'start' not in LABEL.findall(text):
        raise ValueError('脚本缺少入口 label start。')


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
                return {'project_id': project, 'id': '', 'text': '', 'managed_assets': {}, 'previous': ''}
            record = self.library.get('script', ident)
            if record['project_id'] != project or 'managed_assets' not in record['metadata']:
                raise ValueError('此脚本版本不属于当前项目的 IDE。')
            return dict(record['metadata'], id=ident)

    def _asset(self, project, ident):
        media = self.resources.asset(ident)
        if media.get('project_id') not in (None, project):
            raise ValueError('素材属于其他项目。')
        path = self.resources.asset_path(media)
        destination = asset_destination(path, Path(media['name']).suffix)
        return media, path, destination

    def _files(self, project, text, assets):
        if not isinstance(assets, dict):
            raise ValueError('脚本素材须为导出路径到资源 ID 的映射。')
        files = {}
        for destination in sorted(set(ASSET_LITERAL.findall(text))):
            if destination not in assets:
                raise ValueError(f'脚本引用了未登记的素材「{destination}」。请从资产栏重新插入。')
            _, path, actual = self._asset(project, assets[destination])
            if actual != destination:
                raise ValueError(f'素材版本与脚本路径不一致「{destination}」。')
            files[destination] = path
        return files

    def save(self, data):
        project = data.get('project_id')
        text = data.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000 or '\0' in text:
            raise ValueError('脚本须为非空文本，最多 100 万字符。')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        assets = data.get('managed_assets', {})
        with self.resources.lock:
            current = self.open({'project_id': project})
            if data.get('base', '') != current['id']:
                raise ValueError('脚本已有较新版本。请重新打开后再保存。')
            files = self._files(project, text, assets)
            used = {name: assets[name] for name in files}
            if (text, used) == (current['text'], current['managed_assets']):
                return current
            metadata = {'text': text, 'managed_assets': used, 'previous': current['id']}
            links = [('uses_asset', 'asset', ident) for ident in sorted(set(used.values()))]
            if current['id']:
                links.append(('previous_script', 'script', current['id']))
            ident = self.resources.note('script', 'Ren’Py script.rpy', metadata, links, project=project)
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
        media, _, path = self._asset(project, ident)
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
        return edit_story(data, self.prepare_asset)

    def make_archive(self, project, root, record):
        text = record['text']
        require_start(text)
        files = self._files(project, text, record['managed_assets'])
        build_project(root / 'project', text, files)
        return Path(shutil.make_archive(str(root / 'prisma-renpy'), 'zip', root / 'project'))
