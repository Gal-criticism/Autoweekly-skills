"""Export the four required deliverables: 周报初稿.docx, 口播稿.md, 审阅清单.md, and evidence.json."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape
import json
import re
import zipfile

SECTION_ORDER = ("新作", "汉化", "周边")

CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Calibri" w:eastAsia="微软雅黑" w:hAnsi="Calibri" w:cs="Calibri"/>
        <w:sz w:val="22"/>
        <w:szCs w:val="22"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
</w:styles>
"""


def _make_paragraph_xml(text: str, *, half_points: int, bold: bool = False, rel_id: str | None = None) -> str:
    escaped = xml_escape(text)
    b_xml = "<w:b/><w:bCs/>" if bold else ""
    rpr = (
        "<w:rPr>"
        '<w:rFonts w:ascii="Calibri" w:eastAsia="微软雅黑" w:hAnsi="Calibri" w:cs="Calibri"/>'
        f"{b_xml}"
        f'<w:sz w:val="{half_points}"/>'
        f'<w:szCs w:val="{half_points}"/>'
        "</w:rPr>"
    )
    run_xml = f"<w:r>{rpr}<w:t xml:space=\"preserve\">{escaped}</w:t></w:r>"
    if rel_id:
        return f'<w:p><w:hyperlink r:id="{rel_id}">{run_xml}</w:hyperlink></w:p>'
    return f"<w:p>{run_xml}</w:p>"


def write_editorial_docx(
    plan: dict[str, Any],
    output_path: Path | str,
) -> Path:
    """Generate 周报初稿.docx with 15pt bold (sz=30) titles and 11pt regular (sz=22) body/links."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    issue_id = str(plan.get("issue_id", "")).strip()
    date_range = str(plan.get("date_range", "")).strip()
    header_line = f"{issue_id}期 {date_range}".strip() if date_range else issue_id
    if not header_line:
        raise ValueError("draft_plan 缺少 issue_id")

    paragraphs_xml: list[str] = []
    relationships_xml: list[str] = [
        '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    ]
    rel_counter = 1

    # Top issue heading (26pt bold = sz 52)
    paragraphs_xml.append(_make_paragraph_xml(header_line, half_points=52, bold=True))

    subtitle = str(plan.get("subtitle", "")).strip()
    if subtitle:
        paragraphs_xml.append(_make_paragraph_xml(subtitle, half_points=22, bold=False))

    entries = plan.get("entries", [])
    by_section: dict[str, list[tuple[int, dict[str, Any]]]] = {sec: [] for sec in SECTION_ORDER}
    for idx, entry in enumerate(entries):
        sec = entry.get("section", "新作")
        if sec not in by_section:
            by_section[sec] = []
        by_section[sec].append((idx, entry))

    for section in SECTION_ORDER:
        sec_entries = by_section.get(section, [])
        if not sec_entries:
            continue
        # Section heading (16pt bold = sz 32)
        paragraphs_xml.append(_make_paragraph_xml(section, half_points=32, bold=True))

        for idx, entry in sec_entries:
            raw_title = str(entry.get("title", "")).strip()
            # Keep editorial DOCX title clean (no #author# tag, URL, or 【】) for galgame_news parser
            clean_title = re.sub(r"#[^#\n]+#?$", "", raw_title).strip()
            if not clean_title or "#" in clean_title or re.search(r"https?://", clean_title, re.I):
                raise ValueError(f"新闻标题不合规（不得包含 # 标签或链接）：{raw_title!r}")

            # News title: 15pt bold (sz=30, bold=True)
            paragraphs_xml.append(_make_paragraph_xml(clean_title, half_points=30, bold=True))

            # Body paragraphs: 11pt regular (sz=22, bold=False)
            body_paras = entry.get("body_paragraphs")
            if isinstance(body_paras, list) and body_paras:
                lines = [str(p).strip() for p in body_paras if str(p).strip()]
            else:
                raw_body = str(entry.get("body", "")).strip()
                lines = [ln.strip() for ln in raw_body.splitlines() if ln.strip()]
            if not lines:
                raise ValueError(f"新闻条目缺少正文段落：{clean_title}")

            for line in lines:
                paragraphs_xml.append(_make_paragraph_xml(line, half_points=22, bold=False))

            # Independent source URL paragraphs: 11pt regular (sz=22, bold=False)
            for raw_url in entry.get("source_urls", []):
                url = str(raw_url).strip()
                if not url:
                    continue
                rel_id = f"rIdUrl{rel_counter}"
                rel_counter += 1
                relationships_xml.append(
                    f'<Relationship Id="{rel_id}" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    f'Target="{xml_escape(url)}" TargetMode="External"/>'
                )
                paragraphs_xml.append(_make_paragraph_xml(url, half_points=22, bold=False, rel_id=rel_id))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        "  <w:body>\n    "
        + "\n    ".join(paragraphs_xml)
        + "\n    <w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/></w:sectPr>\n"
        "  </w:body>\n"
        "</w:document>\n"
    )

    doc_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n  '
        + "\n  ".join(relationships_xml)
        + "\n</Relationships>\n"
    )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", ROOT_RELS_XML)
        archive.writestr("word/document.xml", document_xml.encode("utf-8"))
        archive.writestr("word/_rels/document.xml.rels", doc_rels_xml.encode("utf-8"))
        archive.writestr("word/styles.xml", STYLES_XML.encode("utf-8"))

    return target


def write_voiceover_markdown(
    plan: dict[str, Any],
    output_path: Path | str,
) -> Path:
    """Generate clean 口播稿.md without URLs or editorial metadata (ready for weekly-ruby)."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    issue_id = str(plan.get("issue_id", "")).strip()
    date_range = str(plan.get("date_range", "")).strip()
    title_line = f"# 第 {issue_id} 期 Galgame 周报口播稿（{date_range}）" if date_range else f"# 第 {issue_id} 期 Galgame 周报口播稿"

    lines: list[str] = [title_line, ""]
    entries = plan.get("entries", [])
    by_section: dict[str, list[tuple[int, dict[str, Any]]]] = {sec: [] for sec in SECTION_ORDER}
    for idx, entry in enumerate(entries):
        sec = entry.get("section", "新作")
        by_section.setdefault(sec, []).append((idx, entry))

    for section in SECTION_ORDER:
        sec_entries = by_section.get(section, [])
        if not sec_entries:
            continue
        lines.append(f"## {section}")
        lines.append("")
        for _, entry in sec_entries:
            display_title = re.sub(r"#[^#\n]+#?$", "", str(entry.get("title", "")).strip()).strip()
            body_paras = entry.get("body_paragraphs")
            if isinstance(body_paras, list) and body_paras:
                paras = [str(p).strip() for p in body_paras if str(p).strip()]
            else:
                paras = [ln.strip() for ln in str(entry.get("body", "")).splitlines() if ln.strip()]
            lines.append(f"### {display_title}")
            lines.append("")
            for para in paras:
                no_url = re.sub(r"https?://\S+", "", para).strip()
                if no_url:
                    lines.append(no_url)
                    lines.append("")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def write_review_checklist(
    plan: dict[str, Any],
    input_audit: dict[str, Any] | None,
    output_path: Path | str,
) -> Path:
    """Generate 审阅清单.md covering input audit, selection rationale, unselected items, and pending verification."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    issue_id = str(plan.get("issue_id", "")).strip()
    date_range = str(plan.get("date_range", "")).strip()
    audit = input_audit or plan.get("input_audit", {})

    lines: list[str] = [
        f"# 第 {issue_id} 期周报审阅清单（{date_range}）",
        "",
        "## 一、输入覆盖与完整性检查",
        "",
        f"- **期号**：第 {issue_id} 期",
        f"- **报道范围**：{date_range}",
        f"- **覆盖状态**：`{audit.get('coverage_status', plan.get('coverage_status', 'ok'))}`",
        f"- **原始推文总数**：{audit.get('raw_tweet_count', 0)} 条（去重后 {audit.get('unique_tweet_count', 0)} 条，跨文件重复合并 {audit.get('cross_file_duplicates', 0)} 条）",
    ]

    empty_files = audit.get("title_only_files", [])
    if empty_files:
        lines.append(f"- **输入缺失警告（仅含标题的 X 汇总文件）**：{', '.join(f'`{f}`' for f in empty_files)}（已按输入缺失处理，未视为无新闻）")
    else:
        lines.append("- **空文件检查**：输入 X 汇总均包含有效推文记录，无仅含标题文件。")

    manual_files = audit.get("manual_files", [])
    if manual_files:
        lines.append(f"- **人工补充素材**：{', '.join(f'`{f}`' for f in manual_files)}")

    coverage_notes = plan.get("coverage_notes", [])
    for note in coverage_notes:
        lines.append(f"- **覆盖说明**：{note}")

    lines.extend(["", "## 二、入选新闻与选题依据", ""])
    entries = plan.get("entries", [])
    lines.append(f"本期共生成 **{len(entries)}** 条新闻（新作 {sum(1 for e in entries if e.get('section') == '新作')} 条 / 汉化 {sum(1 for e in entries if e.get('section') == '汉化')} 条 / 周边 {sum(1 for e in entries if e.get('section') == '周边')} 条）：")
    lines.append("")

    for idx, entry in enumerate(entries, start=1):
        sec = entry.get("section", "新作")
        title = entry.get("title", "")
        ev_type = entry.get("event_type", "未分类")
        reason = entry.get("selection_reason", "当周有效新进展")
        sids = entry.get("source_status_ids", [])
        omitted = entry.get("omitted_secondary_fields", [])
        lines.append(f"{idx}. **[{sec} | {ev_type}] {title}**")
        lines.append(f"   - 选题理由：{reason}")
        lines.append(f"   - 关联推文 ID：{', '.join(f'`{s}`' for s in sids) if sids else '补充来源/汇总'}")
        if omitted:
            lines.append(f"   - 已省略未核实次要字段：{', '.join(omitted)}")

    lines.extend(["", "## 三、待核实事项清单", ""])
    pending_items = plan.get("pending_verifications", [])
    if not pending_items:
        lines.append("- 暂无阻塞性待核实事项。所有正文中写出的日期、平台、价格及语言支持均已与来源核对。")
    else:
        for idx, item in enumerate(pending_items, start=1):
            topic = item.get("topic", "未命名事项")
            issue_desc = item.get("reason", "")
            urls = item.get("source_urls", [])
            lines.append(f"{idx}. **{topic}**：{issue_desc}")
            if urls:
                lines.append(f"   - 待核实来源：{', '.join(urls)}")

    lines.extend(["", "## 四、未入选事件与排除理由（供人工复核）", ""])
    excluded_items = plan.get("excluded_events", [])
    if not excluded_items:
        lines.append("- 无需要特别列出的候选排除事件。")
    else:
        for idx, item in enumerate(excluded_items, start=1):
            summary = item.get("summary", "")
            reason = item.get("reason", "无新增核心事实或属日常宣传")
            sids = item.get("status_ids", [])
            acc = item.get("account", "")
            prefix = f"`@{acc}` " if acc else ""
            sid_str = f"（推文 ID: {', '.join(f'`{s}`' for s in sids)}）" if sids else ""
            lines.append(f"{idx}. {prefix}{summary}{sid_str} —— **排除理由**：{reason}")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def write_evidence_json(
    plan: dict[str, Any],
    input_audit: dict[str, Any] | None,
    output_path: Path | str,
) -> Path:
    """Generate evidence.json with full fact traceability and historical reference records."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    evidence_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(plan.get("entries", []), start=1):
        evidence_entries.append(
            {
                "sequence": idx,
                "section": entry.get("section", "新作"),
                "title": entry.get("title", ""),
                "event_type": entry.get("event_type", ""),
                "selection_reason": entry.get("selection_reason", ""),
                "source_status_ids": entry.get("source_status_ids", []),
                "source_urls": entry.get("source_urls", []),
                "current_week_facts": entry.get("current_week_facts", []),
                "verified_fields": entry.get("verified_fields", {}),
                "omitted_secondary_fields": entry.get("omitted_secondary_fields", []),
                "historical_background_used": entry.get("historical_background_used", []),
                "style_examples_referenced": entry.get("style_examples_referenced", []),
            }
        )

    payload = {
        "schema_version": "1.0",
        "issue_id": str(plan.get("issue_id", "")),
        "date_range": str(plan.get("date_range", "")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_status": (input_audit or {}).get("coverage_status", plan.get("coverage_status", "ok")),
        "holdout_constraints": plan.get("holdout_constraints", {}),
        "entries": evidence_entries,
        "pending_verifications": plan.get("pending_verifications", []),
        "excluded_events_count": len(plan.get("excluded_events", [])),
    }

    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def export_all_deliverables(
    plan: dict[str, Any],
    run_dir: Path | str,
    input_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export all 4 required outputs into run_dir and return their resolved paths."""
    out_dir = Path(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    docx_path = write_editorial_docx(plan, out_dir / "周报初稿.docx")
    voice_path = write_voiceover_markdown(plan, out_dir / "口播稿.md")
    review_path = write_review_checklist(plan, input_audit, out_dir / "审阅清单.md")
    evidence_path = write_evidence_json(plan, input_audit, out_dir / "evidence.json")

    return {
        "docx": str(docx_path.resolve()),
        "voiceover_md": str(voice_path.resolve()),
        "review_md": str(review_path.resolve()),
        "evidence_json": str(evidence_path.resolve()),
    }
