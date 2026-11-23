"""Serve Prisma's local editing workspace with the Python standard library."""

import argparse
import binascii
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from image_jobs import Busy, JOB_ID, own_workspace
from image_toolkit import IMAGE_TYPES, ImageToolkit, child_file
from lora_export import ARCHIVE_NAME, save_export
from prisma_mcp import PrismaMCP, VERSIONS, rpc_error
from prisma_workflow import inspect, require_text
from script_story import inspect_story


ROOT = Path(__file__).resolve().parent
STATIC = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/app.css": ("web/app.css", "text/css; charset=utf-8"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/plugins.js": ("web/plugins.js", "text/javascript; charset=utf-8"),
    "/providers.js": ("web/providers.js", "text/javascript; charset=utf-8"),
    "/images.js": ("web/images.js", "text/javascript; charset=utf-8"),
    "/pipeline.js": ("web/pipeline.js", "text/javascript; charset=utf-8"),
    "/pipeline-layout.js": ("web/pipeline-layout.js", "text/javascript; charset=utf-8"),
    "/script-workbench.js": ("web/script-workbench.js", "text/javascript; charset=utf-8"),
    "/script-ide.js": ("web/script-ide.js", "text/javascript; charset=utf-8"),
    "/script-ide-workspace.js": ("web/script-ide-workspace.js", "text/javascript; charset=utf-8"),
    "/editor-engine.js": ("web/editor-engine.js", "text/javascript; charset=utf-8"),
    "/script-story.js": ("web/script-story.js", "text/javascript; charset=utf-8"),
    "/docs/media-prompt-guide.md": ("docs/media-prompt-guide.md", "text/plain; charset=utf-8"),
    "/docs/media-checklist.md": ("docs/media-checklist.md", "text/plain; charset=utf-8"),
    "/docs/media-variants.md": ("docs/media-variants.md", "text/plain; charset=utf-8"),
    "/docs/reproducible-workflow.md": ("docs/reproducible-workflow.md", "text/plain; charset=utf-8"),
    "/docs/resource-library.md": ("docs/resource-library.md", "text/plain; charset=utf-8"),
}
MAX_REQUEST = 64 * 1024 * 1024

NODE_ROUTES = {
    '/api/draft': 'scenario_draft', '/api/scenario/production/generate': 'scenario_production_generate',
    '/api/scenario/production/publish': 'scenario_production_publish',
    '/api/asset-prompt': 'scenario_asset_prompt', '/api/scenario/background-prompt': 'scenario_background_prompt',
    '/api/speech/generate': 'speech_generate', '/api/script/preview': 'script_preview',
    '/api/script/bind': 'script_bind', '/api/build': 'renpy_export',
    '/api/images/generate': 'image_generate', '/api/images/install': 'image_install',
    '/api/images/datasets': 'dataset_save', '/api/images/datasets/restore': 'dataset_restore',
    '/api/images/loras': 'lora_import', '/api/images/loras/prepare': 'lora_prepare',
    '/api/images/loras/export': 'lora_export', '/api/images/train': 'lora_train',
    '/api/images/replay': 'image_replay',
}


def roadmap_nav_links(registry) -> bytes:
    return '\n'.join(
        f'<a href="#stage-{node.id}">{escape(node.title)}</a>'
        for node in registry.nodes.values()
    ).encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, content_type: str, body: bytes, *, attachment: bool = False, nonce=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # CodeMirror uses inline style attributes for editor geometry.
        style = f"; style-src 'self' 'nonce-{nonce}'; style-src-attr 'unsafe-inline'" if nonce else ''
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'" + style)
        if attachment:
            self.send_header("Content-Disposition", 'attachment; filename="prisma-renpy.zip"')
        try:
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            # A browser navigation can cancel pending requests from the previous view.
            return

    def respond_json(self, status: int, data: dict) -> None:
        self.respond(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def local_request(self) -> bool:
        port = self.server.server_port
        host = self.headers.get("Host")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{host}"

    def do_GET(self) -> None:
        if not self.local_request():
            self.respond_json(403, {"error": "请使用本地 Prisma 地址。"})
            return
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)
        if path == '/api/health':
            self.respond_json(200, {'ok': True, 'service': 'prisma', 'port': self.server.server_port})
            return
        if path == '/mcp':
            self.mcp_no_stream()
            return
        if path.startswith('/api/exports/'):
            self.get_export(path)
            return
        if path.startswith('/api/script/sdk/files/'):
            try:
                ident, index = path.removeprefix('/api/script/sdk/files/').split('/')
                data = {'project_id': query.get('project_id', [''])[0], 'job_id': ident}
                artifact, filename = self.server.mcp.renpy_sdk.distribution(data, int(index))
                self.send_file(artifact, 'application/octet-stream', attachment=filename)
            except (OSError, ValueError):
                self.respond_json(404, {'error': '发行包不存在。'})
            return
        if path.startswith('/api/images/'):
            self.get_image_resource(path, project=query.get('project_id', [None])[0])
            return
        if path.startswith('/api/workspace/assets/'):
            ident = path.removeprefix('/api/workspace/assets/')
            try:
                resources = self.server.images.resources
                metadata = resources.asset(ident)
                suffix = Path(metadata['name']).suffix.lower()
                audio = {'.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.mp3': 'audio/mpeg',
                         '.wav': 'audio/wav', '.flac': 'audio/flac'}
                mime = (IMAGE_TYPES.get(suffix) or audio.get(suffix)) if query.get('preview') == ['1'] else None
                self.send_file(resources.asset_path(metadata), mime or 'application/octet-stream',
                               attachment=None if mime else f'prisma-asset{suffix}')
            except (OSError, ValueError):
                self.respond_json(404, {'error': '素材不存在或无法读取。'})
            return
        resource = STATIC.get(path)
        if resource is None:
            self.respond_json(404, {"error": "页面不存在。"})
            return
        name, mime = resource
        try:
            content = (ROOT / name).read_bytes()
        except OSError:
            self.respond_json(404, {"error": "本地文件不存在。"})
            return
        nonce = secrets.token_urlsafe() if path == '/' else None
        if nonce:
            content = content.replace(b'__PRISMA_STYLE_NONCE__', nonce.encode('ascii'), 1)
            content = content.replace(b'<!-- PRISMA_ROADMAP_LINKS -->', roadmap_nav_links(self.server.mcp.pipeline.registry), 1)
        self.respond(200, mime, content, nonce=nonce)

    def mcp_no_stream(self):
        self.send_response(405)
        self.send_header('Allow', 'POST')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_DELETE(self):
        if not self.local_request():
            self.respond_json(403, {'error': '请使用本地 Prisma 地址。'})
        elif self.path == '/mcp':
            self.mcp_no_stream()
        else:
            self.respond_json(404, {'error': '接口不存在。'})

    def get_export(self, path):
        try:
            _, _, _, ident, name = path.split('/')
            if not JOB_ID.fullmatch(ident) or name not in ('prisma-renpy.zip', 'prisma-dataset.zip', ARCHIVE_NAME):
                raise FileNotFoundError()
            exports = self.server.images.home / 'exports'
            if not (exports / ident).resolve().is_relative_to(exports.resolve()):
                raise FileNotFoundError()
            self.send_file(child_file(exports / ident, name), 'application/zip', attachment=name)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError):
            self.respond_json(404, {'error': '导出文件不存在。'})

    def get_image_resource(self, path: str, *, project=None) -> None:
        toolkit = self.server.images
        route = [unquote(part) for part in path.split('/')[3:]]
        try:
            if route == ['catalog']:
                self.respond_json(200, toolkit.catalog(project))
            elif len(route) == 2 and route[0] == 'jobs':
                self.respond_json(200, toolkit.jobs.get(route[1], details=True))
            elif len(route) == 2 and route[0] == 'source':
                self.respond_json(200, toolkit.list_source(route[1]))
            elif len(route) == 3 and route[0] == 'source':
                source = toolkit.source_image(route[1], route[2])
                self.send_file(source, IMAGE_TYPES[source.suffix.lower()])
            elif len(route) == 4 and route[0] == 'files':
                artifact = toolkit.artifact(*route[1:])
                if route[2] == 'checkpoints':
                    self.send_file(artifact, 'application/octet-stream', attachment=artifact.name)
                else:
                    self.send_file(artifact, IMAGE_TYPES[artifact.suffix.lower()])
            elif len(route) == 2 and route[0] == 'dataset' and route[1].endswith('.zip'):
                ident = route[1][:-4]
                archive = toolkit.dataset_export_path(ident)
                self.send_file(archive, 'application/zip', attachment=f'prisma-dataset-{ident}.zip')
            else:
                raise FileNotFoundError('接口不存在。')
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, OSError):
            self.respond_json(404, {'error': '图像资源不存在或无法读取。'})

    def send_file(self, path: Path, mime, *, attachment=None, export_id=None) -> None:
        with path.open('rb') as stream:
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if attachment:
                self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
            if export_id:
                self.send_header('X-Prisma-Export-Id', export_id)
            self.end_headers()
            shutil.copyfileobj(stream, self.wfile)

    def do_POST(self) -> None:
        if not self.local_request():
            self.respond_json(403, {"error": "请使用本地 Prisma 地址。"})
            return
        image_routes = {
            '/api/images/generate': self.server.images.generate,
            '/api/images/online/models': self.server.images.image_api_models,
            '/api/images/online/credential': self.server.images.image_api_credential,
            '/api/images/install': self.server.images.provision,
            '/api/images/datasets': self.server.images.save_dataset,
            '/api/images/datasets/restore': self.server.images.restore_dataset,
            '/api/images/loras': self.server.images.import_lora,
            '/api/images/loras/export': lambda data: save_export(self.server.images, data),
            '/api/images/train': self.server.images.train,
            '/api/images/cancel': lambda data: self.server.images.jobs.cancel(require_text(data, 'id')),
            '/api/images/replay': lambda data: self.server.images.replay(require_text(data, 'id')),
        }
        studio = self.server.mcp.workflow
        pipeline = self.server.mcp.pipeline
        editor = self.server.mcp.script_editor
        sdk = self.server.mcp.renpy_sdk
        projects = self.server.images.resources
        routes = {'/api/draft': studio.draft, '/api/scenes': inspect, '/api/scenario/anchor': studio.anchor,
                  '/api/scenario/production/outline': self.server.mcp.production.outline,
                  '/api/scenario/production/generate': self.server.mcp.production.generate,
                  '/api/scenario/production/publish': self.server.mcp.production.publish,
                  '/api/scenario/production/defaults': self.server.mcp.production.save_defaults,
                  '/api/scenario/production/registry': self.server.mcp.production.registry,
                  '/api/scenario/background-prompt': studio.background_prompt,
                  '/api/llm/status': studio.llm_status,
                  '/api/llm/api': studio.llm_api,
                  '/api/llm/credential': studio.llm_credential,
                  '/api/providers/list': studio.providers.list,
                  '/api/providers/save': self.server.mcp.provider_save,
                  '/api/providers/delete': self.server.mcp.provider_delete,
                  '/api/providers/models': self.server.mcp.provider_models,
                  '/api/providers/test': studio.providers.test,
                  '/api/providers/model/save': self.server.mcp.provider_model_save,
                  '/api/providers/model/test': studio.providers.test_model,
                  '/api/providers/defaults/save': studio.providers.save_defaults,
                  '/api/speech/generate': studio.speech,
                  '/api/generation/log': studio.generation_log,
                  '/api/scenario/save': studio.save_documents,
                  '/api/script/preview': studio.preview_script,
                  '/api/script/bind': studio.bind_script,
                  '/api/script/editor/open': editor.open, '/api/script/editor/save': editor.save,
                  '/api/script/editor/asset': editor.prepare_asset,
                  '/api/script/editor/localized-asset': editor.localized_asset,
                  '/api/script/editor/check': editor.check,
                  '/api/script/editor/starter': editor.starter,
                  '/api/script/editor/story': editor.story,
                  '/api/script/editor/screens': editor.screens,
                  '/api/script/editor/variables': editor.variables,
                  '/api/script/editor/history': editor.history,
                  '/api/script/editor/diff': editor.diff,
                  '/api/script/editor/merge': editor.merge,
                  '/api/script/editor/variants': editor.variants,
                  '/api/script/sdk/status': sdk.status, '/api/script/sdk/start': sdk.start,
                  '/api/script/sdk/translations': sdk.translations,
                  '/api/script/sdk/builds': sdk.builds,
                  '/api/script/story': inspect_story, '/api/script/story/edit': editor.edit_story,
                  '/api/asset-prompt': studio.asset_prompt,
                  '/api/workspace/project/save': self.server.images.resources.save_project,
                  '/api/workspace/project/open': self.server.mcp.open_project,
                  '/api/workspace/project/history': self.server.images.resources.project_history,
                  '/api/workspace/project/undo': self.server.images.resources.undo_project,
                  '/api/workspace/project/restore': self.server.images.resources.restore_project_revision,
                  '/api/workspace/project/list': projects.list_projects,
                  '/api/workspace/project/delete': lambda data: projects.set_project_trash(data, trash=True),
                  '/api/workspace/project/restore-deleted': lambda data: projects.set_project_trash(data, trash=False),
                  '/api/pipeline/state': pipeline.state, '/api/pipeline/inputs': pipeline.inputs,
                  '/api/pipeline/approved': pipeline.approved,
                  '/api/pipeline/reusable': pipeline.reusable,
                  '/api/pipeline/action': pipeline.decide,
                  '/api/pipeline/check': pipeline.require_ready,
                  '/api/plugins/list': self.server.mcp.plugins.list,
                  '/api/plugins/set-enabled': self.server.mcp.plugins.set_enabled,
                  '/api/workspace/assets/import': self.server.images.import_workspace_asset,
                  '/api/images/loras/prepare': self.server.images.prepare_lora,
                  '/api/images/library/list': self.server.images.library_list,
                  '/api/images/library/item': self.server.images.library_item,
                  '/api/images/library/refresh': self.server.images.refresh_library}
        is_mcp = self.path == '/mcp'
        if not is_mcp and self.path != '/api/build' and self.path not in routes and self.path not in image_routes:
            self.respond_json(404, {"error": "接口不存在。"})
            return
        if self.headers.get_content_type() != "application/json":
            self.respond_json(415, {"error": "需要 JSON 请求。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_REQUEST:
            self.respond_json(413, {"error": "请求为空或超过 64 MiB。"})
            return

        try:
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                if not is_mcp:
                    raise
                self.respond_json(400, rpc_error(None, -32700, 'Parse error'))
                return
            if is_mcp:
                version = self.headers.get('MCP-Protocol-Version')
                if version is not None and version not in VERSIONS:
                    self.respond_json(400, rpc_error(None, -32600, 'Unsupported MCP-Protocol-Version'))
                    return
                result = self.server.mcp.handle(data)
                if result is None:
                    self.respond(202, 'application/json', b'')
                else:
                    self.respond_json(200, result)
                return
            if not isinstance(data, dict):
                raise ValueError("请求须为 JSON 对象。")
            if data.get('project_id'):
                projects.project_record(data['project_id'])
            if self.path in image_routes:
                payload = self.server.mcp.run_operation(NODE_ROUTES.get(self.path), data, image_routes[self.path])
                self.respond_json(202 if 'state' in payload else 201, payload)
            elif self.path in routes:
                self.respond_json(200, self.server.mcp.run_operation(NODE_ROUTES.get(self.path), data, routes[self.path]))
            else:
                self.server.mcp.run_operation(NODE_ROUTES[self.path], data, self.build)
        except Busy as error:
            self.respond_json(409, {'error': str(error)})
        except (ValueError, binascii.Error, OSError) as error:
            self.respond_json(400, {"error": str(error)})

    def build(self, data: dict) -> None:
        export = self.server.mcp.export(data)
        self.send_file(Path(export['path']), 'application/zip', attachment='prisma-renpy.zip', export_id=export['id'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--image-workspace', type=Path, help='directory for downloads, training datasets and image jobs')
    parser.add_argument('--reference-dir', type=Path, help='directory with training images and optional same-name .txt captions')
    parser.add_argument('--no-existing-media', action='store_true', help='do not discover the older pilot models or tools')
    parser.add_argument('--node-manifest', type=Path, action='append', default=[],
                        help='register additional MetaNode definitions from a local JSON manifest (repeatable)')
    args = parser.parse_args()
    images = ImageToolkit(ROOT, workspace=args.image_workspace, reference=args.reference_dir,
                          use_existing=not args.no_existing_media)
    manifest = images.home / '.service.json'
    try:
        with own_workspace(images.home), ThreadingHTTPServer(('localhost', args.port), Handler) as server:
            images.refresh_library()
            server.images = images
            with images.library.connection() as db:
                db.execute("UPDATE generation_runs SET state='interrupted' WHERE state='running'")
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                server.mcp = PrismaMCP(images, base, node_manifests=args.node_manifest)
            except (OSError, ValueError) as error:
                parser.error(str(error))
            temporary = manifest.with_suffix('.json.part')
            temporary.write_text(json.dumps({'base': base, 'pid': os.getpid()}), encoding='utf-8')
            temporary.replace(manifest)
            print(f'Prisma Web UI: {base}\nPrisma MCP: {base}/mcp', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                if images.jobs.active:
                    images.jobs.active.cancel()
            finally:
                try:
                    current = json.loads(manifest.read_text(encoding='utf-8'))
                    if current.get('pid') == os.getpid():
                        manifest.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
    except Busy as error:
        if args.node_manifest:
            parser.exit(1, f'{error}\n节点清单在服务启动时加载，请先停止当前工作目录对应的服务。\n')
        base = f'http://127.0.0.1:{args.port}'
        try:
            current = json.loads(manifest.read_text(encoding='utf-8'))
            base = current['base'].rstrip('/')
        except (KeyError, OSError, ValueError):
            pass
        try:
            opener = build_opener(ProxyHandler({}))
            probe = json.dumps({
                'jsonrpc': '2.0', 'id': 'prisma-service-probe', 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                           'clientInfo': {'name': 'Prisma service probe', 'version': '1'}},
            }).encode('utf-8')
            request = Request(f'{base}/mcp', data=probe, method='POST', headers={
                'Content-Type': 'application/json', 'Accept': 'application/json',
            })
            with opener.open(request, timeout=2) as response:
                result = json.load(response)
            if result.get('jsonrpc') == '2.0' and result.get('id') == 'prisma-service-probe' and 'result' in result:
                print(f'已连接到使用此工作目录的 Prisma 服务：\nWebUI: {base}/#projects\nMCP: {base}/mcp', flush=True)
                return
        except (OSError, ValueError):
            pass
        parser.exit(1, f'{error}\n')


if __name__ == "__main__":
    main()
