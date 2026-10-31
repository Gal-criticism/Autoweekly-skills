"""Enumerate images and prepare review sheets and slot replacements."""

from __future__ import annotations

import hashlib
from io import BytesIO
import os
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})

_CONTACT_COLUMNS = 5
_CONTACT_ROWS = 6
_CONTACT_PAGE_SIZE = _CONTACT_COLUMNS * _CONTACT_ROWS
_CONTACT_MARGIN = 16
_CONTACT_CELL_WIDTH = 220
_CONTACT_CELL_HEIGHT = 180
_CONTACT_THUMB_WIDTH = 196
_CONTACT_THUMB_HEIGHT = 130
_CONTACT_LABEL_HEIGHT = 28
_CONTACT_BACKGROUND = (28, 31, 36)
_CONTACT_CELL_BACKGROUND = (43, 47, 54)
_CONTACT_TEXT = (238, 240, 244)


def _resolved_allowed_roots(roots: list[str]) -> list[Path]:
    if not isinstance(roots, (list, tuple)) or not roots:
        raise ValueError("allowed roots must contain at least one directory")

    resolved: list[Path] = []
    seen: set[str] = set()
    for raw_root in roots:
        if not isinstance(raw_root, (str, os.PathLike)) or not str(raw_root).strip():
            raise ValueError("allowed root is empty or invalid")
        path = Path(raw_root).expanduser()
        try:
            root = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"allowed root does not exist or is unreadable: {path}") from exc
        if not root.is_dir():
            raise ValueError(f"allowed root is not a directory: {path}")
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            resolved.append(root)
    return resolved


def _is_under_any(path: Path, roots: Iterable[Path]) -> bool:
    normalized_path = os.path.normcase(str(path))
    for root in roots:
        normalized_root = os.path.normcase(str(root))
        try:
            common = os.path.commonpath((normalized_path, normalized_root))
        except ValueError:
            continue
        if os.path.normcase(common) == normalized_root:
            return True
    return False


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    try:
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _oriented_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        oriented = ImageOps.exif_transpose(image)
        oriented.load()
        return oriented.size


def scan_roots(roots: list[str]) -> list[dict]:
    """Return deterministic, unique decoded images found under allowed roots."""

    allowed_roots = _resolved_allowed_roots(roots)
    files: dict[str, Path] = {}

    for root in allowed_roots:
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            current = Path(directory)
            directory_names[:] = sorted(
                (
                    name
                    for name in directory_names
                    if not _is_linklike(current / name)
                ),
                key=lambda value: (os.path.normcase(value), value),
            )
            for name in sorted(file_names, key=lambda value: (os.path.normcase(value), value)):
                lexical_path = current / name
                if lexical_path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                try:
                    resolved = lexical_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if not _is_under_any(resolved, allowed_roots) or not resolved.is_file():
                    continue
                files.setdefault(os.path.normcase(str(resolved)), resolved)

    records: list[dict] = []
    for path in sorted(files.values(), key=lambda value: (os.path.normcase(str(value)), str(value))):
        try:
            before = path.stat()
            sha256 = _sha256_file(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                continue
            width, height = _oriented_size(path)
        except (
            OSError,
            RuntimeError,
            ValueError,
            SyntaxError,
            EOFError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            continue
        records.append(
            {
                "path": str(path.absolute()),
                "sha256": sha256,
                "width": width,
                "height": height,
                "mtime_ns": after.st_mtime_ns,
            }
        )

    unique_content: dict[str, dict] = {}
    for record in records:
        unique_content.setdefault(record["sha256"], record)

    return [
        {"id": f"C{index:04d}", **record}
        for index, record in enumerate(unique_content.values(), start=1)
    ]


def _prepare_output_dir(output_dir: str | Path) -> Path:
    try:
        target = Path(output_dir).expanduser().resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"output directory is invalid: {output_dir}") from exc
    if not target.is_dir():
        raise ValueError(f"output path is not a directory: {output_dir}")
    return target


def _contact_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=16)
    except TypeError:
        return ImageFont.load_default()


def _load_oriented_rgb(path: Path) -> Image.Image:
    try:
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image)
            oriented.load()
            return oriented.convert("RGB")
    except (
        OSError,
        RuntimeError,
        ValueError,
        SyntaxError,
        EOFError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError(f"candidate image is unreadable: {path}") from exc


def build_contact_sheets(
    candidates: list[dict],
    output_dir: str | Path,
) -> list[str]:
    """Write review contact sheets, with at most 30 candidates per sheet."""

    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    if not candidates:
        return []

    validated: list[dict] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        candidate_id = candidate.get("id")
        path_value = candidate.get("path")
        width = candidate.get("width")
        height = candidate.get("height")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("candidate id is invalid")
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)
        if not isinstance(path_value, (str, os.PathLike)) or not str(path_value):
            raise ValueError(f"candidate path is invalid: {candidate_id}")
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            raise ValueError(f"candidate width is invalid: {candidate_id}")
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            raise ValueError(f"candidate height is invalid: {candidate_id}")
        try:
            path = Path(path_value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"candidate path does not exist: {path_value}") from exc
        validated.append(
            {
                "id": candidate_id,
                "path": path,
                "width": width,
                "height": height,
            }
        )

    target = _prepare_output_dir(output_dir)
    font = _contact_font()
    outputs: list[str] = []

    for page_number, start in enumerate(range(0, len(validated), _CONTACT_PAGE_SIZE), start=1):
        page = validated[start : start + _CONTACT_PAGE_SIZE]
        rows = (len(page) + _CONTACT_COLUMNS - 1) // _CONTACT_COLUMNS
        width = (_CONTACT_MARGIN * 2) + (_CONTACT_COLUMNS * _CONTACT_CELL_WIDTH)
        height = (_CONTACT_MARGIN * 2) + (rows * _CONTACT_CELL_HEIGHT)
        sheet = Image.new("RGB", (width, height), _CONTACT_BACKGROUND)
        draw = ImageDraw.Draw(sheet)

        for index, candidate in enumerate(page):
            column = index % _CONTACT_COLUMNS
            row = index // _CONTACT_COLUMNS
            cell_left = _CONTACT_MARGIN + (column * _CONTACT_CELL_WIDTH)
            cell_top = _CONTACT_MARGIN + (row * _CONTACT_CELL_HEIGHT)
            cell_right = cell_left + _CONTACT_CELL_WIDTH
            cell_bottom = cell_top + _CONTACT_CELL_HEIGHT
            draw.rounded_rectangle(
                (cell_left + 4, cell_top + 4, cell_right - 4, cell_bottom - 4),
                radius=8,
                fill=_CONTACT_CELL_BACKGROUND,
            )

            source = _load_oriented_rgb(candidate["path"])
            try:
                source.thumbnail(
                    (_CONTACT_THUMB_WIDTH, _CONTACT_THUMB_HEIGHT),
                    resample=_resampling(),
                )
                thumb_left = cell_left + ((_CONTACT_CELL_WIDTH - source.width) // 2)
                thumb_top = cell_top + 10 + ((_CONTACT_THUMB_HEIGHT - source.height) // 2)
                sheet.paste(source, (thumb_left, thumb_top))
            finally:
                source.close()

            label = f"{candidate['id']}  {candidate['width']}x{candidate['height']}"
            label_top = cell_bottom - _CONTACT_LABEL_HEIGHT - 7
            text_box = draw.textbbox((0, 0), label, font=font)
            text_width = text_box[2] - text_box[0]
            draw.text(
                (cell_left + ((_CONTACT_CELL_WIDTH - text_width) // 2), label_top),
                label,
                fill=_CONTACT_TEXT,
                font=font,
            )

        path = target / f"contact_sheet_{page_number:03d}.png"
        try:
            sheet.save(path, format="PNG")
        finally:
            sheet.close()
        outputs.append(str(path))

    return outputs


def _resampling():
    return getattr(Image, "Resampling", Image).LANCZOS


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _candidate_shape(candidate: object) -> dict:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    candidate_id = candidate.get("id")
    path_value = candidate.get("path")
    sha256 = candidate.get("sha256")
    width = candidate.get("width")
    height = candidate.get("height")
    mtime_ns = candidate.get("mtime_ns")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate id is invalid")
    if not isinstance(path_value, (str, os.PathLike)) or not str(path_value):
        raise ValueError(f"candidate path is invalid: {candidate_id}")
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in sha256)
    ):
        raise ValueError(f"candidate hash is invalid: {candidate_id}")
    if not _is_positive_int(width) or not _is_positive_int(height):
        raise ValueError(f"candidate dimensions are invalid: {candidate_id}")
    if not isinstance(mtime_ns, int) or isinstance(mtime_ns, bool):
        raise ValueError(f"candidate mtime is invalid: {candidate_id}")
    return {
        "id": candidate_id,
        "path": Path(path_value).expanduser(),
        "sha256": sha256.lower(),
        "width": width,
        "height": height,
        "mtime_ns": mtime_ns,
    }


def _validate_candidate_inventory(
    raw_candidates: list[dict],
    allowed_roots: list[Path],
) -> dict[str, dict]:
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a list")
    inventory: dict[str, dict] = {}
    seen_hashes: set[str] = set()
    for raw_candidate in raw_candidates:
        candidate = _candidate_shape(raw_candidate)
        candidate_id = candidate["id"]
        if candidate_id in inventory:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        if candidate["sha256"] in seen_hashes:
            raise ValueError(f"duplicate candidate content: {candidate['sha256']}")
        try:
            resolved = candidate["path"].resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"candidate path does not exist: {candidate['path']}") from exc
        if not _is_under_any(resolved, allowed_roots):
            raise ValueError(f"candidate path scope is outside allowed roots: {resolved}")
        candidate["path"] = resolved
        inventory[candidate_id] = candidate
        seen_hashes.add(candidate["sha256"])
    return inventory


def _validate_slots(raw_slots: list[dict]) -> list[dict]:
    if not isinstance(raw_slots, list):
        raise ValueError("slots must be a list")
    slots: list[dict] = []
    seen_ids: set[str] = set()
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, dict):
            raise ValueError("slot must be an object")
        slot_id = raw_slot.get("id")
        width = raw_slot.get("width")
        height = raw_slot.get("height")
        if not isinstance(slot_id, str) or not slot_id:
            raise ValueError("slot id is invalid")
        if slot_id in seen_ids:
            raise ValueError(f"duplicate slot id: {slot_id}")
        if not _is_positive_int(width) or not _is_positive_int(height):
            raise ValueError(f"slot dimensions are invalid: {slot_id}")
        seen_ids.add(slot_id)
        slots.append({"id": slot_id, "width": width, "height": height})
    return slots


def _validate_selection_order(
    raw_selections: list[dict],
    slots: list[dict],
) -> list[dict]:
    if not isinstance(raw_selections, list):
        raise ValueError("selections must be a list")

    slots_by_id = {slot["id"]: slot for slot in slots}
    selected_slots: set[str] = set()
    selected_candidates: set[str] = set()
    selections: list[dict] = []

    for raw_selection in raw_selections:
        if not isinstance(raw_selection, dict):
            raise ValueError("selection must be an object")
        slot_id = raw_selection.get("slot_id")
        candidate_id = raw_selection.get("candidate_id")
        if not isinstance(slot_id, str) or not isinstance(candidate_id, str):
            raise ValueError("selection slot_id and candidate_id must be strings")
        if slot_id in selected_slots:
            raise ValueError(f"duplicate selection for slot: {slot_id}")
        if slot_id not in slots_by_id:
            raise ValueError(f"selection has extra slot: {slot_id}")
        if candidate_id in selected_candidates:
            raise ValueError(f"candidate is repeated across selections: {candidate_id}")
        selected_slots.add(slot_id)
        selected_candidates.add(candidate_id)
        selections.append(
            {
                "slot_id": slot_id,
                "candidate_id": candidate_id,
                "crop": raw_selection.get("crop"),
            }
        )

    missing_slots = [slot["id"] for slot in slots if slot["id"] not in selected_slots]
    if missing_slots:
        raise ValueError(f"missing selection for slot: {missing_slots[0]}")

    slot_order = {slot["id"]: index for index, slot in enumerate(slots)}
    selections.sort(key=lambda selection: slot_order[selection["slot_id"]])
    return selections


def _validate_crop(
    crop: object,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    slot_id: str,
) -> list[int]:
    if isinstance(crop, (str, bytes)) or not isinstance(crop, (list, tuple)) or len(crop) != 4:
        raise ValueError(f"crop must contain four integers for slot: {slot_id}")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in crop):
        raise ValueError(f"crop must contain four integers for slot: {slot_id}")
    left, top, right, bottom = crop
    if (
        left < 0
        or top < 0
        or right > source_width
        or bottom > source_height
        or right <= left
        or bottom <= top
    ):
        raise ValueError(f"crop is outside source or has no area for slot: {slot_id}")
    crop_width = right - left
    crop_height = bottom - top
    crop_ratio = crop_width / crop_height
    target_ratio = target_width / target_height
    if abs((crop_ratio / target_ratio) - 1.0) > 0.0100000001:
        raise ValueError(f"crop aspect ratio does not match slot: {slot_id}")
    return [left, top, right, bottom]


def _centered_cover_crop(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> list[int]:
    source_ratio = source_width / source_height
    target_ratio = target_width / target_height
    if source_ratio > target_ratio:
        crop_width = min(source_width, max(1, round(source_height * target_ratio)))
        left = (source_width - crop_width) // 2
        return [left, 0, left + crop_width, source_height]
    crop_height = min(source_height, max(1, round(source_width / target_ratio)))
    top = (source_height - crop_height) // 2
    return [0, top, source_width, top + crop_height]


def _revalidate_source(candidate: dict, allowed_roots: list[Path]) -> bytes:
    try:
        resolved = candidate["path"].resolve(strict=True)
        if not _is_under_any(resolved, allowed_roots):
            raise ValueError(f"candidate path scope is outside allowed roots: {resolved}")
        with resolved.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if before.st_mtime_ns != candidate["mtime_ns"]:
                raise ValueError(f"candidate mtime changed: {resolved}")
            data = stream.read()
            after = os.fstat(stream.fileno())
        actual_hash = hashlib.sha256(data).hexdigest()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"candidate source is no longer readable: {candidate['path']}") from exc
    if after.st_mtime_ns != candidate["mtime_ns"]:
        raise ValueError(f"candidate mtime changed: {resolved}")
    if actual_hash != candidate["sha256"]:
        raise ValueError(f"candidate hash changed: {resolved}")
    candidate["path"] = resolved
    return data


def _load_normalized_source(data: bytes, expected_size: tuple[int, int]) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as image:
            oriented = ImageOps.exif_transpose(image)
            oriented.load()
            result = oriented.copy() if oriented is image else oriented
    except (
        OSError,
        RuntimeError,
        ValueError,
        SyntaxError,
        EOFError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError("candidate image bytes are unreadable") from exc
    if result.size != expected_size:
        result.close()
        raise ValueError("candidate dimensions changed")
    return result


def _next_output_path(output_dir: Path, start_index: int) -> tuple[Path, int]:
    index = start_index
    while True:
        path = output_dir / f"{index:03d}.png"
        if not path.exists():
            return path, index + 1
        index += 1


def _write_png_exclusive(image: Image.Image, output_dir: Path, start_index: int) -> tuple[Path, int]:
    index = start_index
    while True:
        path, next_index = _next_output_path(output_dir, index)
        try:
            with path.open("xb") as stream:
                image.save(stream, format="PNG")
            return path, next_index
        except FileExistsError:
            index = next_index
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise


def build_replacements(
    selections: list[dict],
    candidates: list[dict],
    slots: list[dict],
    output_dir: str | Path,
    allowed_roots: list[str],
) -> list[dict]:
    """Validate a selection set and write exact-size normalized PNG replacements."""

    roots = _resolved_allowed_roots(allowed_roots)
    validated_slots = _validate_slots(slots)
    inventory = _validate_candidate_inventory(candidates, roots)
    ordered_selections = _validate_selection_order(selections, validated_slots)
    slots_by_id = {slot["id"]: slot for slot in validated_slots}

    prepared: list[tuple[dict, Image.Image, list[int]]] = []
    selected_hashes: set[str] = set()
    try:
        for selection in ordered_selections:
            candidate_id = selection["candidate_id"]
            if candidate_id not in inventory:
                raise ValueError(f"selection references missing candidate: {candidate_id}")
            candidate = inventory[candidate_id]
            if candidate["sha256"] in selected_hashes:
                raise ValueError(f"candidate content is repeated across selections: {candidate_id}")
            selected_hashes.add(candidate["sha256"])
            source = _load_normalized_source(
                _revalidate_source(candidate, roots),
                (candidate["width"], candidate["height"]),
            )
            slot = slots_by_id[selection["slot_id"]]
            if selection["crop"] is None:
                crop = _centered_cover_crop(
                    candidate["width"],
                    candidate["height"],
                    slot["width"],
                    slot["height"],
                )
            else:
                crop = _validate_crop(
                    selection["crop"],
                    candidate["width"],
                    candidate["height"],
                    slot["width"],
                    slot["height"],
                    slot["id"],
                )
            prepared.append(({"selection": selection, "candidate": candidate, "slot": slot}, source, crop))
    except Exception:
        for _, source, _ in prepared:
            source.close()
        raise

    target = _prepare_output_dir(output_dir)
    next_index = 1
    results: list[dict] = []
    try:
        for item, source, crop in prepared:
            slot = item["slot"]
            candidate = item["candidate"]
            cropped = source.crop(tuple(crop))
            try:
                resized = cropped.resize(
                    (slot["width"], slot["height"]),
                    resample=_resampling(),
                )
            finally:
                cropped.close()
            try:
                replacement = resized.convert("RGB")
            finally:
                resized.close()
            try:
                output_path, next_index = _write_png_exclusive(
                    replacement,
                    target,
                    next_index,
                )
            finally:
                replacement.close()
            results.append(
                {
                    "slot_id": slot["id"],
                    "path": str(output_path),
                    "source_path": str(candidate["path"]),
                    "source_sha256": candidate["sha256"],
                    "crop": crop,
                    "width": slot["width"],
                    "height": slot["height"],
                }
            )
    finally:
        for _, source, _ in prepared:
            source.close()

    return results
