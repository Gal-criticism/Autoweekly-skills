"""OOXML DOCX parsers for X summaries, manual weekly materials, and Chinese editorial drafts."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
import re
import zipfile

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL_NAMESPACE = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

URL_RE = re.compile(r"https?://", re.IGNORECASE)
STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/(?P<account>[A-Za-z0-9_]+)/status/(?P<status_id>\d+)(?:\?[^\s，。；;、！）)】]*)?",
    re.IGNORECASE,
)
HEADING_HASH_RE = re.compile(r"^\s*(?P<title>[^#\n]+?)\s*#\s*(?P<author>[^#\n]+?)\s*#?\s*$")

SECTION_HINTS = {
    "新作", "旧作", "舊作", "动画", "動畫", "周边", "周邊", "业界", "業界", "其他", "其它", "汉化", "漢化",
}
PREAMBLE_HINTS = {"目录", "目錄", "索引", "内容", "內容", "周报目录", "周報目錄"}

X_HEADER_RE = re.compile(
    r"Auto-X-tweets\s*\(\s*(?P<start>\d{4}-\d{2}-\d{2})\s*[~～-]\s*(?P<end>\d{4}-\d{2}-\d{2})\s*\)",
    re.IGNORECASE,
)
X_TWEET_START_RE = re.compile(
    r"(?P<account>[A-Za-z0-9_]{1,50})---(?P<published_at>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*类型:\s*(?P<tweet_type>原创推文|转发推文)"
)
ORIG_TIME_RE = re.compile(r"^原推文发布时间:\s*(?P<orig_time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*")
LINK_SUFFIX_RE = re.compile(r"\(link\)\s*:\s*(?P<url>https?://\S+)\s*$", re.IGNORECASE)
TIME_BUCKET_RE = re.compile(r"^(?:周一到周四|周五|周末|补充).*新闻[：:]?.*$")
EXPLICIT_ISSUE_HEADER_RE = re.compile(r"第\s*(?P<issue>\d+)\s*期")


@dataclass(frozen=True)
class ParagraphInfo:
    index: int
    text: str
    hyperlink_urls: tuple[str, ...]
    all_runs_bold: bool
    font_sizes: frozenset[int]


def split_urls(text: str) -> list[str]:
    """Split URLs from text, including adjacent pasted URLs without whitespace."""
    starts = [
        match.start()
        for match in re.finditer(r"https?://", text, re.IGNORECASE)
        if match.start() == 0 or text[match.start() - 1] != "="
    ]
    if not starts:
        return []
    result: list[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        value = text[start:end].strip()
        value = re.split(r"[\s，。；;、！？\n\r\t]", value, maxsplit=1)[0].rstrip(")]】>'\"")
        # Also trim trailing '(link):' if another marker was attached
        value = re.sub(r"\(link\):?$", "", value, flags=re.IGNORECASE)
        if value:
            result.append(value)
    return list(dict.fromkeys(result))


def extract_status_matches(urls_or_text: str | list[str]) -> list[tuple[str, str, str]]:
    """Return list of (status_id, account, canonical_url) from text or URL list."""
    texts = [urls_or_text] if isinstance(urls_or_text, str) else list(urls_or_text)
    seen: dict[str, tuple[str, str, str]] = {}
    for item in texts:
        for url in split_urls(item) or [item]:
            match = STATUS_URL_RE.search(url)
            if match:
                sid = match.group("status_id")
                acc = match.group("account")
                canonical = f"https://x.com/{acc}/status/{sid}"
                if sid not in seen:
                    seen[sid] = (sid, acc, canonical)
    return list(seen.values())


def read_docx_paragraphs(path: Path | str) -> list[ParagraphInfo]:
    """Read non-empty paragraphs and run formatting from an OOXML .docx file."""
    input_path = Path(path)
    if input_path.suffix.lower() != ".docx" or not input_path.is_file():
        raise ValueError(f"输入必须是存在的 .docx 文件: {input_path}")
    try:
        with zipfile.ZipFile(input_path) as archive:
            xml_bytes = archive.read("word/document.xml")
            try:
                rels_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            except KeyError:
                rels_root = None
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"无法读取 DOCX ({input_path}): {exc}") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"DOCX XML 无效 ({input_path}): {exc}") from exc

    relationships: dict[str, str] = {}
    if rels_root is not None:
        relationships = {rel.get("Id", ""): rel.get("Target", "") for rel in rels_root}

    paragraphs: list[ParagraphInfo] = []
    for raw_idx, paragraph in enumerate(root.iter(WORD_NAMESPACE + "p")):
        pieces: list[str] = []
        hyperlink_urls: list[str] = []
        for node in paragraph.iter():
            if node.tag == WORD_NAMESPACE + "t":
                pieces.append(node.text or "")
            elif node.tag == WORD_NAMESPACE + "tab":
                pieces.append("\t")
            elif node.tag in {WORD_NAMESPACE + "br", WORD_NAMESPACE + "cr"}:
                pieces.append("\n")
            elif node.tag == WORD_NAMESPACE + "hyperlink":
                rel_id = node.get(REL_NAMESPACE + "id", "")
                target = relationships.get(rel_id, "")
                if target.lower().startswith(("http://", "https://")):
                    hyperlink_urls.append(target)
        text = "".join(pieces).strip()
        if not text:
            continue
        runs: list[tuple[bool, int | None]] = []
        for run in paragraph.iter(WORD_NAMESPACE + "r"):
            run_text = "".join(node.text or "" for node in run.iter(WORD_NAMESPACE + "t"))
            if not run_text:
                continue
            properties = run.find(WORD_NAMESPACE + "rPr")
            bold = False
            size: int | None = None
            if properties is not None:
                bold_node = properties.find(WORD_NAMESPACE + "b")
                if bold_node is not None:
                    val = bold_node.get(WORD_NAMESPACE + "val", "true").casefold()
                    bold = val not in {"0", "false", "off", "no"}
                size_node = properties.find(WORD_NAMESPACE + "sz")
                if size_node is not None:
                    try:
                        size = int(size_node.get(WORD_NAMESPACE + "val", ""))
                    except (TypeError, ValueError):
                        size = None
            runs.append((bold, size))
        paragraphs.append(
            ParagraphInfo(
                index=raw_idx,
                text=text,
                hyperlink_urls=tuple(hyperlink_urls),
                all_runs_bold=bool(runs) and all(b for b, _ in runs),
                font_sizes=frozenset(s for _, s in runs if s is not None),
            )
        )
    return paragraphs


def _days_between(ts1: str | None, ts2: str | None) -> float | None:
    if not ts1 or not ts2:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d1 = datetime.strptime(ts1[:19] if len(ts1) >= 19 else ts1[:10], fmt)
            break
        except ValueError:
            d1 = None
    else:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d2 = datetime.strptime(ts2[:19] if len(ts2) >= 19 else ts2[:10], fmt)
            break
        except ValueError:
            d2 = None
    else:
        return None
    return (d1 - d2).total_seconds() / 86400.0


def parse_x_summary_docx(path: Path | str, period_start: str | None = None) -> dict[str, Any]:
    """Parse an Auto-X-tweets DOCX file into structured tweet records and coverage diagnostics."""
    input_path = Path(path)
    paragraphs = read_docx_paragraphs(input_path)
    header_range: dict[str, str] | None = None
    if paragraphs:
        m = X_HEADER_RE.search(paragraphs[0].text)
        if m:
            header_range = {"start": m.group("start"), "end": m.group("end")}
            if not period_start:
                period_start = m.group("start")

    tweets: list[dict[str, Any]] = []
    current_account_section: str | None = None
    multi_paragraph_splits = 0

    for para in paragraphs:
        line = para.text.strip()
        if not line:
            continue
        if X_HEADER_RE.fullmatch(line) or re.fullmatch(r"\d+", line):
            continue
        if re.fullmatch(r"[A-Za-z0-9_]{1,50}", line):
            current_account_section = line
            continue

        # Normalize stuck boundary where a status/<digits> URL immediately touches the next <account>---timestamp
        line = re.sub(
            r"(/status/\d{15,20}(?:\?[A-Za-z0-9_=&-]+)?)(?=[A-Za-z0-9_]{1,50}---\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
            r"\1\n",
            line,
        )
        matches = list(X_TWEET_START_RE.finditer(line))
        if not matches:
            continue
        if len(matches) > 1:
            multi_paragraph_splits += 1

        for idx, match in enumerate(matches):
            seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
            body_part = line[match.end():seg_end].strip()
            account = match.group("account")
            published_at = match.group("published_at")
            tweet_type = match.group("tweet_type")

            original_published_at: str | None = None
            orig_match = ORIG_TIME_RE.match(body_part)
            if orig_match:
                original_published_at = orig_match.group("orig_time")
                body_part = body_part[orig_match.end():].strip()

            link_url: str | None = None
            link_match = LINK_SUFFIX_RE.search(body_part)
            if link_match:
                link_url = link_match.group("url")
                body_part = body_part[:link_match.start()].strip()

            all_urls = split_urls(body_part)
            if link_url:
                all_urls.extend(split_urls(link_url))
            all_urls.extend(para.hyperlink_urls)
            all_urls = list(dict.fromkeys(all_urls))

            status_matches = extract_status_matches(all_urls)
            status_id = status_matches[0][0] if status_matches else None
            status_account = status_matches[0][1] if status_matches else account
            status_url = status_matches[0][2] if status_matches else link_url

            external_urls = [
                u for u in all_urls
                if not STATUS_URL_RE.search(u)
            ]

            # Clean text without trailing t.co links for readability and length check
            text_no_urls = re.sub(r"https?://\S+", "", body_part).strip()
            text_no_tags = re.sub(r"#\S+", "", text_no_urls).strip()

            age_days = _days_between(published_at, original_published_at) if original_published_at else 0.0
            before_period = (
                _days_between(period_start, original_published_at)
                if (period_start and original_published_at)
                else None
            )
            old_retweet_hint = bool(
                tweet_type == "转发推文"
                and (
                    (age_days is not None and age_days > 7.0)
                    or (before_period is not None and before_period > 1.5)
                )
            )
            image_only_hint = len(text_no_tags) < 12

            tweets.append(
                {
                    "status_id": status_id,
                    "account": account,
                    "status_account": status_account,
                    "account_section": current_account_section or account,
                    "published_at": published_at,
                    "tweet_type": tweet_type,
                    "is_retweet": tweet_type == "转发推文",
                    "original_published_at": original_published_at,
                    "text": body_part,
                    "clean_text": text_no_urls,
                    "status_url": status_url,
                    "external_urls": external_urls,
                    "source_file": input_path.name,
                    "source_path": str(input_path.resolve()),
                    "paragraph_index": para.index,
                    "flags": {
                        "old_retweet_hint": old_retweet_hint,
                        "image_only_hint": image_only_hint,
                    },
                }
            )

    is_title_only = len(tweets) == 0
    return {
        "file_name": input_path.name,
        "file_path": str(input_path.resolve()),
        "paragraph_count": len(paragraphs),
        "tweet_count": len(tweets),
        "is_title_only": is_title_only,
        "status": "input_missing_title_only" if is_title_only else "ok",
        "header_range": header_range,
        "multi_paragraph_splits": multi_paragraph_splits,
        "tweets": tweets,
    }


def _is_section_label(line: str) -> bool:
    cleaned = line.strip()
    return bool(
        cleaned
        and len(cleaned) <= 16
        and cleaned not in PREAMBLE_HINTS
        and "#" not in cleaned
        and not URL_RE.search(cleaned)
        and not re.search(r"[。！？.!?]$", cleaned)
        and (cleaned in SECTION_HINTS or re.fullmatch(r"[汉漢]化(?:信息|情報|新闻|新聞)?", cleaned))
    )


def _normalize_section(label: str) -> str:
    cleaned = label.strip()
    if re.match(r"^[汉漢]化", cleaned):
        return "汉化"
    if cleaned in {"周邊", "周边"}:
        return "周边"
    if cleaned in {"新作", "旧作", "舊作"}:
        return "新作"
    return cleaned


def parse_manual_material_docx(path: Path | str) -> dict[str, Any]:
    """Parse a manual categorized material DOCX from weekly_reports/周报."""
    input_path = Path(path)
    paragraphs = read_docx_paragraphs(input_path)
    header_text = paragraphs[0].text if paragraphs else ""
    explicit_issue: str | None = None
    m_issue = EXPLICIT_ISSUE_HEADER_RE.search(header_text)
    if m_issue:
        explicit_issue = m_issue.group("issue")

    items: list[dict[str, Any]] = []
    current_section = "新作"
    current_bucket = "默认"
    buffer_lines: list[str] = []
    buffer_urls: list[str] = []
    buffer_start_idx: int | None = None

    def flush_buffer(end_idx: int) -> None:
        nonlocal buffer_lines, buffer_urls, buffer_start_idx
        if not buffer_lines and not buffer_urls:
            return
        joined_text = "\n".join(buffer_lines).strip()
        all_urls = list(dict.fromkeys(split_urls(joined_text) + buffer_urls))
        statuses = extract_status_matches(all_urls)
        external_urls = [u for u in all_urls if not STATUS_URL_RE.search(u)]
        clean_lines = [
            ln for ln in buffer_lines
            if not (STATUS_URL_RE.search(ln) and len(re.sub(r"https?://\S+|\(link\):?", "", ln).strip()) == 0)
        ]
        clean_text = "\n".join(clean_lines).strip()
        if clean_text or statuses or external_urls:
            items.append(
                {
                    "section": current_section,
                    "time_bucket": current_bucket,
                    "text": clean_text or joined_text,
                    "raw_text": joined_text,
                    "status_ids": [s[0] for s in statuses],
                    "status_accounts": list(dict.fromkeys(s[1] for s in statuses)),
                    "status_urls": [s[2] for s in statuses],
                    "external_urls": external_urls,
                    "source_file": input_path.name,
                    "source_path": str(input_path.resolve()),
                    "paragraph_start": buffer_start_idx if buffer_start_idx is not None else end_idx,
                    "paragraph_end": end_idx,
                }
            )
        buffer_lines = []
        buffer_urls = []
        buffer_start_idx = None

    for idx, para in enumerate(paragraphs):
        line = para.text.strip()
        if not line:
            continue
        if idx == 0 and ("每周Galgame新闻速递" in line or EXPLICIT_ISSUE_HEADER_RE.search(line)):
            continue
        if TIME_BUCKET_RE.match(line) and len(line) <= 20:
            flush_buffer(para.index)
            current_bucket = line.rstrip("：:")
            continue
        if _is_section_label(line):
            flush_buffer(para.index)
            current_section = _normalize_section(line)
            continue

        line_urls = split_urls(line) + list(para.hyperlink_urls)
        line_statuses = extract_status_matches(line_urls)
        text_without_urls = re.sub(r"\(link\)\s*:\s*https?://\S+|https?://\S+", "", line).strip()

        # Case 1: Single paragraph contains both substantial text AND a status URL
        if line_statuses and text_without_urls:
            flush_buffer(para.index)
            buffer_start_idx = para.index
            buffer_lines.append(line)
            buffer_urls.extend(line_urls)
            flush_buffer(para.index)
            continue

        # Case 2: Paragraph is pure status URL(s) terminating a multi-paragraph block
        if line_statuses and not text_without_urls:
            if buffer_start_idx is None:
                buffer_start_idx = para.index
            buffer_lines.append(line)
            buffer_urls.extend(line_urls)
            # Peek ahead: if the next paragraph is also a pure status URL, keep grouping them
            next_is_pure_status = False
            if idx + 1 < len(paragraphs):
                nxt = paragraphs[idx + 1].text.strip()
                nxt_urls = split_urls(nxt) + list(paragraphs[idx + 1].hyperlink_urls)
                nxt_statuses = extract_status_matches(nxt_urls)
                nxt_no_url = re.sub(r"\(link\)\s*:\s*https?://\S+|https?://\S+", "", nxt).strip()
                if nxt_statuses and not nxt_no_url:
                    next_is_pure_status = True
            if not next_is_pure_status:
                flush_buffer(para.index)
            continue

        # Case 3: Regular content or external URL line
        if buffer_start_idx is None:
            buffer_start_idx = para.index
        buffer_lines.append(line)
        buffer_urls.extend(line_urls)

    flush_buffer(paragraphs[-1].index if paragraphs else 0)

    all_status_ids = list(dict.fromkeys(sid for item in items for sid in item["status_ids"]))
    return {
        "file_name": input_path.name,
        "file_path": str(input_path.resolve()),
        "header_text": header_text,
        "explicit_issue": explicit_issue,
        "item_count": len(items),
        "status_ids": all_status_ids,
        "items": items,
    }


def _heading_hash_match(line: str) -> re.Match[str] | None:
    return HEADING_HASH_RE.match(line) if "#" in line and not URL_RE.search(line) else None


def _styled_title_candidate(paragraphs: list[ParagraphInfo], index: int) -> bool:
    """Match the 15pt bold (sz=30) title + 11pt regular (sz=22) body convention in galgame_news."""
    paragraph = paragraphs[index]
    if (
        not paragraph.all_runs_bold
        or paragraph.font_sizes != frozenset({30})
        or "#" in paragraph.text
        or URL_RE.search(paragraph.text)
    ):
        return False
    if index + 1 >= len(paragraphs):
        return False
    following = paragraphs[index + 1]
    return not following.all_runs_bold and following.font_sizes == frozenset({22})


def clean_editorial_prose(body: str) -> str:
    """Remove URL-only lines, trailing editor notes, and stray hashtags from editorial prose."""
    prose_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip lines that are purely URLs
        without_urls = re.sub(r"https?://\S+", "", line).strip()
        if not without_urls:
            continue
        # Remove inline stray URLs if appended at end of prose
        cleaned = re.sub(r"\s*https?://\S+\s*", "", line).strip()
        if cleaned:
            prose_lines.append(cleaned)
    return "\n".join(prose_lines).strip()


def parse_editorial_draft_docx(path: Path | str, issue_id: str | None = None) -> dict[str, Any]:
    """Parse a Chinese editorial weekly report DOCX using the exact galgame_news rules."""
    input_path = Path(path)
    paragraphs = read_docx_paragraphs(input_path)
    inferred_issue = issue_id
    if not inferred_issue:
        m_file = re.match(r"^(\d{3})", input_path.stem)
        if m_file:
            inferred_issue = m_file.group(1)
        elif paragraphs:
            m_first = re.match(r"^(\d{3})", paragraphs[0].text.strip())
            if m_first:
                inferred_issue = m_first.group(1)

    entries: list[dict[str, Any]] = []
    section = "新作"
    section_active = False
    current: dict[str, Any] | None = None

    for index, paragraph in enumerate(paragraphs):
        line = paragraph.text
        heading = _heading_hash_match(line)
        styled_heading = section_active and _styled_title_candidate(paragraphs, index)
        if heading or styled_heading:
            if current:
                entries.append(current)
            current = {
                "sequence": len(entries) + 1,
                "section": section,
                "title": heading.group("title").strip() if heading else line.strip(),
                "author": (heading.group("author").strip() or None) if heading else None,
                "title_style": "hash_author" if heading else "styled_15pt_bold",
                "body": "",
                "prose_paragraphs": [],
                "source_urls": [],
            }
            continue
        next_is_heading = index + 1 < len(paragraphs) and (
            _heading_hash_match(paragraphs[index + 1].text)
            or _styled_title_candidate(paragraphs, index + 1)
        )
        if next_is_heading and _is_section_label(line):
            if current:
                entries.append(current)
                current = None
            section = _normalize_section(line)
            section_active = True
            continue
        if current is None:
            continue
        current["body"] = f"{current['body']}\n{line}".strip()
        line_urls = split_urls(line) + list(paragraph.hyperlink_urls)
        current["source_urls"].extend(line_urls)
        without_urls = re.sub(r"https?://\S+", "", line).strip()
        if without_urls:
            current["prose_paragraphs"].append(without_urls)

    if current:
        entries.append(current)

    all_status_ids: list[str] = []
    for entry in entries:
        entry["source_urls"] = list(dict.fromkeys(entry["source_urls"]))
        statuses = extract_status_matches(entry["source_urls"])
        entry["status_ids"] = [s[0] for s in statuses]
        entry["status_accounts"] = list(dict.fromkeys(s[1] for s in statuses))
        entry["external_urls"] = [u for u in entry["source_urls"] if not STATUS_URL_RE.search(u)]
        entry["clean_body"] = clean_editorial_prose(entry["body"])
        all_status_ids.extend(entry["status_ids"])

    return {
        "issue_id": inferred_issue or "unknown",
        "file_name": input_path.name,
        "file_path": str(input_path.resolve()),
        "entry_count": len(entries),
        "sections": list(dict.fromkeys(e["section"] for e in entries)),
        "status_ids": list(dict.fromkeys(all_status_ids)),
        "entries": entries,
    }
