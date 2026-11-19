"""Serve Prisma's local editing workspace with the Python standard library."""

import argparse
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import unquote, urlsplit

from image_jobs import Busy, JOB_ID, own_workspace
from image_toolkit import IMAGE_TYPES, ImageToolkit, child_file
from prisma_mcp import PrismaMCP, VERSIONS, rpc_error
from prisma_workflow import asset_prompt, draft, inspect, make_archive, require_text


ROOT = Path(__file__).resolve().parent
STATIC = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/app.css": ("web/app.css", "text/css; charset=utf-8"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/images.js": ("web/images.js", "text/javascript; charset=utf-8"),
    "/docs/media-prompt-guide.md": ("docs/media-prompt-guide.md", "text/plain; charset=utf-8"),
    "/docs/media-checklist.md": ("docs/media-checklist.md", "text/plain; charset=utf-8"),
    "/docs/media-variants.md": ("docs/media-variants.md", "text/plain; charset=utf-8"),
    "/docs/reproducible-workflow.md": ("docs/reproducible-workflow.md", "text/plain; charset=utf-8"),
}
MAX_REQUEST = 64 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, content_type: str, body: bytes, *, attachment: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        if attachment:
            self.send_header("Content-Disposition", 'attachment; filename="prisma-renpy.zip"')
        self.end_headers()
        self.wfile.write(body)

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
        path = urlsplit(self.path).path
        if path == '/mcp':
            self.mcp_no_stream()
            return
        if path.startswith('/api/exports/'):
            self.get_export(path)
            return
        if path.startswith('/api/images/'):
            self.get_image_resource(path)
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
        self.respond(200, mime, content)

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
            if not JOB_ID.fullmatch(ident) or name != 'prisma-renpy.zip':
                raise FileNotFoundError()
            exports = self.server.images.home / 'exports'
            if not (exports / ident).resolve().is_relative_to(exports.resolve()):
                raise FileNotFoundError()
            self.send_file(child_file(exports / ident, name), 'application/zip', attachment=name)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError):
            self.respond_json(404, {'error': '导出文件不存在。'})

    def get_image_resource(self, path: str) -> None:
        toolkit = self.server.images
        route = [unquote(part) for part in path.split('/')[3:]]
        try:
            if route == ['catalog']:
                self.respond_json(200, toolkit.catalog())
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
                with tempfile.TemporaryDirectory(prefix='prisma-dataset-') as temp:
                    archive = Path(temp) / 'dataset.zip'
                    toolkit.dataset_archive(ident, archive)
                    self.send_file(archive, 'application/zip', attachment=f'prisma-dataset-{ident}.zip')
            else:
                raise FileNotFoundError('接口不存在。')
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, OSError):
            self.respond_json(404, {'error': '图像资源不存在或无法读取。'})

    def send_file(self, path: Path, mime, *, attachment=None) -> None:
        with path.open('rb') as stream:
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if attachment:
                self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
            self.end_headers()
            shutil.copyfileobj(stream, self.wfile)

    def do_POST(self) -> None:
        if not self.local_request():
            self.respond_json(403, {"error": "请使用本地 Prisma 地址。"})
            return
        image_routes = {
            '/api/images/generate': self.server.images.generate,
            '/api/images/install': self.server.images.provision,
            '/api/images/datasets': self.server.images.save_dataset,
            '/api/images/datasets/restore': self.server.images.restore_dataset,
            '/api/images/loras': self.server.images.import_lora,
            '/api/images/train': self.server.images.train,
            '/api/images/cancel': lambda data: self.server.images.jobs.cancel(require_text(data, 'id')),
            '/api/images/replay': lambda data: self.server.images.replay(require_text(data, 'id')),
        }
        routes = {'/api/draft': draft, '/api/scenes': inspect, '/api/asset-prompt': asset_prompt}
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
            if self.path in image_routes:
                payload = image_routes[self.path](data)
                self.respond_json(202 if 'state' in payload else 201, payload)
            elif self.path in routes:
                self.respond_json(200, routes[self.path](data))
            else:
                self.build(data)
        except Busy as error:
            self.respond_json(409, {'error': str(error)})
        except (ValueError, binascii.Error, OSError) as error:
            self.respond_json(400, {"error": str(error)})

    def build(self, data: dict) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-web-") as temp:
            archive = make_archive(data, Path(temp), self.server.images.artifact)
            self.respond(200, "application/zip", archive.read_bytes(), attachment=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--image-workspace', type=Path, help='directory for downloads, training datasets and image jobs')
    parser.add_argument('--reference-dir', type=Path, help='directory with training images and optional same-name .txt captions')
    parser.add_argument('--no-existing-media', action='store_true', help='do not discover the older pilot models or tools')
    args = parser.parse_args()
    images = ImageToolkit(ROOT, workspace=args.image_workspace, reference=args.reference_dir,
                          use_existing=not args.no_existing_media)
    try:
        with own_workspace(images.home), ThreadingHTTPServer(('localhost', args.port), Handler) as server:
            server.images = images
            base = f'http://127.0.0.1:{server.server_port}'
            server.mcp = PrismaMCP(images, base)
            print(f'Prisma Web UI: {base}\nPrisma MCP: {base}/mcp', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                if images.jobs.active:
                    images.jobs.active.cancel()
    except Busy as error:
        parser.exit(1, f'{error}\n')


if __name__ == "__main__":
    main()
