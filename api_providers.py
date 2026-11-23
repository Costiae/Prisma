"""Workspace provider profiles, model declarations, defaults and protocol adapters."""

import ipaddress
import json
import re
from datetime import datetime, timezone
from http.client import HTTPException
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from generation_transport import buffered_chat, streamed_chat
from provider_model_info import (
    CAPABILITIES, TEST_OPERATIONS, model_capabilities, model_probe, probe_packet, probe_summary, probe_usage,
)

KINDS = ('text', 'image', 'audio')
OPENAI_BASE = 'https://api.openai.com/v1'


class NoApiRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ProviderRequestError(ValueError):
    def __init__(self, message, status, detail=''):
        super().__init__(message)
        self.status = status
        self.detail = detail


def validate_url(value):
    if not isinstance(value, str) or len(value) > 512 or any(ch.isspace() for ch in value.strip()):
        raise ValueError('请填写有效的 API 地址。')
    parsed = urlsplit(value.strip().rstrip('/'))
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('API 地址须为不含账号密码、查询参数或片段的 HTTP(S) 地址。')
    _ = parsed.port
    if parsed.scheme == 'http' and not private_endpoint(parsed.hostname):
        raise ValueError('公网 API 地址须使用 HTTPS。')
    return parsed.geturl()


def private_endpoint(host):
    if host == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or (address.is_private and not address.is_unspecified)
    except ValueError:
        return False


def endpoint(base_url, operation):
    parsed = urlsplit(validate_url(base_url))
    path = parsed.path.rstrip('/')
    for suffix in (*('/' + name for name in TEST_OPERATIONS), '/models'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path:
        path = '/v1'
    return parsed._replace(path=path + '/' + operation).geturl()


def api_request(provider, operation, payload=None, *, limit=2_000_000, timeout=20, binary=False,
                query=None, content_type='application/json', response_meta=False, include_error_detail=False,
                progress=None):
    headers = {'Accept': 'audio/mpeg' if binary else 'application/json'}
    if isinstance(payload, dict) and payload.get('stream') is True:
        headers['Accept'] = 'text/event-stream, application/json'
    key = provider['api_key']
    if provider['protocol'] == 'anthropic':
        headers['anthropic-version'] = '2023-06-01'
        if key:
            headers['x-api-key'] = key
    elif key:
        headers['Authorization'] = f'Bearer {key}'
    body = None
    if payload is not None:
        headers['Content-Type'] = content_type
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
    url = endpoint(provider['base_url'], operation)
    if query:
        url += '?' + urlencode(query)
    try:
        with build_opener(NoApiRedirect()).open(Request(url, data=body, headers=headers), timeout=timeout) as response:
            status = response.status
            is_chat = operation in ('chat/completions', 'messages')
            streaming = is_chat and response.headers.get_content_type() == 'text/event-stream'
            if progress:
                progress('waiting', status=status, transport='stream' if streaming else 'buffered')
            if streaming:
                value = streamed_chat(response, provider['protocol'], limit, progress or (lambda *_args, **_fields: None))
            else:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError('API 响应超过大小限制。')
                value = raw if binary else json.loads(raw)
                if progress and is_chat:
                    value = buffered_chat(value, provider['protocol'], progress)
                elif progress:
                    progress('received', response_bytes=len(raw), usage=probe_usage(value))
        return (status, value) if response_meta else value
    except HTTPError as error:
        hints = {401: '请检查 API Key', 403: '请检查账号权限', 404: '请检查 API 地址或接口是否受支持',
                 429: '请检查请求频率或账号额度'}
        detail = ''
        if include_error_detail and error.code != 401:
            try:
                raw = error.read(8192)
                try:
                    body = json.loads(raw)
                except (UnicodeError, ValueError):
                    body = raw.decode('utf-8', errors='replace') if error.headers.get_content_type() == 'text/plain' else ''
                api_error = body.get('error') or body.get('message') if isinstance(body, dict) else body
                message = api_error.get('message') if isinstance(api_error, dict) else api_error
                if isinstance(message, str):
                    detail = ' ' + scrub(message, key)
            except (OSError, ValueError):
                pass
        raise ProviderRequestError(
            f"API /{operation} 返回 HTTP {error.code}。{hints.get(error.code, '')}{detail}", error.code, detail) from None
    except (OSError, URLError, HTTPException) as error:
        raise ValueError(f'无法连接 API /{operation}：{error}') from None
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError(f'API /{operation} 未返回有效 JSON。') from None


def scrub(value, key):
    if key:
        value = value.replace(key, '••••')
    return ' '.join(value.split())[:280]


def model_kind(protocol, item):
    ident = item['id'].lower()
    if protocol == 'anthropic':
        return 'text'
    modalities = item.get('output_modalities', item.get('modalities', []))
    if not isinstance(modalities, list):
        modalities = []
    if 'image' in modalities or ident.startswith(('gpt-image', 'dall-e', 'flux-', 'stable-diffusion')):
        return 'image'
    if ('audio' in modalities and 'text' not in modalities or ident.startswith('tts-') or '-tts' in ident):
        return 'audio'
    if any(part in ident for part in ('embed', 'moderation', 'whisper', 'transcribe', 'realtime', 'rerank')):
        return ''
    return 'text'


def declarations(provider):
    models = []
    for item in provider['models']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip():
            continue
        if 'metadata' in item and item.get('source') in ('fetched', 'manual'):
            models.append(item)
        else:
            models.append({'id': item['id'], 'kind': model_kind(provider['protocol'], item),
                           'name': item.get('display_name') or item['id'], 'metadata': item, 'source': 'fetched'})
    return models


class ApiProviders:
    def __init__(self, library):
        self.library = library

    def list(self, _data=None):
        providers = self.library.api_providers()
        with self.library.connection() as db:
            tests = {(row['provider_id'], row['model_id']): json.loads(row['result'])
                     for row in db.execute('SELECT provider_id,model_id,result FROM api_model_tests')}
        for provider in providers:
            provider['models'] = [
                self.public_model(provider, model, tests.get((provider['id'], model['id'])))
                for model in declarations(provider)]
        return {'providers': providers, 'defaults': self.defaults()}

    def get(self, ident):
        if not isinstance(ident, str) or not ident:
            raise ValueError('请选择 API Provider。')
        provider = self.library.api_provider(ident)
        provider['models'] = declarations(provider)
        return provider

    def save(self, data):
        name = data.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('Provider 名称须为 1–100 字。')
        ident = data.get('id') or 'provider-' + uuid4().hex
        if not isinstance(ident, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', ident):
            raise ValueError('Provider ID 格式无效。')
        base_url = validate_url(data.get('base_url'))
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM api_providers WHERE id=?', (ident,)).fetchone()
            existing = self.decode_row(row) if row else {}
            protocol = data.get('protocol', existing.get('protocol', 'openai'))
            if protocol not in ('openai', 'anthropic'):
                raise ValueError('请选择 OpenAI 或 Anthropic 兼容格式。')
            same_connection = existing.get('base_url') == base_url and existing.get('protocol') == protocol
            key = data.get('api_key')
            if key is None:
                key = existing.get('api_key', '') if same_connection else ''
            if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in '\r\n'):
                raise ValueError('API Key 格式无效。')
            models = existing.get('models', [])
            if protocol == 'anthropic' and any(item['kind'] in ('image', 'audio') for item in models):
                raise ValueError('此 Provider 已声明图像或音频模型。请为 Anthropic 格式新建 Provider。')
            db.execute("""INSERT INTO api_providers (id,name,protocol,kind,base_url,api_key)
                VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                  protocol=excluded.protocol, kind=excluded.kind, base_url=excluded.base_url, api_key=excluded.api_key,
                  updated=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                       (ident, name.strip(), protocol, 'text' if protocol == 'anthropic' else 'text,image,audio',
                        base_url, key.strip()))
            if existing and (not same_connection or existing['api_key'] != key.strip()):
                db.execute('DELETE FROM api_model_tests WHERE provider_id=?', (ident,))
        return self.public(self.get(ident))

    def delete(self, data):
        provider = self.get(data.get('id'))
        ident = provider['id']
        if ident == 'openai':
            raise ValueError('内置 OpenAI Provider 不可删除。')
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM api_model_defaults WHERE provider_id=?', (ident,)).fetchone():
                raise ValueError('请先更换引用此 Provider 的全局默认模型。')
            revisions = db.execute("""SELECT r.metadata FROM records p JOIN records r
                ON r.kind='revision' AND r.id=json_extract(p.metadata, '$.revision') WHERE p.kind='project'""")
            for row in revisions:
                config = json.loads(row['metadata']).get('config', {})
                override = config.get('llm_overrides', {}).get('scenario', {}).get('settings', {})
                if any(settings.get('provider_id') == ident for settings in
                       (config.get('llm', {}), override, config.get('generation', {}))):
                    raise ValueError('请先更换项目中引用此 Provider 的模型。')
            db.execute('DELETE FROM api_providers WHERE id=?', (ident,))
        return {'deleted': ident}

    @staticmethod
    def public_model(provider, model, test=None):
        return {**model, 'capabilities': model_capabilities(model), 'probe': model_probe(provider['protocol'], model),
                'last_test': test}

    def public(self, provider):
        return {**{name: value for name, value in provider.items() if name not in ('api_key', 'models')},
                'models': [self.public_model(provider, model) for model in provider['models']]}

    def defaults(self):
        with self.library.connection() as db:
            return {row['kind']: {'provider_id': row['provider_id'], 'model': row['model_id']}
                    for row in db.execute('SELECT * FROM api_model_defaults')}

    def save_defaults(self, data):
        defaults = data.get('defaults')
        if not isinstance(defaults, dict) or any(kind not in KINDS for kind in defaults):
            raise ValueError('默认模型用途须为 text、image 或 audio。')
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for kind, ref in defaults.items():
                if ref is None:
                    db.execute('DELETE FROM api_model_defaults WHERE kind=?', (kind,))
                    continue
                if not isinstance(ref, dict):
                    raise ValueError('默认模型须引用 Provider 和模型 ID。')
                provider_id, model = ref.get('provider_id'), ref.get('model')
                if not isinstance(provider_id, str) or not isinstance(model, str) or not provider_id or not model:
                    raise ValueError('默认模型须包含 Provider ID 和模型 ID。')
                row = db.execute('SELECT * FROM api_providers WHERE id=?', (provider_id,)).fetchone()
                provider = self.decode_row(row)
                self.find_model(provider, model, kind)
                db.execute("""INSERT INTO api_model_defaults VALUES (?,?,?) ON CONFLICT(kind)
                    DO UPDATE SET provider_id=excluded.provider_id, model_id=excluded.model_id""",
                           (kind, provider_id, model))
        return {'defaults': self.defaults()}

    @staticmethod
    def decode_row(row):
        if row is None:
            raise ValueError('API Provider 不存在。')
        provider = dict(row, models=json.loads(row['models']))
        provider['models'] = declarations(provider)
        return provider

    @staticmethod
    def find_model(provider, ident, kind):
        model = next((item for item in provider['models'] if item['id'] == ident and item['kind'] == kind), None)
        if model is None:
            raise ValueError(f'模型 {ident or "未选择"} 未在 {provider["name"]} 中声明为{kind}用途。')
        return model

    def resolve(self, kind, provider_id='', model=''):
        if not provider_id and not model:
            ref = self.defaults().get(kind)
            if ref:
                provider_id, model = ref['provider_id'], ref['model']
        if not provider_id or not model:
            raise ValueError('请在「API Provider」声明模型，并设置全局默认或在节点选择模型。')
        provider = self.get(provider_id)
        return provider, self.find_model(provider, model, kind)

    def resolve_node_model(self, kind, override):
        if override is None:
            return self.resolve(kind)
        if (not isinstance(override, dict)
                or any(not isinstance(override.get(key), str) or not override[key].strip()
                       for key in ('provider_id', 'model'))):
            raise ValueError('模型 Override 须包含 Provider ID 和模型 ID。')
        return self.resolve(kind, override['provider_id'], override['model'])

    def update_model(self, data):
        provider_id = data.get('id')
        ident, kind = data.get('model'), data.get('kind')
        name, metadata = data.get('name', ''), data.get('metadata', {})
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError('请选择 API Provider。')
        if (not isinstance(ident, str) or not 1 <= len(ident.strip()) <= 160
                or any(ord(character) < 32 for character in ident) or kind not in (*KINDS, '')):
            raise ValueError('请填写模型 ID 并选择用途。')
        if not isinstance(name, str) or len(name.strip()) > 160 or not isinstance(metadata, dict):
            raise ValueError('模型名称最多 160 字，metadata 须为 JSON 对象。')
        ident = ident.strip()
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            provider = self.decode_row(db.execute('SELECT * FROM api_providers WHERE id=?', (provider_id,)).fetchone())
            if provider['protocol'] == 'anthropic' and kind != 'text':
                raise ValueError('Anthropic Messages 支持文本输出。')
            if db.execute('SELECT 1 FROM api_model_defaults WHERE provider_id=? AND model_id=? AND kind<>?',
                          (provider['id'], ident, kind)).fetchone():
                raise ValueError('此模型正被用作全局默认，请先更换默认模型再调整用途。')
            models = {item['id']: item for item in provider['models']}
            current = models.get(ident, {})
            overrides = data.get('capabilities', current.get('capability_overrides', {}))
            if not isinstance(overrides, dict) or any(key not in CAPABILITIES or type(value) is not bool
                                                      for key, value in overrides.items()):
                raise ValueError('模型能力须使用已知字段，并标为支持或不支持。')
            probe = data.get('probe', current.get('probe_override', ''))
            if not isinstance(probe, str) or (probe and (
                    probe not in TEST_OPERATIONS or (probe == 'messages') != (provider['protocol'] == 'anthropic'))):
                raise ValueError('测试接口须与 Provider 的兼容格式一致。')
            voice = data.get('test_voice', current.get('test_voice', ''))
            if not isinstance(voice, str) or len(voice.strip()) > 100:
                raise ValueError('测试声音名称最多 100 字。')
            modified = {'id': ident, 'kind': kind, 'name': name.strip() or ident,
                'source': 'manual', 'metadata': {**current.get('metadata', {}), **metadata},
                'capability_overrides': overrides, 'probe_override': probe, 'test_voice': voice.strip()}
            models[ident] = modified
            self.write_models(db, provider['id'], models)
            if current and (model_probe(provider['protocol'], current) != model_probe(provider['protocol'], modified)
                            or current.get('test_voice', '') != modified['test_voice']):
                db.execute('DELETE FROM api_model_tests WHERE provider_id=? AND model_id=?', (provider_id, ident))
        return self.public(self.get(provider['id']))

    @staticmethod
    def write_models(db, ident, models):
        db.execute("""UPDATE api_providers SET models=?,updated=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                      WHERE id=?""", (encode(sorted(models.values(), key=lambda item: item['id'])), ident))

    @staticmethod
    def read_page(provider, query=None):
        result = api_request(provider, 'models', query=query)
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise ValueError('模型目录未返回兼容格式。可以手动声明模型。')
        return result

    def models(self, data):
        provider = self.get(data.get('id'))
        fetched = {}
        query = None
        for _ in range(20):
            page = self.read_page(provider, query)
            for item in page['data']:
                if (isinstance(item, dict) and isinstance(item.get('id'), str) and 0 < len(item['id']) <= 160
                        and item['id'].strip() == item['id'] and not any(ord(c) < 32 for c in item['id'])):
                    fetched[item['id']] = item
            if provider['protocol'] != 'anthropic' or not page.get('has_more'):
                break
            cursor = page.get('last_id')
            if not isinstance(cursor, str) or (query and query['after_id'] == cursor):
                raise ValueError('模型目录分页游标无效。')
            query = {'after_id': cursor}
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            stored = self.decode_row(db.execute('SELECT * FROM api_providers WHERE id=?', (provider['id'],)).fetchone())
            if any(stored[key] != provider[key] for key in ('base_url', 'api_key', 'protocol')):
                raise ValueError('Provider 连接已更改，请重新 Fetch Models。')
            merged = {item['id']: item for item in stored['models']}
            for ident, item in fetched.items():
                existing = merged.get(ident, {})
                manual = existing.get('source') == 'manual'
                name = item.get('display_name')
                name = name.strip()[:160] if isinstance(name, str) else ''
                kind = existing.get('kind') or model_kind(provider['protocol'], item)
                updated = {**existing, 'id': ident, 'kind': existing['kind'] if manual else kind,
                    'name': existing['name'] if manual else name or ident, 'source': 'manual' if manual else 'fetched',
                    'metadata': {**existing.get('metadata', {}), **item} if manual else item}
                if existing and model_probe(provider['protocol'], existing) != model_probe(provider['protocol'], updated):
                    db.execute('DELETE FROM api_model_tests WHERE provider_id=? AND model_id=?', (provider['id'], ident))
                merged[ident] = updated
            self.write_models(db, provider['id'], merged)
        public = self.public(self.get(provider['id']))
        return {'provider': public, 'models': public['models'], 'count': len(fetched)}

    def test(self, data):
        provider = self.get(data.get('id'))
        page = self.read_page(provider)
        return {'connected': True, 'provider_id': provider['id'], 'models': len(page['data']),
                'endpoint': endpoint(provider['base_url'], 'models')}

    def test_model(self, data):
        provider = self.get(data.get('id'))
        ident = data.get('model')
        model = next((item for item in provider['models'] if item['id'] == ident), None)
        if model is None:
            raise ValueError('请选择此 Provider 目录中的模型。')
        operation = model_probe(provider['protocol'], model)
        payload, options = probe_packet(provider, model, operation)
        started_at, timer = test_time(), perf_counter()
        report = {'provider_id': provider['id'], 'model': ident, 'operation': operation,
                  'endpoint': endpoint(provider['base_url'], operation), 'http_status': None}
        try:
            report['http_status'], response = api_request(provider, operation, payload, include_error_detail=True, **options)
            report['summary'] = scrub(probe_summary(operation, response), provider['api_key'])
            report['usage'] = probe_usage(response)
            report['ok'] = True
        except ValueError as error:
            if isinstance(error, ProviderRequestError):
                report['http_status'] = error.status
            report.update(ok=False, summary=scrub(str(error), provider['api_key']))
        report.update(checked_at=test_time(), duration_ms=round((perf_counter() - timer) * 1000))
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            stored = self.decode_row(db.execute('SELECT * FROM api_providers WHERE id=?', (provider['id'],)).fetchone())
            current = next((item for item in stored['models'] if item['id'] == ident), {})
            if (any(stored[key] != provider[key] for key in ('base_url', 'api_key', 'protocol'))
                    or not current or model_probe(stored['protocol'], current) != operation
                    or current.get('test_voice', '') != model.get('test_voice', '')):
                raise ValueError('测试期间连接或请求设置已更改，请重新测试。')
            db.execute("""INSERT INTO api_model_tests VALUES (?,?,?,?) ON CONFLICT(provider_id,model_id)
                DO UPDATE SET started_at=excluded.started_at, result=excluded.result
                WHERE excluded.started_at >= api_model_tests.started_at""",
                       (provider['id'], ident, started_at, encode(report)))
        return report


def test_time():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False)
