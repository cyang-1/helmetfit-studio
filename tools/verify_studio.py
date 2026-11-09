"""Run the shipped GLB through the real HTTP, Blender and P1S entry points."""
import argparse
import hashlib
import json
import time
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8778')
    parser.add_argument('--split', choices=('auto', 'x', 'y', 'z'), default='auto')
    parser.add_argument('--save', type=Path)
    args = parser.parse_args()
    base = args.url.rstrip('/')
    if urlsplit(base).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('仅支持本机工作台。')
    browser = build_opener(HTTPCookieProcessor(CookieJar()))

    def read(path, data=None, headers=None):
        request_headers = {'Origin': base, **(headers or {})} if data is not None else {}
        request = Request(base + path, data=data, headers=request_headers)
        with browser.open(request, timeout=120) as response:
            return response.read()

    def state(pid, expected):
        last = ''
        until = time.monotonic() + 2700
        while time.monotonic() < until:
            item = json.loads(read('/api/projects/' + pid))
            if last != item['message']:
                print(item['message'], flush=True)
                last = item['message']
            if item['stage'] == expected:
                return item
            if item['stage'] in ('failed', 'cancelled', 'review'):
                raise RuntimeError(item['message'])
            time.sleep(2)
        raise TimeoutError('等待本机制造任务超时')

    read('/')
    nonce = uuid.uuid4().hex
    boundary = 'studio-verification-' + nonce
    model = read('/static/studio-example/helmet.glb')
    form = (f'--{boundary}\r\nContent-Disposition: form-data; name="request_id"\r\n\r\n{nonce}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="model"; filename="Studio-Example.glb"\r\n'
            'Content-Type: model/gltf-binary\r\n\r\n').encode() + model + f'\r\n--{boundary}--\r\n'.encode()
    headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary}
    pid = json.loads(read('/api/projects', form, headers))['id']
    duplicate = json.loads(read('/api/projects', form, headers))['id']
    if duplicate != pid:
        raise AssertionError('重复提交未去重。')
    project = state(pid, 'ready')
    if project.get('source_report', {}).get('quality', {}).get('status') not in ('passed', 'repaired'):
        raise AssertionError('佩戴调整前缺少源网格检查结果。')
    params = dict(project['params'], split_axis=args.split)
    def post(action):
        return json.loads(read(f'/api/projects/{pid}/{action}', json.dumps(params).encode(),
                               {'Content-Type': 'application/json'}))
    overlays = post('cutter')
    before = hashlib.sha256(read(overlays['cutter'])).digest()
    post('fit')
    result = state(pid, 'done')
    assets = result['assets']
    if hashlib.sha256(read(assets['cutter'])).digest() != before:
        raise AssertionError('预览与实际刀具不一致。')
    if not assets.get('ring3mf') or not assets.get('plates') or result['report'].get('blocking'):
        raise AssertionError('试戴环或整盔工程不完整。')
    for plate in assets['plates']:
        print(plate['name'], plate['layers'], '层', plate['grams'], 'g', flush=True)
    for key, url in [('ring', assets['ring3mf']), ('project', assets['package'])]:
        blob = read(url)
        if blob[:2] != b'PK':
            raise AssertionError('工程下载失败：' + key)
        if args.save:
            args.save.mkdir(parents=True, exist_ok=True)
            (args.save / ('HelmetFit-' + key + ('.zip' if key == 'project' else '.3mf'))).write_bytes(blob)
    if args.save:
        (args.save / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.save / '制作记录.html').write_bytes(read('/api/projects/' + pid + '/report'))
    print('通过 · ' + base + '/#p/' + pid, flush=True)


if __name__ == '__main__':
    main()
