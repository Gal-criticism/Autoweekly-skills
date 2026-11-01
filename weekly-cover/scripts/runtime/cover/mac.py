"""Stage a single-use JSX for Photoshop's File > Scripts > Browse on macOS."""
from datetime import datetime
import json
from pathlib import Path

from .safety import claim_dispatch


def stage_script(folder, driver_name, result_path, sha256, dispatch_name="dispatch.json"):
    """Wrap a freshly rebuilt driver; never launch apps or replay an existing batch."""
    folder = Path(folder).resolve(strict=True)
    driver = folder / driver_name
    receipt_path = folder / dispatch_name
    result_path = Path(result_path).resolve()
    if not result_path.is_relative_to(folder):
        raise ValueError("Result must stay inside the batch")
    if receipt_path.exists() or result_path.exists():
        raise ValueError("Batch already dispatched; inspect status instead of replaying")
    execution_path = folder / (Path(dispatch_name).stem + "_started.json")
    script_path = folder / (Path(driver_name).stem + "_mac.jsx")
    if script_path.exists() or execution_path.exists():
        raise ValueError("Mac script or execution receipt already exists")
    # Photoshop executes scripts sequentially. The marker also blocks a second
    # UI invocation after an error/unknown result, before opening any document.
    marker = json.dumps(str(execution_path), ensure_ascii=True)
    text = driver.read_text(encoding="utf-8")
    guard = (
        "#target photoshop\n(function () {\n"
        "var started = File(" + marker + ");\n"
        "if (started.exists) throw Error('Batch already started; inspect receipts');\n"
        "started.encoding = 'UTF8';\n"
        "if (!started.open('w')) throw Error('Cannot claim batch execution');\n"
        "started.write('{\"status\":\"started\"}'); started.close();\n"
    )
    receipt = {
        "run_id": folder.name, "transport": "macos-photoshop-script-menu",
        "status": "awaiting_photoshop", "driver": script_path.name,
        "submitted_at": datetime.now().astimezone().isoformat(),
        "result_path": str(result_path),
    }
    # Exclusive reservation precedes writing the executable, including when two
    # agents happen to stage the same validated batch at once.
    claim_dispatch(receipt_path, receipt)
    with script_path.open("x", encoding="utf-8") as stream:
        stream.write(guard + text.replace("#target photoshop", "", 1) + "\n}());\n")
    receipt["driver_sha256"] = sha256(script_path)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "awaiting_photoshop", "run_dir": str(folder),
        "script": str(script_path), "driver_sha256": receipt["driver_sha256"],
        "next": "In Photoshop use File > Scripts > Browse, select this script once, then run verify. This command has not rendered any cover.",
    }
