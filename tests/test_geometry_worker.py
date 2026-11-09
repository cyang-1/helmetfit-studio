"""Integration regressions using the real workstation Blender and original stock."""
import hashlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from studio.geometry_head import BROW, SEGMENTS, head_profiles, measurements
from studio.runtime import ROOT, blender_path, geometry


class HeadTapeTest(unittest.TestCase):
    def test_actual_brow_perimeter_follows_tape(self):
        for cm in (54, 58, 62):
            m = measurements({'circumference': cm})
            head, cutter = head_profiles(m)
            row = min(head,key=lambda item: abs(item[0]-BROW*m['height']))[1]
            vertices=[(r*math.cos(i*math.tau/SEGMENTS), r*math.sin(i*math.tau/SEGMENTS))
                      for i,r in enumerate(row)]
            self.assertAlmostEqual(sum(math.dist(vertices[i-1],v) for i,v in enumerate(vertices)), 10*cm, places=4)
            # For upward entry, every higher cross section must fit through each lower one.
            for lower,upper in zip(cutter,cutter[1:]):
                self.assertTrue(all(a>=b for a,b in zip(lower[1],upper[1])))


class GeometryPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            exe = blender_path()
        except (FileNotFoundError, ValueError):
            raise unittest.SkipTest('未安装 Blender') from None
        if not Path(exe).is_file():
            raise unittest.SkipTest('未安装 Blender')
        cls.tmp = tempfile.TemporaryDirectory(prefix='helmetfit-geometry-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.source = cls.root/'source'
        cls.imported = geometry('prepare', cls.source, ROOT/'static/studio-example/helmet.glb')

    def test_default_stock_and_preview_match(self):
        params=self.imported['defaults']
        preview=self.root/'preview'
        project=self.root/'fit'
        geometry('cutter', preview, params=params)
        result=geometry('fit', project,self.source/'model.stl',params)
        self.assertEqual(result['blocking'],[])
        self.assertTrue(result['ring_valid'])
        self.assertGreaterEqual(result['checks']['sampled_wall_min_mm'],2.4)
        self.assertTrue(result['checks']['eye_open'])
        self.assertEqual(result['checks']['insertion_collision_samples'],0)
        self.assertEqual(result['parts'], ['part-1.stl'])
        self.assertLess(result['checks']['volume_cm3'], 550)
        for name in ('head.stl','cutter.stl'):
            self.assertEqual(hashlib.sha256((project/name).read_bytes()).digest(),
                             hashlib.sha256((preview/name).read_bytes()).digest())

    def test_split_and_closed_eyes(self):
        params=dict(self.imported['defaults'],split_axis='y')
        result=geometry('fit',self.root/'split',self.source/'model.stl',params)
        self.assertEqual(result['blocking'],[])
        self.assertEqual(result['parts'],['part-1A.stl','part-1B.stl'])
        self.assertTrue(result['assembly'])
        params['eye_window']=False
        rejected=geometry('fit',self.root/'closed',self.source/'model.stl',params)
        self.assertFalse(rejected['checks']['eye_open'])
        self.assertTrue(rejected['blocking'])
        self.assertFalse(rejected['parts'])

    def test_surface_rebuild_on_open_reference_mesh(self):
        # Remove separated triangles to model gaps in the visible skin without
        # depending on a downloaded or private AI asset as a test fixture.
        data = (self.source/'model.stl').read_bytes()
        count = struct.unpack_from('<I', data, 80)[0]
        faces = [data[84+i*50:134+i*50] for i in range(count) if i % 41]
        stock = self.root/'open-stock.stl'
        stock.write_bytes(data[:80] + struct.pack('<I',len(faces)) + b''.join(faces))
        params = dict(self.imported['defaults'], rebuild_outer=True)
        result = geometry('fit', self.root/'rebuilt', stock, params)
        self.assertEqual(result['checks']['nonmanifold_edges'], 0)
        self.assertTrue(result['checks']['eye_open'])
        self.assertTrue(any('可见外表面' in s for s in result['warnings']))

    def test_external_glb_uri_and_geometry_only_obj(self):
        document={'asset':{'version':'2.0'},'extensions':{'test':{'uri':'../private.png'}}}
        data=json.dumps(document).encode()
        data+=b' '*((-len(data))%4)
        glb=self.root/'external.glb'
        glb.write_bytes(struct.pack('<4s4I',b'glTF',2,len(data)+20,len(data),0x4e4f534a)+data)
        with self.assertRaisesRegex(ValueError,'内嵌'):
            geometry('prepare',self.root/'reject',glb)
        source=self.root/'safe.obj'
        source.write_text('mtllib ../private.mtl\ncall ../tool.obj\nv 0 0 0\nv 50 0 0\nv 0 50 0\nv 0 0 50\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n')
        obj=geometry('prepare',self.root/'obj',source)
        self.assertEqual(obj['dimensions'],[50,50,50])


if __name__=='__main__':
    unittest.main()
