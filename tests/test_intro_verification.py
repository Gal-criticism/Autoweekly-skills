"""Contract tests for weekly-intro's static and media verification helpers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "weekly-intro/scripts/runtime/intro/verification.py"


def load_verification():
    spec = importlib.util.spec_from_file_location("intro_verification", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata(**overrides):
    video = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "r_frame_rate": "30000/1001",
        "avg_frame_rate": "30000/1001",
        "duration": "2.002000",
        "nb_frames": "60",
        "nb_read_frames": "60",
    }
    audio = {"codec_type": "audio", "codec_name": "aac"}
    result = {
        "streams": [video, audio],
        "format": {"duration": "2.002000", "size": "1234"},
    }
    for key, value in overrides.items():
        if key == "video":
            result["streams"][0] = value
        elif key == "audio":
            result["streams"] = (
                [result["streams"][0]]
                if value is None
                else [result["streams"][0], value]
            )
        else:
            result[key] = value
    return result


def expected(**overrides):
    result = {
        "width": 1920,
        "height": 1080,
        "fps": "30000/1001",
        "duration": 2.002,
        "frames": 60,
        "audio_expected": True,
    }
    result.update(overrides)
    return result


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verification = load_verification()

    def test_valid_rational_fps_and_audio(self):
        result = self.verification.validate_metadata(metadata(), expected())

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["checks"]["fps"]["actual"], "30000/1001")
        self.assertEqual(result["checks"]["audio"]["status"], "passed")
        self.assertEqual(result["checks"]["duration"]["status"], "passed")
        self.assertEqual(result["checks"]["frames"]["status"], "passed")

    def test_missing_and_multiple_video_streams_fail(self):
        missing = metadata()
        missing["streams"].pop(0)
        multiple = metadata()
        multiple["streams"].append(
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
            }
        )

        missing_result = self.verification.validate_metadata(missing, expected())
        multiple_result = self.verification.validate_metadata(multiple, expected())

        self.assertEqual(missing_result["status"], "failed")
        self.assertEqual(multiple_result["status"], "failed")
        self.assertTrue(
            any("exactly one video stream" in error for error in missing_result["errors"])
        )
        self.assertTrue(
            any("exactly one video stream" in error for error in multiple_result["errors"])
        )

    def test_dimension_fps_and_frame_errors_are_explicit(self):
        invalid = metadata(
            video={
                **metadata()["streams"][0],
                "width": 1280,
                "height": 720,
                "r_frame_rate": "30/1",
                "nb_read_frames": "59",
            }
        )

        result = self.verification.validate_metadata(invalid, expected())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["checks"]["width"]["status"], "failed")
        self.assertEqual(result["checks"]["height"]["status"], "failed")
        self.assertEqual(result["checks"]["fps"]["status"], "failed")
        self.assertEqual(result["checks"]["frames"]["status"], "failed")

    def test_duration_uses_one_frame_tolerance(self):
        passed = metadata(
            video={**metadata()["streams"][0], "duration": "2.033000"},
            format={"duration": "2.033000"},
        )
        failed = metadata(
            video={**metadata()["streams"][0], "duration": "2.037000"},
            format={"duration": "2.037000"},
        )

        passed_result = self.verification.validate_metadata(passed, expected())
        failed_result = self.verification.validate_metadata(failed, expected())

        self.assertEqual(passed_result["checks"]["duration"]["status"], "passed")
        self.assertEqual(failed_result["checks"]["duration"]["status"], "failed")

    def test_audio_modes_and_unavailable_frame_count_are_reported(self):
        no_audio = metadata(audio=None)
        audio_not_checked = self.verification.validate_metadata(
            no_audio, expected(audio_expected=None)
        )
        audio_required = self.verification.validate_metadata(
            no_audio, expected(audio_expected=False)
        )
        no_count = metadata(
            video={
                key: value
                for key, value in metadata()["streams"][0].items()
                if key not in {"nb_frames", "nb_read_frames"}
            }
        )
        no_count_result = self.verification.validate_metadata(no_count, expected())

        self.assertEqual(audio_not_checked["checks"]["audio"]["status"], "not_checked")
        self.assertEqual(audio_required["status"], "verified")
        self.assertEqual(no_count_result["status"], "failed")
        self.assertEqual(no_count_result["checks"]["frames"]["status"], "not_checked")
        self.assertTrue(
            any("cannot be checked" in error for error in no_count_result["errors"])
        )

    def test_required_exact_720_frames_cannot_pass_when_count_is_unavailable(self):
        no_count = metadata(
            video={
                key: value
                for key, value in metadata()["streams"][0].items()
                if key not in {"nb_frames", "nb_read_frames"}
            }
            | {"duration": "24.024000"},
            format={"duration": "24.024000"},
        )

        result = self.verification.validate_metadata(
            no_count,
            expected(frames=720, duration=24.024),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["checks"]["frames"]["status"], "not_checked")
        self.assertTrue(
            any("frame count cannot be checked" in error for error in result["errors"])
        )

class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verification = load_verification()

    def test_config_then_environment_then_path_and_absolute_results(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config_ffmpeg = folder / "configured-ffmpeg.exe"
            env_ffprobe = folder / "environment-ffprobe.exe"
            config_ffmpeg.write_bytes(b"tool")
            env_ffprobe.write_bytes(b"tool")
            config = {"ffmpeg": str(config_ffmpeg)}
            with patch.dict(
                os.environ,
                {"FFMPEG": "configured-ffmpeg.exe", "FFPROBE": str(env_ffprobe)},
                clear=False,
            ):
                result = self.verification.discover_media_tools(config)

        self.assertEqual(Path(result["ffmpeg"]).is_absolute(), True)
        self.assertEqual(Path(result["ffprobe"]).is_absolute(), True)
        self.assertEqual(result["sources"]["ffmpeg"], "config")
        self.assertEqual(result["sources"]["ffprobe"], "environment")
        self.assertEqual(result["errors"], [])

    def test_invalid_explicit_path_is_diagnosed_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path_tool = Path(directory) / "path-ffmpeg.exe"
            path_tool.write_bytes(b"tool")
            with patch.dict(
                os.environ,
                {"FFMPEG": str(path_tool), "PATH": directory},
                clear=False,
            ), patch.object(
                self.verification.shutil,
                "which",
                side_effect=lambda name: str(path_tool) if name == "ffmpeg" else None,
            ) as which:
                result = self.verification.discover_media_tools(
                    {"ffmpeg": str(Path(directory) / "missing.exe")}
                )

        self.assertIsNone(result["ffmpeg"])
        self.assertTrue(any("explicit ffmpeg" in error for error in result["errors"]))
        self.assertFalse(any(call.args[0] == "ffmpeg" for call in which.call_args_list))


class DoctorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verification = load_verification()

    def test_static_doctor_is_ready_without_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = {}
            for key in ("ae", "aerender", "template", "bridge_image", "ffmpeg", "ffprobe", "voukoder"):
                path = folder / f"{key}.bin"
                path.write_bytes(b"fixture")
                config[key] = str(path)
            with patch.object(self.verification.subprocess, "run") as run, patch.object(
                self.verification.subprocess, "Popen"
            ) as popen, patch.object(
                self.verification.platform, "system", return_value="Windows"
            ):
                result = self.verification.doctor(config)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["prerequisites_found"])
        self.assertFalse(result["setup_required"])
        self.assertEqual(result["missing"], [])
        run.assert_not_called()
        popen.assert_not_called()

    def test_missing_media_tools_and_config_require_setup(self):
        with patch.object(
            self.verification,
            "discover_media_tools",
            return_value={
                "ffmpeg": None,
                "ffprobe": None,
                "sources": {"ffmpeg": "missing", "ffprobe": "missing"},
                "errors": ["ffmpeg is not available", "ffprobe is not available"],
            },
        ):
            result = self.verification.doctor({})

        self.assertEqual(result["status"], "setup_required")
        self.assertFalse(result["prerequisites_found"])
        self.assertTrue(result["setup_required"])
        self.assertIn("ae", result["missing"])
        self.assertIn("ffmpeg", result["missing"])
        self.assertIn("ffprobe", result["missing"])


class VideoOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verification = load_verification()

    def test_probe_extract_contact_sheet_and_never_claim_visual_review(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            run_dir = folder / "run"
            run_dir.mkdir()
            video = folder / "片头.mp4"
            video.write_bytes(b"unchanged-video-bytes")
            before = hashlib.sha256(video.read_bytes()).hexdigest()
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "video_path": str(video),
                        "expected": expected(duration=2.0),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            ffmpeg = folder / "ffmpeg.exe"
            ffprobe = folder / "ffprobe.exe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")

            def fake_run(args, **kwargs):
                self.assertIsInstance(args, list)
                self.assertFalse(kwargs.get('shell'))
                self.assertEqual(kwargs.get('encoding'), 'utf-8')
                self.assertEqual(kwargs.get('errors'), 'replace')
                self.assertEqual(kwargs.get('timeout'), 120)
                if Path(args[0]).name.startswith("ffprobe"):
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        stdout=json.dumps(metadata()),
                        stderr="",
                    )
                output = Path(args[-1])
                Image.new("RGB", (320, 180), (20, 30, 40)).save(output)
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with patch.object(
                self.verification,
                "discover_media_tools",
                return_value={
                    "ffmpeg": str(ffmpeg),
                    "ffprobe": str(ffprobe),
                    "sources": {"ffmpeg": "config", "ffprobe": "config"},
                    "errors": [],
                },
            ), patch.object(self.verification.subprocess, "run", side_effect=fake_run) as run:
                result = self.verification.verify_video(run_dir, {})

            self.assertEqual(result["status"], "verified")
            self.assertFalse(result["visual_review"]["performed"])
            self.assertIn("not performed", result["visual_review"]["note"].lower())
            self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), before)
            self.assertGreaterEqual(len(result["frames"]), 10)
            self.assertLessEqual(len(result["frames"]), 12)
            self.assertTrue(all(Path(path).is_absolute() for path in result["frames"]))
            self.assertTrue(Path(result["contact_sheet"]).is_file())
            self.assertTrue(Path(result["verification_path"]).is_file())
            self.assertEqual(
                json.loads(
                    (run_dir / "verification.json").read_text(encoding="utf-8")
                )["status"],
                "verified",
            )
            probe_calls = [
                call.args[0]
                for call in run.call_args_list
                if Path(call.args[0][0]).name.startswith("ffprobe")
            ]
            extraction_calls = [
                call.args[0]
                for call in run.call_args_list
                if Path(call.args[0][0]).name.startswith("ffmpeg")
            ]
            self.assertEqual(len(probe_calls), 1)
            self.assertIn("-count_frames", probe_calls[0])
            self.assertEqual(len(extraction_calls), 12)
            self.assertTrue(all("-c:v" not in args for args in extraction_calls))

    def test_existing_frame_directory_is_rejected_without_touching_it(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            video = run_dir / "video.mp4"
            video.write_bytes(b"video")
            (run_dir / "run.json").write_text(
                json.dumps({"video_path": str(video), "expected": expected()}),
                encoding="utf-8",
            )
            frames = run_dir / "frames"
            frames.mkdir()
            sentinel = frames / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with patch.object(self.verification.subprocess, "run") as run:
                result = self.verification.verify_video(run_dir, {})

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("frames" in error for error in result["errors"]))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
