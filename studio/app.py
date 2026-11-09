"""Loopback-only HTTP entry point for the portable Windows application."""
import argparse
import cgi
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import urlopen

from .jobs import Busy, Jobs
from .report import project_report
from .runtime import ROOT

MB = 1024 * 1024
IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.webp'}
MODEL_TYPES = {'.obj', '.stl', '.glb'}
VIEW_TITLES = {
    'front': '正面', 'left': '左侧', 'right': '右侧', 'back': '背面',
    'top': '顶部', 'bottom': '底部', 'front_left': '左前斜视', 'front_right': '右前斜视',
}


def upload_layout(form, files):
    """Map named views to the API's ordered image array. Preserve old callers' order."""
    modes = form.getlist('input_mode')
    label_fields = form.getlist('view_labels')
    if len(modes) > 1 or len(label_fields) > 1:
        raise ValueError('上传模式或视角说明出现重复。')
    if any(f.name not in ('images', 'model') for f in files):
        raise ValueError('上传包含未知的文件字段。')
    images = [f for f in files if f.name == 'images']
    models = [f for f in files if f.name == 'model']
    if not (1 <= len(images) <= 8 and not models or len(models) == 1 and not images):
        raise ValueError('请选择 1–8 张同一头盔的图片，或一个 OBJ、STL、GLB 模型。')
    if not modes and not label_fields:
        mode = 'single' if len(images) == 1 else 'multi' if images else 'model'
        return mode, images or models, [None] * len(images)
    mode = modes[0] if modes else None
    if mode not in ('single', 'multi', 'model') or (mode == 'model') != bool(models):
        raise ValueError('上传模式与文件类型不符。')
    try:
        labels = json.loads(label_fields[0]) if label_fields else []
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('视角说明格式有误。') from None
    if not isinstance(labels, list) or len(labels) != len(images):
        raise ValueError('每张参考图都需要对应一个视角。')
    if mode == 'model':
        return mode, models, []
    if mode == 'single':
        if len(images) != 1 or labels != ['reference']:
            raise ValueError('单图模式接收一张参考图，无需指定拍摄角度。')
        return mode, images, labels
    if any(not isinstance(v, str) or v not in VIEW_TITLES for v in labels) or len(set(labels)) != len(labels):
        raise ValueError('每个视角只可上传一张图片，请检查角度标签。')
    if 'front' not in labels:
        raise ValueError('请上传正面主参考图。')
    if mode == 'multi' and len(images) < 2:
        raise ValueError('多视图模式需要正面和至少一个补充视角。')
    by_view = dict(zip(labels, images))
    ordered = [role for role in VIEW_TITLES if role in by_view]
    return mode, [by_view[v] for v in ordered], ordered


def reference_views(item):
    if item.get('source_type') != 'images':
        return []
    labels = item.get('view_labels') or []
    names = item.get('input_names') or []
    views = []
    for i, _ in enumerate(item.get('inputs', [])):
        role = labels[i] if i < len(labels) and (labels[i] == 'reference' or labels[i] in VIEW_TITLES) else None
        label = '单图参考' if role == 'reference' else VIEW_TITLES.get(role, f'参考图 {i + 1}（方向未标注）')
        views.append(dict(role=role, label=label,
                          filename=names[i] if i < len(names) else '',
                          url=f'/api/projects/{item["id"]}/references/{i + 1}'))
    return views


def reference_file(item, folder, index):
    inputs = item.get('inputs', [])
    if item.get('source_type') != 'images' or not 1 <= index <= len(inputs):
        raise KeyError('参考图不存在')
    path = (folder / inputs[index - 1]).resolve()
    if path.parent != folder.resolve() or path.suffix.lower() not in IMAGE_TYPES:
        raise KeyError('参考图不存在')
    return path


def requested_bytes(header, size):
    """Return inclusive bounds for one byte range; ignore multipart or unknown units."""
    if not header:
        return None
    unit, separator, spec = header.strip().partition('=')
    if unit.lower() != 'bytes' or ',' in spec:
        return None
    match = re.fullmatch(r'([0-9]*)-([0-9]*)', spec) if separator else None
    if not match or not any(match.groups()) or not size:
        raise ValueError('不可用的字节范围')

    def offset(digits):
        # Very large numerals still have well-defined clipping semantics.
        value = digits.lstrip('0') or '0'
        return size if len(value) > len(str(size)) else int(value)

    first, last = match.groups()
    if first:
        start = offset(first)
        end = min(offset(last), size - 1) if last else size - 1
    else:
        start = max(0, size - offset(last))
        end = size - 1
    if start > end:
        raise ValueError('字节范围超出文件大小')
    return start, end


class Handler(BaseHTTPRequestHandler):
    server_version = 'HelmetFitStudio/1.0'

    @property
    def jobs(self):
        return self.server.jobs

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def file(self, path, attachment=False, *, ranges=False):
        if not path.is_file():
            raise KeyError('文件不存在')
        with path.open('rb') as source:
            stat = os.fstat(source.fileno())
            size = stat.st_size
            etag = f'"{stat.st_ino:x}-{stat.st_mtime_ns:x}-{size:x}"'
            bounds = None
            if ranges and self.headers.get('If-Range', etag) == etag:
                try:
                    bounds = requested_bytes(','.join(self.headers.get_all('Range', [])), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Length', '0')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    return
            self.send_response(206 if bounds else 200)
            length = size
            if ranges:
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('ETag', etag)
            if bounds:
                start, end = bounds
                length = end - start + 1
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                source.seek(start)
            mime = {'.js': 'text/javascript', '.3mf': 'model/3mf', '.stl': 'model/stl',
                    '.glb': 'model/gltf-binary'}.get(path.suffix.lower())
            self.send_header('Content-Type', mime or mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(length))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            if attachment:
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name))
            self.end_headers()
            while length:
                chunk = source.read(min(length, 256 * 1024))
                if not chunk:
                    raise ConnectionAbortedError('文件在下载时发生变更')
                self.wfile.write(chunk)
                length -= len(chunk)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def authorized(self):
        authority = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        if authority in allowed and (self.command == 'GET' or not origin or origin == 'http://' + authority):
            return True
        self.send_json({'error': '仅允许本机工作台访问。'}, 403)
        return False

    def dispatch(self):
        try:
            if not self.authorized():
                return
            path = unquote(urlsplit(self.path).path)
            if self.command == 'DELETE':
                match = re.fullmatch(r'/api/projects/([a-f0-9]{32})', path)
                if not match:
                    raise KeyError('未知接口')
                return self.send_json(self.jobs.delete(match[1]))
            if self.command == 'POST':
                return self.post(path)
            if path in ('/', '/index.html'):
                return self.file(ROOT / 'static/studio/index.html')
            if path.startswith('/static/'):
                root = (ROOT / 'static').resolve()
                target = (ROOT / path.lstrip('/')).resolve()
                if not target.is_relative_to(root):
                    raise KeyError('资源不存在')
                return self.file(target)
            if path == '/api/status':
                from .lux import configured
                return self.send_json(dict(app='HelmetFit Studio', cloud_configured=configured(),
                                           printer='Bambu Lab P1S', version='1.0'))
            if path == '/api/projects':
                serialize = overview if parse_qs(urlsplit(self.path).query).get('overview') == ['1'] else public
                return self.send_json({'projects': [serialize(r) for r in self.jobs.store.all()]})
            match = re.fullmatch(r'/api/projects/([a-f0-9]{32})', path)
            if match:
                return self.send_json(public(self.jobs.store.get(match[1])))
            match = re.fullmatch(r'/api/projects/([a-f0-9]{32})/source-file', path)
            if match:
                item = self.jobs.store.get(match[1])
                if item.get('source_type') != 'model' or len(item.get('inputs', [])) != 1:
                    raise KeyError('原始模型不存在')
                folder = self.jobs.folder(match[1]).resolve()
                source = (folder / item['inputs'][0]).resolve()
                if source.parent != folder or source.suffix.lower() not in MODEL_TYPES:
                    raise KeyError('原始模型不存在')
                return self.file(source)
            match = re.fullmatch(r'/api/projects/([a-f0-9]{32})/references/([1-8])', path)
            if match:
                item = self.jobs.store.get(match[1])
                return self.file(reference_file(item, self.jobs.folder(match[1]), int(match[2])))
            match = re.fullmatch(r'/api/projects/([a-f0-9]{32})/report', path)
            if match:
                item = self.jobs.store.get(match[1])
                revision = parse_qs(urlsplit(self.path).query).get('revision')
                if revision:
                    wanted = int(revision[0])
                    saved = next((r for r in item.get('history', []) if r['revision'] == wanted), None)
                    if saved:
                        plates = saved['assets'].get('plates', [])
                        item = {**item, **saved, 'source_report': {},
                                'message': f'已验证 {len(plates)} 盘。' if plates else '此版本有待复核。'}
                    elif item['revision'] != wanted:
                        raise KeyError('未找到该适配版本。')
                content = project_report(item).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return self.wfile.write(content)
            match = re.fullmatch(r'/artifacts/([a-f0-9]{32})/(.+)', path)
            if match:
                folder = self.jobs.folder(match[1])
                target = (folder / match[2]).resolve()
                if not target.is_relative_to(folder) or target.suffix.lower() not in {'.3mf', '.stl', '.glb', '.zip'}:
                    raise KeyError('产物不存在')
                item = self.jobs.store.get(match[1])
                known = artwork(item['assets'])
                for entry in item['history']:
                    known.update(artwork(entry['assets']))
                is_preview = re.fullmatch(r'preview/[0-9a-f]{20}/(head|cutter)\.stl', match[2])
                if path not in known and not is_preview:
                    raise KeyError('产物尚未发布')
                return self.file(target, target.suffix in ('.zip', '.3mf'), ranges=True)
            raise KeyError('页面不存在')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Busy as exc:
            self.send_json({'error': str(exc)}, getattr(exc, 'status_code', 409))
        except KeyError as exc:
            self.send_json({'error': exc.args[0]}, 404)
        except (ValueError, UnicodeError) as exc:
            self.send_json({'error': str(exc)}, 400)
        except OSError:
            self.send_json({'error': '无法读写工作目录，请检查磁盘空间和权限。'}, 500)
        except Exception:
            self.send_json({'error': '工作台内部处理失败，请重新启动后重试。'}, 500)

    def post(self, path):
        size = int(self.headers.get('Content-Length', '0'))
        if size < 0:
            raise ValueError('请求长度错误。')
        if path == '/api/projects':
            if self.headers.get_content_type() == 'application/json':
                if size > 16384:
                    return self.send_json({'error': '描述过长。'}, 413)
                data = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(data, dict) or data.get('input_mode') != 'text':
                    raise ValueError('请选择文字生成模式。')
                return self.send_json(public(self.jobs.create_text(data)), 201)
            if size > 151 * MB or not size:
                return self.send_json({'error': '单次上传不能超过 150 MB。'}, 413)
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                environ={'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(size),
                         'CONTENT_TYPE': self.headers.get('Content-Type', '')}, max_num_fields=16)
            return self.send_json(public(self.upload(form)), 201)
        match = re.fullmatch(r'/api/projects/([a-f0-9]{32})/(fit|retry|cancel|cutter|repair|approve|regenerate)', path)
        if not match:
            raise KeyError('未知接口')
        if size > 16384:
            return self.send_json({'error': '参数过大。'}, 413)
        data = json.loads(self.rfile.read(size) or b'{}')
        if not isinstance(data, dict):
            raise ValueError('需要 JSON 对象。')
        if match[2] == 'cutter':
            return self.send_json(self.jobs.cutter(match[1], data))
        return self.send_json(public(self.jobs.action(match[1], match[2], data)), 202)

    def upload(self, form):
        files = [f for f in (form.list or []) if f.filename]
        mode, ordered, labels = upload_layout(form, files)
        structures = form.getlist('structure')
        if structures and (mode != 'model' or len(structures) != 1 or structures[0] not in ('full_shell', 'cap_mask')):
            raise ValueError('导入模型时可指定全包头盔或帽冠＋前面具。')
        images = mode != 'model'
        request_id = str(form.getfirst('request_id', uuid.uuid4().hex))[:100]
        if request_id.startswith(('text:', 'regenerate:')):
            raise ValueError('上传请求编号无效，请重新提交。')
        names = [Path(f.filename.replace('\\', '/')).name[:180] for f in ordered]
        name = str(form.getfirst('name', '')).strip()[:100] or Path(names[0]).stem[:100] or '头盔设计'
        folder = self.jobs.folder(uuid.uuid4().hex)
        folder.mkdir()
        inputs = []
        total = 0
        digests = set()
        try:
            for i, file in enumerate(ordered):
                ext = Path(file.filename).suffix.lower()
                if ext not in (IMAGE_TYPES if images else MODEL_TYPES):
                    raise ValueError('文件类型不受支持。')
                path = folder / (f'input-{i + 1}' + ext)
                with path.open('wb') as dst:
                    shutil.copyfileobj(file.file, dst, 256 * 1024)
                length = path.stat().st_size
                total += length
                if not length or length > (16 if images else 150) * MB or images and total > 64 * MB:
                    raise ValueError('图片每张限 16 MB，合计限 64 MB；模型限 150 MB。')
                if images:
                    with path.open('rb') as src:
                        sig = src.read(12)
                    valid = {'.png': sig.startswith(b'\x89PNG\r\n\x1a\n'), '.jpg': sig.startswith(b'\xff\xd8\xff'),
                             '.jpeg': sig.startswith(b'\xff\xd8\xff'), '.webp': sig[:4] == b'RIFF' and sig[8:] == b'WEBP'}
                    if not valid[ext]:
                        raise ValueError('图片内容与文件格式不匹配。')
                    if mode == 'multi' and all(labels):
                        with path.open('rb') as src:
                            digest = hashlib.file_digest(src, 'sha256').digest()
                        if digest in digests:
                            raise ValueError('不同视角使用了同一张图片，请更换或移除重复视图。')
                        digests.add(digest)
                inputs.append(path.name)
            return self.jobs.create(folder, request_id, name, inputs, 'images' if images else 'model',
                                    input_mode=mode, view_labels=labels, input_names=names,
                                    model_structure=structures[0] if structures else 'full_shell')
        except Exception:
            if not (folder / 'source').exists() and folder.exists():
                shutil.rmtree(folder)
            raise


def artwork(assets):
    urls = {v for v in assets.values() if isinstance(v, str)}
    urls.update(p['url'] for p in assets.get('plates', []))
    return urls


def overview(item):
    record = public(item)
    brief = {k: record.get(k) for k in ('id', 'name', 'stage', 'message', 'updated', 'source_type',
                                         'input_mode', 'source_ready', 'revision', 'remote_id', 'task_kind',
                                         'generation_number', 'parent_id', 'preview_approved', 'wear_structure')}
    brief['quality_status'] = record.get('source_report', {}).get('quality', {}).get('status')
    brief['reference_views'] = record.get('reference_views', [])[:1]
    return brief


def public(item):
    result = {k: v for k, v in item.items() if k not in ('inputs', 'input_names', 'operation')}
    views = reference_views(item)
    result['input_mode'] = item.get('input_mode') or ('model' if not views else 'single' if len(views) == 1 else 'multi')
    result['reference_views'] = views
    if item.get('source_type') == 'model' and len(item.get('inputs', [])) == 1:
        names = item.get('input_names') or []
        result['source_file'] = {'filename': names[0] if names else item['inputs'][0],
                                 'url': f'/api/projects/{item["id"]}/source-file'}
    result['task_kind'] = item.get('operation')
    result.setdefault('wear_structure', 'full_shell')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=int(os.getenv('HELMETFIT_PORT', 8778)))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', default=os.getenv('HELMETFIT_STUDIO_DATA', str(ROOT / 'studio-projects')))
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        url = f'http://127.0.0.1:{args.port}/'
        try:
            with urlopen(url + 'api/status', timeout=2) as response:
                existing = json.load(response)
        except (ValueError, OSError):
            existing = {}
        if existing.get('app') == 'HelmetFit Studio' and not args.no_browser:
            webbrowser.open(url)
            return
        raise SystemExit(f'端口 {args.port} 已被占用，可设置 HELMETFIT_PORT 后重新启动。')
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    lock = (data / 'server.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('已有工作台正在使用此项目目录。')
    server.jobs = Jobs(data)
    url = f'http://127.0.0.1:{server.server_port}/'
    print('HelmetFit Studio ' + url, flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
