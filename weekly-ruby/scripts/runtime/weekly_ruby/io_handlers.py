"""Input loaders (.docx, .md, .txt, draft_plan.json) and output writers for weekly-ruby."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
import json
import re
import zipfile

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

SECTION_LABELS = {
    "新作", "旧作", "舊作", "动画", "動畫", "周边", "周邊", "业界", "業界", "其他", "其它", "汉化", "漢化",
}
HEADING_HASH_RE = re.compile(r"^\s*(?P<title>[^#\n]+?)\s*#\s*[^#\n]+?\s*#?\s*$")
URL_ONLY_RE = re.compile(r"^(?:https?://\S+\s*)+$", re.IGNORECASE)
INLINE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
AUTHOR_TAG_SUFFIX_RE = re.compile(r"\s*#[^#\n]{1,24}#?\s*$")
EXPLICIT_ISSUE_RE = re.compile(r"^(?:第\s*)?\d+\s*期")

CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.relationships+xml"/>
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


def _strip_urls_and_tags(text: str, *, strip_urls: bool) -> str:
    val = AUTHOR_TAG_SUFFIX_RE.sub("", (text or "").strip()).strip()
    if strip_urls:
        val = INLINE_URL_RE.sub("", val).strip()
    return val


def _read_docx_raw(path: Path) -> list[dict[str, Any]]:
    try:
        with zipfile.ZipFile(path) as z:
            xml_bytes = z.read("word/document.xml")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"无法读取 DOCX ({path}): {exc}") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"DOCX XML 解析失败 ({path}): {exc}") from exc

    paragraphs: list[dict[str, Any]] = []
    for p in root.iter(WORD_NS + "p"):
        pieces: list[str] = []
        for node in p.iter():
            if node.tag == WORD_NS + "t":
                pieces.append(node.text or "")
            elif node.tag == WORD_NS + "tab":
                pieces.append("\t")
            elif node.tag in {WORD_NS + "br", WORD_NS + "cr"}:
                pieces.append("\n")
        text = "".join(pieces).strip()
        if not text:
            continue

        p_style = ""
        p_pr = p.find(WORD_NS + "pPr")
        if p_pr is not None:
            ps = p_pr.find(WORD_NS + "pStyle")
            if ps is not None:
                p_style = (ps.get(WORD_NS + "val") or "").strip()

        font_sizes: set[int] = set()
        non_empty_runs = 0
        bold_runs = 0
        for run in p.iter(WORD_NS + "r"):
            run_text = "".join((t.text or "") for t in run.iter(WORD_NS + "t"))
            if not run_text.strip():
                continue
            non_empty_runs += 1
            r_pr = run.find(WORD_NS + "rPr")
            is_bold = False
            if r_pr is not None:
                b_el = r_pr.find(WORD_NS + "b")
                if b_el is not None and (b_el.get(WORD_NS + "val") or "true").lower() not in {"0", "false"}:
                    is_bold = True
                for sz_el in r_pr.findall(WORD_NS + "sz"):
                    val = sz_el.get(WORD_NS + "val")
                    if val and val.isdigit():
                        font_sizes.add(int(val))
            if is_bold:
                bold_runs += 1

        paragraphs.append(
            {
                "text": text,
                "p_style": p_style,
                "font_sizes": font_sizes,
                "all_runs_bold": non_empty_runs > 0 and bold_runs == non_empty_runs,
            }
        )
    return paragraphs


def _load_docx_document(path: Path, *, strip_urls: bool) -> dict[str, Any]:
    raw_paras = _read_docx_raw(path)
    doc_title = ""
    entries: list[dict[str, Any]] = []
    current_section = ""
    current_entry: dict[str, Any] | None = None

    for idx, para in enumerate(raw_paras):
        raw_text = para["text"]
        if strip_urls and URL_ONLY_RE.match(raw_text):
            continue

        sizes = para["font_sizes"]
        all_bold = para["all_runs_bold"]
        p_style = para["p_style"].lower()

        if idx == 0 and (EXPLICIT_ISSUE_RE.match(raw_text) or (sizes and max(sizes) >= 44)):
            doc_title = raw_text
            continue

        norm_label = raw_text.strip().strip(":： ")
        if norm_label in SECTION_LABELS and (len(norm_label) <= 4):
            if current_entry is not None:
                entries.append(current_entry)
                current_entry = None
            current_section = norm_label
            continue

        is_title = False
        if sizes == {30} and all_bold:
            is_title = True
        elif HEADING_HASH_RE.match(raw_text):
            is_title = True
        elif p_style in {"heading1", "heading2", "heading 1", "heading 2", "1", "2"}:
            is_title = True

        if is_title:
            if current_entry is not None:
                entries.append(current_entry)
            m_hash = HEADING_HASH_RE.match(raw_text)
            clean_title = (m_hash.group("title") if m_hash else raw_text).strip()
            clean_title = _strip_urls_and_tags(clean_title, strip_urls=strip_urls)
            current_entry = {
                "section": current_section,
                "title": clean_title,
                "body_paragraphs": [],
            }
            continue

        cleaned = _strip_urls_and_tags(raw_text, strip_urls=strip_urls)
        if not cleaned:
            continue
        if current_entry is None:
            current_entry = {
                "section": current_section,
                "title": "",
                "body_paragraphs": [],
            }
        current_entry["body_paragraphs"].append(cleaned)

    if current_entry is not None:
        entries.append(current_entry)

    return {
        "source_path": str(path.resolve()),
        "source_type": "docx",
        "doc_title": doc_title or path.stem,
        "entries": [e for e in entries if e["title"] or e["body_paragraphs"]],
        "ruby_pairs": {},
    }


def _load_markdown_or_text(path: Path, *, strip_urls: bool) -> dict[str, Any]:
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    doc_title = ""
    entries: list[dict[str, Any]] = []
    current_section = ""
    current_entry: dict[str, Any] | None = None
    para_buf: list[str] = []

    def flush_para() -> None:
        nonlocal current_entry
        if not para_buf:
            return
        joined = "\n".join(para_buf).strip()
        para_buf.clear()
        cleaned = _strip_urls_and_tags(joined, strip_urls=strip_urls)
        if not cleaned:
            return
        if current_entry is None:
            current_entry = {
                "section": current_section,
                "title": "",
                "body_paragraphs": [],
            }
        current_entry["body_paragraphs"].append(cleaned)

    for line in raw_lines:
        s = line.strip()
        if not s:
            flush_para()
            continue
        if strip_urls and URL_ONLY_RE.match(s):
            continue
        if s.startswith("# ") and not doc_title and current_entry is None and not entries:
            doc_title = s[2:].strip()
            continue
        if s.startswith("## "):
            flush_para()
            if current_entry is not None:
                entries.append(current_entry)
                current_entry = None
            current_section = s[3:].strip()
            continue
        if s.startswith("### "):
            flush_para()
            if current_entry is not None:
                entries.append(current_entry)
            title = _strip_urls_and_tags(s[4:].strip(), strip_urls=strip_urls)
            current_entry = {
                "section": current_section,
                "title": title,
                "body_paragraphs": [],
            }
            continue
        para_buf.append(s)

    flush_para()
    if current_entry is not None:
        entries.append(current_entry)

    return {
        "source_path": str(path.resolve()),
        "source_type": "markdown" if path.suffix.lower() == ".md" else "text",
        "doc_title": doc_title or path.stem,
        "entries": [e for e in entries if e["title"] or e["body_paragraphs"]],
        "ruby_pairs": {},
    }


def _load_draft_plan_json(path: Path, *, strip_urls: bool) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or "entries" not in plan:
        raise ValueError(f"JSON 文件不是有效的 draft_plan.json（缺少 entries）：{path}")

    issue_id = str(plan.get("issue_id", "")).strip()
    date_range = str(plan.get("date_range", "")).strip()
    if issue_id and date_range:
        doc_title = f"第 {issue_id} 期 Galgame 周报口播稿（{date_range}）"
    elif issue_id:
        doc_title = f"第 {issue_id} 期 Galgame 周报口播稿"
    else:
        doc_title = path.stem

    merged_pairs: dict[str, str] = {}
    if isinstance(plan.get("ruby_pairs"), dict):
        merged_pairs.update({str(k): str(v) for k, v in plan["ruby_pairs"].items()})

    entries: list[dict[str, Any]] = []
    for entry in plan.get("entries", []):
        if isinstance(entry.get("ruby_pairs"), dict):
            merged_pairs.update({str(k): str(v) for k, v in entry["ruby_pairs"].items()})
        section = str(entry.get("section", "新作")).strip()
        title = _strip_urls_and_tags(str(entry.get("title", "")).strip(), strip_urls=strip_urls)
        body_paras = entry.get("body_paragraphs")
        if isinstance(body_paras, list) and body_paras:
            paras = [_strip_urls_and_tags(str(p), strip_urls=strip_urls) for p in body_paras]
        else:
            paras = [_strip_urls_and_tags(ln, strip_urls=strip_urls) for ln in str(entry.get("body", "")).splitlines()]
        paras = [p for p in paras if p]
        entries.append({"section": section, "title": title, "body_paragraphs": paras})

    return {
        "source_path": str(path.resolve()),
        "source_type": "draft_plan",
        "doc_title": doc_title,
        "entries": entries,
        "ruby_pairs": merged_pairs,
    }


def load_input_document(input_path: Path | str, *, strip_urls: bool = True) -> dict[str, Any]:
    """Load .docx, .md, .txt, or draft_plan.json into a normalized document structure."""
    path = Path(input_path)
    if not path.is_file():
        raise ValueError(f"输入文件不存在: {path}")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        doc = _load_docx_document(path, strip_urls=strip_urls)
    elif suffix in {".md", ".markdown", ".txt"}:
        doc = _load_markdown_or_text(path, strip_urls=strip_urls)
    elif suffix == ".json":
        doc = _load_draft_plan_json(path, strip_urls=strip_urls)
    else:
        raise ValueError(f"不支持的输入格式 ({suffix})，请提供 .docx、.md、.txt 或 .json")

    if not doc["entries"]:
        raise ValueError(f"输入文件未解析出任何有效正文内容: {path}")
    return doc


def render_clean_markdown(doc: dict[str, Any]) -> str:
    """Render normalized document before ruby annotation (baseline for 1:1 verification)."""
    lines: list[str] = []
    doc_title = str(doc.get("doc_title", "")).strip()
    if doc_title:
        lines.extend([f"# {doc_title}", ""])

    last_section = None
    for entry in doc.get("entries", []):
        sec = str(entry.get("section", "")).strip()
        if sec and sec != last_section:
            lines.extend([f"## {sec}", ""])
            last_section = sec
        title = str(entry.get("title", "")).strip()
        if title:
            lines.extend([f"### {title}", ""])
        for p in entry.get("body_paragraphs", []):
            p_str = str(p).strip()
            if p_str:
                lines.extend([p_str, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_annotated_markdown(ruby_result: dict[str, Any]) -> str:
    """Render annotated document to Markdown."""
    lines: list[str] = []
    doc_title = str(ruby_result.get("doc_title", "")).strip()
    if doc_title:
        lines.extend([f"# {doc_title}", ""])

    last_section = None
    for entry in ruby_result.get("annotated_entries", []):
        sec = str(entry.get("section", "")).strip()
        if sec and sec != last_section:
            lines.extend([f"## {sec}", ""])
            last_section = sec
        title = str(entry.get("title", "")).strip()
        if title:
            lines.extend([f"### {title}", ""])
        for p in entry.get("body_paragraphs", []):
            p_str = str(p).strip()
            if p_str:
                lines.extend([p_str, ""])
    return "\n".join(lines).rstrip() + "\n"


def _make_paragraph_xml(text: str, *, half_points: int, bold: bool = False) -> str:
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
    return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escaped}</w:t></w:r></w:p>'


def write_ruby_docx(ruby_result: dict[str, Any], output_path: Path | str) -> Path:
    """Export annotated script as optional Word document (15pt bold titles + 11pt regular body)."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    paragraphs_xml: list[str] = []
    doc_title = str(ruby_result.get("doc_title", "")).strip()
    if doc_title:
        paragraphs_xml.append(_make_paragraph_xml(doc_title, half_points=52, bold=True))

    last_section = None
    for entry in ruby_result.get("annotated_entries", []):
        sec = str(entry.get("section", "")).strip()
        if sec and sec != last_section:
            paragraphs_xml.append(_make_paragraph_xml(sec, half_points=32, bold=True))
            last_section = sec
        title = str(entry.get("title", "")).strip()
        if title:
            paragraphs_xml.append(_make_paragraph_xml(title, half_points=30, bold=True))
        for p in entry.get("body_paragraphs", []):
            p_str = str(p).strip()
            if p_str:
                paragraphs_xml.append(_make_paragraph_xml(p_str, half_points=22, bold=False))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        "  <w:body>\n    "
        + "\n    ".join(paragraphs_xml)
        + "\n    <w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/></w:sectPr>\n"
        "  </w:body>\n"
        "</w:document>\n"
    )
    doc_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
        "</Relationships>\n"
    )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", ROOT_RELS_XML)
        archive.writestr("word/document.xml", document_xml.encode("utf-8"))
        archive.writestr("word/_rels/document.xml.rels", doc_rels_xml.encode("utf-8"))
        archive.writestr("word/styles.xml", STYLES_XML.encode("utf-8"))
    return target


def write_ruby_report_md(ruby_result: dict[str, Any], input_meta: dict[str, Any], output_path: Path | str) -> Path:
    """Generate 注音报告.md with hit breakdown, applied replacements, and unresolved warnings."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# 口播日文注音报告",
        "",
        "## 一、输入与统计",
        "",
        f"- **输入文件**：`{Path(input_meta.get('source_path', '')).name}`（格式：`{input_meta.get('source_type', '')}`）",
        f"- **文档标题**：{ruby_result.get('doc_title', '')}",
        f"- **候选日文专名**：{len(ruby_result.get('candidates', []))} 项",
        f"- **VNDB 命中**：{len(ruby_result.get('vndb_hits', []))} 项",
        f"- **本地假名规则命中**：{len(ruby_result.get('local_hits', []))} 项",
        f"- **额外词典 (`ruby_pairs.json`) 命中**：{len(ruby_result.get('extra_hits', []))} 项",
        f"- **实际回写注音**：{len(ruby_result.get('applied', []))} 处（按去重映射计）",
        "",
    ]

    if ruby_result.get("vndb_service_error"):
        lines.append("> **提示**：本次 VNDB 在线查询不可用或处于离线模式，已回退为本地假名规则与 `ruby_pairs.json`。")
        lines.append("")

    misses = ruby_result.get("unresolved_misses", [])
    lines.extend(["## 二、待补注音词条（含假名未命中）", ""])
    if misses:
        for m in misses:
            lines.append(f"- `{m}`（请在 `ruby_pairs.json` 中补充准确英文或罗马音）")
    else:
        lines.append("- 所有含假名日文专名均已成功注音或无需额外处理。")

    lines.extend(["", "## 三、已应用注音映射清单", ""])
    applied = ruby_result.get("applied", [])
    if applied:
        for pair in applied:
            lines.append(f"- `{pair}`")
    else:
        lines.append("- 本稿件未匹配到需要插入注音的日文专名。")

    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def write_ruby_evidence_json(ruby_result: dict[str, Any], input_meta: dict[str, Any], output_path: Path | str) -> Path:
    """Write ruby_evidence.json with full source attribution."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_path": input_meta.get("source_path", ""),
        "source_type": input_meta.get("source_type", ""),
        "doc_title": ruby_result.get("doc_title", ""),
        "entry_count": len(ruby_result.get("annotated_entries", [])),
        "candidates": ruby_result.get("candidates", []),
        "vndb_hits": ruby_result.get("vndb_hits", []),
        "local_hits": ruby_result.get("local_hits", []),
        "extra_hits": ruby_result.get("extra_hits", []),
        "unresolved_misses": ruby_result.get("unresolved_misses", []),
        "applied": ruby_result.get("applied", []),
        "vndb_service_error": ruby_result.get("vndb_service_error", False),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
