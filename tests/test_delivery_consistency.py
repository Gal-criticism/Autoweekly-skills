"""Regression tests for weekly-cover unified delivery manifest and weekly-bgm-overlay atomic verify."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
for rel in ("weekly-cover/scripts/runtime", "weekly-bgm-overlay/scripts/runtime"):
    path_str = str(ROOT / rel)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from cover import cli as cover_cli  # noqa: E402
from weekly_overlay import cli as overlay_cli  # noqa: E402


def _make_overlay_png(path: Path, color=(255, 255, 255, 255)) -> None:
    """Create a valid 2160x1080 RGBA overlay PNG with alpha only inside y < 500 text boxes."""
    img = Image.new("RGBA", (2160, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 160, 260, 210), fill=color)   # category
    draw.rectangle((40, 220, 320, 260), fill=color)   # date
    draw.rectangle((40, 280, 420, 330), fill=color)   # title
    draw.rectangle((40, 340, 380, 390), fill=color)   # artist
    img.save(path, "PNG")


class DeliveryConsistencyTests(unittest.TestCase):
    def test_cover_refined_t2_is_consistent_across_preview_exports_and_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "20261119-120000-deadbeef"
            (run_dir / "templates").mkdir(parents=True)
            (run_dir / "cg").mkdir(parents=True)
            t1_tpl = run_dir / "templates/template_01.psd"
            t2_tpl = run_dir / "templates/template_02.psd"
            t3_tpl = run_dir / "templates/template_03.psd"
            for tpl in (t1_tpl, t2_tpl, t3_tpl):
                tpl.write_bytes(b"psd")
            cg_file = run_dir / "cg/cg_01.png"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(cg_file)

            job = {
                "schema_version": 1,
                "run_id": run_dir.name,
                "output_dir": str(run_dir.resolve()),
                "period": "2026.10.31 - 2026.11.06",
                "templates": {},
                "cgs": [{"index": 1, "path": str(cg_file)}],
                "covers": [
                    {
                        "index": 1,
                        "filename": "T1_CG1",
                        "template": str(t1_tpl),
                        "issue": 268,
                        "replacements": [{"id": 1, "image": str(cg_file), "cg": 1}],
                    },
                    {
                        "index": 2,
                        "filename": "T2_CG1",
                        "template": str(t2_tpl),
                        "issue": 268,
                        "replacements": [{"id": 2, "image": str(cg_file), "cg": 1}],
                    },
                    {
                        "index": 3,
                        "filename": "T3_CG1",
                        "template": str(t3_tpl),
                        "issue": 268,
                        "replacements": [{"id": 3, "image": str(cg_file), "cg": 1}],
                    },
                ],
            }
            (run_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            (run_dir / "covers_result.json").write_text(json.dumps({"status": "rendered"}), encoding="utf-8")

            # Distinct solid colors: T1=green, T2 original=RED, T3=yellow
            Image.new("RGB", (2400, 1500), (20, 200, 20)).save(run_dir / "T1_CG1.png")
            Image.new("RGB", (3525, 1500), (220, 20, 20)).save(run_dir / "T2_CG1.png")
            Image.new("RGB", (2400, 1800), (220, 220, 20)).save(run_dir / "T3_CG1.png")

            # Initial verify uses original RED T2
            ver_orig = cover_cli.verify(SimpleNamespace(run_dir=str(run_dir), refined=False))
            t2_export_path = run_dir / "exports" / "268 期周报文章封面.png"
            self.assertTrue(t2_export_path.is_file())
            with Image.open(t2_export_path) as im:
                r, g, b = im.getpixel((100, 100))
                self.assertGreater(r, 180)
                self.assertLess(b, 60)

            # Now create a clearly different BLUE refined T2
            refined_dir = run_dir / "refined"
            refined_dir.mkdir()
            Image.new("RGB", (3525, 1500), (20, 40, 230)).save(refined_dir / "T2_CG1.png")
            (refined_dir / "refine_batch_result.json").write_text(
                json.dumps([{"filename": "T2_CG1", "status": "rendered"}]), encoding="utf-8"
            )

            # verify --refined updates unified exports/, contact_sheet.png, delivery_manifest.json, verification.json
            ref_report = cover_cli.verify(SimpleNamespace(run_dir=str(run_dir), refined=True))
            # Plain verify afterwards must ALSO stay consistent with refined BLUE T2
            plain_report = cover_cli.verify(SimpleNamespace(run_dir=str(run_dir), refined=False))
            self.assertEqual(ref_report["deliverables_fingerprint"], plain_report["deliverables_fingerprint"])

            # 1. Final exported PNG must be BLUE (refined), not RED (original)
            with Image.open(t2_export_path) as im:
                r, g, b = im.getpixel((100, 100))
                self.assertGreater(b, 180)
                self.assertLess(r, 60)

            # 2. Contact sheet row 2 (T2) must also be BLUE
            with Image.open(run_dir / "contact_sheet.png") as sheet:
                # Row 1 height = round(1500*800/2400) + 48 = 548, y_row2_start = 32 + 548 + 24 = 604
                sr, sg, sb = sheet.getpixel((200, 700))
                self.assertGreater(sb, 180)
                self.assertLess(sr, 60)

            # 3. delivery_manifest.json, verification.json, and review must all reference the exact same BLUE export hash
            manifest = json.loads((run_dir / "delivery_manifest.json").read_text(encoding="utf-8"))
            by_name = {d["filename"]: d for d in manifest["deliverables"]}
            self.assertEqual(by_name["T2_CG1"]["source_kind"], "refined")
            actual_export_sha = hashlib.sha256(t2_export_path.read_bytes()).hexdigest()
            self.assertEqual(by_name["T2_CG1"]["export_sha256"], actual_export_sha)

            reviewed = cover_cli.review(SimpleNamespace(run_dir=str(run_dir), note="T2 蓝调校色目视通过"))
            self.assertEqual(reviewed["visual_review"]["status"], "passed")
            self.assertEqual(reviewed["visual_review"]["deliverables_fingerprint"], manifest["deliverables_fingerprint"])
            rev_by_name = {d["filename"]: d for d in reviewed["visual_review"]["deliverables"]}
            self.assertEqual(rev_by_name["T2_CG1"]["export_sha256"], actual_export_sha)
            self.assertEqual(rev_by_name["T2_CG1"]["source_kind"], "refined")

            # Re-verifying identical deliverables preserves the passed visual_review
            rechecked = cover_cli.verify(SimpleNamespace(run_dir=str(run_dir), refined=False))
            self.assertEqual(rechecked["visual_review"]["status"], "passed")
            self.assertEqual(rechecked["visual_review"]["note"], "T2 蓝调校色目视通过")

    def test_overlay_verify_is_atomic_and_preserves_visual_review_by_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "master.psd"
            template.write_bytes(b"master-psd")
            bgm_txt = root / "bgm.txt"
            bgm_txt.write_text("其余：ArtistA - SongA", encoding="utf-8")

            prep = overlay_cli.prepare(
                SimpleNamespace(
                    task=None,
                    start="2026-10-31",
                    end="2026-11-06",
                    today=None,
                    input=str(bgm_txt),
                    template=str(template),
                    output=str(root / "runs"),
                )
            )
            run_dir = Path(prep["run_dir"])
            job = overlay_cli.read_json(run_dir / "job.json")
            out_name = job["filenames"][0]
            out_png = run_dir / out_name
            outputs_list = []
            for fname, item in zip(job["filenames"], job["task"]["items"]):
                _make_overlay_png(run_dir / fname, color=(255, 255, 255, 255))
                outputs_list.append(
                    {
                        "filename": fname,
                        "category": item["category"],
                        "title": item["title"],
                        "artist": item["artist"],
                        "fields": {
                            "category": {"font": "MiSans-Semibold", "text": item["category"], "bounds": [40, 160, 260, 210]},
                            "date": {"font": "NotoSerifCJKsc-SemiBold", "text": job["period"], "bounds": [40, 220, 320, 260]},
                            "title": {"font": "MiSans-Semibold", "text": item["title"], "bounds": [40, 280, 420, 330]},
                            "artist": {"font": "MiSans-Regular", "text": item["artist"], "bounds": [40, 340, 380, 390]},
                        },
                    }
                )
            result_payload = {
                "schema_version": 1,
                "run_id": job["run_id"],
                "status": "rendered",
                "outputs": outputs_list,
            }
            (run_dir / "result.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            # Initial verify & record visual_review
            ver1 = overlay_cli.verify_run(run_dir)
            self.assertEqual(ver1["status"], "verified")
            self.assertEqual(ver1["visual_review"], "pending")
            ver1["visual_review"] = {
                "status": "passed",
                "note": "字形清晰无裁切",
                "recorded_at": "2026-11-19T12:00:00+08:00",
                "outputs_fingerprint": ver1["outputs_fingerprint"],
                "outputs_sha256": {r["filename"]: r["sha256"] for r in ver1["outputs"]},
            }
            overlay_cli.save_json(run_dir / "verification.json", ver1)

            # Case 1: If reading PNG fails mid-verify, previous verification.json & visual_review stay intact
            orig_bytes = out_png.read_bytes()
            out_png.write_bytes(b"corrupted-not-a-png")
            with self.assertRaises(Exception):
                overlay_cli.verify_run(run_dir)
            preserved = overlay_cli.read_json(run_dir / "verification.json")
            self.assertEqual(preserved["status"], "verified")
            self.assertEqual(preserved["visual_review"]["status"], "passed")
            self.assertEqual(preserved["visual_review"]["note"], "字形清晰无裁切")

            # Case 2: Restoring identical PNG and re-verifying preserves passed visual_review
            out_png.write_bytes(orig_bytes)
            ver2 = overlay_cli.verify_run(run_dir)
            self.assertEqual(ver2["status"], "verified")
            self.assertEqual(ver2["visual_review"]["status"], "passed")
            self.assertEqual(ver2["visual_review"]["note"], "字形清晰无裁切")

            # Case 3: Changing PNG content resets visual_review to pending
            _make_overlay_png(out_png, color=(240, 240, 255, 255))
            ver3 = overlay_cli.verify_run(run_dir)
            self.assertEqual(ver3["status"], "verified")
            self.assertNotEqual(ver3["outputs_fingerprint"], ver1["outputs_fingerprint"])
            self.assertEqual(ver3["visual_review"], "pending")


if __name__ == "__main__":
    unittest.main()
