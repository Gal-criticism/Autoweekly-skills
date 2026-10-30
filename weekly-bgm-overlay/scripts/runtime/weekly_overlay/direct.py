"""Call the native Photoshop scripting interface through Windows PowerShell 5.1."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def render_run(folder, photoshop, timeout=300):
    from .cli import ROOT, load_job, read_json, save_json, sha256

    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("timeout must be greater than zero and no more than 300 seconds")
    if sys.platform != "win32":
        raise ValueError("Direct Photoshop invocation requires Windows")
    folder = Path(folder).resolve(strict=True)
    executable = Path(photoshop).resolve(strict=True)
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Expected a Photoshop executable")
    job = load_job(folder)
    for name in ("started.json", "result.json", "dispatch.json"):
        if (folder / name).exists():
            raise ValueError("Run already started or submitted; inspect its status instead of resubmitting")
    helper = ROOT / "photoshop" / "invoke.ps1"
    legacy = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not helper.is_file() or not legacy.is_file():
        raise FileNotFoundError("Windows PowerShell 5.1 or the Photoshop helper is missing")
    receipt = {"run_id": job["run_id"], "transport": "powershell51-com",
               "status": "submitted", "driver_sha256": sha256(folder / "run.jsx"),
               "submitted_at": datetime.now().astimezone().isoformat()}
    # Exclusive creation claims this run before either a cold start or COM call.
    with (folder / "dispatch.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    try:
        process = subprocess.Popen(
            [str(legacy), "-NoProfile", "-NonInteractive", "-STA", "-File", str(helper),
             "-RunDir", str(folder), "-Photoshop", str(executable)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        receipt["helper_pid"] = process.pid
        save_json(folder / "dispatch.json", receipt)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            # Stop only our waiting helper. Photoshop may still be executing JSX.
            process.kill()
            process.communicate()
            receipt["status"] = "timed_out"
            receipt["error"] = "Photoshop may still be running. Do not resubmit this run; inspect status."
            save_json(folder / "dispatch.json", receipt)
            raise TimeoutError(receipt["error"]) from error
        (folder / "invoke.log").write_text(stdout + stderr, encoding="utf-8")
        if process.returncode:
            detail = read_json(folder / "connection.json").get("error", "") if (folder / "connection.json").exists() else stderr
            raise ValueError("Photoshop COM helper failed: " + detail)
        if not (folder / "result.json").exists():
            raise ValueError("Photoshop returned without a result receipt")
        result = read_json(folder / "result.json")
        if result.get("run_id") != job["run_id"] or result.get("status") != "rendered":
            raise ValueError("Photoshop render failed or returned another run: " + str(result.get("error", result.get("status"))))
        receipt["status"] = "rendered"
        save_json(folder / "dispatch.json", receipt)
        return result
    except Exception as error:
        if receipt["status"] != "timed_out":
            receipt.update(status="failed", error=str(error))
            save_json(folder / "dispatch.json", receipt)
        raise
