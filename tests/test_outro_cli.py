import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'weekly-outro/scripts/runtime'))
from outro import cli
from outro.transport import save_json,sha256
from outro.verification import sample_outro_times,VerificationError

class OutroCLITests(unittest.TestCase):
    def test_all_staff_sample_times_are_retained_and_invalid_times_fail(self):
        pages=[4.26,9.955,16.83,23.78,30.0]
        actual=sample_outro_times(41.016666667,60,pages)
        self.assertTrue(set(pages).issubset(actual))
        self.assertEqual(actual,sorted(set(actual)))
        for invalid in ([],None,[float('nan')],[41.016666667],[-1],[True]):
            with self.assertRaises(VerificationError):sample_outro_times(41.016666667,60,invalid)

    def fixture(self,root,issue='250'):
        template=root/'t.aep';template.write_bytes(b'master')
        images=root/'images';images.mkdir();Image.new('RGB',(80,40)).save(images/'x.png')
        bridge=root/'bridge.png';Image.new('RGB',(80,40)).save(bridge)
        save_json(root/'config.json',{'template':str(template),'bridge_image':str(bridge)})
        slot={'id':'c2:l1','width':80,'height':40}
        save_json(root/'profile.json',{'template':str(template),'template_sha256':sha256(template),
                  'groups':[{'role':'后期','fixed':False,'slots':[dict(slot)]}],
                  'slots':[slot],'sample_times':[5],'expected':{'duration':10},'layout_sampled_at_readable_time':True})
        save_json(root/'roster.json',{'issue':issue,'roles':[{'role':'后期','members':[{'display':'A'}]}]})
        task={'week_id':'250','config':str(root/'config.json'),'profile':str(root/'profile.json'),
              'roster_snapshot':str(root/'roster.json'),'allowed_roots':[str(images)]}
        save_json(root/'task.json',task)
        return root/'task.json'

    def test_prepare_freezes_roster_and_rejects_changed_snapshot_before_ae(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);task=self.fixture(root);result=cli.prepare_task(task,root/'runs');run=Path(result['run_dir'])
            self.assertEqual(Path(result['video_path']).name,'250片尾.mp4')
            self.assertEqual(json.loads((run/'text-plan.json').read_text()) [0]['text'],'A')
            (run/'roster.json').write_text('{}')
            with patch.object(cli,'run_script') as native:
                with self.assertRaisesRegex(ValueError,'Frozen input'):cli.render_run(run)
                native.assert_not_called()

    def test_wrong_issue_fails_without_creating_batch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);task=self.fixture(root,'249')
            with self.assertRaisesRegex(ValueError,'another issue'):cli.prepare_task(task,root/'runs')
            self.assertFalse((root/'runs').exists())

    def test_verified_receipt_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            save_json(root/'run.json',{'status':'rendered','project_copy':str(root/'i.aep'),
                     'aep_path':str(root/'o.aep'),'video_path':str(root/'o.mp4')})
            receipt=root/'verification.json';receipt.write_text('{"status":"verified"}')
            with patch.object(cli,'verify_video') as verifier:
                with self.assertRaisesRegex(ValueError,'receipt already'):cli.verify_run(root)
                verifier.assert_not_called()
            self.assertEqual(receipt.read_text(),'{"status":"verified"}')

if __name__=='__main__':unittest.main()
