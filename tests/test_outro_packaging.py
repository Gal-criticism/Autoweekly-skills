import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

class OutroPackagingTests(unittest.TestCase):
    def test_portable_zip_runs_without_intro_or_private_materials(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='outro independent ') as d:
            target=Path(d)
            p=subprocess.run([sys.executable,str(root/'tools/package_skills.py'),'--output',str(target)],
                             cwd=root,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            with ZipFile(target/'weekly-outro.zip') as z:
                names=z.namelist()
                for name in ('SKILL.md','scripts/outro.py','scripts/runtime/outro/roster.py','scripts/runtime/ae/worker.jsx'):
                    self.assertIn(name,names)
                self.assertFalse(any(Path(n).suffix.lower() in ('.aep','.xlsx','.png','.jpg','.mp4','.pyc') for n in names))
                unpack=target/'only-outro';z.extractall(unpack)
            p=subprocess.run([sys.executable,str(unpack/'scripts/outro.py'),'doctor'],cwd=target,capture_output=True)
            report=json.loads(p.stdout)
            self.assertEqual(report['status'],'setup_required')
            self.assertIn('template',report['missing'])

if __name__=='__main__':unittest.main()
