"""Cap/mask regressions on an original fixture, without cloud or printer access."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from studio.geometry_head import measurements, suggested_pose
from studio.runtime import ROOT, blender_path, geometry


class OpenWearableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            executable = blender_path()
        except ValueError:
            raise unittest.SkipTest("未安装 Blender") from None
        cls.temp = tempfile.TemporaryDirectory(prefix="helmetfit-open-wearable-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "source"
        geometry("prepare", cls.source, ROOT / "static/studio-example/helmet.glb")
        # Keep a connected crown and front mask, with a deliberately open back
        # and one intentional eye aperture. The other eye remains covered.
        build = cls.root / "stock.py"
        build.write_text(
            "import bpy, sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(ROOT / 'studio')!r})\n"
            "from geometry_mesh import import_mesh, cube, boolean, remove, write_stl\n"
            "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
            f"stock = import_mesh(Path({str(cls.source / 'model.stl')!r}))\n"
            "for lo,hi in [((-300,15,-20),(300,300,166)),((-55,-300,116),(-10,0,147))]:\n"
            "    tool = cube(lo,hi)\n"
            "    boolean(stock,tool,'DIFFERENCE')\n"
            "    remove(tool)\n"
            f"write_stl(stock,Path({str(cls.root / 'stock.stl')!r}))\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                str(executable),
                "-b",
                "--factory-startup",
                "--threads",
                "3",
                "--python-exit-code",
                "1",
                "--python",
                str(build),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        cls.params = dict(structure="cap_mask", scale=1.05, dy=-4, dz=-4, eye_z=0.59, ring_z=0.70)

    def test_explicit_rebuild_preserves_open_cap_and_one_existing_eye(self):
        source = self.root / "stock.stl"
        check = self.root / "checked"
        quality = geometry("preflight", check, source, {"structure": "cap_mask", "repair": "outer"})[
            "quality"
        ]
        self.assertEqual(quality["status"], "passed", quality)
        self.assertEqual(quality["before"]["volume_cm3"], quality["after"]["volume_cm3"])
        blocked = geometry("fit", self.root / "preserved", check / "model.stl", self.params)
        self.assertFalse(blocked["checks"]["eye_open"])
        self.assertTrue(blocked["blocking"])
        self.assertEqual(blocked["checks"]["eye_ports"]["cut"], [])
        opened = geometry(
            "fit", self.root / "paired", check / "model.stl", {**self.params, "eye_window": True}
        )
        self.assertFalse(opened["blocking"], opened)
        self.assertEqual(opened["checks"]["eye_ports"]["cut"], ["right"])
        self.assertTrue(opened["checks"]["mask_bridged"])
        self.assertTrue(opened["checks"]["rear_open"])
        self.assertTrue(opened["checks"]["eye_open"])
        self.assertTrue(opened["ring_valid"])
        self.assertEqual(opened["ring_kind"], "open_band")

    def test_rear_panel_is_detected_even_if_two_eye_corridors_clear(self):
        params = {**self.params, "eye_window": True, "dy": 0}
        result = geometry("fit", self.root / "rear", self.source / "model.stl", params)
        self.assertTrue(result["checks"]["eye_open"])
        self.assertFalse(result["checks"]["ear_height_open"])
        self.assertFalse(result["checks"]["rear_open"])
        self.assertFalse(result["parts"])

    def test_headform_pose_keeps_face_clear_of_entry_cutter(self):
        dims = measurements({"circumference": 58})
        cap = suggested_pose((200, 210, 245), dims, "cap_mask")
        shell = suggested_pose((200, 210, 245), dims, "full_shell")
        self.assertLess(cap["dy"], -30)
        self.assertEqual(shell["dy"], 0)
        self.assertEqual(cap["scale"], shell["scale"])


if __name__ == "__main__":
    unittest.main()
