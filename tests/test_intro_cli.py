import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]/'weekly-intro/scripts'
sys.path.insert(0, str(ROOT/'runtime'))


class CLITests(unittest.TestCase):
    def module(self):
        self.assertTrue((ROOT/'runtime/intro/cli.py').exists(), 'CLI has not been implemented')
        return importlib.import_module('intro.cli')

    def test_output_name_cannot_escape_batch(self):
        module = self.module()
        for name in ('../240', '240/other', '240\\other', '..', 'a:b', ''):
            with self.assertRaises(ValueError): module.safe_week_id(name)
        self.assertEqual(module.safe_week_id('验收240'), '验收240')

    def test_prepare_is_local_and_binds_master_hash(self):
        module = self.module()
        from intro.transport import sha256
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); images = root/'images'; images.mkdir()
            for i in range(4): Image.new('RGB', (80, 40), (i*50, 25, 30)).save(images/f'{i}.png')
            template = root/'master.aep'; template.write_bytes(b'AEP fixture')
            bridge = root/'bridge.png'; Image.new('RGB', (80, 40)).save(bridge)
            config = root/'config.json'; config.write_text(json.dumps({'template':str(template),'bridge_image':str(bridge)}))
            profile = root/'profile.json'; profile.write_text(json.dumps({'template':str(template),'template_sha256':sha256(template),'slots':[{'id':f'c1:l{i}','width':80,'height':40} for i in range(4)],'expected':{'width':2160,'height':1080,'fps':60,'duration':12,'frames':720,'audio_expected':False}}))
            task = root/'task.json'; task.write_text(json.dumps({'week_id':'240','allowed_roots':[str(images)],'profile':str(profile),'config':str(config)}))
            before = template.read_bytes(); result = module.prepare_task(task,root/'runs')
            run = Path(result['run_dir'])
            self.assertTrue((run/'run.json').is_file())
            self.assertTrue((run/'candidates.json').is_file())
            self.assertFalse((run/'render-dispatch.json').exists())
            self.assertEqual(template.read_bytes(),before)
            self.assertEqual(Path(result['video_path']).name,'240片头.mp4')
            template.write_bytes(b'changed master')
            with self.assertRaises(ValueError): module.prepare_task(task,root/'runs')

    def test_render_accepts_doctors_explicit_readiness_boolean(self):
        module = self.module()
        from intro.transport import sha256
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); template=root/'master.aep'; template.write_bytes(b'master')
            (root/'profile.json').write_text(json.dumps({'template':str(template),'template_sha256':sha256(template)}))
            copy=root/'copy.aep'; copy.write_bytes(b'master')
            (root/'run.json').write_text(json.dumps({'status':'prepared','project_copy':str(copy),'video_path':str(root/'out.mp4'),'aep_path':str(root/'out.aep'),'config':{}}))
            with patch.object(module,'doctor',return_value={'status':'ready','prerequisites_found':True}):
                with self.assertRaises(FileNotFoundError): module.render_run(root)

    def test_run_paths_cannot_target_original_or_external_outputs(self):
        module=self.module()
        self.assertTrue(hasattr(module,'validate_run_paths'),'batch path isolation not implemented')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'run'; root.mkdir()
            run={'project_copy':str(root/'work.aep'),'aep_path':str(root/'out.aep'),'video_path':str(root/'out.mp4')}
            module.validate_run_paths(root,run)
            for key in run:
                wrong=dict(run); wrong[key]=str(Path(d)/'outside.file')
                with self.assertRaises(ValueError): module.validate_run_paths(root,wrong)

    def test_repeated_verify_keeps_successful_record(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            run={'status':'verified','project_copy':str(root/'work.aep'),'aep_path':str(root/'out.aep'),'video_path':str(root/'out.mp4'),'config':{}}
            state=root/'run.json'; state.write_text(json.dumps(run))
            receipt=root/'verification.json'; receipt.write_text('{"status":"verified"}')
            before_state=state.read_bytes(); before_receipt=receipt.read_bytes()
            with patch.object(module,'verify_video',return_value={'status':'failed'}) as verifier:
                with self.assertRaisesRegex(ValueError,'Already verified'): module.verify_run(root)
                verifier.assert_not_called()
            self.assertEqual(state.read_bytes(),before_state)
            self.assertEqual(receipt.read_bytes(),before_receipt)

    def test_environment_ready_but_configuration_missing_stops_before_apply(self):
        module=self.module()
        from intro.transport import sha256
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); template=root/'master.aep'; template.write_bytes(b'master')
            copy=root/'copy.aep'; copy.write_bytes(b'master')
            (root/'profile.json').write_text(json.dumps({'template':str(template),'template_sha256':sha256(template)}))
            run={'status':'prepared','project_copy':str(copy),'video_path':str(root/'out.mp4'),'aep_path':str(root/'out.aep'),'config':{}}
            (root/'run.json').write_text(json.dumps(run))
            with patch.object(module,'doctor',return_value={'status':'setup_required','prerequisites_found':True,'setup_required':True}):
                with self.assertRaisesRegex(ValueError,'Preflight failed'): module.render_run(root)
            self.assertFalse((root/'replacements').exists())
            self.assertEqual(json.loads((root/'run.json').read_text())['status'],'prepared')

    def test_success_receipt_survives_crash_before_state_commit(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            run={'status':'rendered','project_copy':str(root/'work.aep'),'aep_path':str(root/'out.aep'),'video_path':str(root/'out.mp4'),'config':{}}
            state=root/'run.json'; state.write_text(json.dumps(run))
            receipt=root/'verification.json'; receipt.write_text('{"status":"verified"}')
            with patch.object(module,'verify_video',return_value={'status':'failed'}) as verifier:
                with self.assertRaisesRegex(ValueError,'receipt already exists'): module.verify_run(root)
                verifier.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text())['status'],'verified')
            self.assertEqual(json.loads(state.read_text())['status'],'rendered')

    def test_second_render_controller_cannot_change_first_run_state(self):
        module=self.module()
        from intro.transport import sha256
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); template=root/'master.aep'; template.write_bytes(b'master')
            copy=root/'copy.aep'; copy.write_bytes(b'master')
            (root/'profile.json').write_text(json.dumps({'template':str(template),'template_sha256':sha256(template)}))
            state=root/'run.json'; state.write_text(json.dumps({'status':'prepared','project_copy':str(copy),'aep_path':str(root/'out.aep'),'video_path':str(root/'out.mp4'),'config':{}}))
            (root/'pipeline-dispatch.json').write_text('{"status":"submitted"}')
            before=state.read_bytes()
            with patch.object(module,'doctor',return_value={'status':'ready','prerequisites_found':True}):
                with self.assertRaises(FileExistsError): module.render_run(root)
            self.assertEqual(state.read_bytes(),before)


if __name__ == '__main__': unittest.main()
