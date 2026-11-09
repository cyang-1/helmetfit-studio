import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studio.app import Handler, public, reference_file
from studio.jobs import Jobs
from studio.store import Store


class QuietHandler(Handler):
    def log_message(self, *_):
        pass


class UploadViewsTests(unittest.TestCase):
    """Exercise the real upload endpoint and stored reference mapping without creating paid jobs."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.jobs = Jobs(self.root)
        self.addCleanup(self.jobs.lane.shutdown)
        pause = patch.object(self.jobs, '_schedule')
        pause.start()
        self.addCleanup(pause.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.jobs = self.jobs
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, payload=None, kind=None, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        headers = {'Content-Type': kind} if kind else {}
        if origin:
            headers['Origin'] = origin
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def post(self, modes, roles, files, structures=()):
        segments = []
        for key, values in [('input_mode', modes), ('view_labels', roles), ('structure', structures)]:
            for value in values:
                segments.append(f'--split\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        for field, filename, data in files:
            head = f'--split\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n\r\n'.encode()
            segments.append(head + data + b'\r\n')
        code, body = self.request('POST', '/api/projects', b''.join(segments) + b'--split--\r\n',
                                  'multipart/form-data; boundary=split')
        return code, json.loads(body)

    @staticmethod
    def photo(label):
        return ('images', label + '.png', b'\x89PNG\r\n\x1a\n' + label.encode())

    def test_import_structure_applies_to_local_model(self):
        code, item = self.post(['model'], [], [('model','cap.stl',b'solid cap')], ['cap_mask'])
        self.assertEqual(code, 201)
        self.assertEqual(item['wear_structure'], 'cap_mask')
        code, _ = self.post(['single'], ['["reference"]'], [self.photo('input')], ['cap_mask'])
        self.assertEqual(code, 400)

    def test_original_model_and_deletion_are_scoped_to_one_project(self):
        content = b'solid original cap'
        _, model = self.post(['model'], [], [('model', 'my-cap.stl', content)])
        _, sibling = self.post(['model'], [], [('model', 'copy.stl', content)])
        self.assertEqual(model['source_file']['filename'], 'my-cap.stl')
        self.assertEqual(self.request('GET', model['source_file']['url']), (200, content))
        route = '/api/projects/' + model['id']
        self.assertEqual(self.request('DELETE', route)[0], 409)
        self.jobs.store.update(model['id'], stage='ready')
        self.assertEqual(self.request('DELETE', route, origin='https://external.example')[0], 403)
        self.assertEqual(self.request('DELETE', route)[0], 200)
        self.assertEqual(self.request('DELETE', route)[0], 200)
        self.assertEqual(self.request('GET', route)[0], 404)
        self.assertEqual(self.request('GET', model['source_file']['url'])[0], 404)
        self.assertFalse(self.jobs.folder(model['id']).exists())
        self.assertEqual(self.request('GET', sibling['source_file']['url']), (200, content))

    def test_locked_output_cleanup_resumes_on_restart(self):
        _, item = self.post(['single'], ['["reference"]'], [self.photo('waiting')])
        pid = item['id']
        self.jobs.store.update(pid, stage='failed')
        with patch('studio.jobs.shutil.rmtree', side_effect=PermissionError):
            code, body = self.request('DELETE', '/api/projects/' + pid)
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)['cleanup_pending'])
        self.assertEqual(self.request('GET', item['reference_views'][0]['url'])[0], 404)
        restarted = Jobs(self.root)
        self.addCleanup(restarted.lane.shutdown)
        self.assertFalse(restarted.folder(pid).exists())
        self.assertFalse(restarted.store.pending_deletions())

    def test_reordered_upload_and_reopened_record_have_matching_pictures(self):
        files = [self.photo(x) for x in ('right', 'back', 'front', 'left')]
        status, result = self.post(['multi'], [json.dumps(['right','back','front','left'])], files)
        self.assertEqual(status, 201, result)
        pid = result['id']
        reopened = public(Store(self.root).get(pid))
        self.assertEqual(reopened['input_mode'], 'multi')
        views = reopened['reference_views']
        self.assertEqual([v['role'] for v in views], ['front','left','right','back'])
        for i, view in enumerate(views):
            self.assertEqual(view['filename'], view['role'] + '.png')
            status, raw = self.request('GET', view['url'])
            self.assertEqual(status,200)
            self.assertEqual(raw, self.photo(view['role'])[2])
            raw_path = self.jobs.folder(pid) / self.jobs.store.get(pid)['inputs'][i]
            self.assertEqual(raw_path.read_bytes(),raw)
        self.assertEqual(self.request('GET', f'/api/projects/{pid}/references/5')[0],404)

    def test_old_clients_and_old_records_do_not_invent_directions(self):
        code, rec = self.post([], [], [self.photo('a'),self.photo('b')])
        self.assertEqual(code,201)
        self.assertEqual([v['role'] for v in rec['reference_views']], [None,None])
        historical = {'id':'a'*32, 'source_type':'images', 'inputs':['input-1.png']}
        self.assertEqual(public(historical)['input_mode'], 'single')
        self.assertIsNone(public(historical)['reference_views'][0]['role'])

    def test_single_reference_preserves_arbitrary_camera_angle(self):
        code, rec = self.post(['single'], ['["reference"]'], [self.photo('concept')])
        self.assertEqual(code,201,rec)
        self.assertEqual(rec['reference_views'][0]['role'],'reference')
        self.assertEqual(rec['reference_views'][0]['label'],'单图参考')

    def test_illegal_mode_mix_repeats_and_missing_front_are_rejected(self):
        a, b = self.photo('a'), self.photo('b')
        cases = [
            (['multi'], ['["left","right"]'], [a,b]), (['multi'], ['["front"]'], [a]),
            (['multi'], ['["front","front"]'], [a,b]), (['single'], ['["front","back"]'], [a,b]),
            (['multi'], ['["front","sideways"]'], [a,b]), (['multi'], ['["front","left"]'], [a,a]),
            (['single','multi'], ['["front"]'], [a]), (['model'], [], [a]),
            (['single'], ['["front"]'], [a, ('model','file.obj',b'obj')]),
            (['single'], ['{}'], [a]), ([], ['["front"]'], [a]),
        ]
        for modes, labels, files in cases:
            with self.subTest(modes=modes,labels=labels):
                status, body = self.post(modes,labels,files)
                self.assertEqual(status,400,body)
        self.assertEqual(self.jobs.store.all(),[])

    def test_reference_handler_rejects_unregistered_sources(self):
        for path, kind in [('../external.png','images'), ('input.obj','images'), ('input.png','model')]:
            with self.assertRaises(KeyError):
                reference_file({'source_type':kind,'inputs':[path]},self.root,1)
