"""Verify upload-ready PNG names and actual byte limits."""
from pathlib import Path
import random
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "weekly-cover/scripts/runtime"))
from cover.delivery import export_pngs, write_png


class CoverDeliveryTests(unittest.TestCase):
    def test_noisy_png_is_reduced_to_actual_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "source.png"
            Image.frombytes("RGB", (320, 240), random.Random(7).randbytes(320 * 240 * 3)).save(source)
            record = write_png(source, folder / "final.png", limit=35_000)
            self.assertLessEqual(record["bytes"], 35_000)
            self.assertEqual(record["bytes"], (folder / "final.png").stat().st_size)
            with Image.open(folder / "final.png") as result:
                self.assertEqual(result.format, "PNG")
                self.assertLess(result.width, 320)
                self.assertAlmostEqual(result.width / result.height, 4 / 3, delta=0.02)

    def test_three_names_and_multiple_cg_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            covers = []
            for cg in (1, 2):
                for template in (1, 2, 3):
                    name = f"T{template}_CG{cg}"
                    Image.new("RGB", (32, 24), "red").save(folder / f"{name}.png")
                    covers.append({"filename": name, "issue": 267})
            records = export_pngs(folder, {"covers": covers, "cgs": [{}, {}]})
            self.assertEqual(len({r["path"] for r in records}), 6)
            self.assertEqual({Path(r["path"]).name for r in records}, {
                "267 期周报视频封面 4-3.png", "267 期周报视频封面 16-10.png", "267 期周报文章封面.png"})
            self.assertTrue(all(r["bytes"] <= 5_000_000 for r in records))


if __name__ == "__main__":
    unittest.main()
