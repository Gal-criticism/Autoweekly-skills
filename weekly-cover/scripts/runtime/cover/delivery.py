"""Produce named, size-limited PNG deliverables from Photoshop renders."""
import hashlib
from io import BytesIO
from pathlib import Path
import re

from PIL import Image


MAX_PNG_BYTES = 5_000_000
LABELS = {1: "视频封面 16-10", 2: "文章封面", 3: "视频封面 4-3"}


def write_png(source, target, limit=MAX_PNG_BYTES):
    """Optimize losslessly first, then downsample proportionally if necessary."""
    source_path = Path(source).resolve(strict=True)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    with Image.open(source_path) as original:
        image = original.convert("RGBA" if "A" in original.getbands() else "RGB")
    while True:
        stream = BytesIO()
        image.save(stream, "PNG", optimize=True, compress_level=9)
        data = stream.getvalue()
        if len(data) <= limit:
            break
        width, height = image.size
        if min(width, height) <= 1:
            raise ValueError("PNG cannot fit the size limit")
        ratio = min(0.9, (limit / len(data)) ** 0.5 * 0.95)
        image = image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))),
                             Image.Resampling.LANCZOS)
    target = Path(target)
    if target.is_symlink():
        raise ValueError("PNG output cannot be a symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "path": str(target),
        "bytes": len(data),
        "size": list(image.size),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source": str(source_path),
        "source_sha256": source_sha256,
    }


def export_pngs(folder, job, *, source_overrides=None):
    folder = Path(folder).resolve()
    output = folder / "exports"
    if output.is_symlink():
        raise ValueError("Export directory cannot be a symlink")
    overrides = source_overrides or {}
    multiple = len(job.get("cgs", [])) > 1
    records = []
    for cover in job["covers"]:
        name = cover["filename"]
        match = re.fullmatch(r"T([123])_CG([1-9][0-9]*)", name)
        if not match:
            raise ValueError("Invalid internal cover name")
        template, cg = map(int, match.groups())
        issue = cover.get("issue")
        if not isinstance(issue, int) or not 1 <= issue <= 99999:
            raise ValueError("An explicit issue number is required for named PNG exports")
        target = output / (f"CG{cg}" if multiple else "") / f"{issue} 期周报{LABELS[template]}.png"
        if target.parent.is_symlink():
            raise ValueError("Export directory cannot be a symlink")
        if name in overrides:
            source_path = Path(overrides[name])
            source_kind = "refined"
        else:
            source_path = folder / f"{name}.png"
            source_kind = "original"
        rec = write_png(source_path, target)
        rec["filename"] = name
        rec["source_kind"] = source_kind
        records.append(rec)
    return records
