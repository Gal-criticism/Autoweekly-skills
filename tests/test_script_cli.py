import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

SKILL_RUNTIME = Path(__file__).resolve().parents[1] / "weekly-script/scripts/runtime"
if str(SKILL_RUNTIME) not in sys.path:
    sys.path.insert(0, str(SKILL_RUNTIME))

from script.cli import prepare_run, render_run
from script.exporter import write_editorial_docx
from script.indexer import build_indices
from script.parsers import parse_x_summary_docx, parse_editorial_draft_docx
from script.retriever import retrieve_context
from script.verifier import verify_run_deliverables


def _create_mock_x_docx(path: Path, paragraphs: list[str]) -> Path:
    from script.exporter import CONTENT_TYPES_XML, ROOT_RELS_XML, STYLES_XML, _make_paragraph_xml

    p_xml = "\n    ".join(_make_paragraph_xml(p, half_points=22, bold=False) for p in paragraphs)
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        f"  <w:body>\n    {p_xml}\n  </w:body>\n</w:document>\n"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>\n'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        z.writestr("_rels/.rels", ROOT_RELS_XML)
        z.writestr("word/document.xml", doc_xml.encode("utf-8"))
        z.writestr("word/_rels/document.xml.rels", rels_xml.encode("utf-8"))
        z.writestr("word/styles.xml", STYLES_XML.encode("utf-8"))
    return path


class ScriptCLITests(unittest.TestCase):
    def test_ambiguous_issue_or_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            x_file = _create_mock_x_docx(root / "a.docx", ["Auto-X-tweets(2026-10-24~2026-10-29)"])
            with self.assertRaisesRegex(ValueError, "期号"):
                prepare_run(
                    issue_id="X",
                    date_range="2026-10-24~2026-10-30",
                    x_docx_paths=[x_file],
                    manual_docx_paths=[],
                    output_root=root / "runs",
                )
            with self.assertRaisesRegex(ValueError, "起止日期"):
                prepare_run(
                    issue_id="267",
                    date_range="X.X-X.X",
                    x_docx_paths=[x_file],
                    manual_docx_paths=[],
                    output_root=root / "runs",
                )

    def test_title_only_input_blocks_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            empty_x = _create_mock_x_docx(root / "empty.docx", ["Auto-X-tweets(2026-11-12~2026-11-13)"])
            res = prepare_run(
                issue_id="269",
                date_range="2026-11-07~2026-11-13",
                x_docx_paths=[empty_x],
                manual_docx_paths=[],
                output_root=root / "runs",
            )
            self.assertEqual(res["status"], "empty_input_blocked")
            self.assertEqual(res["coverage_status"], "empty_input_blocked")
            run_dir = Path(res["run_dir"])
            with self.assertRaisesRegex(ValueError, "输入为空"):
                render_run(run_dir)

    def test_multi_tweet_split_dedup_and_flags(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stuck_para = (
                "AirVelo2026---2026-10-29 12:00:00\n类型: 原创推文\n"
                "AirVelocity最新作『ハッピーウィークエンド』Switch版・PS4版の新サンプルCGと体験版が初公開！ https://int-creative.com/air/weekend/ "
                "(link):https://twitter.com/AirVelo2026/status/2115788882256257449"
                "circus_info---2026-10-28 10:00:00\n类型: 转发推文\n原推文发布时间: 2026-10-01 10:00:00\n"
                "旧聞リポストテストです (link):https://twitter.com/circus_info/status/2100000000000000001"
            )
            img_only_para = (
                "some_brand---2026-10-29 15:00:00\n类型: 原创推文\n"
                "更新！ https://t.co/abc (link):https://twitter.com/some_brand/status/2115999999999999999"
            )
            x1 = _create_mock_x_docx(root / "x1.docx", ["Auto-X-tweets(2026-10-24~2026-10-29)", stuck_para, img_only_para])
            # Second file has duplicate status_id 2115788882256257449 and one empty file
            dup_para = (
                "AirVelo2026---2026-10-29 12:00:00\n类型: 原创推文\n"
                "AirVelocity最新作『ハッピーウィークエンド』Switch版・PS4版の新サンプルCGと体験版が初公開！ "
                "(link):https://twitter.com/AirVelo2026/status/2115788882256257449"
            )
            x2 = _create_mock_x_docx(root / "x2.docx", ["Auto-X-tweets(2026-10-29~2026-10-30)", dup_para])
            x_empty = _create_mock_x_docx(root / "x3_empty.docx", ["Auto-X-tweets(2026-10-30~2026-10-31)"])

            res = prepare_run(
                issue_id="267",
                date_range="2026-10-24~2026-10-30",
                x_docx_paths=[x1, x2, x_empty],
                manual_docx_paths=[],
                output_root=root / "runs",
                max_issue=266,
                exclude_issues=["267"],
            )
            self.assertEqual(res["status"], "prepared")
            self.assertEqual(res["coverage_status"], "partial_input_missing")
            self.assertEqual(res["input_audit"]["raw_tweet_count"], 4)
            self.assertEqual(res["input_audit"]["unique_tweet_count"], 3)
            self.assertEqual(res["input_audit"]["cross_file_duplicates"], 1)
            self.assertEqual(res["input_audit"]["title_only_files"], ["x3_empty.docx"])

            parsed = json.loads((Path(res["run_dir"]) / "parsed_tweets.json").read_text(encoding="utf-8"))
            by_sid = {t["status_id"]: t for t in parsed["tweets"]}
            self.assertEqual(by_sid["2115788882256257449"]["source_files"], ["x1.docx", "x2.docx"])
            self.assertTrue(by_sid["2100000000000000001"]["flags"]["old_retweet_hint"])
            self.assertEqual(by_sid["2100000000000000001"]["signal"], "old_retweet_warning")
            self.assertTrue(by_sid["2115999999999999999"]["flags"]["image_only_hint"])
            self.assertEqual(by_sid["2115999999999999999"]["signal"], "image_only_warning")

    def test_export_verify_and_holdout_protection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            x1 = _create_mock_x_docx(
                root / "x1.docx",
                [
                    "Auto-X-tweets(2026-10-24~2026-10-30)",
                    "AirVelo2026---2026-10-29 12:00:00\n类型: 原创推文\n『ハッピーウィークエンド』体験版公開 (link):https://twitter.com/AirVelo2026/status/2115788882256257449",
                    "shiravune---2026-10-29 13:00:00\n类型: 原创推文\nSteam store page open (link):https://twitter.com/shiravune/status/2115609834469113960",
                ],
            )
            res = prepare_run(
                issue_id="267",
                date_range="2026-10-24~2026-10-30",
                x_docx_paths=[x1],
                manual_docx_paths=[],
                output_root=root / "runs",
                max_issue=266,
                exclude_issues=["267"],
            )
            run_dir = Path(res["run_dir"])

            valid_plan = {
                "issue_id": "267",
                "date_range": "2026-10-24~2026-10-30",
                "holdout_constraints": {"max_issue": 266, "exclude_issues": ["267"]},
                "entries": [
                    {
                        "section": "新作",
                        "title": "《Happy Weekend》体验版公开",
                        "event_type": "体验版",
                        "selection_reason": "公布主机版新CG与体验版及预约折扣",
                        "body_paragraphs": [
                            "本周，AirVelocity公开了《Happy Weekend》主机版的全新CG，并同步放出了游戏的体验版。",
                            "本作原版是2025年6月27日发售的PC游戏，本次为登陆Switch与PS4的移植版，由綾瀬はづき负责原画，内藤自由、モーリー、さんざんまん负责剧本。游戏预定于2026年11月26日发售，感兴趣的玩家可以前往官网了解详情。",
                        ],
                        "source_status_ids": ["2115788882256257449"],
                        "source_urls": [
                            "https://int-creative.com/air/weekend/",
                            "https://x.com/AirVelo2026/status/2115788882256257449",
                        ],
                        "current_week_facts": ["公开主机版新CG与体验版"],
                        "verified_fields": {"platform": "Switch / PS4", "release_date": "2026-11-26"},
                        "omitted_secondary_fields": [],
                        "historical_background_used": [{"source_issue": "259", "work_name": "Happy Weekend"}],
                        "style_examples_referenced": ["261-04"],
                    },
                    {
                        "section": "汉化",
                        "title": "《公爵千金的家庭教师》Steam商店页面公开",
                        "event_type": "汉化与海外发行",
                        "selection_reason": "Shiravune 公开 Steam 商店页面",
                        "body_paragraphs": [
                            "本周，Shiravune公开了《公爵千金的家庭教师》的Steam商店页面，游戏支持简繁中文，感兴趣的玩家可以前往Steam添加愿望单。"
                        ],
                        "source_status_ids": ["2115609834469113960"],
                        "source_urls": [
                            "https://store.steampowered.com/app/4463810",
                            "https://x.com/shiravune/status/2115609834469113960",
                        ],
                        "current_week_facts": ["公开Steam商店页面"],
                        "verified_fields": {"platform": "Steam", "language": "简繁中文"},
                        "omitted_secondary_fields": ["具体售价"],
                        "historical_background_used": [],
                        "style_examples_referenced": ["261-10"],
                    },
                ],
                "pending_verifications": [],
                "excluded_events": [
                    {
                        "account": "whisp_news",
                        "status_ids": ["2110000000000000000"],
                        "summary": "日常抽奖宣传",
                        "reason": "无新增作品情报",
                    }
                ],
            }
            (run_dir / "draft_plan.json").write_text(json.dumps(valid_plan, ensure_ascii=False, indent=2), encoding="utf-8")
            render_res = render_run(run_dir)
            self.assertEqual(render_res["status"], "verified")
            self.assertEqual(render_res["verification"]["errors"], [])

            # Check compatibility with galgame_news DocxDocumentParser if present on machine
            galgame_src = Path(r"E:\project\galgame news\src")
            if galgame_src.is_dir():
                if str(galgame_src) not in sys.path:
                    sys.path.insert(0, str(galgame_src))
                from galgame_news.ingestion.docx_parser import DocxDocumentParser

                parsed_draft = DocxDocumentParser().parse(run_dir / "周报初稿.docx", "267")
                self.assertEqual(len(parsed_draft.entries), 2)
                self.assertEqual(parsed_draft.entries[0].section, "新作")
                self.assertEqual(parsed_draft.entries[0].title, "《Happy Weekend》体验版公开")
                self.assertIsNone(parsed_draft.entries[0].author)
                self.assertEqual(parsed_draft.entries[1].section, "汉化")

            # Now test holdout violation detection (referencing issue 267 in historical_background_used)
            bad_plan = json.loads(json.dumps(valid_plan))
            bad_plan["entries"][0]["historical_background_used"].append({"source_issue": "267", "work_name": "Leak"})
            (run_dir / "draft_plan.json").write_text(json.dumps(bad_plan, ensure_ascii=False, indent=2), encoding="utf-8")
            bad_res = render_run(run_dir, force=True)
            self.assertEqual(bad_res["status"], "exported_with_errors")
            self.assertTrue(any("留出测试违规" in err for err in bad_res["verification"]["errors"]))

    def test_real_corpus_index_alignment_and_holdout_retrieval(self):
        reports_dir = Path(r"E:\project\周报图片设计\weekly_reports")
        drafts_dir = Path(r"E:\project\galgame news\input")
        if not (reports_dir.is_dir() and drafts_dir.is_dir()):
            self.skipTest("Local corpus directories not present")

        index_dir = Path(r"E:\project\周报图片设计\script_runs\index")
        if not (index_dir / "selection_index.json").is_file():
            build_indices(reports_dir, drafts_dir, index_dir)

        sel = json.loads((index_dir / "selection_index.json").read_text(encoding="utf-8"))
        self.assertEqual(sel["summary"]["total_draft_issues"], 23)
        self.assertEqual(sel["summary"]["total_x_files"], 25)
        self.assertEqual(sel["summary"]["title_only_x_files_count"], 8)

        by_issue = {item["issue_id"]: item for item in sel["issues"]}
        # Issue 252 alignment check
        self.assertEqual(by_issue["252"]["aligned_manual_file"], "Galgame_252_X.X-X.X_.docx")
        self.assertEqual(by_issue["252"]["aligned_manual_overlap"], 20)
        # Issue 267 alignment check: 25 status IDs all in Galgame_X_X.X-X.X_83.docx, 16 in X summaries
        self.assertEqual(by_issue["267"]["aligned_manual_file"], "Galgame_X_X.X-X.X_83.docx")
        self.assertEqual(by_issue["267"]["aligned_manual_overlap"], 25)
        self.assertEqual(by_issue["267"]["x_direct_matched_status_count"], 16)

        # Holdout retrieval check for 263 and 267
        ret_263 = retrieve_context(index_dir, query="Summer Pockets", max_issue=262, exclude_issues=["263", "267"])
        for w in ret_263["matched_works"]:
            for app in w["appearances"]:
                self.assertLessEqual(int(app["issue_id"]), 262)
        for ex in ret_263["style_examples"]:
            self.assertLessEqual(int(ex["issue_id"]), 262)

    def test_prose_lint_warns_on_robotic_calque_and_repeat_verb(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            x1 = _create_mock_x_docx(
                root / "x1.docx",
                [
                    "Auto-X-tweets(2026-10-24~2026-10-30)",
                    "key_official---2026-10-29 12:00:00\n类型: 原创推文\n『テスト作』情報 (link):https://x.com/key_official/status/2116000000000000001",
                ],
            )
            res = prepare_run(
                issue_id="268",
                date_range="2026-10-31~2026-11-06",
                x_docx_paths=[x1],
                manual_docx_paths=[],
                output_root=root / "runs",
            )
            run_dir = Path(res["run_dir"])
            plan = {
                "issue_id": "268",
                "date_range": "2026-10-31~2026-11-06",
                "entries": [
                    {
                        "section": "新作",
                        "title": "《テスト作》情报更新",
                        "event_type": "官网更新",
                        "body_paragraphs": [
                            "本周，あざらしそふと公开了新作情报，并公开了新CG。目前游戏正在锐意制作中，敬请期待吧！"
                        ],
                        "source_status_ids": ["2116000000000000001"],
                        "source_urls": ["https://x.com/key_official/status/2116000000000000001"],
                        "current_week_facts": ["公开新CG"],
                    }
                ],
            }
            (run_dir / "draft_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            render_res = render_run(run_dir)
            self.assertEqual(render_res["status"], "verified")
            warnings = render_res["verification"]["warnings"]
            self.assertTrue(any("生硬翻译腔" in w for w in warnings))
            self.assertTrue(any("重复使用了" in w for w in warnings))
            voice_md = (run_dir / "口播稿.md").read_text(encoding="utf-8")
            # Clean base voiceover script has no ruby annotations and no URLs
            self.assertNotIn("【", voice_md)
            self.assertNotIn("https://", voice_md)
            self.assertIn("《テスト作》情报更新", voice_md)

    def test_signal_classifier_and_monthly_release_and_title_date_lints(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            x1 = _create_mock_x_docx(
                root / "x_month_end.docx",
                [
                    "Auto-X-tweets(2026-09-19~2026-09-25)",
                    "KagamiGames---2026-09-23 12:00:00\n类型: 原创推文\nThe wait is over! Hooksoft's latest title, Triple Pairing, is now available in English and Chinese with a 22% launch discount! (link):https://x.com/KagamiGames/status/2103363878583177341",
                    "figsoku---2026-09-20 12:00:00\n类型: 原创推文\n【Summer Pockets】「空門蒼」フィギュア化決定！ (link):https://x.com/figsoku/status/2095798056801403192",
                    "FANZA_Kuji---2026-09-21 12:00:00\n类型: 原创推文\n『遺作』オンラインくじ引き開始！ (link):https://x.com/FANZA_Kuji/status/2100600664879398915",
                    "will_ensemble---2026-09-22 12:00:00\n类型: 原创推文\n【天獄のヴィネア】第9回プレゼントキャンペーン＆フォロー＆リポスト開始！ギャラリーにCGを2枚追加しました。 (link):https://x.com/will_ensemble/status/2100846950665142362",
                ],
            )
            res = prepare_run(
                issue_id="262",
                date_range="2026-09-19~2026-09-25",
                x_docx_paths=[x1],
                manual_docx_paths=[],
                output_root=root / "runs",
            )
            self.assertTrue(res["input_audit"]["monthly_release_roundup_hint"]["applicable"])
            self.assertEqual(res["input_audit"]["monthly_release_roundup_hint"]["recommended_title"], "九月新作本周发售")
            self.assertEqual(res["input_audit"]["signal_breakdown"]["candidate_news"], 4)

            run_dir = Path(res["run_dir"])
            plan = {
                "issue_id": "262",
                "date_range": "2026-09-19~2026-09-25",
                "entries": [
                    {
                        "section": "新作",
                        "title": "《测试作》官网公开并宣布开启预约，同步释出新CG",
                        "event_type": "官网更新",
                        "body_paragraphs": [
                            "9月23日，某品牌公开了新作官网。感兴趣的玩家可以前往官网了解详情。"
                        ],
                        "source_status_ids": ["2103363878583177341"],
                        "source_urls": ["https://x.com/KagamiGames/status/2103363878583177341"],
                        "current_week_facts": ["公开官网"],
                    }
                ],
            }
            (run_dir / "draft_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            render_res = render_run(run_dir)
            warnings = render_res["verification"]["warnings"]
            self.assertTrue(any("九月新作本周发售" in w for w in warnings))
            self.assertTrue(any("单句单重心短标题" in w for w in warnings))
            self.assertTrue(any("首段以具体日期" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
