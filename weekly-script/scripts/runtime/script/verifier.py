"""Verify exported weekly script deliverables for DOCX parser compatibility, style rules, and fact traceability."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from .parsers import (
    parse_editorial_draft_docx,
    read_docx_paragraphs,
    _is_section_label,
)
from .retriever import _issue_allowed

FORBIDDEN_AUTHOR_TAG_RE = re.compile(r"#[^#\s]{1,20}#")
JAPANESE_KANA_RUN_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]{12,}")
ROBOTIC_PHRASE_RE = re.compile(
    r"锐意制作中|敬请期待吧|为您带来|值得一提的是|相信会给玩家带来|令人期待不已|沉浸式体验|一场.*之旅"
)
REPEAT_VERB_RE = re.compile(r"(公开了|公布了|宣布了|发布了)[^。！？\n]{1,45}\1")
DATE_OPENER_RE = re.compile(r"^\s*(?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日[，,]")
COMPOUND_TITLE_RE = re.compile(r"[，,]|并公开|并宣布|并开启|同步公开|同步开启|同步释出")


def verify_run_deliverables(run_dir: Path | str) -> dict[str, Any]:
    """Run deterministic verification on a weekly-script run directory."""
    root = Path(run_dir)
    errors: list[str] = []
    warnings: list[str] = []

    plan_path = root / "draft_plan.json"
    docx_path = root / "周报初稿.docx"
    voice_path = root / "口播稿.md"
    review_path = root / "审阅清单.md"
    evidence_path = root / "evidence.json"
    run_meta_path = root / "run.json"

    for label, path in (
        ("draft_plan.json", plan_path),
        ("周报初稿.docx", docx_path),
        ("口播稿.md", voice_path),
        ("审阅清单.md", review_path),
        ("evidence.json", evidence_path),
    ):
        if not path.is_file():
            errors.append(f"缺少必需产物文件: {label}")

    if errors:
        return {"status": "failed", "errors": errors, "warnings": warnings}

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8")) if run_meta_path.is_file() else {}
    voice_text = voice_path.read_text(encoding="utf-8")
    review_text = review_path.read_text(encoding="utf-8")

    expected_entries = plan.get("entries", [])
    if not expected_entries:
        errors.append("draft_plan.json 中没有任何新闻条目")

    # 1. Verify DOCX paragraph formatting and DocxDocumentParser round-trip
    paragraphs = read_docx_paragraphs(docx_path)
    parsed_docx = parse_editorial_draft_docx(docx_path, issue_id=str(plan.get("issue_id", "")))

    if parsed_docx["entry_count"] != len(expected_entries):
        errors.append(
            f"DOCX 解析条数不匹配: 期望 {len(expected_entries)} 条，实际解析出 {parsed_docx['entry_count']} 条"
        )

    for idx, (exp, act) in enumerate(zip(expected_entries, parsed_docx["entries"]), start=1):
        if act["title"] != exp.get("title", "").strip():
            errors.append(f"第 {idx} 条标题不匹配: 期望 {exp.get('title')!r}，实际 {act['title']!r}")
        if act["section"] != exp.get("section", "新作"):
            errors.append(
                f"第 {idx} 条 ({act['title']}) 栏目不匹配: 期望 {exp.get('section')!r}，实际 {act['section']!r}"
            )
        if act["author"] is not None or act["title_style"] != "styled_15pt_bold":
            errors.append(f"第 {idx} 条 ({act['title']}) 使用了虚构 #作者# 标签或非 15磅加粗标题样式")
        exp_urls = [u.strip() for u in exp.get("source_urls", []) if u.strip()]
        if act["source_urls"] != exp_urls:
            errors.append(
                f"第 {idx} 条 ({act['title']}) 来源链接不匹配: 期望 {len(exp_urls)} 个，实际 {len(act['source_urls'])} 个"
            )

    # Check raw paragraph font sizes in DOCX
    for para in paragraphs[1:]:
        if _is_section_label(para.text):
            continue
        if para.font_sizes == frozenset({30}):
            if not para.all_runs_bold:
                errors.append(f"15磅标题未设置加粗: {para.text[:40]!r}")
        elif para.font_sizes == frozenset({22}):
            if para.all_runs_bold:
                errors.append(f"11磅正文错误地设置了全段加粗: {para.text[:40]!r}")
        else:
            errors.append(f"发现非标准字号段落 (sz={set(para.font_sizes)}): {para.text[:40]!r}")

    # 2. Verify clean 口播稿.md (no URLs, no #author# tags, all titles present cleanly)
    if re.search(r"https?://", voice_text, re.I):
        errors.append("口播稿.md 中残留了 URL 链接")
    if FORBIDDEN_AUTHOR_TAG_RE.search(voice_text):
        errors.append("口播稿.md 中残留了 #作者# 署名标签")
    for exp in expected_entries:
        t = exp.get("title", "").strip()
        if t and t not in voice_text:
            errors.append(f"口播稿.md 缺少新闻标题: {t}")

    # 3. Verify 审阅清单.md sections
    for required_heading in ("输入覆盖", "入选新闻", "待核实", "未入选"):
        if required_heading not in review_text:
            errors.append(f"审阅清单.md 缺少必要章节: {required_heading}")

    # 4. Verify evidence.json and holdout constraints
    holdout = run_meta.get("holdout_constraints") or plan.get("holdout_constraints") or {}
    max_issue = holdout.get("max_issue")
    exclude_issues = set(str(x) for x in holdout.get("exclude_issues", []))

    seen_status_ids: dict[str, str] = {}
    for idx, ev_entry in enumerate(evidence.get("entries", []), start=1):
        title = ev_entry.get("title", f"#{idx}")
        ev_type = ev_entry.get("event_type", "")
        urls = ev_entry.get("source_urls", [])
        facts = ev_entry.get("current_week_facts", [])

        if ev_type != "月度发售汇总" and not urls:
            errors.append(f"第 {idx} 条 ({title}) 缺少来源 URL")
        if not facts:
            errors.append(f"第 {idx} 条 ({title}) 在 evidence.json 中缺少 current_week_facts 事实记录")

        allow_shared = bool(plan["entries"][idx - 1].get("allow_shared_status_id")) if idx - 1 < len(plan["entries"]) else False
        for sid in ev_entry.get("source_status_ids", []):
            sid_str = str(sid).strip()
            if not sid_str:
                continue
            if sid_str in seen_status_ids and not allow_shared:
                errors.append(
                    f"重复报道同一推文 ID ({sid_str}): 同时出现在《{seen_status_ids[sid_str]}》与《{title}》"
                )
            seen_status_ids[sid_str] = title

        # Check holdout leakage in historical background or style examples
        for bg in ev_entry.get("historical_background_used", []):
            src_issue = bg.get("source_issue")
            if not _issue_allowed(src_issue, max_issue=max_issue, exclude_issues=exclude_issues):
                errors.append(
                    f"留出测试违规: 第 {idx} 条 ({title}) 引用了被屏蔽期次 ({src_issue}) 的历史背景"
                )
        for ex_id in ev_entry.get("style_examples_referenced", []):
            ex_issue = str(ex_id).split("-", 1)[0]
            if not _issue_allowed(ex_issue, max_issue=max_issue, exclude_issues=exclude_issues):
                errors.append(
                    f"留出测试违规: 第 {idx} 条 ({title}) 引用了被屏蔽期次 ({ex_id}) 的文风范例"
                )

    # 5. Check prose quality, "说人话" natural phrasing, and human draft alignment rules
    monthly_hint = run_meta.get("monthly_release_roundup_hint") or plan.get("monthly_release_roundup_hint") or {}
    if monthly_hint.get("applicable"):
        has_roundup = any(
            e.get("event_type") == "月度发售汇总" or "新作本周发售" in str(e.get("title", ""))
            for e in expected_entries
        )
        if not has_roundup:
            rec_title = monthly_hint.get("recommended_title", "X月新作本周发售")
            warnings.append(
                f"本期覆盖月末集中发售日（{monthly_hint.get('release_friday')}），但未发现《{rec_title}》汇总条目；建议在「新作」首条合并当周发售作品"
            )

    for exp in expected_entries:
        title = str(exp.get("title", "")).strip()
        paras = exp.get("body_paragraphs") or [exp.get("body", "")]
        joined_body = "\n".join(str(p) for p in paras)

        if COMPOUND_TITLE_RE.search(title):
            warnings.append(
                f"《{title}》标题包含逗号或双谓语并列（如“并公开/同步开启”），往期成稿偏好单句单重心短标题（如《作品名》体验版公开）"
            )
        if paras and DATE_OPENER_RE.match(str(paras[0])):
            warnings.append(
                f"《{title}》首段以具体日期（如“X月X日，”）起笔，往期成稿统一以“本周，{{品牌/社团}}...”或作品名起笔"
            )

        # Strip bracketed game titles 《...》 and known staff names before checking long Japanese kana runs
        stripped_brackets = re.sub(r"《[^》]+》|『[^』]+』|「[^」]+」|（[^）]+）|\([^)]+\)", "", joined_body)
        if JAPANESE_KANA_RUN_RE.search(stripped_brackets):
            warnings.append(f"《{title}》正文中可能存在较长的未翻译日文片段，请人工复核是否为专有名词")
        for p in paras:
            p_str = str(p)
            m_robot = ROBOTIC_PHRASE_RE.search(p_str)
            if m_robot:
                warnings.append(f"《{title}》正文包含生硬翻译腔或模板腔（{m_robot.group(0)}），建议改写成更自然的口播人话")
            m_rep = REPEAT_VERB_RE.search(p_str)
            if m_rep:
                warnings.append(f"《{title}》同一句内重复使用了“{m_rep.group(1)}”，建议替换动词避免啰嗦")
        if exp.get("event_type") == "预约与发售日" and "已正式发售" in joined_body and not re.search(r"原版|前作|PC版", joined_body):
            warnings.append(f"《{title}》为预约/发售日新闻，但正文中出现“已正式发售”，请核实是否混淆预售与发售")

    status = "verified" if not errors else "failed"
    report = {
        "status": status,
        "issue_id": str(plan.get("issue_id", "")),
        "entry_count": len(expected_entries),
        "docx_parsed_entry_count": parsed_docx["entry_count"],
        "sections_present": parsed_docx["sections"],
        "holdout_constraints_checked": holdout,
        "errors": errors,
        "warnings": warnings,
    }
    (root / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
