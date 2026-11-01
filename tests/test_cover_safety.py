import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cover_safety', ROOT / 'weekly-cover/scripts/runtime/cover/safety.py')
safety = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(safety)


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CoverSafetyTests(unittest.TestCase):
    def test_concurrent_dispatch_has_one_winner(self):
        with tempfile.TemporaryDirectory() as d:
            receipt = Path(d) / 'dispatch.json'
            def submit(index):
                try: safety.claim_dispatch(receipt, {'caller': index}); return True
                except FileExistsError: return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                self.assertEqual(sum(executor.map(submit, range(16))), 1)
            self.assertIn(json.loads(receipt.read_text())['caller'], range(16))

    def test_asset_changes_paths_and_outputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d).resolve()
            (folder / 'templates').mkdir(); (folder / 'cg').mkdir()
            template = folder / 'templates/template_01.psd'; template.write_bytes(b'template')
            image = folder / 'cg/input.png'; image.write_bytes(b'image')
            job = {'schema_version': 1, 'run_id': folder.name, 'output_dir': str(folder),
                   'templates': {'1': {'sha256': digest(template)}},
                   'cgs': [{'path': str(image), 'sha256': digest(image)}],
                   'covers': [{'filename': 'T1_CG1', 'template': str(template), 'replacements': [{'image': str(image)}]}]}
            safety.validate_batch(folder, job, digest)
            (folder / 'T1_CG1.png').touch()
            with self.assertRaises(FileExistsError): safety.validate_batch(folder, job, digest)
            (folder / 'T1_CG1.png').unlink()
            image.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'changed'): safety.validate_batch(folder, job, digest)
            image.write_bytes(b'image')
            job['covers'][0]['filename'] = '../outside'
            with self.assertRaises(ValueError): safety.validate_batch(folder, job, digest)
            job['covers'][0]['filename'] = 'T1_CG1'
            image.unlink(); image.symlink_to(template)
            with self.assertRaises(ValueError): safety.validate_batch(folder, job, digest)

    def test_received_driver_cannot_execute_or_overwrite_symlink_target(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d); outside = folder / 'outside.jsx'; outside.write_text('keep me')
            run = folder / 'run'; run.mkdir()
            (run / 'run.jsx').symlink_to(outside)
            engine = folder / 'engine.jsx'; engine.write_text('function renderCovers(job) {}')
            text = '"; throw new Error("injection"); //'
            safety.write_driver(run, engine, {'title': text})
            self.assertEqual(outside.read_text(), 'keep me')
            self.assertFalse((run / 'run.jsx').is_symlink())
            driver = (run / 'run.jsx').read_text()
            encoded = driver.split('\nrenderCovers(', 1)[1][:-3]
            self.assertEqual(json.loads(encoded), {'title': text})

    def test_overlay_rejects_code_prepended_to_valid_job(self):
        sys.path.insert(0, str(ROOT / 'weekly-bgm-overlay/scripts/runtime'))
        from weekly_overlay import cli
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d); template = folder / 'master.psd'; template.write_bytes(b'master')
            text = folder / 'bgm.txt'; text.write_text('其余：Artist - Song', encoding='utf-8')
            result = cli.prepare(SimpleNamespace(task=None, start=None, end=None, today='2026-10-30',
                input=str(text), template=str(template), output=str(folder / 'runs')))
            run = Path(result['run_dir'])
            cli.load_job(run)
            driver = run / 'run.jsx'; driver.write_text('throw new Error("injected");\n' + driver.read_text())
            with self.assertRaisesRegex(ValueError, 'installed engine'):
                cli.load_job(run)

    def test_mac_diagnostic_does_not_claim_native_render_support(self):
        if sys.platform != 'darwin': self.skipTest('macOS diagnostic')
        for skill in ('weekly-cover', 'weekly-bgm-overlay'):
            result = subprocess.run([sys.executable, str(ROOT / skill / 'scripts/doctor.py')], capture_output=True, text=True)
            report = json.loads(result.stdout)
            self.assertFalse(report['render_supported'])
            self.assertFalse(report['render_prerequisites_found'])
            self.assertFalse(report['photoshop_started'])
            self.assertIn('macOS', report['render_limitation'])


if __name__ == '__main__': unittest.main()
