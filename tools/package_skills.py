"""Build self-contained ZIP files for skill importers (no PSDs or run outputs)."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("weekly-cover", "weekly-bgm-overlay", "weekly-intro", "weekly-outro", "weekly-script", "weekly-ruby"):
        source = root / name
        target = args.output / (name + ".zip")
        with ZipFile(target, "w", ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source)
                if path.is_file() and "__pycache__" not in relative.parts and path.suffix != ".pyc" and (relative.as_posix() == "SKILL.md" or relative.parts[0] in ("scripts", "references", "agents")):
                    archive.write(path, relative.as_posix())
        print(target.resolve())


if __name__ == "__main__":
    main()
