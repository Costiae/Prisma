"""Local API credentials stored outside versioned project data."""

from urllib.parse import urlsplit


class LocalLlmCredentials:
    def __init__(self, library):
        self.library = library

    @staticmethod
    def _scope(base_url):
        if not isinstance(base_url, str) or len(base_url) > 512:
            raise ValueError('请填写有效的 API 地址。')
        parsed = urlsplit(base_url.strip())
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError('API 地址须为不含账号密码、查询参数或片段的 HTTP(S) 地址。')
        try:
            port = parsed.port
        except ValueError:
            raise ValueError('API 地址端口无效。') from None
        host = parsed.hostname.lower()
        if ':' in host:
            host = f'[{host}]'
        return f'{parsed.scheme.lower()}://{host}{f":{port}" if port else ""}'

    def get(self, base_url):
        return self.library.get_local_secret(self._scope(base_url))

    def status(self, base_url):
        return {'configured': bool(self.get(base_url)), 'storage': '本机 Prisma 数据库'}

    def save(self, base_url, api_key):
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
            raise ValueError('API 密钥不能为空，且不能超过 4096 个字符。')
        self.library.set_local_secret(self._scope(base_url), api_key.strip())
        return self.status(base_url)

    def clear(self, base_url):
        self.library.clear_local_secret(self._scope(base_url))
        return self.status(base_url)
