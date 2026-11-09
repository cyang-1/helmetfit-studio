import tempfile
import unittest
from pathlib import Path

from studio.jobs import Jobs, archive_current, fit_params
from studio.store import Store
from studio.slicer import resolve_profile


class CoreTests(unittest.TestCase):
    def test_fit_params_estimate_and_optional_measurements(self):
        self.assertNotIn('width', fit_params({'width': ''}))
        self.assertEqual(fit_params({'width': 164})['width'], 164)
        for data in ({'circumference': float('nan')}, {'rx': 181}, {'eye_window': 'false'}):
            with self.assertRaises(ValueError):
                fit_params(data)

    def test_duplicates_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            self.assertTrue(store.create({'id': 'first'}, 'same')[1])
            self.assertFalse(store.create({'id': 'second'}, 'same')[1])
            store.update('first', stage='ready')
            self.assertEqual(Store(folder).all()[0]['stage'], 'ready')
            store.delete('first')
            with self.assertRaisesRegex(ValueError, '已删除'):
                store.create({'id': 'replayed'}, 'same')
            self.assertTrue(store.create({'id': 'new'}, 'different')[1])

    def test_preflight_gate_for_historic_fit_and_review(self):
        with tempfile.TemporaryDirectory() as folder:
            jobs = Jobs(folder)
            self.addCleanup(jobs.lane.shutdown)
            old = {'id':'old','stage':'done','source_ready':True,'revision':2,
                   'params':{},'report':{},'assets':{'fitted':'/old.stl','plates':[]},'history':[]}
            jobs.store.create(old, 'imported')
            with self.assertRaisesRegex(ValueError, '检查并修复'):
                jobs.action('old', 'fit', {})
            jobs.store.update('old', source_report={'quality':{'status':'needs_review'}})
            with self.assertRaisesRegex(ValueError, '检查并修复'):
                jobs.action('old', 'fit', {})
            previous = archive_current(jobs.store.get('old'))
            self.assertEqual(previous[0]['assets']['fitted'], '/old.stl')
            after = dict(old, history=previous, assets={'model':'/new.stl','plates':[]})
            self.assertEqual(archive_current(after), previous)

    def test_profile_inheritance_and_templates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'base.json').write_text('{"height":250,"width":256}')
            (root / 'start.json').write_text('{"start":"P1S"}')
            (root / 'p1s.json').write_text('{"inherits":"base","include":["start"],"height":249}')
            self.assertEqual(resolve_profile(root, 'p1s'), {'width': 256, 'height': 249, 'start': 'P1S'})
            (root / 'cycle.json').write_text('{"inherits":"cycle"}')
            with self.assertRaises(ValueError):
                resolve_profile(root, 'cycle')
