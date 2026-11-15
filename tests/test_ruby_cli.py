import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

RUBY_RUNTIME = Path(__file__).resolve().parents[1] / "weekly-ruby/scripts/runtime"
SCRIPT_RUNTIME = Path(__file__).resolve().parents[1] / "weekly-script/scripts/runtime"
for p in (RUBY_RUNTIME, SCRIPT_RUNTIME):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from weekly_ruby import core as ruby_core  # noqa: E402
from weekly_ruby.cli import prepare_ruby_run, render_ruby_run, verify_ruby_run  # noqa: E402
from weekly_ruby.core import annotate_text, extract_jp_candidates  # noqa: E402
from script.exporter import CONTENT_TYPES_XML, ROOT_RELS_XML, STYLES_XML, _make_paragraph_xml, write_editorial_docx  # noqa: E402


class RubyCLITests(unittest.TestCase):
    def test_ruby_annotation_vndb_mock_local_kana_and_1to1_safety(self):
        sample = (
            "本周，キャベツソフト公开了《セレクトオブリージュ》的体验版，"
            "由綾瀬はづき负责原画，新島夕负责剧本，あざらしそふと协助制作。"
        )
        cands = extract_jp_candidates(sample)
        self.assertIn("セレクトオブリージュ", cands)
        self.assertIn("綾瀬はづき", cands)
        self.assertIn("あざらしそふと", cands)

        mock_vndb = {
            "hits": [
                {"surface": "セレクトオブリージュ", "romaji": "Select Oblige", "source": "vndb:vn:v1", "score": 100},
                {"surface": "綾瀬はづき", "romaji": "Ayase Hazuki", "source": "vndb:staff:s1", "score": 100},
            ],
            "misses": ["キャベツソフト", "あざらしそふと", "新島夕"],
            "service_error": False,
        }
        with patch.object(ruby_core, "query_vndb_romaji", return_value=mock_vndb):
            res = annotate_text(sample, use_vndb=True)

        self.assertTrue(res["ok"])
        ann = res["annotated"]
        self.assertIn("《セレクトオブリージュ》【Select Oblige】", ann)
        self.assertIn("綾瀬はづき【Ayase Hazuki】", ann)
        self.assertIn("あざらしそふと【Azarashisofuto】", ann)
        self.assertIn("キャベツソフト【Kyabetsusofuto】", ann)
        self.assertEqual(res["unresolved_misses"], [])
        self.assertEqual(re.sub(r"【[^】]*】", "", ann), sample)

    def test_ruby_skips_ascii_titles_and_annotates_between_guillemets(self):
        sample = (
            "### 《Summer Pockets Sunnyside Stories》公开片头动画\n"
            "本作片头曲《Luster piece》由新島夕作词。\n"
            "### 《アマカノ》再版决定\n"
            "本周，あざらしそふと宣布《アマカノ》的特别服装抱枕套将升级为ライクトロンリッチ材质进行再版。"
        )
        cands = extract_jp_candidates(sample)
        self.assertNotIn("Luster piece", cands)
        self.assertNotIn("Summer Pockets Sunnyside Stories", cands)
        self.assertIn("あざらしそふと", cands)
        self.assertIn("アマカノ", cands)
        self.assertIn("ライクトロンリッチ", cands)

        res = annotate_text(sample, use_vndb=False, extra_pairs={"Luster piece": "Luster Piece"})
        self.assertTrue(res["ok"])
        ann = res["annotated"]
        self.assertNotIn("【Luster Piece】", ann)
        self.assertIn("《Luster piece》", ann)
        self.assertIn("あざらしそふと【Azarashisofuto】", ann)
        self.assertIn("《アマカノ》【Amakano】", ann)
        self.assertIn("ライクトロンリッチ【Raikutoronricchi】", ann)

    def test_prepare_render_verify_from_editorial_docx_and_docx_export(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            plan = {
                "issue_id": "267",
                "date_range": "2026-10-24~2026-10-29",
                "entries": [
                    {
                        "section": "新作",
                        "title": "《月に寄りそう乙女の作法3》制作决定",
                        "body_paragraphs": [
                            "本周，Navel宣布新作《月に寄りそう乙女の作法3》决定制作，本作与あざらしそふと无关。"
                        ],
                        "source_urls": ["https://x.com/navel_official/status/123456"],
                    },
                    {
                        "section": "周边",
                        "title": "《アマカノ》再版决定",
                        "body_paragraphs": [
                            "官方宣布抱枕套采用ライクトロンリッチ材质再版。"
                        ],
                        "source_urls": ["https://example.com/merch"],
                    },
                ],
            }
            editorial_docx = write_editorial_docx(plan, root / "周报初稿.docx")
            prep = prepare_ruby_run(
                input_path=editorial_docx,
                output_root=root / "ruby_runs",
                use_vndb=False,
            )
            self.assertEqual(prep["status"], "prepared")
            self.assertEqual(prep["source_type"], "docx")
            self.assertEqual(prep["entry_count"], 2)
            # Mixed kanji+kana title should appear in unresolved_misses
            self.assertIn("月に寄りそう乙女の作法3", prep["unresolved_misses"])

            run_dir = Path(prep["run_dir"])
            (run_dir / "ruby_pairs.json").write_text(
                json.dumps({"月に寄りそう乙女の作法3": "Tsuki ni Yorisou Otome no Sahou 3"}, ensure_ascii=False),
                encoding="utf-8",
            )

            ren = render_ruby_run(run_dir, use_vndb=False, export_docx=True)
            self.assertEqual(ren["status"], "verified")
            self.assertEqual(ren["verification"]["errors"], [])
            self.assertEqual(ren["ruby_summary"]["unresolved_misses"], [])

            voice_md = (run_dir / "口播注音稿.md").read_text(encoding="utf-8")
            self.assertNotIn("https://", voice_md)
            self.assertIn("《月に寄りそう乙女の作法3》【Tsuki ni Yorisou Otome no Sahou 3】", voice_md)
            self.assertIn("あざらしそふと【Azarashisofuto】", voice_md)
            self.assertIn("《アマカノ》【Amakano】", voice_md)
            self.assertIn("ライクトロンリッチ【Raikutoronricchi】", voice_md)
            self.assertTrue((run_dir / "口播注音版.docx").is_file())

            with self.assertRaisesRegex(ValueError, "verified"):
                render_ruby_run(run_dir, use_vndb=False)

    def test_manual_hash_author_docx_and_markdown_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manual_docx = root / "manual.docx"
            paras = [
                "第268期 周报",
                "新作",
                "《テスト作》情报公开 #编辑甲#",
                "本周，あざらしそふと公布了新消息。 https://example.com/news",
            ]
            p_xml = "\n    ".join(_make_paragraph_xml(p, half_points=22, bold=False) for p in paras)
            doc_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
                f"  <w:body>\n    {p_xml}\n  </w:body>\n</w:document>\n"
            )
            rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>\n'
            )
            with zipfile.ZipFile(manual_docx, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
                z.writestr("_rels/.rels", ROOT_RELS_XML)
                z.writestr("word/document.xml", doc_xml.encode("utf-8"))
                z.writestr("word/_rels/document.xml.rels", rels_xml.encode("utf-8"))
                z.writestr("word/styles.xml", STYLES_XML.encode("utf-8"))

            prep = prepare_ruby_run(input_path=manual_docx, output_root=root / "runs", use_vndb=False)
            run_dir = Path(prep["run_dir"])
            (run_dir / "ruby_pairs.json").write_text(
                json.dumps({"テスト作": "Tesuto Saku"}, ensure_ascii=False),
                encoding="utf-8",
            )
            ren = render_ruby_run(run_dir, use_vndb=False)
            self.assertEqual(ren["status"], "verified")
            voice_md = (run_dir / "口播注音稿.md").read_text(encoding="utf-8")
            self.assertNotIn("#编辑甲#", voice_md)
            self.assertNotIn("https://example.com", voice_md)
            self.assertIn("### 《テスト作》【Tesuto Saku】情报公开", voice_md)
            self.assertIn("あざらしそふと【Azarashisofuto】", voice_md)

            # Plain markdown input also works
            md_in = root / "voice.md"
            md_in.write_text("# 口播稿\n\n## 新作\n\n### 《アマカノ》更新\n\n本周あざらしそふと更新了官网。\n", encoding="utf-8")
            prep_md = prepare_ruby_run(input_path=md_in, output_root=root / "runs_md", use_vndb=False)
            ren_md = render_ruby_run(Path(prep_md["run_dir"]), use_vndb=False)
            self.assertEqual(ren_md["status"], "verified")
            ver = verify_ruby_run(Path(prep_md["run_dir"]))
            self.assertEqual(ver["status"], "verified")


if __name__ == "__main__":
    unittest.main()
