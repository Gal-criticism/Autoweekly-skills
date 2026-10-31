import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / 'weekly-intro/scripts/runtime/intro/transport.py'


def load_transport():
    spec = importlib.util.spec_from_file_location('intro_transport_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransportTests(unittest.TestCase):
    def test_rejects_result_from_another_instance_or_project(self):
        self.assertTrue(MODULE.exists(), 'native transport has not been implemented')
        module = load_transport()
        with tempfile.TemporaryDirectory() as folder:
            project = str(Path(folder) / 'work.aep')
            good = {'token': 'this-run', 'operation': 'inspect', 'status': 'ok', 'project_path': project}
            self.assertEqual(module.validate_result(good, 'this-run', 'inspect', project), good)
            for overrides in ({'token': 'other-run'}, {'operation': 'apply'}, {'project_path': str(Path(folder) / 'original.aep')}):
                with self.assertRaises(module.AEError):
                    module.validate_result(dict(good, **overrides), 'this-run', 'inspect', project)

    def test_launcher_preserves_space_and_unicode_arguments(self):
        self.assertTrue(MODULE.exists(), 'native transport has not been implemented')
        module = load_transport()
        command = module.ae_command('C:/Adobe AE/AfterFX.com', 'C:/test dir/driver 中文.jsx')
        self.assertEqual(command, ['C:/Adobe AE/AfterFX.com', '-m', '-r', 'C:/test dir/driver 中文.jsx'])
        render = module.render_command('C:/Adobe AE/aerender.exe', 'E:/本期 文件/work.aep', 64)
        self.assertEqual(render, ['C:/Adobe AE/aerender.exe', '-project', 'E:/本期 文件/work.aep', '-rqindex', '64', '-v', 'ERRORS_AND_PROGRESS'])
        self.assertNotIn('-reuse', render)

    def test_dispatch_cannot_be_submitted_twice(self):
        self.assertTrue(MODULE.exists(), 'native transport has not been implemented')
        module = load_transport()
        with tempfile.TemporaryDirectory() as folder:
            first = module.claim_dispatch(folder, 'inspect', {'token': 'one'})
            self.assertTrue(first.is_file())
            with self.assertRaises(FileExistsError):
                module.claim_dispatch(folder, 'inspect', {'token': 'two'})

    def test_worker_failure_keeps_native_diagnostic_before_output_save(self):
        self.assertTrue(MODULE.exists())
        module=load_transport()
        result={'token':'one','operation':'apply','status':'failed','project_path':'E:/work/input.aep','error':'Cannot change completed queue item'}
        with self.assertRaisesRegex(module.AEError,'Cannot change completed queue item'):
            module.validate_result(result,'one','apply','E:/work/output.aep')


if __name__ == '__main__':
    unittest.main()
