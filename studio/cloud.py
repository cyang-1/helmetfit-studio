"""Hosted, browser-session-isolated entry point for the HelmetFit Docker Studio."""
import argparse
import ipaddress
import os
import re
import secrets
import threading
from http.cookies import CookieError, SimpleCookie
from http.server import ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .app import Handler
from .cloud_policy import SESSION_SECONDS, Limit, Workspaces


def authority(value):
    if not value or not re.fullmatch(r'[A-Za-z0-9.\-:\[\]]+', value):
        return None
    try:
        parts = urlsplit('http://' + value)
        if parts.port is not None and parts.port < 1:
            return None
        if parts.hostname and (':' in parts.hostname or
                all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                    for label in parts.hostname.split('.'))):
            return parts.hostname.lower()
    except ValueError:
        pass
    return None


def internal_host(host):
    if host in ('localhost', 'localhost.localdomain') or '.' not in host or host.endswith(('.internal', '.svc', '.cluster.local')):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def origin_parts(value):
    if not value or not value.isascii() or any(c.isspace() for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or value != parsed.scheme + '://' + parsed.netloc:
            return None
        if not authority(parsed.netloc):
            return None
        return parsed
    except ValueError:
        return None


def trusted_origin(value):
    if not value:
        return ''
    value = value.rstrip('/')
    parts = origin_parts(value)
    if not parts or parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('公开 origin 必须是完整 HTTPS 源；仅本机调试可使用 HTTP 回环地址。')
    return value


def same_origin(headers, configured, host):
    origin = headers.get('Origin', '')
    parts = origin_parts(origin)
    site = headers.get('Sec-Fetch-Site')
    if not parts or site not in (None, 'same-origin'):
        return False
    if configured:
        return origin == configured
    if site != 'same-origin' or parts.scheme != 'https':
        return False
    if parts.netloc.lower() == headers['Host'].lower():
        return True
    # ModelScope can forward an internal Host. Browser fetch metadata is required
    # in autodetection mode. Forwarded headers are never used as trust anchors.
    return internal_host(host) and parts.hostname.endswith(('.ms.fun', '.ms.show'))


class CloudHandler(Handler):
    timeout = 30
    server_version = 'HelmetFitStudioCloud/1.0'

    @property
    def jobs(self):
        return self.server.workspaces.space(self.owner, create=True)

    def dispatch(self):
        self.pinned_owner = None
        try:
            super().dispatch()
        finally:
            if self.pinned_owner:
                self.server.workspaces.unpin(self.pinned_owner)

    def send_header(self, keyword, value):
        if keyword.lower() == 'cache-control' and not self.path.startswith('/static/'):
            value = 'private, no-store'
        super().send_header(keyword, value)

    def end_headers(self):
        if getattr(self, 'set_session', None):
            name = self.server.cookie_name
            token = self.server.workspaces.ledger.cookie(self.set_session)
            flags = 'SameSite=Lax' if self.server.local_http else 'Secure; SameSite=None; Partitioned'
            self.send_header('Set-Cookie', f'{name}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; {flags}')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "base-uri 'none'; object-src 'none'; form-action 'self'; "
                         "frame-ancestors 'self' https://modelscope.cn https://www.modelscope.cn "
                         "https://modelscope.ai https://www.modelscope.ai")
        super().end_headers()

    def reject(self, status, message):
        self.close_connection = True
        self.send_json({'error': message}, status)
        return False

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            cookie = cookies.get(self.server.cookie_name)
            return self.server.workspaces.ledger.verify(cookie.value) if cookie else None
        except (CookieError, ValueError):
            return None

    def authorized(self):
        headers = self.headers
        host = authority(headers.get('Host'))
        if not host or len(headers.get_all('Host', [])) != 1 or len(headers.get_all('Origin', [])) > 1:
            return self.reject(400, '请求来源格式无效。')
        if len(headers.get_all('Content-Length', [])) > 1 or headers.get('Transfer-Encoding'):
            return self.reject(400, '不支持此请求传输格式。')
        path = unquote(urlsplit(self.path).path)
        writing = self.command != 'GET'
        if writing and not same_origin(headers, self.server.public_origin, host):
            return self.reject(403, '仅允许从本工作台页面提交。')
        if not writing and path in ('/', '/index.html', '/static/studio/index.html'):
            self.set_session = self.session() or secrets.token_hex(24)
            return True
        if not writing and (path.startswith('/static/') or path == '/api/status'):
            return True
        if not writing and headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            return self.reject(403, '请从当前工作台查看自己的文件。')
        self.owner = self.session()
        if not self.owner:
            return self.reject(401, '未建立浏览器工作区，请刷新首页；嵌入页面无法保存 Cookie 时请直接打开工作台。')
        self.server.workspaces.pin(self.owner)
        self.pinned_owner = self.owner
        # Reading an empty history must not allocate a database or worker object.
        workspace = self.server.workspaces.space(self.owner)
        if not workspace and not (self.command == 'POST' and path == '/api/projects'):
            if not writing and path == '/api/projects':
                self.send_json({'projects': []})
                return False
            return self.reject(404, '作品不存在。')
        if self.command == 'POST' and path == '/api/projects':
            if headers.get_content_type() not in ('application/json', 'multipart/form-data'):
                return self.reject(415, '新作品需要 JSON 描述或 multipart 文件上传。')
            length = int(headers.get('Content-Length', '0'))
            if not 0 < length <= 151 * 1024**2:
                return self.reject(413, '请求体为空或超过上传限制。')
            self.server.workspaces.require_space(length)
        return True

    def post(self, path):
        if path != '/api/projects':
            return super().post(path)
        if not self.server.workspaces.uploads.acquire(blocking=False):
            raise Limit('当前上传较多，请稍后再次提交。')
        try:
            return super().post(path)
        finally:
            self.server.workspaces.uploads.release()


class CloudServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 32

    def __init__(self, address, data, public_origin=''):
        self.public_origin = trusted_origin(public_origin)
        self.local_http = self.public_origin.startswith('http://')
        self.cookie_name = 'helmetfit-local' if self.local_http else '__Host-helmetfit'
        self.requests = threading.BoundedSemaphore(24)
        self.workspaces = Workspaces(data)
        try:
            super().__init__(address, CloudHandler)
        except BaseException:
            self.workspaces.close()
            raise

    def process_request(self, request, client_address):
        if not self.requests.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.requests.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.requests.release()

    def server_close(self):
        super().server_close()
        self.workspaces.close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Daily Lux3D create attempts (UTC): HELMETFIT_DAILY_GENERATIONS=12 globally, '
               'HELMETFIT_SESSION_DAILY_GENERATIONS=3 per browser workspace. Set either to 0 '
               'to disable new paid submissions. Existing tasks can still be resumed.')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=7860)
    parser.add_argument('--public-origin', default=os.getenv('HELMETFIT_PUBLIC_ORIGIN', ''),
                        help='Exact HTTPS origin; automatically recognize same-origin browser requests if omitted.')
    parser.add_argument('--data-dir', default=os.getenv('HELMETFIT_CLOUD_DATA', '/data/helmetfit'),
                        help='Persistent volume for sessions, projects and quota ledger.')
    args = parser.parse_args()
    server = CloudServer((args.host, args.port), args.data_dir, args.public_origin)
    print(f'HelmetFit Studio hosted service listening on port {server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
