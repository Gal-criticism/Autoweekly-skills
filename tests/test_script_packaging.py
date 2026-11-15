import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


class ScriptPackagingTests(unittest.TestCase):
    def test_portable_zip_contains_skill_files_and_no_private_corpus(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="script_pkg_") as d:
            target = Path(d)
            p = subprocess.run(
                [sys.executable, str(root / "tools/package_skills.py"), "--output", str(target)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            zip_path = target / "weekly-script.zip"
            self.assertTrue(zip_path.is_file())
            with ZipFile(zip_path) as z:
                names = z.namelist()
                for required in (
                    "SKILL.md",
                    "scripts/script.py",
                    "scripts/runtime/script/cli.py",
                    "scripts/runtime/script/parsers.py",
                    "scripts/runtime/script/indexer.py",
                    "scripts/runtime/script/retriever.py",
                    "scripts/runtime/script/exporter.py",
                    "scripts/runtime/script/verifier.py",
                    "references/style-guide.md",
                    "references/selection-rules.md",
                    "references/category-examples.md",
                    "references/output-spec.md",
                ):
                    self.assertIn(required, names)
                self.assertFalse(
                    any(Path(n).suffix.lower() in (".docx", ".pyc", ".json") for n in names),
                    f"Unexpected private or cache files in zip: {names}",
                )
                unpack = target / "only-script"
                z.extractall(unpack)

            doc_proc = subprocess.run(
                [sys.executable, str(unpack / "scripts/script.py"), "doctor"],
                cwd=target,
                capture_output=True,
                text=True,
            )
            self.assertEqual(doc_proc.returncode, 0, doc_proc.stderr)
            report = json.loads(doc_proc.stdout)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["skill"], "weekly-script")


if __name__ == "__main__":
    unittest.main()
