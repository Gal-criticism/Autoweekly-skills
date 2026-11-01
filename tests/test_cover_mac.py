"""Exercise Mac handoff receipts and the single-use guard without Photoshop."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "weekly-cover/scripts/runtime"))
from cover.mac import stage_script


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MacCoverTests(unittest.TestCase):
    def test_staging_is_not_rendering_and_cannot_be_repeated(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "run.jsx").write_text("#target photoshop\nvar count = 1;", encoding="utf-8")
            response = stage_script(folder, "run.jsx", folder / "covers_result.json", digest)
            self.assertEqual(response["status"], "awaiting_photoshop")
            self.assertFalse((folder / "covers_result.json").exists())
            script = Path(response["script"])
            original = script.read_bytes()
            self.assertEqual(response["driver_sha256"], digest(script))
            with self.assertRaisesRegex(ValueError, "already dispatched"):
                stage_script(folder, "run.jsx", folder / "covers_result.json", digest)
            self.assertEqual(script.read_bytes(), original)

    @unittest.skipUnless(shutil.which("node"), "Node.js needed to exercise JSX guard")
    def test_photoshop_replay_stops_before_engine_even_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "run.jsx").write_text("#target photoshop\nengineCalls++; throw Error('engine failure');")
            script = Path(stage_script(folder, "run.jsx", folder / "covers_result.json", digest)["script"])
            javascript = script.read_text().replace("#target photoshop", "")
            harness = """
let claimed = false, engineCalls = 0;
function File(path) { return {get exists() {return claimed}, open() {claimed=true;return true}, write() {}, close() {}}; }
const script = SCRIPT;
for(let i=0;i<2;i++) { try { eval(script); } catch(error) {} }
if(engineCalls !== 1) throw Error('Engine replayed: '+engineCalls);
""".replace("SCRIPT", json.dumps(javascript))
            result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_result_path_must_stay_inside_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "run.jsx").write_text("#target photoshop")
            with self.assertRaisesRegex(ValueError, "inside the batch"):
                stage_script(folder, "run.jsx", folder.parent / "external.json", digest)
            self.assertFalse((folder / "dispatch.json").exists())


if __name__ == "__main__":
    unittest.main()
