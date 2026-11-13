"""Serve Prisma's local editing workspace with the Python standard library."""

import argparse
import base64
import binascii
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path, PureWindowsPath
import shutil
import tempfile

from prisma import build_project, draft_scenario, parse_scenario, read_mapping, render_script


ROOT = Path(__file__).resolve().parent
STATIC = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/app.css": ("web/app.css", "text/css; charset=utf-8"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/docs/media-prompt-guide.md": ("docs/media-prompt-guide.md", "text/plain; charset=utf-8"),
    "/docs/media-checklist.md": ("docs/media-checklist.md", "text/plain; charset=utf-8"),
    "/docs/media-variants.md": ("docs/media-variants.md", "text/plain; charset=utf-8"),
}
MAX_REQUEST = 64 * 1024 * 1024


def require_text(data: dict, key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是文本。")
    return value


def upload_path(directory: Path, name: str) -> Path:
    if (
        not isinstance(name, str) or not name or name != name.strip(" .")
        or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
        or PureWindowsPath(name).is_reserved()
    ):
        raise ValueError("素材文件名须为有效的单个文件名。")
    return directory / name


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, content_type: str, body: bytes, *, attachment: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
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
        resource = STATIC.get(self.path)
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

    def do_POST(self) -> None:
        if not self.local_request():
            self.respond_json(403, {"error": "请使用本地 Prisma 地址。"})
            return
        if self.path not in ("/api/draft", "/api/scenes", "/api/build"):
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
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("请求须为 JSON 对象。")
            if self.path == "/api/draft":
                speakers = data.get("speakers", {})
                if not isinstance(speakers, dict):
                    raise ValueError("说话人映射须为 JSON 对象。")
                heading = require_text(data, "heading").strip()
                scenario, pending = draft_scenario(require_text(data, "raw"), speakers, heading or None)
                self.respond_json(200, {"scenario": scenario, "pending": pending})
            elif self.path == "/api/scenes":
                scenes = parse_scenario(require_text(data, "scenario"), allow_unresolved=True)
                self.respond_json(200, {"scenes": [asdict(scene) for scene in scenes]})
            else:
                self.build(data)
        except (ValueError, binascii.Error, OSError) as error:
            self.respond_json(400, {"error": str(error)})

    def build(self, data: dict) -> None:
        scenes = parse_scenario(require_text(data, "scenario"))
        uploads = data.get("files", {})
        if not isinstance(uploads, dict):
            raise ValueError("素材须为文件名映射。")
        with tempfile.TemporaryDirectory(prefix="prisma-web-") as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            for name, encoded in uploads.items():
                if not isinstance(encoded, str):
                    raise ValueError("素材文件内容无效。")
                path = upload_path(assets, name)
                with path.open("xb") as stream:
                    stream.write(base64.b64decode(encoded, validate=True))

            font_name = require_text(data, "font")
            font = upload_path(assets, font_name) if font_name else None
            mapping = root / "cues.json"
            mapping.write_text(require_text(data, "cues").strip() or "{}", encoding="utf-8")
            script, files = render_script(scenes, read_mapping(mapping), assets, font, confine_assets=True)
            project = root / "project"
            build_project(project, script, files)
            archive = Path(shutil.make_archive(str(root / "prisma-renpy"), "zip", project))
            self.respond(200, "application/zip", archive.read_bytes(), attachment=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Prisma Web UI: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
