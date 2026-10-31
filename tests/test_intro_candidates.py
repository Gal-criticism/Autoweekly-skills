"""Tests for scoped intro image preparation."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = ROOT / "weekly-intro/scripts/runtime/intro/candidates.py"

candidates = None


def setUpModule():
    global candidates
    spec = importlib.util.spec_from_file_location("intro_candidates", CANDIDATES_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CANDIDATES_PATH}")
    candidates = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidates)


def save_image(path, size=(80, 40), color=(255, 0, 0), orientation=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    image.save(path, **options)
    return path


def make_directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise unittest.SkipTest("directory symlinks and junctions are unavailable")


def color_bounds(image, predicate):
    xs = []
    ys = []
    for y in range(image.height):
        for x in range(image.width):
            if predicate(image.getpixel((x, y))):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise AssertionError("expected color was not found")
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


class ScanRootsTests(unittest.TestCase):
    def test_scan_is_deterministic_and_deduplicates_oriented_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "images"
            nested = root / "nested"
            nested.mkdir(parents=True)
            first = save_image(root / "a.png", size=(10, 5), color=(12, 34, 56))
            duplicate = root / "duplicate.png"
            duplicate.write_bytes(first.read_bytes())
            oriented = save_image(
                nested / "oriented.jpg",
                size=(7, 3),
                color=(90, 80, 70),
                orientation=6,
            )
            (root / "broken.png").write_bytes(b"not an image")
            (root / "notes.txt").write_text("ignored", encoding="utf-8")

            result = candidates.scan_roots([str(root)])
            repeated = candidates.scan_roots([str(root)])

            self.assertEqual(result, repeated)
            self.assertEqual([item["id"] for item in result], ["C0001", "C0002"])
            self.assertEqual([Path(item["path"]).name for item in result], ["a.png", "oriented.jpg"])
            self.assertEqual((result[0]["width"], result[0]["height"]), (10, 5))
            self.assertEqual((result[1]["width"], result[1]["height"]), (3, 7))
            self.assertEqual(
                result[1]["sha256"],
                hashlib.sha256(oriented.read_bytes()).hexdigest(),
            )
            self.assertEqual(result[1]["mtime_ns"], oriented.stat().st_mtime_ns)
            self.assertTrue(Path(result[0]["path"]).is_absolute())

    def test_scan_rejects_invalid_roots_and_skips_external_link_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "allowed"
            outside = base / "outside"
            root.mkdir()
            save_image(root / "inside.png", color=(1, 2, 3))
            save_image(outside / "outside.png", color=(4, 5, 6))
            make_directory_link(root / "external", outside)

            result = candidates.scan_roots([str(root)])
            self.assertEqual([Path(item["path"]).name for item in result], ["inside.png"])

            with self.assertRaisesRegex(ValueError, "root"):
                candidates.scan_roots([])
            with self.assertRaisesRegex(ValueError, "root"):
                candidates.scan_roots([str(base / "missing")])
            with self.assertRaisesRegex(ValueError, "root"):
                candidates.scan_roots([str(root), str(base / "missing")])


class ContactSheetTests(unittest.TestCase):
    def test_contact_sheets_paginate_and_keep_thumbnail_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            images = base / "images"
            for index in range(31):
                color = (20 + index, 120, 220 - index)
                save_image(images / f"{index:02d}.png", size=(80, 20), color=color)
            scanned = candidates.scan_roots([str(images)])
            output = base / "sheets"

            paths = candidates.build_contact_sheets(scanned, output)

            self.assertEqual(len(paths), 2)
            self.assertTrue(all(Path(path).is_absolute() for path in paths))
            self.assertTrue(all(Path(path).parent == output.resolve() for path in paths))
            with Image.open(paths[0]) as sheet:
                first_color = (20, 120, 220)
                left, top, right, bottom = color_bounds(
                    sheet.convert("RGB"),
                    lambda pixel: pixel == first_color,
                )
                self.assertAlmostEqual((right - left) / (bottom - top), 4.0, delta=0.35)

    def test_contact_sheets_with_no_candidates_are_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(candidates.build_contact_sheets([], Path(directory)), [])


class ReplacementTests(unittest.TestCase):
    def test_decoding_uses_the_same_bytes_that_were_hashed(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); images=root/'images'; images.mkdir()
            source=images/'one.png'; Image.new('RGB',(40,40),(255,0,0)).save(source)
            scanned=candidates.scan_roots([str(images)])
            real_open=Image.open
            def change_file_before_decode(stream,*args,**kwargs):
                Image.new('RGB',(40,40),(0,0,255)).save(source)
                return real_open(stream,*args,**kwargs)
            with patch.object(candidates.Image,'open',side_effect=change_file_before_decode):
                result=candidates.build_replacements([{'slot_id':'one','candidate_id':scanned[0]['id']}],scanned,[{'id':'one','width':40,'height':40}],root/'out',[str(images)])
            with real_open(result[0]['path']) as image:
                self.assertEqual(image.convert('RGB').getpixel((20,20)),(255,0,0))

    def test_builds_exif_oriented_replacements_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            images = base / "images"
            first = save_image(images / "wide.png", size=(400, 100), color=(20, 180, 40))
            oriented = save_image(
                images / "portrait.jpg",
                size=(8, 4),
                color=(200, 50, 100),
                orientation=6,
            )
            scanned = candidates.scan_roots([str(images)])
            by_name = {Path(item["path"]).name: item for item in scanned}
            slots = [
                {"id": "../../unsafe cover", "width": 160, "height": 90},
                {"id": "portrait", "width": 40, "height": 80},
            ]
            selections = [
                {"slot_id": "../../unsafe cover", "candidate_id": by_name["wide.png"]["id"]},
                {
                    "slot_id": "portrait",
                    "candidate_id": by_name["portrait.jpg"]["id"],
                    "crop": [0, 0, 4, 8],
                },
            ]
            output = base / "output"
            output.mkdir()
            existing = output / "001.png"
            existing.write_bytes(b"keep me")

            result = candidates.build_replacements(
                selections,
                scanned,
                slots,
                output,
                [str(images)],
            )

            self.assertEqual(existing.read_bytes(), b"keep me")
            self.assertEqual([Path(item["path"]).name for item in result], ["002.png", "003.png"])
            self.assertEqual([item["slot_id"] for item in result], ["../../unsafe cover", "portrait"])
            self.assertEqual(result[0]["crop"], [111, 0, 289, 100])
            self.assertEqual(result[1]["crop"], [0, 0, 4, 8])
            self.assertEqual(result[0]["source_path"], str(first.resolve()))
            self.assertEqual(result[1]["source_path"], str(oriented.resolve()))
            self.assertEqual(result[1]["source_sha256"], by_name["portrait.jpg"]["sha256"])
            with Image.open(result[0]["path"]) as image:
                self.assertEqual(image.size, (160, 90))
                self.assertEqual(image.format, "PNG")
            with Image.open(result[1]["path"]) as image:
                self.assertEqual(image.size, (40, 80))

    def test_rejects_duplicate_missing_and_extra_slots_or_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            images = base / "images"
            save_image(images / "one.png", color=(80, 10, 10))
            save_image(images / "two.png", color=(10, 80, 10))
            scanned = candidates.scan_roots([str(images)])
            slot = {"id": "main", "width": 20, "height": 20}
            selection = {"slot_id": "main", "candidate_id": scanned[0]["id"]}

            cases = [
                (
                    [selection],
                    scanned,
                    [slot, dict(slot)],
                    "slot id",
                ),
                (
                    [selection, dict(selection)],
                    scanned,
                    [slot],
                    "selection",
                ),
                (
                    [],
                    scanned,
                    [slot],
                    "missing",
                ),
                (
                    [selection, {"slot_id": "extra", "candidate_id": scanned[1]["id"]}],
                    scanned,
                    [slot],
                    "extra",
                ),
                (
                    [{"slot_id": "main", "candidate_id": "C9999"}],
                    scanned,
                    [slot],
                    "missing candidate",
                ),
            ]
            for selections, candidate_list, slots, message in cases:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError, message):
                        candidates.build_replacements(
                            selections,
                            candidate_list,
                            slots,
                            base / "output",
                            [str(images)],
                        )

            duplicate_id = [scanned[0], dict(scanned[0])]
            with self.assertRaisesRegex(ValueError, "candidate id"):
                candidates.build_replacements(
                    [selection],
                    duplicate_id,
                    [slot],
                    base / "output",
                    [str(images)],
                )

            duplicate_content = [scanned[0], dict(scanned[0])]
            duplicate_content[1]["id"] = "C0002"
            with self.assertRaisesRegex(ValueError, "candidate content"):
                candidates.build_replacements(
                    [selection],
                    duplicate_content,
                    [slot],
                    base / "output",
                    [str(images)],
                )

    def test_rejects_invalid_crops_and_repeated_selected_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            images = base / "images"
            save_image(images / "one.png", size=(100, 100), color=(80, 10, 10))
            save_image(images / "two.png", size=(100, 100), color=(10, 80, 10))
            scanned = candidates.scan_roots([str(images)])
            slot = {"id": "main", "width": 50, "height": 50}
            invalid_crops = [
                [0, 0, 50, 100],
                [-1, 0, 50, 50],
                [0, 0, 101, 100],
                [50, 0, 50, 100],
                [60, 0, 50, 100],
                [0, 0, 50],
            ]
            for crop in invalid_crops:
                with self.subTest(crop=crop):
                    with self.assertRaisesRegex(ValueError, "crop"):
                        candidates.build_replacements(
                            [{"slot_id": "main", "candidate_id": scanned[0]["id"], "crop": crop}],
                            scanned,
                            [slot],
                            base / "output",
                            [str(images)],
                        )

            repeated = [
                {"slot_id": "one", "candidate_id": scanned[0]["id"]},
                {"slot_id": "two", "candidate_id": scanned[0]["id"]},
            ]
            with self.assertRaisesRegex(ValueError, "candidate"):
                candidates.build_replacements(
                    repeated,
                    scanned,
                    [
                        {"id": "one", "width": 20, "height": 20},
                        {"id": "two", "width": 20, "height": 20},
                    ],
                    base / "output",
                    [str(images)],
                )

    def test_revalidates_scope_hash_and_mtime_before_writing_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            images = base / "images"
            outside = base / "outside"
            first = save_image(images / "one.png", color=(80, 10, 10))
            second = save_image(images / "two.png", color=(10, 80, 10))
            save_image(outside / "outside.png", color=(10, 10, 80))
            scanned = candidates.scan_roots([str(images)])
            by_name = {Path(item["path"]).name: item for item in scanned}
            slots = [
                {"id": "one", "width": 20, "height": 20},
                {"id": "two", "width": 20, "height": 20},
            ]
            selections = [
                {"slot_id": "one", "candidate_id": by_name["one.png"]["id"]},
                {"slot_id": "two", "candidate_id": by_name["two.png"]["id"]},
            ]

            changed_mtime = by_name["two.png"]["mtime_ns"] + 10_000_000
            save_image(second, color=(1, 2, 3))
            os.utime(second, ns=(changed_mtime, changed_mtime))
            output = base / "output"
            with self.assertRaisesRegex(ValueError, "hash|mtime"):
                candidates.build_replacements(
                    selections,
                    scanned,
                    slots,
                    output,
                    [str(images)],
                )
            self.assertEqual(list(output.glob("*.png")), [])

            outside_path = outside / "outside.png"
            outside_candidate = {
                "id": "C9001",
                "path": str(outside_path.resolve()),
                "sha256": hashlib.sha256(outside_path.read_bytes()).hexdigest(),
                "width": 80,
                "height": 40,
                "mtime_ns": outside_path.stat().st_mtime_ns,
            }
            with self.assertRaisesRegex(ValueError, "scope"):
                candidates.build_replacements(
                    [{"slot_id": "one", "candidate_id": outside_candidate["id"]}],
                    [outside_candidate],
                    [{"id": "one", "width": 20, "height": 20}],
                    output,
                    [str(images)],
                )
            self.assertEqual(list(output.glob("*.png")), [])
            self.assertTrue(first.exists())


if __name__ == "__main__":
    unittest.main()
