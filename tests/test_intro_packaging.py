from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


class PackagingTests(unittest.TestCase):
    def test_intro_zip_is_self_contained_code_without_private_assets(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='intro package ') as d:
            result=subprocess.run([sys.executable,str(root/'tools/package_skills.py'),'--output',d],cwd=root,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            archive=Path(d)/'weekly-intro.zip'
            self.assertTrue(archive.is_file(),'intro is not included in skill packaging')
            with ZipFile(archive) as z:
                names=set(z.namelist())
                for name in ('SKILL.md','scripts/intro.py','scripts/runtime/intro/cli.py','scripts/runtime/ae/worker.jsx','references/task-schema.md'):
                    self.assertIn(name,names)
                self.assertFalse(any(Path(n).suffix.lower() in ('.aep','.png','.jpg','.mp4','.pyc','.zip') for n in names))


if __name__=='__main__': unittest.main()
