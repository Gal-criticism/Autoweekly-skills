"""Static environment checks and independent MP4 verification for weekly-outro."""

from __future__ import annotations

from fractions import Fraction
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid
from typing import Any


_MEDIA_KEYS = ("ffmpeg", "ffprobe")
_CONFIG_FILE_KEYS = ("ae", "aerender", "template", "bridge_image")
_CHECKED_STATUSES = {"passed", "failed", "not_checked"}
_MEDIA_COMMAND_TIMEOUT_SECONDS = 120


class VerificationError(RuntimeError):
    """An expected verification failure with a concise, user-facing message."""


def _absolute_existing_file(value: Any, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise VerificationError(f"{label} must be a file path")
    text = os.path.expandvars(os.path.expanduser(os.fspath(value)))
    if not text.strip():
        raise VerificationError(f"{label} path is empty")
    path = Path(text)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve(strict=False)
    if not path.is_file():
        raise VerificationError(f"{label} file does not exist: {path}")
    return path


def _path_from_environment(name: str) -> str | None:
    value = os.environ.get(name.upper())
    return value if value else None


def _path_from_path(name: str) -> Path | None:
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found).resolve(strict=False)
    return path if path.is_file() else None


def _path_from_imageio_ffmpeg() -> Path | None:
    try:
        if importlib.util.find_spec("imageio_ffmpeg") is None:
            return None
        module = importlib.import_module("imageio_ffmpeg")
        getter = getattr(module, "get_ffmpeg_exe", None)
        if getter is None:
            return None
        value = getter()
        return _absolute_existing_file(value, "imageio-ffmpeg")
    except (ImportError, OSError, RuntimeError, VerificationError):
        return None


def discover_media_tools(config: dict) -> dict:
    """Find ffmpeg and ffprobe without installing tools or running them."""

    if not isinstance(config, dict):
        raise TypeError("config must be a dict")

    result: dict[str, Any] = {
        "ffmpeg": None,
        "ffprobe": None,
        "sources": {},
        "errors": [],
    }
    for name in _MEDIA_KEYS:
        source = "missing"
        found: Path | None = None
        try:
            if name in config and config[name] is not None:
                source = "config"
                found = _absolute_existing_file(config[name], f"explicit {name} path")
            else:
                environment_value = _path_from_environment(name)
                if environment_value is not None:
                    source = "environment"
                    try:
                        found = _absolute_existing_file(
                            environment_value, f"{name.upper()} environment path"
                        )
                    except VerificationError as error:
                        raise VerificationError(
                            f"{name.upper()} environment path is invalid: {error}"
                        ) from error
                else:
                    found = _path_from_path(name)
                    if found is not None:
                        source = "PATH"
                    elif name == "ffmpeg":
                        found = _path_from_imageio_ffmpeg()
                        if found is not None:
                            source = "imageio_ffmpeg"
        except VerificationError as error:
            result["errors"].append(str(error))

        if found is not None:
            result[name] = str(found.resolve(strict=False))
        elif source == "missing":
            result["errors"].append(f"{name} is not available")
        elif source != "missing":
            result["errors"].append(f"configured {name} is unavailable")
        result["sources"][name] = source
    return result


def _version_string(version_info: Any) -> str:
    return ".".join(str(part) for part in tuple(version_info)[:3])


def _doctor_config_checks(config: dict, missing: list[str], errors: list[str]) -> dict:
    checks: dict[str, Any] = {}
    for key in _CONFIG_FILE_KEYS:
        raw = config.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            missing.append(key)
            checks[key] = {
                "status": "failed",
                "path": None,
                "detail": "required path is missing",
            }
            continue
        try:
            path = _absolute_existing_file(raw, key)
        except VerificationError as error:
            errors.append(str(error))
            checks[key] = {
                "status": "failed",
                "path": os.fspath(raw),
                "detail": str(error),
            }
        else:
            checks[key] = {
                "status": "passed",
                "path": str(path),
                "detail": "file exists",
            }

    raw_voukoder = config.get("voukoder")
    if raw_voukoder is None or (
        isinstance(raw_voukoder, str) and not raw_voukoder.strip()
    ):
        checks["voukoder"] = {
            "status": "not_checked",
            "path": None,
            "detail": "candidate was not supplied",
        }
    else:
        try:
            if not isinstance(raw_voukoder, (str, os.PathLike)):
                raise VerificationError("voukoder must be a path")
            path = Path(
                os.path.expandvars(os.path.expanduser(os.fspath(raw_voukoder)))
            )
            if not path.is_absolute():
                path = Path.cwd() / path
            path = path.resolve(strict=False)
            if not path.exists():
                raise VerificationError(f"voukoder candidate does not exist: {path}")
        except VerificationError as error:
            errors.append(str(error))
            checks["voukoder"] = {
                "status": "failed",
                "path": os.fspath(raw_voukoder),
                "detail": str(error),
            }
        else:
            checks["voukoder"] = {
                "status": "passed",
                "path": str(path),
                "detail": "candidate exists",
            }
    return checks


def doctor(config: dict) -> dict:
    """Return a readiness report using static checks only."""

    if not isinstance(config, dict):
        raise TypeError("config must be a dict")

    missing: list[str] = []
    errors: list[str] = []
    checks: dict[str, Any] = {}

    windows_ok = platform.system().strip().lower() == "windows"
    checks["windows"] = {
        "status": "passed" if windows_ok else "failed",
        "actual": platform.system(),
        "required": "Windows",
    }
    if not windows_ok:
        errors.append("Windows is required for the native After Effects workflow")

    python_ok = sys.version_info >= (3, 11)
    checks["python"] = {
        "status": "passed" if python_ok else "failed",
        "actual": _version_string(sys.version_info),
        "required": ">=3.11",
    }
    if not python_ok:
        errors.append("Python 3.11 or newer is required")

    pillow_spec = importlib.util.find_spec("PIL")
    pillow_ok = pillow_spec is not None
    checks["pillow"] = {
        "status": "passed" if pillow_ok else "failed",
        "actual": (
            str(Path(pillow_spec.origin).resolve(strict=False))
            if pillow_spec is not None and pillow_spec.origin
            else None
        ),
    }
    if not pillow_ok:
        missing.append("Pillow")
        errors.append("Pillow is required to build the verification contact sheet")

    tools = discover_media_tools(config)
    checks["media_tools"] = {
        "status": (
            "passed"
            if tools["ffmpeg"] is not None and tools["ffprobe"] is not None
            else "failed"
        ),
        "ffmpeg": tools["ffmpeg"],
        "ffprobe": tools["ffprobe"],
        "sources": tools["sources"],
    }
    for name in _MEDIA_KEYS:
        if tools[name] is None:
            missing.append(name)
    errors.extend(tools["errors"])

    config_checks = _doctor_config_checks(config, missing, errors)
    required_config_ok = all(
        check["status"] != "failed"
        for key, check in config_checks.items()
        if key != "voukoder" or "voukoder" in config
    )
    checks["configuration"] = {
        "status": "passed" if required_config_ok else "failed",
        "fields": config_checks,
    }

    prerequisites_found = windows_ok and python_ok and pillow_ok and all(
        tools[name] is not None for name in _MEDIA_KEYS
    )
    setup_required = not prerequisites_found or not required_config_ok
    # Preserve insertion order while removing duplicate setup messages.
    missing = list(dict.fromkeys(missing))
    errors = list(dict.fromkeys(errors))
    return {
        "status": "setup_required" if setup_required else "ready",
        "prerequisites_found": prerequisites_found,
        "setup_required": setup_required,
        "missing": missing,
        "errors": errors,
        "checks": checks,
    }


def _parse_fraction(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        parsed = value
    elif isinstance(value, (int, float, str)):
        text = str(value).strip()
        if not text:
            raise ValueError("empty value")
        parsed = Fraction(text)
    else:
        raise ValueError("not a number")
    if parsed <= 0:
        raise ValueError("must be positive")
    return parsed


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE"}:
        return None
    number = int(text)
    return number if number >= 0 else None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE"}:
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _check(
    status: str,
    *,
    expected_value: Any = None,
    actual_value: Any = None,
    detail: str | None = None,
    **extra: Any,
) -> dict:
    if status not in _CHECKED_STATUSES:
        raise ValueError(f"Unknown check status: {status}")
    result = {
        "status": status,
        "expected": expected_value,
        "actual": actual_value,
    }
    if detail:
        result["detail"] = detail
    result.update(extra)
    return result


def validate_metadata(metadata: dict, expected: dict) -> dict:
    """Validate ffprobe JSON against the exact expected video properties."""

    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a dict")
    if not isinstance(expected, dict):
        raise TypeError("expected must be a dict")

    checks: dict[str, dict] = {}
    errors: list[str] = []
    streams = metadata.get("streams")
    if not isinstance(streams, list):
        streams = []
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]

    video_count_ok = len(video_streams) == 1
    checks["video_stream_count"] = _check(
        "passed" if video_count_ok else "failed",
        expected_value=1,
        actual_value=len(video_streams),
        detail="exactly one video stream is required",
    )
    if not video_count_ok:
        errors.append(
            f"Expected exactly one video stream, found {len(video_streams)}"
        )

    video = video_streams[0] if video_count_ok else None
    for field in ("width", "height"):
        expected_value = expected.get(field)
        actual_value = video.get(field) if video else None
        try:
            field_ok = int(expected_value) == int(actual_value)
        except (TypeError, ValueError):
            field_ok = False
        checks[field] = _check(
            "passed" if field_ok else "failed",
            expected_value=expected_value,
            actual_value=actual_value,
        )
        if not field_ok:
            errors.append(
                f"{field} mismatch: expected {expected_value!r}, got {actual_value!r}"
            )

    actual_fps: Fraction | None = None
    fps_source = None
    if video:
        for candidate in ("r_frame_rate", "avg_frame_rate"):
            try:
                actual_fps = _parse_fraction(video.get(candidate))
            except (ValueError, ZeroDivisionError):
                continue
            fps_source = candidate
            break
    expected_fps: Fraction | None = None
    if "fps" in expected:
        try:
            expected_fps = _parse_fraction(expected["fps"])
        except (ValueError, ZeroDivisionError):
            errors.append(f"expected fps is invalid: {expected.get('fps')!r}")
    fps_ok = (
        actual_fps is not None
        and expected_fps is not None
        and actual_fps == expected_fps
    )
    checks["fps"] = _check(
        "passed" if fps_ok else "failed",
        expected_value=str(expected_fps) if expected_fps is not None else expected.get("fps"),
        actual_value=str(actual_fps) if actual_fps is not None else None,
        source=fps_source,
    )
    if not fps_ok and expected_fps is not None:
        errors.append(
            f"fps mismatch: expected "
            f"{expected_fps}, got {actual_fps if actual_fps is not None else 'unavailable'}"
        )

    format_info = metadata.get("format")
    if not isinstance(format_info, dict):
        format_info = {}
    actual_duration = None
    duration_source = None
    if video:
        actual_duration = _parse_float(video.get("duration"))
        duration_source = "video"
    if actual_duration is None:
        actual_duration = _parse_float(format_info.get("duration"))
        duration_source = "format"
    try:
        expected_duration = _parse_float(expected.get("duration"))
    except (TypeError, ValueError):
        expected_duration = None
    if expected_duration is None:
        checks["duration"] = _check(
            "failed",
            expected_value=expected.get("duration"),
            actual_value=actual_duration,
        )
        errors.append(f"expected duration is invalid: {expected.get('duration')!r}")
    elif actual_duration is None:
        checks["duration"] = _check(
            "failed",
            expected_value=expected_duration,
            actual_value=None,
        )
        errors.append("media duration is unavailable")
    else:
        tolerance_fps = expected_fps or actual_fps
        if tolerance_fps is None:
            duration_ok = False
            tolerance = None
            errors.append("cannot apply one-frame duration tolerance without fps")
        else:
            tolerance = float(Fraction(1, 1) / tolerance_fps)
            difference = abs(actual_duration - expected_duration)
            duration_ok = difference <= tolerance + 1e-9
        checks["duration"] = _check(
            "passed" if duration_ok else "failed",
            expected_value=expected_duration,
            actual_value=actual_duration,
            tolerance=tolerance,
            source=duration_source,
        )
        if not duration_ok:
            errors.append(
                f"duration mismatch: expected {expected_duration:.6f}s, "
                f"got {actual_duration:.6f}s (tolerance {tolerance}fps frame)"
            )

    frame_count = None
    frame_count_source = None
    required_frame_count_unavailable = False
    if video:
        for candidate in ("nb_read_frames", "nb_frames"):
            try:
                frame_count = _parse_int(video.get(candidate))
            except (TypeError, ValueError):
                frame_count = None
            if frame_count is not None:
                frame_count_source = candidate
                break
    try:
        expected_frames = _parse_int(expected.get("frames"))
    except (TypeError, ValueError):
        expected_frames = None
    if expected_frames is None:
        checks["frames"] = _check(
            "failed",
            expected_value=expected.get("frames"),
            actual_value=frame_count,
            source=frame_count_source,
        )
        errors.append(f"expected frames is invalid: {expected.get('frames')!r}")
    elif frame_count is None:
        required_frame_count_unavailable = True
        checks["frames"] = _check(
            "not_checked",
            expected_value=expected_frames,
            actual_value=None,
            detail="frame count cannot be checked because ffprobe returned no exact count",
        )
        errors.append(
            "frame count cannot be checked because ffprobe returned no exact count"
        )
    else:
        frames_ok = frame_count == expected_frames
        checks["frames"] = _check(
            "passed" if frames_ok else "failed",
            expected_value=expected_frames,
            actual_value=frame_count,
            source=frame_count_source,
        )
        if not frames_ok:
            errors.append(
                f"frame count mismatch: expected {expected_frames}, got {frame_count}"
            )

    audio_expected = expected.get("audio_expected")
    audio_count = len(audio_streams)
    if audio_expected is None:
        checks["audio"] = _check(
            "not_checked",
            expected_value=None,
            actual_value=audio_count,
            detail="audio presence was not requested",
        )
    elif not isinstance(audio_expected, bool):
        checks["audio"] = _check(
            "failed",
            expected_value=audio_expected,
            actual_value=audio_count,
        )
        errors.append("audio_expected must be true, false, or null")
    else:
        audio_ok = (audio_count > 0) if audio_expected else (audio_count == 0)
        checks["audio"] = _check(
            "passed" if audio_ok else "failed",
            expected_value=audio_expected,
            actual_value=audio_count,
        )
        if not audio_ok:
            requirement = "at least one audio stream" if audio_expected else "no audio stream"
            errors.append(f"Expected {requirement}, found {audio_count}")

    failed = required_frame_count_unavailable or any(
        check["status"] == "failed" for check in checks.values()
    )
    return {
        "status": "failed" if failed else "verified",
        "checks": checks,
        "errors": errors,
    }


def _safe_stderr(completed: subprocess.CompletedProcess) -> str:
    stderr = completed.stderr or ""
    if not isinstance(stderr, str):
        stderr = stderr.decode(errors="replace")
    return stderr.strip()[-4000:]


def _probe_video(ffprobe: str, video_path: Path) -> dict:
    command = [
        ffprobe,
        "-v",
        "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_MEDIA_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VerificationError(
            f"ffprobe timed out after {_MEDIA_COMMAND_TIMEOUT_SECONDS} seconds"
        ) from error
    except OSError as error:
        raise VerificationError(f"ffprobe could not start: {error}") from error
    if completed.returncode != 0:
        detail = _safe_stderr(completed)
        raise VerificationError(
            f"ffprobe failed with exit code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    try:
        metadata = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise VerificationError("ffprobe returned invalid JSON") from error
    if not isinstance(metadata, dict):
        raise VerificationError("ffprobe JSON must be an object")
    return metadata


def _sample_times(duration: float, fps: float, count: int = 12) -> list[float]:
    if duration <= 0 or fps <= 0:
        raise VerificationError("duration and fps must be positive for frame sampling")
    if count < 2:
        return [0.0]
    last_frame_start = max(0.0, duration - (1.0 / fps))
    step = last_frame_start / (count - 1)
    return [round(index * step, 6) for index in range(count)]


def sample_outro_times(duration, fps, required_times):
    if not isinstance(required_times,list) or not required_times:
        raise VerificationError('Missing per-STAFF readable frame sampling times')
    if any(not isinstance(t,(int,float)) or isinstance(t,bool) or not math.isfinite(t)
           or t<0 or t>=duration for t in required_times):
        raise VerificationError('Invalid STAFF frame sampling time')
    return sorted(set(_sample_times(duration,fps)+required_times))


def _extract_frames(
    ffmpeg: str,
    video_path: Path,
    frame_dir: Path,
    sample_times: list[float],
) -> list[Path]:
    paths: list[Path] = []
    for index, timestamp in enumerate(sample_times, start=1):
        output = frame_dir / f"keyframe-{index:02d}.png"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-an",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_MEDIA_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise VerificationError(
                f"ffmpeg timed out after {_MEDIA_COMMAND_TIMEOUT_SECONDS} seconds"
            ) from error
        except OSError as error:
            raise VerificationError(f"ffmpeg could not start: {error}") from error
        if completed.returncode != 0:
            detail = _safe_stderr(completed)
            raise VerificationError(
                f"ffmpeg frame extraction failed at {timestamp:.3f}s "
                f"with exit code {completed.returncode}"
                + (f": {detail}" if detail else "")
            )
        if not output.is_file() or output.stat().st_size == 0:
            raise VerificationError(
                f"ffmpeg did not create keyframe output at {timestamp:.3f}s"
            )
        paths.append(output)
    return paths


def _build_contact_sheet(frame_paths: list[Path], output: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as error:
        raise VerificationError("Pillow is required to build the contact sheet") from error

    columns = 4
    cell_width = 360
    image_height = 202
    label_height = 24
    margin = 12
    rows = math.ceil(len(frame_paths) / columns)
    width = margin + columns * (cell_width + margin)
    height = margin + rows * (image_height + label_height + margin)
    sheet = Image.new("RGB", (width, height), (26, 30, 34))
    draw = ImageDraw.Draw(sheet)

    for index, path in enumerate(frame_paths):
        row, column = divmod(index, columns)
        x = margin + column * (cell_width + margin)
        y = margin + row * (image_height + label_height + margin)
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            thumbnail = ImageOps.contain(
                image,
                (cell_width, image_height),
                method=Image.Resampling.LANCZOS,
            )
        image_x = x + (cell_width - thumbnail.width) // 2
        image_y = y + (image_height - thumbnail.height) // 2
        sheet.paste(thumbnail, (image_x, image_y))
        draw.text(
            (x + 4, y + image_height + 4),
            f"Frame {index + 1:02d}",
            fill=(235, 238, 240),
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="JPEG", quality=90)


def _artifact_paths(run_dir: Path, frame_count: int) -> tuple[Path, list[Path]]:
    contact_sheet = run_dir / "frames" / "contact-sheet.jpg"
    frames = [
        run_dir / "frames" / f"keyframe-{index:02d}.png"
        for index in range(1, frame_count + 1)
    ]
    return contact_sheet, frames


def _write_result(run_dir: Path, result: dict) -> dict:
    verification_path = (run_dir / "verification.json").resolve(strict=False)
    result["verification_path"] = str(verification_path)
    temporary = run_dir / f".verification-{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, verification_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return result


def _failure(run_dir: Path | None, errors: list[str], checks: dict | None = None) -> dict:
    result = {
        "status": "failed",
        "checks": checks or {},
        "errors": errors,
        "frames": [],
        "contact_sheet": None,
        "visual_review": {
            "performed": False,
            "note": "Semantic visual review was not performed.",
        },
    }
    if run_dir is not None and run_dir.is_dir():
        return _write_result(run_dir, result)
    result["verification_path"] = None
    return result


def verify_video(run_dir: str | Path, config: dict) -> dict:
    """Probe, sample, and write independent verification artifacts for one run."""

    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    run_path = Path(run_dir).expanduser().resolve(strict=False)
    if not run_path.is_dir():
        return _failure(None, [f"run directory does not exist: {run_path}"])
    if (run_path / "frames").exists():
        return _failure(
            run_path,
            [
                f"Frame output already exists and will not be overwritten: "
                f"{run_path / 'frames'}"
            ],
        )

    try:
        run_data = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return _failure(run_path, [f"run.json cannot be read: {error}"])
    if not isinstance(run_data, dict):
        return _failure(run_path, ["run.json must contain a JSON object"])

    run_expected = run_data.get("expected")
    if not isinstance(run_expected, dict):
        return _failure(run_path, ["run.json expected must be an object"])
    raw_video_path = run_data.get("video_path")
    if not isinstance(raw_video_path, (str, os.PathLike)):
        return _failure(run_path, ["run.json video_path must be a file path"])
    video_path = Path(raw_video_path)
    if not video_path.is_absolute():
        video_path = run_path / video_path
    video_path = video_path.resolve(strict=False)
    if not video_path.is_file():
        return _failure(
            run_path,
            [f"video file does not exist: {video_path}"],
        )
    try:
        size = video_path.stat().st_size
        with video_path.open("rb") as handle:
            handle.read(1)
    except OSError as error:
        return _failure(run_path, [f"video file cannot be read: {error}"])
    if size <= 0:
        return _failure(run_path, [f"video file is empty: {video_path}"])

    tools = discover_media_tools(config)
    if tools["ffmpeg"] is None or tools["ffprobe"] is None:
        errors = tools["errors"] or ["ffmpeg and ffprobe are required"]
        return _failure(run_path, errors)

    try:
        probe = _probe_video(tools["ffprobe"], video_path)
        validation = validate_metadata(probe, run_expected)
        checks = {"metadata": validation}
        checks["video_file"] = _check(
            "passed",
            actual_value={"path": str(video_path), "size": size},
        )
        if validation["status"] != "verified":
            return _failure(run_path, validation["errors"], checks)

        try:
            fps = float(_parse_fraction(run_expected["fps"]))
            duration = float(run_expected["duration"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            return _failure(
                run_path,
                [f"cannot determine frame sampling times: {error}"],
                checks,
            )
        try:
            sample_times = sample_outro_times(duration,fps,run_data.get('sample_times'))
        except VerificationError as error:
            return _failure(run_path, [str(error)], checks)

        staging = run_path / f".frames-{uuid.uuid4().hex}.tmp"
        final_frames_dir = run_path / "frames"
        try:
            staging.mkdir()
            extracted = _extract_frames(
                tools["ffmpeg"],
                video_path,
                staging,
                sample_times,
            )
            _build_contact_sheet(extracted, staging / "contact-sheet.jpg")
            os.replace(staging, final_frames_dir)
        except (OSError, VerificationError) as error:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            return _failure(run_path, [str(error)], checks)
    except VerificationError as error:
        return _failure(run_path, [str(error)], locals().get("checks", {}))

    contact_sheet, frames = _artifact_paths(run_path, len(sample_times))
    checks["metadata"] = validation
    checks["extraction"] = _check(
        "passed",
        actual_value=len(frames),
        sample_times=sample_times,
    )
    checks["contact_sheet"] = _check(
        "passed",
        actual_value=str(contact_sheet),
    )
    result = {
        "status": "verified",
        "checks": checks,
        "errors": [],
        "video_path": str(video_path),
        "frames": [str(path.resolve(strict=False)) for path in frames],
        "contact_sheet": str(contact_sheet.resolve(strict=False)),
        "visual_review": {
            "performed": False,
            "note": (
                "Semantic visual review was not performed. An agent must inspect "
                "the contact sheet before claiming visual acceptance."
            ),
        },
    }
    return _write_result(run_path, result)
