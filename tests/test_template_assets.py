"""Exercise both independently installable copies without network access."""

import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class TemplateAssetsTests(unittest.TestCase):
    def test_each_skill_downloader(self):
        for skill in ("weekly-cover", "weekly-bgm-overlay"):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as directory:
                spec = importlib.util.spec_from_file_location("assets", ROOT / skill / "scripts/runtime/template_assets.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                folder = Path(directory)
                payload = b"8BPS\x00\x01" + b"\x00" * 6 + struct.pack(">HIIHH", 3, 20, 40, 8, 3)
                module.ASSETS = {"test.psd": (len(payload), hashlib.sha256(payload).hexdigest(), (40, 20))}

                with patch.object(module, "urlopen", return_value=io.BytesIO(payload)) as network:
                    result = module.fetch("test.psd", folder)
                    self.assertEqual(result["status"], "downloaded")
                    self.assertEqual(module.fetch("test.psd", folder)["status"], "cached")
                    self.assertEqual(network.call_count, 1)
                self.assertEqual(list(folder.glob("*.part")), [])
                self.assertEqual((folder / "test.psd").read_bytes(), payload)

                (folder / "test.psd").write_bytes(b"user file")
                with patch.object(module, "urlopen") as network:
                    with self.assertRaises(ValueError):
                        module.fetch("test.psd", folder)
                    network.assert_not_called()
                self.assertEqual((folder / "test.psd").read_bytes(), b"user file")
                (folder / "test.psd").unlink()

                for broken in (payload[:-1], payload + b"extra", b"x" * len(payload)):
                    with patch.object(module, "urlopen", return_value=io.BytesIO(broken)):
                        with self.assertRaises(ValueError):
                            module.fetch("test.psd", folder)
                    self.assertFalse((folder / "test.psd").exists())
                    self.assertEqual(list(folder.glob("*.part")), [])

                module.ASSETS["test.psd"] = (len(payload), hashlib.sha256(payload).hexdigest(), (1, 1))
                with patch.object(module, "urlopen", return_value=io.BytesIO(payload)):
                    with self.assertRaisesRegex(ValueError, "canvas"):
                        module.fetch("test.psd", folder)

                with patch.dict(module.os.environ, {"AUTOWEEKLY_TEMPLATE_DIR": str(folder)}, clear=True):
                    self.assertEqual(module.template_path("CUSTOM", folder / "missing", "test.psd"), str(folder / "test.psd"))
                    legacy = folder / "legacy.psd"
                    legacy.write_bytes(payload)
                    self.assertEqual(module.template_path("CUSTOM", legacy, "test.psd"), str(legacy))
                    with patch.dict(module.os.environ, {"CUSTOM": "explicit.psd"}):
                        self.assertEqual(module.template_path("CUSTOM", legacy, "test.psd"), "explicit.psd")


if __name__ == "__main__":
    unittest.main()
