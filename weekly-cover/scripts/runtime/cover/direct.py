"""Invoke the native Photoshop scripting interface through PowerShell 5.1."""

from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from .cli import read_json, save_json, sha256
from .safety import claim_dispatch


def run_photoshop(folder, photoshop, driver_name="run.jsx", result_path=None,
                  dispatch_name="dispatch.json", timeout=300):
    """Run one JSX driver exactly once and validate its result receipt."""
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("timeout must be greater than zero and no more than 300 seconds")
    if sys.platform != "win32":
        raise ValueError("Direct Photoshop invocation requires Windows")
    folder = Path(folder).resolve(strict=True)
    result_path = Path(result_path).resolve() if result_path else folder / "covers_result.json"
    executable = Path(photoshop).resolve(strict=True)
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Expected a Photoshop executable")
    driver = folder / driver_name
    if not driver.is_file():
        raise FileNotFoundError(driver)

    helper = Path(__file__).resolve().parent.parent / "photoshop" / "invoke.ps1"
    legacy = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not helper.is_file() or not legacy.is_file():
        raise FileNotFoundError("Windows PowerShell 5.1 or the Photoshop helper is missing")

    receipt_path = folder / dispatch_name
    if receipt_path.exists():
        raise ValueError("Run already submitted; inspect status instead of resubmitting")
    if result_path.exists():
        raise ValueError("Result receipt already exists; inspect status instead of resubmitting")

    receipt = {
        "run_id": folder.name,
        "transport": "powershell51-com",
        "status": "submitted",
        "driver": driver_name,
        "driver_sha256": sha256(driver),
        "submitted_at": datetime.now().astimezone().isoformat(),
    }
    claim_dispatch(receipt_path, receipt)

    try:
        process = subprocess.Popen(
            [str(legacy), "-NoProfile", "-NonInteractive", "-STA", "-File", str(helper),
             "-RunDir", str(folder), "-Photoshop", str(executable), "-Driver", driver_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        receipt["helper_pid"] = process.pid
        save_json(receipt_path, receipt)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            receipt["status"] = "timed_out"
            receipt["error"] = "Photoshop may still be running; inspect status before resubmitting"
            save_json(receipt_path, receipt)
            raise TimeoutError(receipt["error"]) from error
        (folder / "invoke.log").write_text(stdout + stderr, encoding="utf-8")
        if process.returncode:
            detail = ""
            if (folder / "connection.json").exists():
                detail = read_json(folder / "connection.json").get("error", "")
            raise ValueError("Photoshop COM helper failed: " + (detail or stderr.strip()))
        if not result_path.exists():
            raise ValueError("Photoshop returned without a result receipt")
        result = read_json(result_path)
        # The render engine returns {status: "rendered"}; the refine engine
        # returns a list of {filename, status} items.
        if isinstance(result, list):
            if not result or any(item.get("status") != "rendered" for item in result):
                raise ValueError("Photoshop refine failed: " + str(result))
        elif result.get("status") != "rendered":
            raise ValueError("Photoshop render failed: " + str(result.get("error", result.get("status"))))
        receipt["status"] = "rendered"
        save_json(receipt_path, receipt)
        return result
    except Exception:
        if receipt.get("status") not in ("timed_out", "rendered"):
            receipt.update(status="failed", error=str(sys.exc_info()[1]))
            save_json(receipt_path, receipt)
        raise
