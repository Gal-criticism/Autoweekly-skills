"""Read-only environment report for any local agent; does not launch Photoshop."""

import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys

PROBE_ERRORS = []


def is_file(path):
    try:
        return Path(path).is_file()
    except OSError as error:
        PROBE_ERRORS.append({"path": str(path), "error": str(error)})
        return False


def photoshop_candidates(default):
    candidates = [default] if default else []
    if sys.platform == "win32":
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe", 0, winreg.KEY_READ | view) as key:
                        candidates.append(winreg.QueryValueEx(key, None)[0].strip('"'))
                except OSError:
                    pass
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(variable):
                try:
                    candidates.extend(str(path) for path in sorted((Path(os.environ[variable]) / "Adobe").glob("Adobe Photoshop */Photoshop.exe"), reverse=True))
                except OSError as error:
                    PROBE_ERRORS.append({"path": os.environ[variable], "error": str(error)})
    return list(dict.fromkeys(str(Path(path).resolve()) for path in candidates if path and is_file(path)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photoshop", help="Check an explicit Photoshop.exe path")
    args = parser.parse_args()
    scripts = Path(__file__).resolve().parent
    is_cover = (scripts / "cover.py").is_file()
    group = "cover" if is_cover else "weekly_overlay"
    entry = scripts / ("cover.py" if is_cover else "overlay.py")
    sys.path.insert(0, str(scripts / "runtime"))
    dependencies = {name: importlib.util.find_spec(name) is not None for name in (["PIL"] if is_cover else ["PIL", "numpy"])}
    templates, load_error, default_ps = [], None, None
    try:
        cli = importlib.import_module(group + ".cli")
        default_ps = cli.DEFAULT_PHOTOSHOP
        paths = {str(index): spec["source"] for index, spec in cli.DEFAULT_TEMPLATES.items()} if is_cover else {"overlay": cli.DEFAULT_TEMPLATE}
        templates = [{"key": key, "path": str(Path(path).resolve()), "exists": is_file(path)} for key, path in paths.items()]
    except (ImportError, OSError, ValueError) as error:
        load_error = str(error)
    candidates = photoshop_candidates(default_ps)
    selected = str(Path(args.photoshop).expanduser().resolve()) if args.photoshop else (candidates[0] if candidates else None)
    ps_exists = bool(selected and is_file(selected) and Path(selected).suffix.lower() == ".exe")
    powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    prepare_ready = sys.version_info >= (3, 11) and all(dependencies.values()) and not load_error and bool(templates) and all(item["exists"] for item in templates)
    powershell_exists = is_file(powershell)
    render_prerequisites = prepare_ready and sys.platform == "win32" and powershell_exists and ps_exists
    response = {
        "status": "prerequisites_found" if render_prerequisites else "setup_required",
        "skill_dir": str(scripts.parent), "python": sys.executable,
        "python_supported": sys.version_info >= (3, 11), "platform": sys.platform,
        "dependencies": dependencies, "cli_load_error": load_error,
        "templates": templates, "photoshop": selected, "photoshop_exists": ps_exists,
        "photoshop_candidates": candidates, "powershell51_exists": powershell_exists,
        "probe_errors": PROBE_ERRORS,
        "prepare_ready": prepare_ready, "render_prerequisites_found": render_prerequisites,
        "fonts_checked": False, "photoshop_started": False,
        "commands": {
            "download": [sys.executable, str(scripts / "download_templates.py")],
            "help": [sys.executable, str(entry), "--help"],
            "render_example": [sys.executable, str(entry), "render", "<run_dir>", "--photoshop", selected or "<Photoshop.exe>"],
        },
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if render_prerequisites else 1


if __name__ == "__main__":
    raise SystemExit(main())
