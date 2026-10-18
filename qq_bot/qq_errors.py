"""对用户和运维输出可定位、去敏感信息的 QQ 接口错误。"""
import re
import aiohttp


def safe_message(value, secrets=()):
    text = str(value or "未返回错误说明")
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[已隐藏]")
    text = re.sub(r'https?://\S+', '[URL已隐藏]', text)
    text = re.sub(r'(?i)(authorization|access_token|appsecret|clientsecret|token|file_info|openid)\s*[:=]\s*[^\s,;]+', r'\1=[已隐藏]', text)
    text = re.sub(r'[A-Za-z0-9_\-+/=]{24,}', '[标识已隐藏]', text)
    return text[:350]


class QQAPIError(RuntimeError):
    def __init__(self, endpoint, status, data=None, headers=None, secrets=()):
        data = data if isinstance(data, dict) else {}
        headers = headers or {}
        self.endpoint = re.sub(r'/v2/(users|groups)/[^/]+', r'/v2/\1/{openid}', endpoint.split('?')[0])
        self.status = status
        self.code = data.get('code', 'unknown')
        self.message = safe_message(data.get('message') or data.get('msg') or data.get('error'), secrets)
        self.trace = str(headers.get('X-Tps-trace-ID') or headers.get('X-Tps-Trace-Id') or data.get('trace_id') or '-')[:100]
        super().__init__(f"接口 {self.endpoint}\nHTTP {status}，code={self.code}\n说明：{self.message}\ntrace_id：{self.trace}")


async def read_api_response(response, endpoint, secrets=()):
    try:
        data = await response.json()
    except (ValueError, TypeError, aiohttp.ContentTypeError):
        raise QQAPIError(endpoint, response.status, {'message': '接口返回非 JSON 响应'}, getattr(response, 'headers', {}), secrets)
    if response.status >= 400 or not isinstance(data, dict) or data.get('code', 0) not in (None,0,'0',''):
        raise QQAPIError(endpoint, response.status, data, getattr(response, 'headers', {}), secrets)
    return data
