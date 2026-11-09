import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from studio import lux
from studio.cloud import CloudServer
from studio.cloud_policy import Limit
from studio.jobs import Jobs
from studio.prompts import make_spec
from studio.runtime import ROOT


class HostedBoundary(unittest.TestCase):
    origin = 'https://test-helmet.ms.fun'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fake_jobs = patch.object(Jobs, '_schedule', lambda *_: None)
        self.fake_jobs.start()
        self.addCleanup(self.fake_jobs.stop)
        self.start()
        self.addCleanup(self.stop)

    def start(self, origin=None):
        self.server = CloudServer(('127.0.0.1', 0), self.root, self.origin if origin is None else origin)
        self.server.workspaces.min_free = 0
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .03})
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()

    def request(self, path, *, cookie='', method='GET', data=None, headers=None, content=None):
        payload = json.dumps(data).encode() if data is not None else content
        hdr = {'Content-Type': 'application/json', 'Cookie': cookie}
        if method != 'GET':
            hdr['Origin'] = self.origin
        hdr.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            conn.request(method, path, body=payload, headers=hdr)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, dict(resp.getheaders()), raw
        finally:
            conn.close()

    def cookie(self):
        status, headers, _ = self.request('/')
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie']
        for attr in ('HttpOnly', 'Secure', 'SameSite=None', 'Partitioned', 'Path=/'):
            self.assertIn(attr, cookie)
        return cookie.split(';')[0]

    def create(self, cookie, token='abc'):
        status, _, raw = self.request('/api/projects', cookie=cookie, method='POST',
                                      data={'input_mode': 'text', 'theme': '测试头盔', 'request_id': token})
        self.assertEqual(status, 201, raw)
        return json.loads(raw)

    def workspace(self, cookie):
        sid = self.server.workspaces.ledger.verify(cookie.split('=',1)[1])
        return self.server.workspaces.space(sid)

    def test_artifacts_and_every_project_operation_are_session_scoped(self):
        a, b = self.cookie(), self.cookie()
        for cookie in (a, b):
            status, _, body = self.request('/api/projects?overview=1', cookie=cookie)
            self.assertEqual((status,json.loads(body)), (200,{'projects':[]}))
        self.assertFalse(self.server.workspaces.instances)
        pa, pb = self.create(a), self.create(b)
        self.assertNotEqual(pa['id'],pb['id'])
        self.assertEqual(pa['id'],self.create(a)['id'])
        for c, want in ((a,pa['id']),(b,pb['id'])):
            self.assertEqual([p['id'] for p in json.loads(self.request('/api/projects', cookie=c)[2])['projects']], [want])
        pid=pa['id']
        job=self.workspace(a)
        folder=job.folder(pid)
        fixture=folder/'fixture.3mf'
        fixture.write_bytes(b'authorized project fixture')
        url=job.url(pid, fixture)
        job.store.update(pid, stage='done', assets={'plates':[{'url':url,'name':'test'}], 'package':url})
        self.assertEqual(self.request(url,cookie=a)[2],fixture.read_bytes())
        for path in (url, '/api/projects/'+pid, '/api/projects/'+pid+'/source-file',
                     '/api/projects/'+pid+'/report', '/api/projects/'+pid+'/references/1'):
            self.assertEqual(self.request(path,cookie=b)[0],404,path)
        for action in ('approve','fit','cutter','repair','cancel','retry','regenerate'):
            self.assertEqual(self.request('/api/projects/'+pid+'/'+action,cookie=b,method='POST',data={})[0],404,action)
        self.assertEqual(self.request('/api/projects/'+pid,cookie=b,method='DELETE')[0],404)
        self.assertEqual(self.request(url,cookie=a)[0],200)
        self.stop()
        self.start()
        self.assertEqual(self.request(url,cookie=a)[0],200)
        self.assertEqual(self.request(url,cookie=b)[0],404)

    def test_proxy_origin_cookie_and_fetch_metadata(self):
        cookie=self.cookie()
        self.assertEqual(self.request('/api/projects')[0],401)
        tampered=cookie[:-1]+('1' if cookie[-1]=='0' else '0')
        self.assertEqual(self.request('/api/projects',cookie=tampered)[0],401)
        for headers in ({'Origin':'https://attacker.ms.fun'}, {'Origin':'null'},
                        {'Origin':self.origin,'Sec-Fetch-Site':'cross-site'},
                        {'Origin':'https://wrong.invalid','X-Forwarded-Host':'test-helmet.ms.fun'}):
            self.assertEqual(self.request('/api/projects',cookie=cookie,method='POST',data={},headers=headers)[0],403)
        self.assertEqual(self.request('/api/projects',cookie=cookie,headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.request('/api/projects',cookie=cookie,headers={'Host':'invalid/path'})[0],400)
        self.server.public_origin=''
        common={'Host':'studio-internal:7860','Sec-Fetch-Site':'same-origin'}
        body={'input_mode':'text','theme':'local fixture','request_id':'metadata'}
        self.assertEqual(self.request('/api/projects',cookie=cookie,method='POST',data=body,headers=common)[0],201)
        for hdr in ({'Host':'internal:7860'}, {**common,'Origin':'https://attacker.net'},
                    {**common,'Origin':'https://ms.fun.attacker.net'},
                    {**common,'Host':'public-unrelated.example.org'}):
            self.assertEqual(self.request('/api/projects',cookie=cookie,method='POST',data=body,headers=hdr)[0],403)

    def test_authenticated_single_range_download_and_resume(self):
        a, b = self.cookie(), self.cookie()
        pid = self.create(a)['id']
        jobs = self.workspace(a)
        path = jobs.folder(pid) / 'plate.3mf'
        data = bytes(range(256)) * 3000
        path.write_bytes(data)
        url = jobs.url(pid, path)
        jobs.store.update(pid, stage='done', assets={'plates': [{'url': url, 'name': 'plate'}]})
        cut = 300001
        for value, first, last in (
            (f'bytes=0-{cut-1}', 0, cut - 1),
            (f'bytes={cut}-', cut, len(data) - 1),
            ('bytes=-17', len(data) - 17, len(data) - 1),
            ('bytes=1-999999999999999999999999', 1, len(data) - 1),
            ('bytes=-999999999999999999999999', 0, len(data) - 1),
            ('bytes=0-0', 0, 0),
        ):
            with self.subTest(value=value):
                status, headers, chunk = self.request(url, cookie=a, headers={'Range': value})
                self.assertEqual(status, 206)
                self.assertEqual(headers['Content-Range'], f'bytes {first}-{last}/{len(data)}')
                self.assertEqual(headers['Accept-Ranges'], 'bytes')
                self.assertEqual(int(headers['Content-Length']), len(chunk))
                self.assertIn('attachment', headers['Content-Disposition'])
                self.assertEqual(chunk, data[first:last+1])
        status, _, chunk = self.request(url, cookie=a, headers={'Range': 'bytes=1-2', 'If-Range': headers['ETag']})
        self.assertEqual((status, chunk), (206, data[1:3]))
        for value in (f'bytes={len(data)}-', 'bytes=-0', 'bytes=8-5', 'bytes=oops'):
            status, headers, chunk = self.request(url, cookie=a, headers={'Range': value})
            self.assertEqual((status, chunk, headers['Content-Range']), (416, b'', f'bytes */{len(data)}'))
        for request_headers in ({}, {'Range': 'bytes=0-4,10-12'}, {'Range': 'items=0-5'},
                                {'Range':'bytes=0-1','If-Range':'"unknown"'}):
            status, headers, chunk = self.request(url, cookie=a, headers=request_headers)
            self.assertEqual((status, chunk), (200, data))
            self.assertNotIn('Content-Range', headers)
        for cookie, expected in ((b,404),('',401)):
            status, headers, _ = self.request(url, cookie=cookie, headers={'Range': 'bytes=999999999-'})
            self.assertEqual(status, expected)
            self.assertNotIn('Content-Range', headers)

    def test_foreign_remote_task_cannot_be_claimed(self):
        c=self.cookie()
        p=self.create(c)
        job=self.workspace(c)
        job.store.update(p['id'],stage='failed',uncertain=True)
        path='/api/projects/'+p['id']+'/retry'
        self.assertEqual(self.request(path,cookie=c,method='POST',data={'remote_id':'12345'})[0],400)
        job.store.update(p['id'],remote_id='999')
        self.assertEqual(self.request(path,cookie=c,method='POST',data={'remote_id':'12345'})[0],400)
        self.assertEqual(self.request(path,cookie=c,method='POST',data={'remote_id':'999'})[0],202)

    def test_real_provider_submission_meter_is_atomic_durable_and_resume_is_free(self):
        a,b=self.cookie(),self.cookie()
        pa,pb=self.create(a),self.create(b)
        j1,j2=self.workspace(a),self.workspace(b)
        ledger=self.server.workspaces.ledger
        ledger.session_daily=1
        ledger.global_daily=2
        calls=[]
        def gateway(_key,_region,route,body=None):
            if body is not None:
                calls.append(route)
                return len(calls)
            return {'taskId':route.rsplit('=',1)[1],'status':3,'outputs':[{'content':'https://example.invalid/result.glb'}]}
        def local_glb(_url,path):
            shutil.copyfile(ROOT/'static/studio-example/helmet.glb',path)
        def run(job,record,name):
            return lux.generate_text(make_spec('test'),job.folder(record['id'])/name,lambda *_:None,
                                     **job.generation_options(record))
        with patch.object(lux,'_settings',return_value=('fixture','cn')), patch.object(lux,'_gateway',side_effect=gateway), patch.object(lux,'_download',side_effect=local_glb):
            def race(i):
                try:
                    return run(j1,pa,str(i)).is_file()
                except Limit:
                    return False
            with ThreadPoolExecutor(4) as pool:
                results=list(pool.map(race,range(4)))
            self.assertEqual(sum(results),1)
            # A successful idempotent retry and even a re-download use the same task id.
            name=str(results.index(True))
            run(j1,pa,name).unlink()
            run(j1,pa,name)
            run(j2,pb,'second')
            self.assertEqual(len(calls),2)
            self.assertEqual(self.request('/api/projects',cookie=self.cookie(),method='POST',
                data={'input_mode':'text','theme':'test','request_id':'new-cookie'})[0],429)
        # Changing cookies does not reset the global allowance. SQLite persists across restarts.
        self.stop()
        self.start()
        self.server.workspaces.ledger.global_daily=2
        with self.assertRaises(Limit):
            self.server.workspaces.ledger.quota('new','provider',consume=False)
        tomorrow=datetime.fromtimestamp(time.time()+86400,timezone.utc)
        with patch('studio.cloud_policy.datetime') as clock:
            clock.now.return_value=tomorrow
            self.server.workspaces.ledger.quota('new','provider',consume=True)

    def test_one_global_executor_and_bounded_queued_work(self):
        a,b=self.cookie(),self.cookie()
        self.create(a)
        self.create(b)
        ja,jb=self.workspace(a),self.workspace(b)
        self.assertIs(ja.lane,jb.lane)
        self.assertIs(ja.host.compute,jb.host.compute)
        active = peak = 0
        counter = threading.Lock()

        def measure(*_args, **_kwargs):
            nonlocal active, peak
            with counter:
                active += 1
                peak = max(peak, active)
            time.sleep(.04)
            with counter:
                active -= 1

        with patch('studio.jobs.geometry', side_effect=measure), patch('studio.jobs.slice_plate', side_effect=measure):
            with ThreadPoolExecutor(2) as pool:
                calls = [pool.submit(ja.geometry, 'cutter'), pool.submit(jb.slice_plate, 'fixture')]
                for future in calls:
                    future.result()
        self.assertEqual(peak, 1)
        ja.running.update({'queued-1':object(),'queued-2':object()})
        self.assertEqual(self.request('/api/projects',cookie=a,method='POST',
            data={'input_mode':'text','theme':'test','request_id':'another'})[0],429)
        self.assertEqual(self.request('/api/projects',cookie=b,method='POST',
            data={'input_mode':'text','theme':'test','request_id':'another'})[0],201)
        ja.running.clear()

    def test_cold_workspaces_reopen_without_resetting_storage_or_budget(self):
        host = self.server.workspaces
        host.max_sessions = 1
        a, b = self.cookie(), self.cookie()
        pa = self.create(a)
        ja = self.workspace(a)
        host.ledger.session_daily = 1
        host.ledger.quota(ja.owner, 'provider', consume=True)
        host.pin(ja.owner)
        try:
            self.assertEqual(self.request('/api/projects',cookie=b,method='POST',
                data={'input_mode':'text','theme':'test','request_id':'b'})[0], 429)
        finally:
            host.unpin(ja.owner)
        pb = self.create(b)
        self.assertNotIn(ja.owner, host.instances)
        self.assertEqual(json.loads(self.request('/api/projects', cookie=a)[2])['projects'][0]['id'], pa['id'])
        self.assertEqual(len(host.instances), 1)
        self.assertIsNot(self.workspace(a), ja)
        self.assertEqual(self.request('/api/projects/'+pb['id'],cookie=a)[0], 404)
        with self.assertRaises(Limit):
            host.ledger.quota(ja.owner, 'provider', consume=False)


if __name__=='__main__':
    unittest.main()
