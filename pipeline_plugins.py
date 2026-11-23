"""Workspace plugin configuration for declarative MetaNode manifests."""

import json

from image_jobs import write_json
from pipeline_nodes import BUILTIN_NODES, LOCAL_IMAGE_MANIFEST, NodeRegistry, read_manifest_details


class PluginManager:
    def __init__(self, resources, manifests=()):
        self.lock = resources.lock
        self.settings = resources.home / 'node-plugins.json'
        self.base_nodes = list(BUILTIN_NODES)
        self.plugins = {}
        for path in (LOCAL_IMAGE_MANIFEST, *manifests):
            nodes, plugin = read_manifest_details(path)
            if plugin is None:
                self.base_nodes.extend(nodes)
                continue
            if plugin['id'] in self.plugins or not nodes:
                raise ValueError(f'插件 ID 重复或无节点：{plugin["id"]}。')
            self.plugins[plugin['id']] = (plugin, nodes)
        self._registry(set(self.plugins))
        self.enabled = set()
        if self.settings.is_file():
            stored = json.loads(self.settings.read_text(encoding='utf-8'))
            if not isinstance(stored, list) or any(not isinstance(item, str) for item in stored):
                raise ValueError('插件启用记录须为 ID 列表。')
            self.enabled = set(stored)
        self.registry = self._registry(self.enabled)

    def _registry(self, enabled):
        active = list(self.base_nodes)
        inactive = []
        for ident, (_, nodes) in self.plugins.items():
            if ident in enabled:
                active.extend(nodes)
            else:
                inactive.extend(node.id for node in nodes)
        return NodeRegistry(active, inactive=inactive)

    def list(self, data=None):
        with self.lock:
            return {'plugins': [{**plugin, 'enabled': ident in self.enabled,
                                 'nodes': [{'id': node.id, 'title': node.title} for node in nodes]}
                                for ident, (plugin, nodes) in self.plugins.items()],
                    'nodes': [{'id': node.id, 'title': node.title} for node in self.registry.nodes.values()]}

    def set_enabled(self, data):
        ident, enabled = data.get('id'), data.get('enabled')
        if not isinstance(ident, str) or ident not in self.plugins or type(enabled) is not bool:
            raise ValueError('请填写已登记的插件 ID 和启用状态。')
        with self.lock:
            selected = self.enabled | {ident} if enabled else self.enabled - {ident}
            if selected != self.enabled:
                registry = self._registry(selected)
                write_json(self.settings, sorted(selected))
                self.enabled, self.registry = selected, registry
            return self.list()
