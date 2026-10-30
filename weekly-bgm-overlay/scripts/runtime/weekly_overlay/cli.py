"""Prepare, render directly in Photoshop, verify, or inspect weekly overlays."""
import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from .model import display_period, parse_bgm, validate_task
from template_assets import template_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = Path(
    template_path("WEEKLY_OVERLAY_TEMPLATE", "E:/gal批评/gal视频后期/周报角标.psd", "weekly-bgm-overlay-master.psd")
)
DEFAULT_PHOTOSHOP = os.environ.get(
    "WEEKLY_OVERLAY_PHOTOSHOP",
    "D:/114514/Adobe/Adobe Photoshop 2023/Photoshop.exe",
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(args):
    if args.task:
        task = read_json(args.task)
        if args.start is not None or args.end is not None:
            if args.start is None or args.end is None:
                raise ValueError("--start and --end must be supplied together")
            task = dict(task, date_start=args.start, date_end=args.end)
        task = validate_task(task)
    else:
        task = parse_bgm(Path(args.input).read_text(encoding="utf-8-sig"),
                         today=date.fromisoformat(args.today) if args.today else None,
                         date_start=args.start, date_end=args.end)
    template = Path(args.template).resolve(strict=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = Path(args.output).resolve() / run_id
    folder.mkdir(parents=True, exist_ok=False)
    staged = folder / "template.psd"
    original_hash = sha256(template)
    shutil.copyfile(template, staged)
    if sha256(staged) != original_hash:
        raise ValueError("Template changed while copying; prepare again")
    filenames = [f"{i:02d}_{item['category']}.png" for i, item in enumerate(task["items"], 1)]
    job = {"schema_version": 1, "run_id": run_id, "source_template": str(template),
           "source_sha256": original_hash, "template": str(staged),
           "output_dir": str(folder), "filenames": filenames,
           "period": display_period(task), "task": task}
    save_json(folder / "job.json", job)
    # JSON is a JS literal; escape non-ASCII and line separators, never interpolate raw text.
    driver = '#target photoshop\n' + (ROOT / "photoshop" / "render.jsx").read_text(encoding="utf-8")
    driver += "\nweeklyOverlayRender(" + json.dumps(job, ensure_ascii=True) + ");\n"
    (folder / "run.jsx").write_text(driver, encoding="utf-8")
    return {"run_dir": str(folder), "driver": str(folder / "run.jsx"), "period": job["period"],
            "items": task["items"], "next": "Run render <run_dir> to invoke Photoshop directly and verify"}


def load_job(folder):
    """Validate the prepared task and driver before executing or verifying it."""
    folder = Path(folder).resolve(strict=True)
    job = read_json(folder / "job.json")
    task = validate_task(job["task"])
    if (task != job["task"] or job.get("schema_version") != 1
            or job.get("run_id") != folder.name
            or Path(job["output_dir"]).resolve() != folder
            or Path(job["template"]).resolve() != folder / "template.psd"
            or job["period"] != display_period(task)):
        raise ValueError("Invalid job manifest")
    expected_names = [f"{i:02d}_{item['category']}.png" for i, item in enumerate(task["items"], 1)]
    if job.get("filenames") != expected_names:
        raise ValueError("Filename manifest count or mapping mismatch")
    if Path(job["source_template"]).samefile(job["template"]):
        raise ValueError("Invalid source identity: source and staged PSD are aliases")
    driver = (folder / "run.jsx").read_text(encoding="utf-8")
    invocation = "\nweeklyOverlayRender("
    if invocation not in driver or not driver.endswith(");\n"):
        raise ValueError("Invalid prepared driver")
    embedded = json.loads(driver.rsplit(invocation, 1)[1][:-3])
    if embedded != job:
        raise ValueError("Job manifest differs from the executed driver")
    if sha256(job["source_template"]) != job["source_sha256"] or sha256(job["template"]) != job["source_sha256"]:
        raise ValueError("Source or staged PSD changed")
    return job


def verify_run(folder):
    from PIL import Image
    import numpy as np
    folder = Path(folder).resolve(strict=True)
    if (folder / "verification.json").exists():
        save_json(folder / "verification.json", {"status": "rechecking", "visual_review": "pending"})
    job = load_job(folder)
    result = read_json(folder / "result.json")
    if (result.get("schema_version") != 1 or result.get("run_id") != job["run_id"]
            or result.get("status") != "rendered"):
        raise ValueError("Photoshop did not complete this run: " + str(result.get("error", result.get("status"))))
    outputs = result.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != len(job["task"]["items"]):
        raise ValueError("Output count mismatch")
    images, records = [], []
    expected_fonts = {"category": "MiSans-Semibold", "date": "NotoSerifCJKsc-SemiBold",
                      "title": "MiSans-Semibold", "artist": "MiSans-Regular"}
    for name, item, record in zip(job["filenames"], job["task"]["items"], outputs):
        if record.get("filename") != name or any(record.get(k) != item[k] for k in ("category", "title", "artist")):
            raise ValueError("Output mapping mismatch")
        expected_text = dict(item, date=job["period"])
        for field, font in expected_fonts.items():
            info = record.get("fields", {}).get(field, {})
            if info.get("font") != font or info.get("text") != expected_text[field]:
                raise ValueError("Text or original font mismatch: " + field)
        path = folder / name
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != (2160, 1080) or image.mode != "RGBA":
                raise ValueError("Expected full 2160x1080 RGBA PNG")
            image.load()
            rgba = np.array(image)
            alpha = rgba[:, :, 3]
            count = int(np.count_nonzero(alpha))
            if not 100 < count < alpha.size * 0.08 or int(alpha[500:, :].max()) != 0:
                raise ValueError("Unexpected alpha coverage/background")
            for field in expected_fonts:
                x0, y0, x1, y1 = record["fields"][field]["bounds"]
                if not (0 <= x0 < x1 <= 2160 and 0 <= y0 < y1 <= 1080):
                    raise ValueError("Invalid text bounds")
                if not np.any(alpha[max(0, int(y0)):min(1080, int(y1) + 1),
                                    max(0, int(x0)):min(2160, int(x1) + 1)]):
                    raise ValueError("Empty text region: " + field)
            images.append(image.copy())
            records.append({"filename": name, "sha256": sha256(path), "nonzero_alpha_pixels": count,
                            "alpha_max": int(alpha.max())})
    contact_sheet(folder / "preview.jpg", images, job["task"]["items"])
    report = {"schema_version": 1, "run_id": job["run_id"], "status": "verified",
              "period": job["period"], "source_unchanged": True, "outputs": records,
              "preview": str(folder / "preview.jpg"), "visual_review": "pending"}
    save_json(folder / "verification.json", report)
    return report


def contact_sheet(path, images, items):
    from PIL import Image, ImageDraw, ImageFont
    width, row = 1460, 400
    sheet = Image.new("RGB", (width, 70 + row * len(images)), "#f3f1eb")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)
    draw.text((24, 15), "周报 BGM 角标 · 全画布 / 文字放大（预览底纹不导出）", font=font, fill="#222222")
    for index, (image, item) in enumerate(zip(images, items)):
        top = 70 + index * row
        draw.text((24, top), f"{index + 1:02d}  {item['category']}", font=font, fill="#222222")
        bg = Image.new("RGBA", image.size, "#dededb")
        bd = ImageDraw.Draw(bg)
        for y in range(0, 1080, 24):
            for x in range(0, 2160, 24):
                if (x // 24 + y // 24) % 2:
                    bd.rectangle((x, y, x + 23, y + 23), fill="#f2f2ef")
        composed = Image.alpha_composite(bg, image).convert("RGB")
        sheet.paste(composed.resize((540, 270), Image.Resampling.LANCZOS), (24, top + 48))
        # Keep the complete text width visible; expand short labels more.
        box = image.getbbox()
        right = min(2160, max(580, box[2] + 30))
        crop = composed.crop((20, 140, right, 480))
        crop.thumbnail((840, 300), Image.Resampling.LANCZOS)
        sheet.paste(crop, (590, top + 48))
    sheet.save(path, quality=95)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    source = prep.add_mutually_exclusive_group(required=True)
    source.add_argument("--input")
    source.add_argument("--task")
    prep.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    prep.add_argument("--output", default=str(ROOT / "runs"))
    prep.add_argument("--today", help="ISO date for reproducible period selection")
    prep.add_argument("--start")
    prep.add_argument("--end")
    for cmd in ("verify", "status", "review", "render"):
        sub.add_parser(cmd).add_argument("run_dir")
    sub.choices["render"].add_argument("--photoshop", default=DEFAULT_PHOTOSHOP)
    sub.choices["render"].add_argument("--timeout", type=float, default=300)
    sub.choices["review"].add_argument("--note", required=True,
                                       help="Record completed visual inspection; does not perform it")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            response = prepare(args)
        elif args.command == "verify":
            response = verify_run(args.run_dir)
        elif args.command == "render":
            from .direct import render_run
            render_run(args.run_dir, args.photoshop, timeout=args.timeout)
            response = verify_run(args.run_dir)
        elif args.command == "review":
            if not args.note.strip():
                raise ValueError("Visual inspection note is required")
            response = verify_run(args.run_dir)
            response["visual_review"] = {"status": "passed", "note": args.note.strip(),
                                         "recorded_at": datetime.now().astimezone().isoformat()}
            save_json(Path(args.run_dir) / "verification.json", response)
        else:
            folder = Path(args.run_dir)
            response = read_json(folder / "result.json") if (folder / "result.json").exists() else {
                "status": "running_or_blocked" if (folder / "started.json").exists() else "not_started"}
            if (folder / "dispatch.json").exists():
                response["dispatch"] = read_json(folder / "dispatch.json")
        print(json.dumps(response, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
