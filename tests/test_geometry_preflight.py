"""Manufacturing preflight fixtures are generated here and contain no user assets."""

import hashlib
import math
import struct
import tempfile
import unittest
from pathlib import Path

from studio.runtime import blender_path, geometry


def write_mesh(path, vertices, faces):
    triangles = [(face[0], face[i], face[i + 1]) for face in faces for i in range(1, len(face) - 1)]
    with path.open("wb") as output:
        output.write(bytes(80) + struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            output.write(struct.pack("<12fH", 0, 0, 0, *vertices[a], *vertices[b], *vertices[c], 0))
    return path


def box_with_top_hole(path, half_hole, seal_inside=False):
    def corners(size, z):
        return [(-size, -size, z), (size, -size, z), (size, size, z), (-size, size, z)]

    vertices = corners(30, 0) + corners(30, 50) + corners(half_hole, 50)
    faces = []
    for i in range(4):
        j = (i + 1) % 4
        faces += [(i, j, j + 4, i + 4), (i + 4, j + 4, j + 8, i + 8)]
    if seal_inside:
        vertices += corners(half_hole, 0)
        for i in range(4):
            j = (i + 1) % 4
            faces += [(i + 8, j + 8, j + 12, i + 12), (j, i, i + 12, j + 12)]
    else:
        faces += [(3, 2, 1, 0)]
    return write_mesh(path, vertices, faces)


def pitted_sphere(path):
    around, rows = 144, 76
    vertices = [(0, 0, 180)]
    pit_direction = (0.56, 0.65, math.sqrt(1 - 0.56**2 - 0.65**2))
    for j in range(1, rows):
        theta = math.pi * j / rows
        for i in range(around):
            angle = math.tau * i / around
            d = (math.sin(theta) * math.cos(angle), math.sin(theta) * math.sin(angle), math.cos(theta))
            distance_sq = 90**2 * sum((a - b) ** 2 for a, b in zip(d, pit_direction))
            radius = 90 - 8.5 * math.exp(-distance_sq / 24)
            vertices.append((radius * d[0], radius * d[1], 90 + radius * d[2]))
    vertices.append((0, 0, 0))

    def idx(row, col):
        return 1 + (row - 1) * around + col % around

    faces = []
    for i in range(around):
        faces += [
            (0, idx(1, i), idx(1, i + 1)),
            (idx(rows - 1, i), len(vertices) - 1, idx(rows - 1, i + 1)),
        ]
        for row in range(1, rows - 1):
            faces.append((idx(row, i), idx(row + 1, i), idx(row + 1, i + 1), idx(row, i + 1)))
    return write_mesh(path, vertices, faces)


class PreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            blender_path()
        except ValueError:
            raise unittest.SkipTest("未安装 Blender") from None

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="helmetfit-preflight-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def check(self, source, name, generated=False, repair="auto"):
        directory = self.folder / name
        original_hash = hashlib.sha256(source.read_bytes()).digest()
        result = geometry("preflight", directory, source, {"generated": generated, "repair": repair})
        self.assertEqual(original_hash, hashlib.sha256(source.read_bytes()).digest())
        quality = result["quality"]
        self.assertEqual((directory / "model.stl").exists(), quality["status"] in ("passed", "repaired"))
        return quality

    def test_watertight_designed_opening_stays_open(self):
        stock = box_with_top_hole(self.folder / "visor.stl", 14, seal_inside=True)
        q = self.check(stock, "checked")
        self.assertEqual(q["status"], "passed")
        expected_volume = (60**2 - 28**2) * 50 / 1000
        self.assertAlmostEqual(q["after"]["volume_cm3"], expected_volume, places=2)

    def test_pinholes_repaired_large_open_boundary_retained_for_review(self):
        small = box_with_top_hole(self.folder / "pinhole.stl", 0.45)
        q = self.check(small, "small")
        self.assertEqual(q["status"], "repaired", q)
        self.assertGreater(q["before"]["boundary_edges"], 0)
        self.assertEqual(q["after"]["boundary_edges"], 0)
        self.assertEqual(q["local_repair"]["filled_tiny_loops"], 1)
        wide = box_with_top_hole(self.folder / "open.stl", 14)
        self.assertEqual(self.check(wide, "wide")["status"], "needs_review")

    def test_closed_pit_requires_explicit_outer_and_is_measurably_repaired(self):
        source = pitted_sphere(self.folder / "pit.stl")
        q = self.check(source, "review", generated=True)
        self.assertEqual(q["status"], "needs_review", q)
        self.assertEqual(q["before"]["nonmanifold_edges"], 0)
        repaired = self.check(source, "rebuilt", generated=True, repair="outer")
        self.assertEqual(repaired["status"], "repaired", repaired)
        self.assertGreaterEqual(repaired["pits"]["patched_count"], 1)
        expected_center = (90 * 0.56, 90 * 0.65, 90 * (1 + math.sqrt(1 - 0.56**2 - 0.65**2)))
        pit = min(repaired["pits"]["areas"], key=lambda area: math.dist(area["center_mm"], expected_center))
        self.assertLess(math.dist(pit["center_mm"], expected_center), 5)
        self.assertGreater(pit["depth_mm"], 4)
        self.assertLess(repaired["deviation"]["outside_p99_mm"], 0.60)
        self.assertLess(repaired["deviation"]["silhouette_mm"], 0.60)


if __name__ == "__main__":
    unittest.main()
