"""Prepare, render, refine, verify, or inspect weekly report covers."""

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from .safety import validate_batch, write_driver
from .delivery import export_pngs

from .model import (
    DEFAULT_TEMPLATES,
    T2_REFINE,
    default_period,
    expected_size,
    format_period,
    parse_period,
    resolve_templates,
    tint_for,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(
    os.environ.get("WEEKLY_COVER_OUTPUT", "E:/project/周报图片设计/cover_runs" if sys.platform == "win32" else "./cover_runs")
)
DEFAULT_PHOTOSHOP = os.environ.get(
    "WEEKLY_COVER_PHOTOSHOP",
    "D:/114514/Adobe/Adobe Photoshop 2023/Photoshop.exe",
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage_file(source, folder, name):
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"not a file: {source}")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    shutil.copyfile(source, target)
    return source, target, sha256(source)


def prepare(args):
    if not args.cg:
        raise ValueError("at least one --cg image is required")
    templates = resolve_templates(args.template)
    start, end = parse_period(args)
    issue = getattr(args, "issue", None)
    if not isinstance(issue, int) or not 1 <= issue <= 99999:
        raise ValueError("--issue must be between 1 and 99999")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = Path(args.output).resolve() / run_id
    folder.mkdir(parents=True, exist_ok=False)

    staged_templates = {}
    for index, spec in templates.items():
        source, target, digest = stage_file(spec["source"], folder / "templates", f"template_{index:02d}.psd")
        staged_templates[index] = {**spec, "source": str(source), "path": str(target), "sha256": digest}

    cgs = []
    for cg_index, cg_path in enumerate(args.cg, start=1):
        suffix = Path(cg_path).suffix.lower() or ".png"
        source, target, digest = stage_file(cg_path, folder / "cg", f"cg_{cg_index:02d}{suffix}")
        cgs.append({"index": cg_index, "source": str(source), "path": str(target), "sha256": digest})

    period = format_period(start, end)
    covers = []
    for cg in cgs:
        tint = tint_for(cg["path"])
        for template_index in sorted(templates):
            spec = templates[template_index]
            covers.append({
                "index": len(covers) + 1,
                "filename": f"T{template_index}_CG{cg['index']}",
                "template": str(folder / "templates" / f"template_{template_index:02d}.psd"),
                "tint": tint,
                "hide": spec["hide"],
                "date": period if spec["has_date"] else None,
                "issue": issue,
                "replacements": [
                    {"id": layer_id, "image": cg["path"], "cg": cg["index"], "box": spec["box"]}
                    for layer_id in spec["layers"]
                ],
            })

    (folder / "working").mkdir()

    job = {
        "schema_version": 1,
        "run_id": run_id,
        "output_dir": str(folder),
        "period": period,
        "templates": {
            str(index): {"source": staged_templates[index]["source"], "sha256": staged_templates[index]["sha256"],
                         "size": staged_templates[index]["size"]}
            for index in staged_templates
        },
        "cgs": cgs,
        "covers": covers,
    }
    save_json(folder / "job.json", job)

    engine = (ROOT / "photoshop" / "render_covers.jsx").read_text(encoding="utf-8")
    (folder / "run.jsx").write_text(
        "#target photoshop\n" + engine + "\nrenderCovers(" + json.dumps(job, ensure_ascii=True) + ");\n",
        encoding="utf-8",
    )
    return {
        "run_dir": str(folder),
        "period": period,
        "covers": [cover["filename"] for cover in covers],
        "next": "Run 'render <run_dir>' to invoke Photoshop directly and verify",
    }


def load_job(folder):
    folder = Path(folder).resolve(strict=True)
    job = read_json(folder / "job.json")
    if job.get("schema_version") != 1 or job.get("run_id") != folder.name:
        raise ValueError("Invalid cover job manifest")
    if Path(job["output_dir"]).resolve() != folder:
        raise ValueError("Job output_dir does not match the run folder")
    covers = job.get("covers")
    if not isinstance(covers, list) or not covers:
        raise ValueError("Job has no covers")
    for cover in covers:
        for replacement in cover.get("replacements", []):
            if not Path(replacement["image"]).is_file():
                raise ValueError("Missing staged CG image for " + cover["filename"])
        if not Path(cover["template"]).is_file():
            raise ValueError("Missing staged template for " + cover["filename"])
    return job


def verify_outputs(folder, job):
    from PIL import Image
    records = []
    for cover in job["covers"]:
        name = cover["filename"]
        template_index = int(name.split("_")[0][1])
        path = folder / f"{name}.png"
        if not path.is_file():
            raise ValueError(f"Missing output: {name}.png")
        with Image.open(path) as image:
            if image.size != expected_size(template_index):
                raise ValueError(f"Unexpected size for {name}: {image.size}")
            image.load()
            if image.convert("RGB").getextrema() == [(0, 0), (0, 0), (0, 0)]:
                raise ValueError(f"Empty output: {name}")
            records.append({"filename": name, "sha256": sha256(path), "size": list(image.size)})
    return records


def contact_sheet(folder, job, use_refined_t2=True):
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    cell_width = 800
    margin, gap, label_height = 32, 24, 48
    background = (24, 27, 32)
    label_background = (16, 18, 22)
    label_color = (232, 236, 242)

    def load_font(size):
        for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def open_rgb(path):
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"}:
                rgba = image.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (*background, 255))
                image = Image.alpha_composite(bg, rgba).convert("RGB")
            else:
                image = image.convert("RGB")
        return image

    by_name = {cover["filename"]: cover for cover in job["covers"]}
    refined_dir = folder / "refined"
    rows = []
    for template_index in (1, 2, 3):
        row = []
        for cg_index in range(1, 4):
            name = f"T{template_index}_CG{cg_index}"
            if name not in by_name:
                continue
            rel = f"refined/{name}.png" if (template_index == 2 and use_refined_t2 and (refined_dir / f"{name}.png").is_file()) else f"{name}.png"
            path = folder / rel
            if not path.is_file():
                raise FileNotFoundError(path)
            row.append((rel, open_rgb(path)))
        if row:
            rows.append(row)

    font = load_font(27)
    resized_rows = []
    for row in rows:
        resized_rows.append([(rel, image.resize((cell_width, round(image.height * cell_width / image.width)), Image.Resampling.LANCZOS)) for rel, image in row])
    row_heights = [max(image.height for _, image in row) + label_height for row in resized_rows]
    width = margin * 2 + cell_width * max(len(row) for row in resized_rows) + gap * (max(len(row) for row in resized_rows) - 1)
    height = margin * 2 + sum(row_heights) + gap * (len(resized_rows) - 1)
    canvas = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(canvas)
    y = margin
    for row, row_height in zip(resized_rows, row_heights):
        for column, (rel, image) in enumerate(row):
            x = margin + column * (cell_width + gap)
            cell_top = y + (row_height - label_height - image.height) // 2
            canvas.paste(image, (x, cell_top))
            label_top = y + row_height - label_height
            draw.rectangle((x, label_top, x + cell_width - 1, label_top + label_height - 1), fill=label_background)
            box = draw.textbbox((0, 0), rel, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            draw.text((x + (cell_width - tw) / 2, label_top + (label_height - th) / 2 - box[1]), rel, fill=label_color, font=font)
        y += row_height + gap
    out = folder / "contact_sheet.png"
    canvas.save(out, "PNG", optimize=True)
    return out


def write_verification(folder, job, records, extra=None):
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "run_id": job["run_id"],
        "overall_pass": True,
        "outputs": records,
        "visual_review": "pending",
    }
    if extra:
        report.update(extra)
    save_json(folder / "verification.json", report)
    return report


def verify(args):
    if getattr(args, "refined", False):
        return verify_refined(args)
    folder = Path(args.run_dir).resolve(strict=True)
    job = load_job(folder)
    result = read_json(folder / "covers_result.json")
    if result.get("status") != "rendered":
        raise ValueError("Photoshop did not complete this run: " + str(result.get("error", result.get("status"))))
    records = verify_outputs(folder, job)
    sheet = contact_sheet(folder, job)
    exports = export_pngs(folder, job)
    report = write_verification(folder, job, records, {"status": "verified", "contact_sheet": str(sheet), "exports": exports})
    receipt_path = folder / "dispatch.json"
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        receipt["status"] = "rendered"
        save_json(receipt_path, receipt)
    return report


def render(args):
    from .direct import run_photoshop
    folder = Path(args.run_dir).resolve(strict=True)
    job = load_job(folder)
    validate_batch(folder, job, sha256)
    # Never execute a driver received inside a run folder. Rebuild from our engine and validated data.
    write_driver(folder, ROOT / "photoshop" / "render_covers.jsx", job)
    if sys.platform == "darwin":
        from .mac import stage_script
        return stage_script(folder, "run.jsx", folder / "covers_result.json", sha256)
    run_photoshop(folder, args.photoshop, driver_name="run.jsx", result_path=folder / "covers_result.json", timeout=args.timeout)
    return verify(args)


def refine(args):
    from .direct import run_photoshop
    folder = Path(args.run_dir).resolve(strict=True)
    job = load_job(folder)
    validate_batch(folder, job, sha256, check_outputs=False)
    t2_covers = [cover for cover in job["covers"] if cover["filename"].startswith("T2_")]
    if not t2_covers:
        raise ValueError("This run has no T2 covers to refine")
    out_dir = folder / "refined"
    if out_dir.is_symlink():
        raise ValueError("Refined directory cannot be a symlink")
    out_dir.mkdir(exist_ok=True)
    items = []
    for cover in t2_covers:
        tint = cover["tint"]
        items.append({
            "template": str(folder / "working" / f"{cover['filename']}.psd") if (folder / "working" / f"{cover['filename']}.psd").is_file() else str(folder / f"{cover['filename']}.psd"),
            "output_dir": str(out_dir),
            "filename": cover["filename"],
            "layers": [
                {"id": layer_id, "hue": T2_REFINE["hue"], "saturation": T2_REFINE["saturation"], "lightness": T2_REFINE["lightness"]}
                for layer_id in T2_REFINE["layers"]
            ],
            "fills": [
                {"id": T2_REFINE["fills"][0], "rgb": tint},
                {"id": T2_REFINE["fills"][1], "rgb": [min(255, channel + 6) for channel in tint]},
            ],
        })
    refine_job = {"run_id": job["run_id"] + "-t2-refine", "output_dir": str(out_dir), "items": items}
    save_json(folder / "refine_job.json", refine_job)
    write_driver(folder, ROOT / "photoshop" / "refine_cover_background_batch.jsx", refine_job,
                 entry="refineCoverBackgroundBatch", driver_name="refine_run.jsx")
    if sys.platform == "darwin":
        from .mac import stage_script
        return stage_script(folder, "refine_run.jsx", out_dir / "refine_batch_result.json", sha256,
                            dispatch_name="refine_dispatch.json")
    run_photoshop(folder, args.photoshop, driver_name="refine_run.jsx", result_path=out_dir / "refine_batch_result.json",
                  dispatch_name="refine_dispatch.json", timeout=args.timeout)
    return verify_refined(args)


def verify_refined(args):
    folder = Path(args.run_dir).resolve(strict=True)
    job = load_job(folder)
    out_dir = folder / "refined"
    result = read_json(out_dir / "refine_batch_result.json")
    if not isinstance(result, list) or not result or any(item.get("status") != "rendered" for item in result):
        raise ValueError("Photoshop has not completed this refine batch")
    t2_covers = [cover for cover in job["covers"] if cover["filename"].startswith("T2_")]
    records = verify_outputs(out_dir, {"covers": [dict(cover, filename=cover["filename"]) for cover in t2_covers]})
    sheet = contact_sheet(folder, job, use_refined_t2=True)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "run_id": job["run_id"],
        "status": "refined",
        "overall_pass": True,
        "refined_dir": str(out_dir),
        "contact_sheet": str(sheet),
        "outputs": records,
        "exports": export_pngs(out_dir, {**job, "covers": t2_covers}),
    }
    save_json(folder / "refined_verification.json", report)
    receipt_path = folder / "refine_dispatch.json"
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        receipt["status"] = "rendered"
        save_json(receipt_path, receipt)
    return report


def status(args):
    folder = Path(args.run_dir)
    response = {"run_dir": str(folder)}
    if not folder.is_dir():
        response["status"] = "missing"
        return response
    if (folder / "covers_result.json").exists():
        response["result"] = read_json(folder / "covers_result.json")
    if (folder / "refined/refine_batch_result.json").exists():
        response["refine_result"] = read_json(folder / "refined/refine_batch_result.json")
    if (folder / "dispatch.json").exists():
        response["dispatch"] = read_json(folder / "dispatch.json")
    if (folder / "refine_dispatch.json").exists():
        response["refine_dispatch"] = read_json(folder / "refine_dispatch.json")
    if (folder / "verification.json").exists():
        response["verification"] = read_json(folder / "verification.json")
    response["status"] = response.get("result", {}).get("status") or response.get("dispatch", {}).get("status", "not_started")
    return response


def review(args):
    if not args.note.strip():
        raise ValueError("Visual inspection note is required")
    folder = Path(args.run_dir).resolve(strict=True)
    job = load_job(folder)
    if not (folder / "verification.json").exists():
        verify(args)
    report = read_json(folder / "verification.json")
    report["visual_review"] = {"status": "passed", "note": args.note.strip(), "recorded_at": datetime.now().astimezone().isoformat()}
    save_json(folder / "verification.json", report)
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--cg", action="append", required=True, help="CG art image; repeat for multiple")
    prep.add_argument("--template", default="all", help="1, 2, 3, or all")
    prep.add_argument("--output", default=str(DEFAULT_OUTPUT))
    prep.add_argument("--date", help='Explicit period, e.g. "2026.10.24 - 2026.10.30"')
    prep.add_argument("--issue", type=int, required=True, help="Issue number for cover text and PNG filenames")
    prep.add_argument("--period-start", help="ISO start date")
    prep.add_argument("--period-end", help="ISO end date")

    for cmd in ("verify", "status", "review"):
        sub.add_parser(cmd).add_argument("run_dir")
    sub.choices["verify"].add_argument("--refined", action="store_true", help="Verify outputs from a completed refine script")
    for cmd in ("render", "refine"):
        parser_cmd = sub.add_parser(cmd)
        parser_cmd.add_argument("run_dir")
        parser_cmd.add_argument("--photoshop", default=DEFAULT_PHOTOSHOP)
        parser_cmd.add_argument("--timeout", type=float, default=300)
    sub.choices["review"].add_argument("--note", required=True, help="Record completed visual inspection; does not perform it")

    args = parser.parse_args()
    try:
        if args.command == "prepare":
            response = prepare(args)
        elif args.command == "render":
            response = render(args)
        elif args.command == "refine":
            response = refine(args)
        elif args.command == "verify":
            response = verify(args)
        elif args.command == "review":
            response = review(args)
        else:
            response = status(args)
        print(json.dumps(response, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, FileNotFoundError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
