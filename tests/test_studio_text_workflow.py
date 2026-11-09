import json
import shutil
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from studio.app import Handler
from studio.jobs import Jobs
from studio.runtime import ROOT


class QuietHandler(Handler):
    def log_message(self, *_):
        pass


class GeneratedPreviewWorkflow(unittest.TestCase):
    """Exercise the user endpoints with real local geometry and a cost-free cloud fixture."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.jobs = Jobs(temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.jobs = self.jobs
        self.addCleanup(self.jobs.lane.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def call(self, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.base + path, data=body, headers={'Content-Type':'application/json'})
        with urlopen(request, timeout=20) as res:
            return json.load(res)

    def wait(self, pid, stage):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            p = self.call('/api/projects/' + pid)
            if p['stage'] == stage:
                return p
            self.assertNotIn(p['stage'], ('failed', 'cancelled'), p['message'])
            time.sleep(.1)
        self.fail(f'等待 {stage} 超时')

    def test_review_regenerate_and_approve(self):
        def fixture(spec, folder, status, resume_id=None):
            self.assertIn('圣诞老人', spec['prompt'])
            status('生成中', '1234')
            folder.mkdir(exist_ok=True)
            return Path(shutil.copyfile(ROOT/'static/studio-example/helmet.glb', folder/'original.glb'))

        with patch('studio.lux.generate_text', side_effect=fixture) as provider:
            form = dict(input_mode='text', theme='圣诞老人', preset='character', structure='full_shell',
                        request_id='first-text')
            pid = self.call('/api/projects', form)['id']
            self.assertEqual(self.call('/api/projects', form)['id'], pid)
            first = self.wait(pid, 'preview')
            self.assertFalse(first['preview_approved'])
            self.assertIn('glb', first['assets'])
            self.assertNotIn('quality', first['source_report'])
            route = '/api/projects/' + pid
            for operation in ('fit', 'cutter', 'repair'):
                with self.assertRaises(HTTPError) as blocked:
                    self.call(route + '/' + operation, {})
                self.assertEqual(blocked.exception.code, 400)
            regen = {'request_id': 'regenerate-1'}
            child = self.call(route + '/regenerate', regen)
            self.assertEqual(self.call(route + '/regenerate', regen)['id'], child['id'])
            self.assertEqual(child['parent_id'], pid)
            self.assertEqual(child['generation_number'], 2)
            second = self.wait(child['id'], 'preview')
            self.assertEqual(first['prompt_spec'], second['prompt_spec'])
            self.assertNotEqual(first['assets']['glb'], second['assets']['glb'])
            self.assertEqual(self.call(route)['assets'], first['assets'])
            adjusted = {**regen, 'structure':'cap_mask'}
            open_child = self.call(route + '/regenerate', adjusted)
            self.assertEqual(self.call(route + '/regenerate', adjusted)['id'], open_child['id'])
            self.assertNotEqual(child['id'], open_child['id'])
            self.assertEqual(open_child['generation_number'], 3)
            open_child = self.wait(open_child['id'], 'preview')
            self.assertEqual(open_child['wear_structure'], 'cap_mask')
            self.assertEqual(open_child['prompt_spec']['resolved_structure'], 'cap_mask')
            self.assertNotEqual(open_child['prompt_spec']['prompt'], first['prompt_spec']['prompt'])
            self.assertEqual(self.call(route)['wear_structure'], 'full_shell')
            self.call(route + '/approve', {})
            self.call(route + '/approve', {})
            self.assertEqual(self.call(route + '/regenerate', regen)['id'], child['id'])
            ready = self.wait(pid, 'ready')
            self.assertTrue(ready['preview_approved'])
            self.assertEqual(ready['source_report']['quality']['status'], 'passed')
            self.assertFalse(self.call('/api/projects/' + child['id'])['preview_approved'])
            self.assertEqual(provider.call_count, 3)

    def test_invalid_text_never_creates_cloud_job(self):
        for args in ({'theme':' '}, {'theme':'猫','preset':'unknown'}, {'theme':'A'*501}):
            with self.assertRaises(HTTPError) as result:
                self.call('/api/projects', {'input_mode':'text','request_id':'bad-input', **args})
            self.assertEqual(result.exception.code, 400)
        self.assertEqual(self.jobs.store.all(), [])
