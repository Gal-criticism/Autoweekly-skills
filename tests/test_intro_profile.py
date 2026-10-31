import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'weekly-intro/scripts/runtime/intro/profile.py'


def fixture():
    scenes = []
    for index, name in enumerate(('第一镜头', '第二镜', '第三镜头', '第四镜头'), 10):
        scenes.append({'id': index, 'name': name, 'layers': [
            {'id': f'c{index}:l1', 'index': 1, 'name': '新闻图', 'source_id': 100+index, 'source_file': 'E:/图包/新闻.png', 'width': 1600, 'height': 900, 'has_audio': False, 'effects': []},
            {'id': f'c{index}:l2', 'index': 2, 'name': 'logo', 'source_id': 200+index, 'source_file': 'E:/母版/(素材)/logo/logo.png', 'width': 100, 'height': 100, 'has_audio': False, 'effects': []}]})
    scenes[-1]['layers'].append({'id': 'c13:l3', 'index': 3, 'name': '视频_底图_春a.png', 'source_id': 999, 'source_file': 'E:/视频_底图_春a.png', 'width': 2160, 'height': 1080, 'has_audio': False, 'effects': []})
    main = {'id': 1, 'name': '总合成', 'width': 2160, 'height': 1080, 'fps': 60, 'duration': 12, 'layers': [{'source_id': x['id'], 'has_audio': False, 'effects': []} for x in scenes]}
    queues = [{'index': i, 'comp_id': 1, 'comp_name': '总合成', 'done': i < 3, 'start': 0, 'duration': 12, 'settings': {'Quality': 'Best'}, 'modules': [{'index': 1, 'file': f'E:/{i}片头.mp4', 'settings': {'Format': 'Voukoder', 'Output Audio': 'Auto'}}]} for i in (1, 2, 3)]
    return {'comps': scenes+[main], 'queues': queues, 'missing': []}


class ProfileTests(unittest.TestCase):
    def module(self):
        self.assertTrue(MODULE.exists(), 'profile compiler has not been implemented')
        spec = importlib.util.spec_from_file_location('profile_test', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_only_news_and_one_season_bridge_are_replaceable(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as d:
            template = Path(d)/'master.aep'
            template.write_bytes(b'template')
            p = module.compile_profile(fixture(), template)
            self.assertEqual(len(p['slots']), 4)
            self.assertEqual(p['bridge']['source_id'], 999)
            self.assertFalse(any('logo' in x['source_file'] for x in p['slots']))
            self.assertEqual(p['reference_queue']['index'], 2)
            self.assertEqual(p['expected']['frames'], 720)
            self.assertIs(p['expected']['audio_expected'], False)

    def test_missing_media_or_multiple_bridges_stops_inspection(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as d:
            template = Path(d)/'master.aep'; template.write_bytes(b'template')
            raw = fixture(); raw['missing'] = [{'name': 'lost.png'}]
            with self.assertRaises(ValueError): module.compile_profile(raw, template)

    def test_disabled_images_and_text_do_not_require_new_assets(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as d:
            template=Path(d)/'master.aep'; template.write_bytes(b'template')
            raw=fixture()
            raw['comps'][0]['layers'].append(dict(raw['comps'][0]['layers'][0],id='disabled',enabled=False,font='MissingButDisabled'))
            p=module.compile_profile(raw,template)
            self.assertEqual(len(p['slots']),4)
            self.assertNotIn('MissingButDisabled',p['fonts'])
            raw = fixture(); raw['comps'][3]['layers'].append(dict(raw['comps'][3]['layers'][-1]))
            with self.assertRaises(ValueError): module.compile_profile(raw, template)


if __name__ == '__main__': unittest.main()
