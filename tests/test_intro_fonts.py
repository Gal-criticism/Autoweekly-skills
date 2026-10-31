import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from fontTools.ttLib import TTFont, newTable

ROOT=Path(__file__).resolve().parents[1]/'weekly-intro/scripts/runtime'
sys.path.insert(0,str(ROOT))


class FontTests(unittest.TestCase):
    def test_postscript_font_identity_and_missing_dependency(self):
        self.assertTrue((ROOT/'intro/fonts.py').exists(),'font preflight not implemented')
        module=importlib.import_module('intro.fonts')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'unrelated-filename.ttf'
            font=TTFont(); names=newTable('name'); names.names=[]
            names.setName('ActualPostScript-Regular',6,3,1,0x409); font['name']=names; font.save(path); font.close()
            self.assertEqual(module.require_fonts(['ActualPostScript-Regular'],[path])['status'],'checked')
            with self.assertRaisesRegex(ValueError,'MissingPostScript'):
                module.require_fonts(['MissingPostScript'],[path])
        self.assertEqual(module.require_fonts([],[])['status'],'not_required')


if __name__=='__main__': unittest.main()
