"""Versioned outputs and input gates for a project's production nodes."""

import json

from pipeline_nodes import DEFAULT_NODE_REGISTRY, REFERENCE_KINDS, SNAPSHOT_KINDS
from script_editor import require_project_start


def short_text(value, limit, name):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{name}须为不超过 {limit} 字的文本。')
    return value.strip()


class PipelineWorkflow:
    def __init__(self, resources, jobs=None, *, registry=DEFAULT_NODE_REGISTRY, plugins=None):
        self.resources = resources
        self.library = resources.library
        self.jobs = jobs
        self._registry = registry
        self.plugins = plugins

    @property
    def registry(self):
        return self.plugins.registry if self.plugins is not None else self._registry

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
        for source in self.registry.upstream[node]:
            if version := heads.get(source):
                entry = self._entry(project, version)
                if entry['status'] == 'released':
                    releases[source] = entry
        return releases

    def _resolve(self, node, incoming, previous=None, sources=None):
        previous = previous or {}
        if sources is None:
            sources = {}
        if not isinstance(sources, dict) or any(port not in {item.id for item in self.registry.ports[node]}
                                                for port in sources):
            raise ValueError('输入口选择无效。')
        resolved = {}
        ports = []
        for port in self.registry.describe_ports(node):
            key = port['id']
            available = {source: incoming[source] for source in port['sources'] if source in incoming}
            if key in sources:
                selected = sources[key]
                if not isinstance(selected, str) or selected not in available:
                    raise ValueError(f'{port["title"]}的所选来源尚未放行或不可用。')
            else:
                preferred = previous.get('input_sources', {}).get(key)
                selected = preferred if preferred in available else next(
                    (source for source in available if source in previous.get('inputs', {})), next(iter(available), None))
            if selected is not None:
                resolved[selected] = available[selected]
            ports.append({**port, 'available': available, 'selected': selected})
        for source in incoming:
            if source not in self.registry.required_upstream[node]:
                resolved[source] = incoming[source]
        return {'received': resolved, 'ports': ports,
                'missing': [port['id'] for port in ports if port['required'] and port['selected'] is None]}

    @staticmethod
    def _fresh(entry, inputs):
        previous = entry.get('inputs', {})
        current = inputs['received']
        return not inputs['missing'] and all(previous.get(source) == version for source, version in current.items()) and (
            'input_sources' not in entry or previous == current)

    @staticmethod
    def _require_inputs(inputs):
        if inputs['missing']:
            titles = [port['title'] for port in inputs['ports'] if port['id'] in inputs['missing']]
            raise ValueError(f'等待上游输入：{"、".join(titles)}。')

    def require_ready(self, data):
        project = data.get('project_id')
        with self.resources.lock:
            node = self.registry.require_id(data.get('node'))
            self._project(project)
            heads = self._heads(project)
            incoming = {source: entry['id'] for source, entry in self._incoming(project, heads, node).items()}
            inputs = self._resolve(node, incoming, self._current(project, heads, node), data.get('input_sources'))
            self._require_inputs(inputs)
            return {'node': node, **inputs}

    def _statuses(self, project, record, entries):
        revision_id = record['metadata']['revision']
        revision = self.library.get('revision', revision_id)['metadata']
        upstream = {node: {source: entries[source]['id'] for source in sources
                           if source in entries and entries[source]['status'] == 'released'}
                    for node, sources in self.registry.upstream.items()}
        resolved = {node: self._resolve(node, incoming, entries.get(node)) for node, incoming in upstream.items()}
        fresh = {node for node, entry in entries.items() if self._fresh(entry, resolved[node])}
        activities = self._activities(project)
        result = {}
        for node, definition in self.registry.nodes.items():
            entry = entries.get(node, {})
            inputs = resolved[node]
            incoming, missing = inputs['received'], inputs['missing']
            job = activities.get(node, {})
            recent = job and job['at'] > entry.get('updated', '')
            saved = self._has_new_snapshot(definition.snapshot, entry, revision_id, revision)
            status = entry.get('status')
            if job.get('state') in ('running', 'cancelling'):
                value = 'processing'
            elif not missing and ((recent and job['state'] == 'done') or saved or (
                    status == 'pending' and node in fresh and (entry.get('content') or entry.get('references')))):
                value = 'waiting_review'
            elif status == 'released' and node in fresh:
                value = 'released'
            elif self.registry.forward[node] and all(
                    entries.get(target, {}).get('status') == 'released' and target in fresh
                    and node not in entries[target].get('inputs', {}) for target in self.registry.forward[node]):
                value = 'skipped'
            elif not missing and (incoming or entry or job):
                value = 'waiting_processing'
            elif missing and (incoming or entry or job):
                value = 'waiting_upstream'
            else:
                value = 'unfinished'
            result[node] = {'state': value, **inputs}
        return result

    def _has_new_snapshot(self, snapshot, entry, revision_id, revision):
        if not snapshot:
            return False
        kind = SNAPSHOT_KINDS[snapshot]
        previous = [ref['id'] for ref in entry.get('references', []) if ref['kind'] == kind]
        if entry.get('status') == 'released' and not previous:
            return False
        if kind != 'revision':
            ident = revision.get(f'{kind}_id')
            return bool(ident and ident not in previous)
        assets = revision.get('assets')
        return bool(assets and revision_id not in previous and not any(
            self.library.get('revision', ident)['metadata'].get('assets') == assets for ident in previous))

    def _activities(self, project):
        with self.library.connection() as db:
            runs = db.execute("""
                SELECT node, state, coalesce(replace(json_extract(events,'$[#-1].at'),'+00:00','Z'),'') AS at
                FROM (SELECT *, row_number() OVER (
                    PARTITION BY node ORDER BY (state='running') DESC, rowid DESC) AS latest
                    FROM generation_runs WHERE project=?) WHERE latest=1
                """, (project,)).fetchall()
            outputs = db.execute("""
                WITH scoped AS (
                    SELECT id, CASE WHEN kind='job' THEN state ELSE 'done' END AS state, updated AS at,
                           CASE kind
                               WHEN 'job' THEN CASE json_extract(metadata,'$.job_kind')
                                   WHEN 'train' THEN 'train'
                                   WHEN 'install' THEN 'models'
                                   WHEN 'generate' THEN coalesce(json_extract(metadata,'$.options.pipeline_node'),
                                       CASE WHEN json_extract(metadata,'$.options.scene_design_id')
                                       THEN 'background-generation' ELSE 'generation' END)
                               END
                               WHEN 'prompt' THEN CASE WHEN json_type(metadata,'$.context.context')='array' THEN 'prompt' END
                               ELSE kind
                           END AS node
                    FROM records WHERE project_id=? AND kind IN ('job', 'prompt', 'dataset', 'script', 'export')
                )
                SELECT id, node, state, at FROM (SELECT *, row_number() OVER (
                    PARTITION BY node ORDER BY at DESC, id DESC) AS latest FROM scoped WHERE node IS NOT NULL)
                WHERE latest=1
                """, (project,)).fetchall()
        result = {row['node']: dict(row) for row in runs}
        active = self.jobs.active if self.jobs is not None else None
        for row in outputs:
            activity = dict(row)
            if activity['state'] in ('running', 'cancelling') and self.jobs is not None:
                if active is None or active.directory.name != activity['id']:
                    activity['state'] = 'interrupted'
            result[row['node']] = activity
        return result

    def state(self, data):
        project = data.get('project_id')
        with self.resources.lock:
            record = self._project(project)
            heads = self._heads(project)
            entries = {node: self._entry(project, ident) for node, ident in heads.items() if node in self.registry.nodes}
            return {'project_id': project, 'roadmap': self.registry.roadmap,
                    'nodes': entries, 'statuses': self._statuses(project, record, entries)}

    def inputs(self, data):
        project = data.get('project_id')
        with self.resources.lock:
            node = self.registry.require_id(data.get('node'))
            record = self._project(project)
            heads = self._heads(project)
            available = self._incoming(project, heads, node)
            entries = {source: self._entry(project, ident) for source, ident in heads.items() if source in self.registry.nodes}
            status = self._statuses(project, record, entries)[node]
            if 'input_sources' in data:
                selection = self._resolve(node, {source: entry['id'] for source, entry in available.items()},
                                          entries.get(node), data['input_sources'])
                status = {**status, **selection}
            incoming = {source: available[source] for source in status['received']}
            result = {'project_id': project, 'node': node, 'inputs': list(incoming.values()), 'status': status}
            if ('background-assets' in self.registry.upstream[node] and 'background-assets' not in incoming
                    and (version := heads.get('background-assets'))):
                background = self._entry(project, version)
                if background['status'] in ('pending', 'refine'):
                    refs = [ref for ref in background['references'] if ref['kind'] == 'scene-design']
                    if refs:
                        result['draft_scene_background'] = {'id': version, 'references': refs}
            if node == 'music-assets':
                music_inputs = dict(incoming)
                if draft := result.get('draft_scene_background'):
                    music_inputs['background-assets'] = draft
                result['music_briefs'] = self._music_briefs(music_inputs)
        return result

    def _music_briefs(self, incoming):
        groups = {}
        for source, kind in (('content-overview', 'work-style'), ('background-assets', 'scene-design'),
                             ('character-overview', 'character-design')):
            records = [self.library.get(kind, ref['id'])
                       for ref in incoming.get(source, {}).get('references', []) if ref['kind'] == kind]
            if kind == 'work-style':
                groups[source] = [{'label': row['label'], 'style': row['metadata']['music']} for row in records]
            elif kind == 'scene-design':
                groups[source] = [{'label': row['label'], 'scene': row['metadata']['id'],
                                   'cues': row['metadata'].get('music', [])} for row in records]
            else:
                groups[source] = [{'label': row['label'], 'traits': row['metadata'].get('traits', [])}
                                  for row in records if row['metadata'].get('role') == 'primary']
        return groups

    def approved(self, data):
        """Read the current passed outputs without copying their resources."""
        project = data.get('project_id')
        with self.resources.lock:
            selected = self.registry.require_id(data['node']) if 'node' in data else None
            self._project(project)
            heads = self._heads(project)
            entries = {node: self._entry(project, ident) for node, ident in heads.items() if node in self.registry.nodes}
            items = []
            for node, entry in entries.items():
                if entry['status'] != 'released' or (selected and node != selected):
                    continue
                incoming = {source: entries[source]['id'] for source in self.registry.upstream[node]
                            if source in entries and entries[source]['status'] == 'released'}
                references = {(ref['kind'], ref['id']): ref for ref in entry['references']}
                for ref in entry['references']:
                    if ref['kind'] == 'revision':
                        revision = self.library.get('revision', ref['id'])['metadata']
                        for name, ident in revision.get('assets', {}).items():
                            references.setdefault(('asset', ident), {'kind': 'asset', 'id': ident, 'label': name})
                items.append({**entry, 'title': self.registry.nodes[node].title,
                              'needs_review': not self._fresh(entry, self._resolve(node, incoming, entry)),
                              'references': list(references.values()),
                              'content': entry['content'] if selected else entry['content'][:240]})
        items.sort(key=lambda item: (item['updated'], item['node']), reverse=True)
        return {'project_id': project, 'items': items}

    def reusable(self, data):
        project = data.get('project_id')
        registry = self.registry
        node = registry.require_id(data.get('node'))
        self._project(project)
        query = short_text(data.get('query', ''), 200, '搜索词')
        items = {}
        definition = registry.nodes[node]
        for kind in definition.reuse:
            owned = self.library.list(kind=kind, project=project, query=query, limit=35)['items']
            shared = self.library.list(kind=kind, shared=True, query=query, limit=20)['items']
            for item in (*owned, *shared):
                if item['available'] is False or item['state'] in ('not_installed', 'running', 'cancelling', 'failed', 'cancelled', 'interrupted'):
                    continue
                if definition.required_output == 'image' and not definition.accepts_reference(self.library.get(kind, item['id'])):
                    continue
                items[kind, item['id']] = {
                    'kind': kind, 'id': item['id'], 'label': item['label'], 'shared': item['project_id'] is None,
                }
        return {'project_id': project, 'node': node, 'items': list(items.values())}

    def _references(self, project, node, data):
        definition = self.registry.nodes[node]
        references = data.get('references', [])
        if not isinstance(references, list) or len(references) > 20:
            raise ValueError('最多选用 20 个关联产物。')
        references = list(references)
        capture = data.get('capture_project', False)
        if type(capture) is not bool:
            raise ValueError('是否关联当前项目版本须为布尔值。')
        result = {}
        has_output = False
        for item in references:
            if (not isinstance(item, dict) or item.get('kind') not in REFERENCE_KINDS
                    or not isinstance(item.get('id'), str) or not 0 < len(item['id']) <= 300):
                raise ValueError('关联产物须包含有效的资源类别和 ID。')
            kind, ident = item['kind'], item['id']
            record = self.library.get(kind, ident)
            if record.get('project_id') not in (None, project):
                raise ValueError('关联产物属于其他项目。')
            if definition.inline_output == 'script' and kind == 'script' and 'managed_assets' in record['metadata']:
                require_project_start(record['metadata'])
            has_output = has_output or definition.accepts_reference(record)
            result[kind, ident] = {'kind': kind, 'id': ident, 'label': record['label']}
        if capture:
            kind = SNAPSHOT_KINDS.get(definition.snapshot)
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
        if definition.required_output == 'image' and not has_output:
            raise ValueError('请选用一张可用的实际图片后再通过。')
        return list(result.values())

    def _append(self, project, heads, node, event):
        links = [('uses_resource', ref['kind'], ref['id']) for ref in event['references']]
        links.extend(('from_node_version', self._version_kind(ident), ident) for ident in event['inputs'].values())
        if event['previous']:
            links.append(('previous_node_version', self._version_kind(event['previous']), event['previous']))
        ident = self.resources.note('node-version', self.registry.nodes[node].title, event, links, project=project)
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
        definition = self.registry.nodes[node]
        if not isinstance(output, dict):
            raise ValueError('节点产物须为对象。')
        if definition.inline_output == 'prompt':
            text = short_text(output.get('prompt'), 4000, '正面提示词')
            negative = short_text(output.get('negative', ''), 4000, '负面提示词')
            if not text:
                raise ValueError('请先在本节点填写正面提示词。')
            return {'kind': 'prompt', 'id': self.resources.prompt(text, negative, project=project)}
        if definition.inline_output == 'script':
            text = output.get('script')
            if not short_text(text, 1_000_000, 'Ren’Py 脚本'):
                raise ValueError('请先生成脚本预览。')
            ident = self.resources.note('script', definition.title, {'text': text}, project=project)
            return {'kind': 'script', 'id': ident}
        raise ValueError('此节点的产物请通过当前项目版本或资源引用提交。')

    def _check_image_result(self, project, node, references, resolution):
        if node not in ('generation', 'background-generation'):
            return
        model = next(port for port in resolution['ports'] if port['id'] == 'model')
        selected_version = model['available'][model['selected']]
        for ref in references:
            kind, ident = ref['kind'], ref['id']
            if kind in ('asset', 'artifact'):
                media = self.library.get(kind, ident)['metadata']
                ident = media.get('job_id') or (media.get('artifact') or {}).get('job_id')
            elif kind != 'job':
                continue
            if not ident:
                continue
            job = self.library.get('job', ident)
            options = job['metadata'].get('options', {})
            if job['project_id'] != project or options.get('pipeline_node') != node or 'pipeline_inputs' not in options:
                continue
            if job['state'] != 'done':
                raise ValueError('请等待图像任务完成后再通过。')
            if options['pipeline_inputs'].get(model['selected']) != selected_version:
                raise ValueError('图像任务使用的模型来源或版本与当前输入口不同，请切换回运行时的来源或重新生成。')

    def decide(self, data):
        """Apply an explicit decision from a node workspace."""
        project = data.get('project_id')
        decision = data.get('decision')
        if decision not in ('retry', 'pass', 'withdraw'):
            raise ValueError('请选择重试或通过。')
        with self.resources.lock:
            node = self.registry.require_id(data.get('node'))
            self._project(project)
            heads = self._heads(project)
            current = self._current(project, heads, node)
            if data.get('base', '') != current['id']:
                raise ValueError('节点已有较新的版本，请刷新后再操作。')
            available = {source: item['id'] for source, item in self._incoming(project, heads, node).items()}
            resolution = self._resolve(node, available, current, data.get('input_sources'))
            incoming = resolution['received']
            if decision != 'withdraw':
                self._require_inputs(resolution)
            changed = {}
            if decision == 'pass':
                targets = list(self.registry.downstream[node])
                if 'inputs' in data and data['inputs'] != incoming:
                    raise ValueError('上游输入已更新，请刷新并复核本节点产物。')
                changed_inputs = not self._fresh(current, resolution)
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
                self._check_image_result(project, node, references, resolution)
                if not content and not references:
                    raise ValueError('请在本节点制作产物，或选择已有产物后再通过。')
                event = dict(node=node, previous=current['id'], status='released', decision='pass',
                             content=content, references=references, inputs=incoming, targets=targets,
                             input_sources={port['id']: port['selected'] for port in resolution['ports']})
            else:
                event = {key: current[key] for key in ('node', 'content', 'references', 'inputs')}
                if event['inputs'] != incoming:
                    event.update(content='', references=[])
                event.update(previous=current['id'], status='refine', decision=decision, targets=[])
            changed[node] = self._append(project, heads, node, event)
            self._save_heads(project, heads)
            return {'node': node, 'changed': changed, 'next': node}

