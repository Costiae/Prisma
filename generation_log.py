"""Project-local, queryable generation events and provider request diagnostics."""
from datetime import datetime, timezone
import json
from threading import Lock
from time import monotonic
from uuid import uuid4

from api_providers import ProviderRequestError, api_request
from generation_transport import chat_body


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def measured_usage(requests):
    usage = {}
    for request in requests:
        for name, value in (request.get('usage') or {}).items():
            usage[name] = usage.get(name, 0) + value
    return usage


class RunJournal:
    def __init__(self, library, ident):
        self.library = library
        self.ident = ident
        self.started = monotonic()
        self.events = []
        self.lock = Lock()

    def __call__(self, message, state='running'):
        with self.lock, self.library.connection() as db:
            self.events.append({'at': timestamp(), 'elapsed': round(monotonic() - self.started, 2),
                                'message': message})
            db.execute('UPDATE generation_runs SET state=?,events=? WHERE id=?',
                       (state, json.dumps(self.events, ensure_ascii=False), self.ident))

    def _store(self, request, evidence=None):
        with self.library.connection() as db:
            db.execute("""INSERT INTO generation_requests (id,run_id,snapshot,evidence) VALUES (?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot,
                    evidence=coalesce(excluded.evidence,generation_requests.evidence)""",
                       (request['id'], self.ident, json.dumps(request, ensure_ascii=False), evidence))

    def cached(self, context):
        self._store({'id': uuid4().hex, 'at': timestamp(), 'phase': 'cached', 'elapsed': 0,
                     'context': context})

    def request(self, provider, operation, payload, *, timeout, limit, parse=None, context=None,
                binary=False, stream=True):
        key = provider.get('api_key')

        def redact(value):
            return value.replace(key, '••••') if key else value

        is_chat = operation in ('chat/completions', 'messages')
        opts = dict(payload)
        declaration = next((model for model in provider.get('models', []) if model['id'] == payload['model']), {})
        if is_chat and stream and declaration.get('metadata', {}).get('supports_streaming') is not False:
            opts['stream'] = True
            if provider['protocol'] == 'openai':
                opts['stream_options'] = {'include_usage': True}
        response = None
        start, last_update = monotonic(), 0
        prompts = [item['content'] for item in payload.get('messages', []) if isinstance(item.get('content'), str)]
        snapshot = {'id': uuid4().hex, 'at': timestamp(), 'provider': provider['name'], 'model': payload['model'],
                    'operation': '/' + operation, 'context': context or {}, 'timeout': timeout,
                    'max_tokens': payload.get('max_tokens', payload.get('max_completion_tokens')),
                    'prompt_chars': sum(len(item) for item in (*prompts, payload.get('system', ''), payload.get('input', '')))}

        def update(phase, *, evidence=None, **fields):
            nonlocal last_update
            now = monotonic()
            changed = phase != snapshot.get('phase') or (fields.get('content_chars') and not snapshot.get('content_chars'))
            if fields.get('content_chars') or fields.get('reasoning_chars'):
                snapshot.setdefault('first_output', round(now - start, 2))
            if isinstance(fields.get('preview'), str):
                fields['preview'] = redact(fields['preview'])
            snapshot.update(phase=phase, elapsed=round(now - start, 2), **fields)
            if phase != 'stream' or changed or now - last_update >= .7:
                self._store(snapshot, evidence)
                last_update = now

        def fetch(attempt):
            update('connecting', attempt=attempt, transport='requested-stream' if opts.get('stream') else 'buffered')
            return api_request(provider, operation, opts, timeout=timeout, limit=limit, binary=binary,
                               response_meta=True, include_error_detail=True, progress=update)

        try:
            try:
                status, response = fetch(1)
            except ProviderRequestError as error:
                if not opts.get('stream') or not (error.status == 501 or (
                        error.status in (400, 422) and 'stream' in error.detail.lower())):
                    raise
                opts.pop('stream')
                opts.pop('stream_options', None)
                retry = {'attempt': 1, 'status': error.status, 'error': redact(str(error))}
                update('retrying', retries=[retry])
                self(f'模型接口拒绝流式参数（HTTP {error.status}），改用普通响应。')
                status, response = fetch(2)
            update('validating', status=status)
            result = parse(response) if parse else response
        except (ValueError, OSError) as error:
            body = redact(chat_body(response, provider['protocol'])) if is_chat else ''
            evidence = body[:262_144] if body else None
            if evidence is not None and len(body) > len(evidence):
                evidence += '\n\n[仅保存前 262,144 个字符]'
            message = redact(str(error))
            update('failed', evidence=evidence, error=message,
                   **({'status': error.status} if isinstance(error, ProviderRequestError) else {}))
            if message != str(error):
                raise ValueError(message) from None
            raise
        except Exception:
            update('failed', error='服务端内部错误。')
            raise
        update('completed', status=status)
        return result
