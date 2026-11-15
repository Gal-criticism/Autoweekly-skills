import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


class RubyPackagingTests(unittest.TestCase):
    def test_portable_zip_contains_ruby_skill_and_runs_doctor(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="ruby_pkg_") as d:
            target = Path(d)
            p = subprocess.run(
                [sys.executable, str(root / "tools/package_skills.py"), "--output", str(target)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            zip_path = target / "weekly-ruby.zip"
            self.assertTrue(zip_path.is_file())
            with ZipFile(zip_path) as z:
                names = z.namelist()
                for required in (
                    "SKILL.md",
                    "scripts/ruby.py",
                    "scripts/runtime/weekly_ruby/__init__.py",
                    "scripts/runtime/weekly_ruby/core.py",
                    "scripts/runtime/weekly_ruby/io_handlers.py",
                    "scripts/runtime/weekly_ruby/cli.py",
                    "references/ruby-rules.md",
                ):
                    self.assertIn(required, names)
                self.assertFalse(
                    any(Path(n).suffix.lower() in (".docx", ".pyc", ".json") for n in names),
                    f"Unexpected private or cache files in zip: {names}",
                )
                unpack = target / "only-ruby"
                z.extractall(unpack)

            doc_proc = subprocess.run(
                [sys.executable, str(unpack / "scripts/ruby.py"), "doctor"],
                cwd=target,
                capture_output=True,
                text=True,
            )
            self.assertEqual(doc_proc.returncode, 0, doc_proc.stderr)
            report = json.loads(doc_proc.stdout)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["skill"], "weekly-ruby")


if __name__ == "__main__":
    unittest.main()
