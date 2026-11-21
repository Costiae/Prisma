"""Bridge a stdio MCP client to the running Prisma loopback service."""

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from prisma_mcp import rpc_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, help='override the port discovered for the image workspace')
    parser.add_argument('--image-workspace', type=Path,
                        help='image workspace whose running Prisma service should receive MCP requests')
    options = parser.parse_args()
    workspace = (options.image_workspace or Path(__file__).resolve().parent / 'build' / 'prisma-images').resolve()
    base = None
    if options.port is None:
        try:
            service = json.loads((workspace / '.service.json').read_text(encoding='utf-8'))
            base = service.get('base')
        except (OSError, ValueError):
            pass
    endpoint = f'{base.rstrip("/")}/mcp' if base else f'http://127.0.0.1:{options.port or 8765}/mcp'
    opener = build_opener(ProxyHandler({}))
    protocol = None
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeError):
            reply = rpc_error(None, -32700, 'Parse error')
        else:
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
            if protocol:
                headers['MCP-Protocol-Version'] = protocol
            try:
                with opener.open(Request(endpoint, data=raw, headers=headers), timeout=120) as response:
                    if response.status == 202:
                        continue
                    reply = json.load(response)
            except HTTPError as error:
                if isinstance(message, dict) and 'id' not in message:
                    continue
                reply = rpc_error(message.get('id') if isinstance(message, dict) else None,
                                  -32000, f'Prisma service returned HTTP {error.code}.')
            except (URLError, OSError, ValueError):
                if isinstance(message, dict) and 'id' not in message:
                    continue
                reply = rpc_error(message.get('id') if isinstance(message, dict) else None, -32000,
                                  f'Prisma service is unavailable. Start python webui.py --port {options.port}.')
            if isinstance(message, dict) and message.get('method') == 'initialize':
                protocol = reply.get('result', {}).get('protocolVersion')
        sys.stdout.buffer.write(json.dumps(reply, ensure_ascii=False).encode('utf-8') + b'\n')
        sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
