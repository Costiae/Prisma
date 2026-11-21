"""Versioned outputs and input gates for a project's production nodes."""

from collections import deque
import json
from pathlib import Path
import re

from image_library import KINDS
from script_editor import require_project_start


SNAPSHOT_KINDS = {'source': 'source', 'scenario': 'scenario', 'cues': 'cues', 'assets': 'revision'}
REFERENCE_KINDS = tuple(kind for kind in KINDS if kind not in ('project', 'pipeline', 'handoff', 'node-version', 'script-draft'))
SLUG = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')


def compile_roadmap(nodes):
    """Validate the registry and derive its directed edges."""
    if not isinstance(nodes, list) or not nodes:
        raise ValueError('节点注册表须包含至少一个节点。')
    by_id = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError('注册节点须为对象。')
        ident = node.get('id')
        if not isinstance(ident, str) or not SLUG.fullmatch(ident) or ident in by_id:
            raise ValueError(f'节点 ID 无效或重复：{ident!r}。')
        if any(not isinstance(node.get(field), str) or not node[field].strip()
               for field in ('title', 'input', 'output')):
            raise ValueError(f'请为 {ident} 填写名称、输入说明和产物说明。')
        if node.get('kind', 'live') not in ('live', 'partial', 'plan', 'reference'):
            raise ValueError(f'{ident} 的节点类别无效。')
        panels = node.get('panels', [])
        if not isinstance(panels, list) or any(not isinstance(panel, str) or not SLUG.fullmatch(panel) for panel in panels):
            raise ValueError(f'{ident} 的工具面板须为锚点 ID 列表。')
        retry_control = node.get('retry_control')
        if retry_control is not None and (not isinstance(retry_control, str) or not SLUG.fullmatch(retry_control)):
            raise ValueError(f'{ident} 的重试控件须为锚点 ID。')
        for field in ('workspace', 'reuse_workspace'):
            workspace = node.get(field)
            if workspace is not None and (not isinstance(workspace, str) or not SLUG.fullmatch(workspace)):
                raise ValueError(f'{ident} 的工作区入口须为页面锚点 ID。')
        reuse = node.get('reuse', ['source', 'asset'])
        if not isinstance(reuse, list) or any(kind not in REFERENCE_KINDS for kind in reuse):
            raise ValueError(f'{ident} 含有无效的可复用产物类别。')
        snapshot = node.get('snapshot')
        if snapshot is not None and (not isinstance(snapshot, str) or snapshot not in SNAPSHOT_KINDS):
            raise ValueError(f'{ident} 的快照类别无效。')
        by_id[ident] = node

    edges = []
    forward = {ident: [] for ident in by_id}
    degree = dict.fromkeys(by_id, 0)
    for ident, node in by_id.items():
        dependencies = node.get('depends_on', [])
        if not isinstance(dependencies, list):
            raise ValueError(f'{ident} 的依赖须为列表。')
        seen = set()
        for item in dependencies:
            feedback = isinstance(item, dict) and item.get('feedback') is True
            source = item.get('node') if feedback and item.keys() == {'node', 'feedback'} else item
            if not isinstance(source, str) or source not in by_id or source == ident or source in seen:
                raise ValueError(f'{ident} 含有未知、重复或无效的依赖：{item!r}。')
            seen.add(source)
            edges.append({'source': source, 'target': ident, 'feedback': feedback})
            if not feedback:
                forward[source].append(ident)
                degree[ident] += 1

    remaining = deque(ident for ident, count in degree.items() if count == 0)
    visited = 0
    while remaining:
        ident = remaining.popleft()
        visited += 1
        for target in forward[ident]:
            degree[target] -= 1
            if degree[target] == 0:
                remaining.append(target)
    if visited != len(by_id):
        raise ValueError('节点依赖存在环。请修正依赖，或将有意的回流边标记为 feedback。')
    return {'nodes': nodes, 'edges': edges}


ROADMAP = compile_roadmap(json.loads(Path(__file__).with_name('pipeline-nodes.json').read_text(encoding='utf-8')))
NODES = {node['id']: node for node in ROADMAP['nodes']}
UPSTREAM = {node: [] for node in NODES}
DOWNSTREAM = {node: [] for node in NODES}
for edge in ROADMAP['edges']:
    UPSTREAM[edge['target']].append(edge['source'])
    DOWNSTREAM[edge['source']].append(edge['target'])



def node_id(value):
    if not isinstance(value, str) or value not in NODES:
        raise ValueError('路线图中没有这个节点。')
    return value


def short_text(value, limit, name):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{name}须为不超过 {limit} 字的文本。')
    return value.strip()


class PipelineWorkflow:
    def __init__(self, resources):
        self.resources = resources
        self.library = resources.library

    def _project(self, project):
        return self.resources.project_record(project)

    def _heads(self, project):
        path = self.resources.record_path('pipeline', project)
        if not path.is_file():
            return {}
        metadata = json.loads(path.read_text(encoding='utf-8'))['metadata']
        return metadata.get('current_versions', metadata.get('heads', {}))

    def _entry(self, project, ident):
        record = self.library.get(self._version_kind(ident), ident)
        data = record['metadata']
        if data['project_id'] != project:
            raise ValueError('节点版本不属于此项目。')
        return dict(data, id=ident, updated=record['updated'])

    def _version_kind(self, ident):
        try:
            self.library.get('node-version', ident)
            return 'node-version'
        except FileNotFoundError:
            return 'handoff'

    def _incoming(self, project, heads, node):
        releases = {}
        for source in UPSTREAM[node]:
            if version := heads.get(source):
                entry = self._entry(project, version)
                if entry['status'] == 'released' and node in entry['targets']:
                    releases[source] = entry
        return releases

    def state(self, data):
        project = data.get('project_id')
        with self.resources.lock:
            self._project(project)
            heads = self._heads(project)
            return {'project_id': project, 'roadmap': ROADMAP,
                    'nodes': {node: self._entry(project, ident) for node, ident in heads.items() if node in NODES}}

    def inputs(self, data):
        project = data.get('project_id')
        node = node_id(data.get('node'))
        with self.resources.lock:
            self._project(project)
            incoming = self._incoming(project, self._heads(project), node)
        return {'project_id': project, 'node': node, 'inputs': list(incoming.values())}

    def approved(self, data):
        """Read the current passed outputs without copying their resources."""
        project = data.get('project_id')
        selected = node_id(data['node']) if 'node' in data else None
        with self.resources.lock:
            self._project(project)
            heads = self._heads(project)
            entries = {node: self._entry(project, ident) for node, ident in heads.items() if node in NODES}
            items = []
            for node, entry in entries.items():
                if entry['status'] != 'released' or (selected and node != selected):
                    continue
                incoming = {source: entries[source]['id'] for source in UPSTREAM[node]
                            if source in entries and entries[source]['status'] == 'released'
                            and node in entries[source]['targets']}
                references = {(ref['kind'], ref['id']): ref for ref in entry['references']}
                for ref in entry['references']:
                    if ref['kind'] == 'revision':
                        revision = self.library.get('revision', ref['id'])['metadata']
                        for name, ident in revision.get('assets', {}).items():
                            references.setdefault(('asset', ident), {'kind': 'asset', 'id': ident, 'label': name})
                items.append({**entry, 'title': NODES[node]['title'],
                              'needs_review': entry['inputs'] != incoming,
                              'references': list(references.values()),
                              'content': entry['content'] if selected else entry['content'][:240]})
        items.sort(key=lambda item: (item['updated'], item['node']), reverse=True)
        return {'project_id': project, 'items': items}

    def reusable(self, data):
        project = data.get('project_id')
        node = node_id(data.get('node'))
        self._project(project)
        query = short_text(data.get('query', ''), 200, '搜索词')
        items = {}
        for kind in NODES[node].get('reuse', ['source', 'asset']):
            owned = self.library.list(kind=kind, project=project, query=query, limit=35)['items']
            shared = self.library.list(kind=kind, shared=True, query=query, limit=20)['items']
            for item in (*owned, *shared):
                if item['available'] is False or item['state'] in ('not_installed', 'running', 'cancelling', 'failed', 'cancelled', 'interrupted'):
                    continue
                items[kind, item['id']] = {
                    'kind': kind, 'id': item['id'], 'label': item['label'], 'shared': item['project_id'] is None,
                }
        return {'project_id': project, 'node': node, 'items': list(items.values())}

    def _references(self, project, node, data):
        references = data.get('references', [])
        if not isinstance(references, list) or len(references) > 20:
            raise ValueError('最多选用 20 个关联产物。')
        references = list(references)
        capture = data.get('capture_project', False)
        if type(capture) is not bool:
            raise ValueError('是否关联当前项目版本须为布尔值。')
        result = {}
        for item in references:
            if (not isinstance(item, dict) or item.get('kind') not in REFERENCE_KINDS
                    or not isinstance(item.get('id'), str) or not 0 < len(item['id']) <= 300):
                raise ValueError('关联产物须包含有效的资源类别和 ID。')
            kind, ident = item['kind'], item['id']
            record = self.library.get(kind, ident)
            if record.get('project_id') not in (None, project):
                raise ValueError('关联产物属于其他项目。')
            if node == 'script' and kind == 'script' and 'managed_assets' in record['metadata']:
                require_project_start(record['metadata'])
            result[kind, ident] = {'kind': kind, 'id': ident, 'label': record['label']}
        if capture:
            kind = SNAPSHOT_KINDS.get(NODES[node].get('snapshot'))
            if not kind:
                raise ValueError('此节点请直接选择关联产物。')
            version = self._project(project)['metadata']['revision']
            revision = self.library.get('revision', version)['metadata']
            ident = (version if revision.get('assets') else None) if kind == 'revision' else revision.get(f'{kind}_id')
            if not ident:
                raise ValueError('当前项目还没有这个节点的可关联产物。')
            record = self.library.get(kind, ident)
            result = {key: ref for key, ref in result.items() if ref['kind'] != kind}
            result[kind, ident] = {'kind': kind, 'id': ident, 'label': record['label']}
        return list(result.values())

    def _append(self, project, heads, node, event):
        links = [('uses_resource', ref['kind'], ref['id']) for ref in event['references']]
        links.extend(('from_node_version', self._version_kind(ident), ident) for ident in event['inputs'].values())
        if event['previous']:
            links.append(('previous_node_version', self._version_kind(event['previous']), event['previous']))
        ident = self.resources.note('node-version', NODES[node]['title'], event, links, project=project)
        heads[node] = ident
        return self._entry(project, ident)

    def _save_heads(self, project, heads):
        self.resources._save({
            'kind': 'pipeline', 'id': project, 'label': '项目节点状态',
            'metadata': {'project_id': project, 'current_versions': heads},
            'links': [*self.resources.ownership(project),
                      *(('current_node_version', self._version_kind(head), head) for head in heads.values())],
        })

    def _current(self, project, heads, node):
        if version := heads.get(node):
            return self._entry(project, version)
        return dict(node=node, id='', content='', references=[], inputs={})

    def _output_reference(self, project, node, output):
        if not isinstance(output, dict):
            raise ValueError('节点产物须为对象。')
        if node == 'prompt':
            text = short_text(output.get('prompt'), 4000, '正面提示词')
            negative = short_text(output.get('negative', ''), 4000, '负面提示词')
            if not text:
                raise ValueError('请先在本节点填写正面提示词。')
            return {'kind': 'prompt', 'id': self.resources.prompt(text, negative, project=project)}
        if node == 'script':
            text = output.get('script')
            if not short_text(text, 1_000_000, 'Ren’Py 脚本'):
                raise ValueError('请先生成脚本预览。')
            ident = self.resources.note('script', NODES[node]['title'], {'text': text}, project=project)
            return {'kind': 'script', 'id': ident}
        raise ValueError('此节点的产物请通过当前项目版本或资源引用提交。')

    def decide(self, data):
        """Apply an explicit decision from a node workspace."""
        project = data.get('project_id')
        node = node_id(data.get('node'))
        decision = data.get('decision')
        if decision not in ('retry', 'pass'):
            raise ValueError('请选择重试或通过。')
        with self.resources.lock:
            self._project(project)
            heads = self._heads(project)
            current = self._current(project, heads, node)
            if data.get('base', '') != current['id']:
                raise ValueError('节点已有较新的版本，请刷新后再操作。')
            changed = {}
            if decision == 'pass':
                targets = list(DOWNSTREAM[node])
                incoming = {source: item['id'] for source, item in self._incoming(project, heads, node).items()}
                if 'inputs' in data and data['inputs'] != incoming:
                    raise ValueError('上游输入已更新，请刷新并复核本节点产物。')
                changed_inputs = current.get('inputs', {}) != incoming
                new_output = (bool(data.get('capture_project')) or bool(data.get('references'))
                              or 'output' in data or data.get('content', current['content']) != current['content'])
                if changed_inputs and current['id'] and not new_output:
                    raise ValueError('上游输入已变化。请重试本节点，或选择新的产物后再通过。')
                content = short_text(data.get('content', current['content']), 12000, '产物说明')
                payload = dict(data)
                if 'references' not in payload and not data.get('capture_project') and 'output' not in data:
                    payload['references'] = current['references']
                if 'output' in data:
                    refs = payload.get('references', [])
                    if not isinstance(refs, list):
                        raise ValueError('关联产物须为列表。')
                    payload['references'] = [*refs, self._output_reference(project, node, data['output'])]
                references = self._references(project, node, payload)
                if not content and not references:
                    raise ValueError('请在本节点制作产物，或选择已有产物后再通过。')
                event = dict(node=node, previous=current['id'], status='released', decision='pass',
                             content=content, references=references, inputs=incoming, targets=targets)
            else:
                event = {key: current[key] for key in ('node', 'content', 'references', 'inputs')}
                incoming = {source: item['id'] for source, item in self._incoming(project, heads, node).items()}
                if event['inputs'] != incoming:
                    event.update(content='', references=[])
                event.update(previous=current['id'], status='refine', decision=decision, targets=[])
            changed[node] = self._append(project, heads, node, event)
            self._save_heads(project, heads)
            return {'node': node, 'changed': changed, 'next': node}

