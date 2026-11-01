"""Validate frozen cover assets before generating an executable JSX driver."""
import re
from pathlib import Path


def validate_batch(folder, job, sha256, check_outputs=True):
    folder = Path(folder).resolve(strict=True)
    if job.get("schema_version") != 1 or job.get("run_id") != folder.name:
        raise ValueError("Invalid cover batch identity")
    if Path(job["output_dir"]).resolve() != folder:
        raise ValueError("Output directory escapes the cover batch")

    def staged_file(value, subdirectory, expected_hash):
        path = Path(value).resolve(strict=True)
        root = (folder / subdirectory).resolve(strict=True)
        if not root.is_relative_to(folder) or not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Staged asset escapes the cover batch")
        if sha256(path) != expected_hash:
            raise ValueError("Staged asset changed after prepare")
        return path

    templates = {}
    for index, spec in job["templates"].items():
        if str(index) not in ("1", "2", "3"):
            raise ValueError("Unknown template")
        templates[str(index)] = staged_file(folder / "templates" / f"template_{int(index):02d}.psd",
                                             "templates", spec["sha256"])
    images = {str(staged_file(cg["path"], "cg", cg["sha256"])) for cg in job["cgs"]}
    names = set()
    if not job.get("covers"):
        raise ValueError("No covers")
    for cover in job["covers"]:
        name = cover["filename"]
        if not re.fullmatch(r"T[123]_CG[1-9][0-9]*", name) or name in names:
            raise ValueError("Unsafe or duplicate cover output filename")
        names.add(name)
        if Path(cover["template"]).resolve() != templates.get(name[1]):
            raise ValueError("Cover template is not the staged template")
        for replacement in cover["replacements"]:
            if str(Path(replacement["image"]).resolve()) not in images:
                raise ValueError("Cover references an unstaged image")
        for extension in ("psd", "png", "jpg"):
            if check_outputs and (folder / f"{name}.{extension}").exists():
                raise FileExistsError("Cover output already exists; not overwriting")


def write_driver(folder, engine, job, entry="renderCovers", driver_name="run.jsx"):
    import json
    import tempfile
    text = ("#target photoshop\n" + engine.read_text(encoding="utf-8") +
            "\n" + entry + "(" + json.dumps(job, ensure_ascii=True) + ");\n")
    # Replacing the directory entry cannot follow a received driver's symlink.
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text.encode("utf-8"))
    try:
        temporary.replace(Path(folder) / driver_name)
    finally:
        temporary.unlink(missing_ok=True)


def claim_dispatch(path, receipt):
    import json
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
