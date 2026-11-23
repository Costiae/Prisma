"""The shared node contract and the registry used by Prisma's pipeline."""

from collections import deque
from dataclasses import dataclass, fields
import json
from pathlib import Path
import re

from image_library import KINDS


SNAPSHOT_KINDS = {'source': 'source', 'scenario': 'scenario', 'cues': 'cues', 'assets': 'revision'}
REFERENCE_KINDS = tuple(kind for kind in KINDS if kind not in ('project', 'pipeline', 'handoff', 'node-version', 'script-draft'))
SLUG = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')
DEFAULT_REUSE = ('source', 'asset')
BUILTIN_MANIFEST = Path(__file__).with_name('pipeline-nodes.json')
LOCAL_IMAGE_MANIFEST = Path(__file__).with_name('plugins') / 'local-image.json'


def is_slug(value):
    return isinstance(value, str) and SLUG.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class NodeDependency:
    source: str
    feedback: bool = False

    @classmethod
    def parse(cls, value):
        if is_slug(value):
            return cls(value)
        if (isinstance(value, dict) and value.keys() == {'node', 'feedback'}
                and value['feedback'] is True and is_slug(value['node'])):
            return cls(value['node'], feedback=True)
        raise ValueError(f'无效的节点依赖：{value!r}。')


@dataclass(frozen=True, slots=True)
class NodeInput:
    id: str
    sources: tuple[str, ...]
    title: str | None = None
    required: bool = True

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or data.keys() - {'id', 'sources', 'title', 'required'}:
            raise ValueError('输入口须包含 ID 和来源列表。')
        if not isinstance(data.get('sources'), list):
            raise ValueError('输入口来源须为列表。')
        return cls(data.get('id'), tuple(data['sources']), data.get('title'), data.get('required', True))

    def __post_init__(self):
        if not is_slug(self.id) or not self.sources or not all(is_slug(source) for source in self.sources):
            raise ValueError('输入口须包含有效的 ID 和至少一个来源节点。')
        if len(set(self.sources)) != len(self.sources) or type(self.required) is not bool:
            raise ValueError(f'输入口 {self.id} 的来源重复或必填设置无效。')
        if self.title is not None and (not isinstance(self.title, str) or not self.title.strip()):
            raise ValueError(f'输入口 {self.id} 的名称须为非空文本。')


@dataclass(frozen=True, slots=True, kw_only=True)
class MetaNode:
    id: str
    title: str
    input: str
    output: str
    kind: str = 'live'
    depends_on: tuple[NodeDependency, ...] = ()
    input_ports: tuple[NodeInput, ...] = ()
    panels: tuple[str, ...] = ()
    reuse: tuple[str, ...] = DEFAULT_REUSE
    workspace: str | None = None
    work_label: str | None = None
    reuse_workspace: str | None = None
    reuse_label: str | None = None
    retry_control: str | None = None
    snapshot: str | None = None
    activity: str | None = None
    required_output: str | None = None
    inline_output: str | None = None

    @classmethod
    def from_manifest(cls, definition):
        if not isinstance(definition, dict):
            raise ValueError('节点定义须为对象。')
        known = {field.name for field in fields(cls)}
        if unknown := definition.keys() - known:
            raise ValueError(f'未知的节点字段：{", ".join(sorted(unknown))}。')
        if missing := {'id', 'title', 'input', 'output'} - definition.keys():
            raise ValueError(f'缺少节点字段：{", ".join(sorted(missing))}。')
        data = dict(definition)
        for field in ('depends_on', 'input_ports', 'panels', 'reuse'):
            if field in data:
                if not isinstance(data[field], list):
                    raise ValueError(f'{field} 须为列表。')
                data[field] = tuple(data[field])
        if 'depends_on' in data:
            data['depends_on'] = tuple(NodeDependency.parse(value) for value in data['depends_on'])
        if 'input_ports' in data:
            data['input_ports'] = tuple(NodeInput.parse(value) for value in data['input_ports'])
        return cls(**data)

    def __post_init__(self):
        if not is_slug(self.id):
            raise ValueError(f'节点 ID 无效：{self.id!r}。')
        for field in ('title', 'input', 'output', 'work_label', 'reuse_label'):
            value = getattr(self, field)
            if value is None and field in ('work_label', 'reuse_label'):
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{self.id} 的 {field} 须为非空文本。')
        for field in ('workspace', 'reuse_workspace', 'retry_control', 'activity'):
            value = getattr(self, field)
            if value is not None and not is_slug(value):
                raise ValueError(f'{self.id} 的 {field} 无效。')
        if self.kind not in ('live', 'partial', 'plan', 'reference'):
            raise ValueError(f'{self.id} 的节点类别无效。')
        if not isinstance(self.depends_on, tuple) or not all(
                isinstance(edge, NodeDependency) and is_slug(edge.source) and type(edge.feedback) is bool
                for edge in self.depends_on):
            raise ValueError(f'{self.id} 的节点依赖无效。')
        if not isinstance(self.input_ports, tuple) or not all(isinstance(port, NodeInput) for port in self.input_ports):
            raise ValueError(f'{self.id} 的输入口无效。')
        if not isinstance(self.panels, tuple) or not all(is_slug(panel) for panel in self.panels):
            raise ValueError(f'{self.id} 的工具面板须为锚点 ID。')
        if not isinstance(self.reuse, tuple) or any(kind not in REFERENCE_KINDS for kind in self.reuse):
            raise ValueError(f'{self.id} 含有无效的可复用产物类别。')
        if self.snapshot is not None and self.snapshot not in tuple(SNAPSHOT_KINDS):
            raise ValueError(f'{self.id} 的快照类别无效。')
        if self.required_output not in (None, 'image'):
            raise ValueError(f'{self.id} 的必需产物类别无效。')
        if self.inline_output not in (None, 'prompt', 'script'):
            raise ValueError(f'{self.id} 的内联产物类别无效。')

    def accepts_reference(self, record):
        if self.required_output != 'image':
            return True
        metadata = record['metadata']
        return record['available'] is True and (
            record['kind'] == 'asset' and metadata.get('media_type', '').startswith('image/')
            or record['kind'] == 'artifact' and metadata.get('category') == 'images')

    def describe(self):
        """Project a node definition into the existing WebUI/MCP JSON format."""
        data = {field.name: getattr(self, field.name) for field in fields(MetaNode)
                if field.name != 'input_ports' and getattr(self, field.name) is not None}
        data['depends_on'] = [dict(node=edge.source, feedback=True) if edge.feedback else edge.source
                              for edge in self.depends_on]
        data['panels'] = list(self.panels)
        data['reuse'] = list(self.reuse)
        return data


class NodeRegistry:
    """Resolve all nodes and dependencies once, then share the graph between callers."""

    def __init__(self, definitions, *, inactive=()):
        self.nodes = {}
        for node in definitions:
            if not isinstance(node, MetaNode):
                raise ValueError('注册节点必须继承 MetaNode。')
            if node.id in self.nodes:
                raise ValueError(f'节点 ID 重复：{node.id}。')
            self.nodes[node.id] = node
        if not self.nodes:
            raise ValueError('节点注册表须包含至少一个节点。')

        self.upstream = {node: [] for node in self.nodes}
        self.downstream = {node: [] for node in self.nodes}
        self.required_upstream = {node: [] for node in self.nodes}
        self.forward = {node: [] for node in self.nodes}
        self.ports = {}
        edges = {}
        inactive = set(inactive)
        for node in self.nodes.values():
            ports = (*node.input_ports, *(NodeInput(edge.source, (edge.source,)) for edge in node.depends_on
                                           if not edge.feedback))
            if len({port.id for port in ports}) != len(ports):
                raise ValueError(f'{node.id} 有重复的输入口。')
            self.ports[node.id] = ports
            feeds = [(source, False, port.id) for port in ports for source in port.sources]
            feeds.extend((edge.source, True, None) for edge in node.depends_on if edge.feedback)
            for source, feedback, port_id in feeds:
                if source == node.id or (source not in self.nodes and source not in inactive):
                    raise ValueError(f'{node.id} 含有未知或无效的依赖：{source}。')
                if source in inactive:
                    continue
                key = (source, node.id)
                if key in edges:
                    if feedback or edges[key]['feedback']:
                        raise ValueError(f'{node.id} 的依赖重复：{source}。')
                    edges[key]['ports'].append(port_id)
                    continue
                self.upstream[node.id].append(source)
                self.downstream[source].append(node.id)
                if not feedback:
                    self.required_upstream[node.id].append(source)
                    self.forward[source].append(node.id)
                edges[key] = {'source': source, 'target': node.id, 'feedback': feedback,
                              'ports': [port_id] if port_id is not None else []}

        degree = {node: len(sources) for node, sources in self.required_upstream.items()}
        remaining = deque(node for node, count in degree.items() if count == 0)
        visited = 0
        while remaining:
            node = remaining.popleft()
            visited += 1
            for successor in self.forward[node]:
                degree[successor] -= 1
                if degree[successor] == 0:
                    remaining.append(successor)
        if visited != len(degree):
            raise ValueError('节点依赖存在环。请修正依赖，或将有意的回流边标记为 feedback。')
        self.roadmap = {'nodes': [dict(node.describe(), input_ports=self.describe_ports(node.id))
                                  for node in self.nodes.values()], 'edges': list(edges.values())}

    def describe_ports(self, node):
        return [{'id': port.id, 'title': port.title or (
                    self.nodes[port.sources[0]].title if port.sources[0] in self.nodes else port.id),
                 'required': port.required, 'sources': [source for source in port.sources if source in self.nodes]}
                for port in self.ports[node]]

    def require_id(self, value):
        if not isinstance(value, str) or value not in self.nodes:
            raise ValueError('路线图中没有这个节点。')
        return value


def read_manifest_details(path):
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        plugin = None
        if isinstance(document, dict):
            if (document.keys() - {'version', 'extends', 'nodes', 'plugin'} or type(document.get('version')) is not int
                    or document['version'] != 1 or document.get('extends') != 'MetaNode'):
                raise ValueError('节点清单须声明 version 1 和 extends MetaNode。')
            plugin = document.get('plugin')
            if 'plugin' in document and (
                    not isinstance(plugin, dict) or plugin.keys() != {'id', 'title', 'description'}
                    or not is_slug(plugin['id']) or any(not isinstance(plugin[key], str) or not plugin[key].strip()
                                                        for key in ('title', 'description'))):
                raise ValueError('插件须声明有效的 ID、名称和说明。')
            document = document.get('nodes')
        if not isinstance(document, list):
            raise ValueError('节点清单须包含节点列表。')
        nodes = []
        for index, definition in enumerate(document, 1):
            try:
                nodes.append(MetaNode.from_manifest(definition))
            except ValueError as error:
                raise ValueError(f'第 {index} 个节点：{error}') from error
        return nodes, plugin
    except (OSError, ValueError) as error:
        raise ValueError(f'无法加载节点清单 {path}：{error}') from error


def read_manifest(path):
    return read_manifest_details(path)[0]


BUILTIN_NODES = read_manifest(BUILTIN_MANIFEST)
LOCAL_NODE_IDS = frozenset(node.id for node in read_manifest(LOCAL_IMAGE_MANIFEST))
DEFAULT_NODE_REGISTRY = NodeRegistry(BUILTIN_NODES, inactive=LOCAL_NODE_IDS)
