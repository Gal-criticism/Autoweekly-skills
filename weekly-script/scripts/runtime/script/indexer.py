"""Build and incrementally update local historical indices (selection, style examples, works)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from .parsers import (
    parse_editorial_draft_docx,
    parse_manual_material_docx,
    parse_x_summary_docx,
)

GAME_BRACKET_RE = re.compile(r"《([^》]+)》|『([^』]+)』|「([^」]+)」")
BRAND_ANNOUNCE_RE = re.compile(
    r"^\s*(?:本周\s*[，,]?\s*)?(?P<brand>[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff&.+_・\-\s]{1,28}?)\s*"
    r"(?:宣布|公开|公開|公布|发布|发售|推出|更新|在新作|对新作)"
)
STAFF_EXTRACT_RE = re.compile(
    r"(?P<clause>(?:由\s*)?[^，。；\n]{1,60}?(?:负责|担当|担任|献唱|演唱|作词|作曲|编曲)(?:[^，。；\n]{0,25})?|"
    r"(?:原画|剧本|脚本|企划|音乐|主题曲|片头曲|OP)[^，。；\n]{1,50})"
)
PLATFORM_TOKENS = ("PC", "Switch", "NS", "PS4", "PS5", "Steam", "DLsite", "FANZA", "iOS", "Android")
PRICE_CLUE_RE = re.compile(r"[^，。；\n]*?(?:\d[\d,]*\s*日元|售价|价格|含税|税后|折扣|优惠)[^，。；\n]*")
DATE_CLUE_RE = re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?|\d{1,2}\s*月\s*\d{1,2}\s*日")


def classify_event_type(title: str, body: str, section: str) -> str:
    """Classify an editorial entry into fine-grained Galgame weekly event types."""
    text = f"{title}\n{body}"
    if re.search(r"[一二三四五六七八九十\d]+月新作本周发售|新作已于本周发售", text):
        return "月度发售汇总"
    if section == "汉化" or re.search(r"汉化|官中|官方中文|简繁中文|支持中文|本地化|Steam商店页面公开", text, re.I):
        return "汉化与海外发行"
    if section == "周边" or re.search(r"周边|抱枕|挂画|快闪店|联动咖啡|POP\s*UP|グッズ|众筹|音乐会|演唱会|展会|WEPLAY", text, re.I):
        return "周边与活动"
    if re.search(r"推迟|延期|变更.*发售|跳票", text):
        return "延期与变更"
    if re.search(r"制作完成|マスターアップ|Master\s*Up", text, re.I):
        return "制作完成"
    if re.search(r"体验版|试玩版|Demo", text, re.I):
        return "体验版"
    if re.search(r"主机版|Switch版|PS4版|PS5版|NS版|移植", text, re.I):
        if re.search(r"体验版", title):
            return "体验版"
        return "主机移植"
    if re.search(r"正式发售|现已发售|本日发售|已于本周发售", title):
        return "正式发售"
    if re.search(r"宣布制作|新作公开|预告官网|公开了新作|发表新作", text):
        return "新作发表"
    if re.search(r"开始接受预约|预订开始|开启预约|发售日决定|发售决定", text):
        return "预约与发售日"
    if re.search(r"官网公开|官方网站|官网更新|CG|OP|片头|声优|CV|角色介绍|背景", text, re.I):
        return "官网更新"
    if re.search(r"发售", text):
        return "预约与发售日"
    return "官网更新"


def extract_games_from_entry(title: str, body: str) -> list[str]:
    """Extract game names from 《...》 brackets in title first, then body."""
    title_matches = [m.group(1) or m.group(2) or m.group(3) for m in GAME_BRACKET_RE.finditer(title)]
    games = [g.strip() for g in title_matches if g and g.strip()]
    if games:
        return list(dict.fromkeys(games))
    body_matches = [m.group(1) or m.group(2) or m.group(3) for m in GAME_BRACKET_RE.finditer(body)]
    games = [g.strip() for g in body_matches if g and g.strip()]
    return list(dict.fromkeys(games[:3]))


def extract_brands_from_entry(title: str, body: str) -> list[str]:
    """Extract developer/publisher brand names from title and opening sentence."""
    brands: list[str] = []
    first_sentence = re.split(r"[。！？\n]", body, maxsplit=1)[0] if body else ""
    for source in (title, first_sentence):
        for match in BRAND_ANNOUNCE_RE.finditer(source):
            candidate = match.group("brand").strip(" ，,、：:「」『』《》")
            # Filter out generic words
            if (
                candidate
                and len(candidate) <= 28
                and candidate not in {"本周", "近日", "日前", "今日", "官方", "游戏", "本作", "该作"}
                and not candidate.startswith(("本周", "《", "『"))
            ):
                brands.append(candidate)
    return list(dict.fromkeys(brands))


def extract_staff_clauses(body: str) -> list[str]:
    clauses = [m.group("clause").strip(" ，,。；") for m in STAFF_EXTRACT_RE.finditer(body)]
    return list(dict.fromkeys(c for c in clauses if c))


def extract_platforms(text: str) -> list[str]:
    found: list[str] = []
    for token in PLATFORM_TOKENS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text, re.I):
            canonical = "Switch" if token.upper() == "NS" else token
            if canonical not in found:
                found.append(canonical)
    if "主机" in text and "Switch" not in found and "PS4" not in found:
        found.append("主机平台")
    return found


def detect_structure_tags(clean_body: str) -> list[str]:
    tags = ["current_week_change"]
    if re.search(r"原版|前作|系列|改编自|外传|重制|第[一二三四五六七八九十\d]+作|由.*负责(?:原画|剧本)", clean_body):
        tags.append("work_background")
    if re.search(r"发售|预约|预订|售价|日元|折扣|商店|官网|体验版", clean_body):
        tags.append("release_or_event_info")
    if re.search(r"感兴趣的玩家|不妨关注|敬请期待|可以前往|不要错过|来看看吧", clean_body):
        tags.append("closing_cta")
    return tags


def _file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"


def build_indices(
    reports_dir: Path | str,
    drafts_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Scan local weekly_reports and editorial drafts to build/update the 3 local indices."""
    reports_root = Path(reports_dir)
    drafts_root = Path(drafts_dir)
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    x_dir = reports_root / "x新闻" if (reports_root / "x新闻").is_dir() else reports_root
    manual_dir = reports_root / "周报" if (reports_root / "周报").is_dir() else reports_root

    # 1. Parse all X summaries
    x_files = sorted(x_dir.glob("*.docx")) if x_dir.is_dir() else []
    parsed_x_list: list[dict[str, Any]] = []
    tweet_by_id: dict[str, dict[str, Any]] = {}
    title_only_x_files: list[str] = []

    for x_path in x_files:
        if x_path.name.startswith("~$"):
            continue
        parsed_x = parse_x_summary_docx(x_path)
        parsed_x_list.append(parsed_x)
        if parsed_x["is_title_only"]:
            title_only_x_files.append(parsed_x["file_name"])
        for tw in parsed_x["tweets"]:
            sid = tw.get("status_id")
            if sid and sid not in tweet_by_id:
                tweet_by_id[sid] = tw

    # 2. Parse all manual classified materials
    manual_files = sorted(manual_dir.glob("*.docx")) if manual_dir.is_dir() else []
    parsed_manuals: list[dict[str, Any]] = []
    for m_path in manual_files:
        if m_path.name.startswith("~$"):
            continue
        try:
            parsed_manuals.append(parse_manual_material_docx(m_path))
        except ValueError:
            continue

    # 3. Parse all finished Chinese editorial drafts
    draft_files = sorted(drafts_root.glob("*.docx")) if drafts_root.is_dir() else []
    parsed_drafts: list[dict[str, Any]] = []
    for d_path in draft_files:
        if d_path.name.startswith("~$"):
            continue
        try:
            parsed_drafts.append(parse_editorial_draft_docx(d_path))
        except ValueError:
            continue

    # Sort drafts by numeric issue_id
    parsed_drafts.sort(key=lambda d: int(d["issue_id"]) if str(d["issue_id"]).isdigit() else 999999)

    # 4. Build Selection Index, Style Examples Index, and Works Index
    selection_issues: list[dict[str, Any]] = []
    style_examples: list[dict[str, Any]] = []
    works_map: dict[str, dict[str, Any]] = {}

    for draft in parsed_drafts:
        issue_id = str(draft["issue_id"])
        draft_sids = set(draft["status_ids"])

        # Align manual materials by status_id overlap (never rely on file suffix alone)
        manual_candidates: list[tuple[int, dict[str, Any]]] = []
        for m_doc in parsed_manuals:
            m_sids = set(m_doc["status_ids"])
            overlap = len(draft_sids & m_sids)
            if overlap > 0 or (m_doc.get("explicit_issue") == issue_id):
                manual_candidates.append((overlap, m_doc))
        manual_candidates.sort(key=lambda pair: (-pair[0], pair[1]["file_name"]))
        primary_manual = manual_candidates[0][1] if manual_candidates and manual_candidates[0][0] >= 2 else None

        # Align X summaries by status_id overlap
        aligned_x: list[dict[str, Any]] = []
        matched_x_sids: set[str] = set()
        for x_doc in parsed_x_list:
            if x_doc["is_title_only"]:
                continue
            x_sids = {tw["status_id"] for tw in x_doc["tweets"] if tw.get("status_id")}
            overlap_sids = draft_sids & x_sids
            if overlap_sids:
                matched_x_sids.update(overlap_sids)
                aligned_x.append(
                    {
                        "file_name": x_doc["file_name"],
                        "matched_status_count": len(overlap_sids),
                        "total_tweets": x_doc["tweet_count"],
                    }
                )
        aligned_x.sort(key=lambda item: -item["matched_status_count"])

        selected_events: list[dict[str, Any]] = []
        for entry in draft["entries"]:
            event_type = classify_event_type(entry["title"], entry["clean_body"], entry["section"])
            games = extract_games_from_entry(entry["title"], entry["clean_body"])
            brands = extract_brands_from_entry(entry["title"], entry["clean_body"])
            staff = extract_staff_clauses(entry["clean_body"])
            platforms = extract_platforms(f"{entry['title']}\n{entry['clean_body']}")
            structure_tags = detect_structure_tags(entry["clean_body"])

            entry_sids = entry["status_ids"]
            matched_tweets_preview = [
                {
                    "status_id": sid,
                    "account": tweet_by_id[sid]["account"],
                    "tweet_type": tweet_by_id[sid]["tweet_type"],
                    "clean_text": tweet_by_id[sid]["clean_text"][:160],
                }
                for sid in entry_sids
                if sid in tweet_by_id
            ]

            selected_events.append(
                {
                    "issue_id": issue_id,
                    "sequence": entry["sequence"],
                    "section": entry["section"],
                    "event_type": event_type,
                    "title": entry["title"],
                    "game_names": games,
                    "brands": brands,
                    "status_ids": entry_sids,
                    "status_accounts": entry["status_accounts"],
                    "merged_tweet_count": len(entry_sids),
                    "is_multi_tweet_merge": len(entry_sids) > 1,
                    "source_urls": entry["source_urls"],
                    "matched_x_tweets": matched_tweets_preview,
                }
            )

            # Add to style_examples (cleaned of #author# tags and URLs)
            if entry["clean_body"]:
                style_examples.append(
                    {
                        "example_id": f"{issue_id}-{entry['sequence']:02d}",
                        "issue_id": issue_id,
                        "section": entry["section"],
                        "event_type": event_type,
                        "title": entry["title"],
                        "clean_body": entry["clean_body"],
                        "char_count": len(entry["clean_body"]),
                        "game_names": games,
                        "brands": brands,
                        "structure_tags": structure_tags,
                        "source_url_count": len(entry["source_urls"]),
                    }
                )

            # Add to works_index (skip monthly batch list entry for individual work profile unless single game)
            if event_type != "月度发售汇总":
                for game in games:
                    norm_key = game.strip().casefold()
                    if norm_key not in works_map:
                        works_map[norm_key] = {
                            "canonical_name": game,
                            "aliases": [game],
                            "brands": [],
                            "platforms": [],
                            "staff": [],
                            "accounts": [],
                            "historical_clues": [],
                            "appearances": [],
                        }
                    w = works_map[norm_key]
                    if game not in w["aliases"]:
                        w["aliases"].append(game)
                    for b in brands:
                        if b not in w["brands"]:
                            w["brands"].append(b)
                    for p in platforms:
                        if p not in w["platforms"]:
                            w["platforms"].append(p)
                    for acc in entry["status_accounts"]:
                        if acc not in w["accounts"]:
                            w["accounts"].append(acc)
                    for st in staff:
                        if not any(existing["clause"] == st for existing in w["staff"]):
                            w["staff"].append({"clause": st, "source_issue": issue_id})

                    dates = DATE_CLUE_RE.findall(entry["clean_body"])
                    prices = PRICE_CLUE_RE.findall(entry["clean_body"])
                    if dates or prices:
                        w["historical_clues"].append(
                            {
                                "source_issue": issue_id,
                                "dates_mentioned": list(dict.fromkeys(dates)),
                                "price_clauses": [p.strip() for p in prices[:2]],
                                "verification_clue_only": True,
                                "warning": "历史发售日期、价格与语言信息仅作核实线索，不能直接当作本周事实写入新稿。",
                            }
                        )
                    w["appearances"].append(
                        {
                            "issue_id": issue_id,
                            "section": entry["section"],
                            "event_type": event_type,
                            "title": entry["title"],
                            "clean_body": entry["clean_body"],
                            "source_urls": entry["source_urls"],
                        }
                    )

        # Unselected items in aligned manual material
        unselected_manual: list[dict[str, Any]] = []
        if primary_manual:
            for item in primary_manual["items"]:
                item_sids = set(item["status_ids"])
                if item_sids and not (item_sids & draft_sids):
                    unselected_manual.append(
                        {
                            "section": item["section"],
                            "time_bucket": item["time_bucket"],
                            "status_ids": item["status_ids"],
                            "status_accounts": item["status_accounts"],
                            "text_preview": item["text"][:160],
                            "reference_only": True,
                            "note": "未入选仅作历史选题参考，不能直接当作不相关新闻。",
                        }
                    )

        selection_issues.append(
            {
                "issue_id": issue_id,
                "draft_file": draft["file_name"],
                "entry_count": draft["entry_count"],
                "draft_status_id_count": len(draft_sids),
                "aligned_manual_file": primary_manual["file_name"] if primary_manual else None,
                "aligned_manual_overlap": manual_candidates[0][0] if primary_manual else 0,
                "aligned_manual_total_status_ids": len(primary_manual["status_ids"]) if primary_manual else 0,
                "aligned_x_files": aligned_x,
                "x_direct_matched_status_count": len(matched_x_sids),
                "x_missing_status_ids": sorted(draft_sids - matched_x_sids),
                "selected_events": selected_events,
                "unselected_manual_items": unselected_manual,
            }
        )

    built_at = datetime.now(timezone.utc).isoformat()

    selection_payload = {
        "schema_version": "1.0",
        "built_at": built_at,
        "summary": {
            "total_draft_issues": len(selection_issues),
            "total_manual_files": len(parsed_manuals),
            "total_x_files": len(parsed_x_list),
            "title_only_x_files_count": len(title_only_x_files),
            "title_only_x_files": title_only_x_files,
        },
        "issues": selection_issues,
    }

    # Summarize event_type counts for style_examples
    type_counts: dict[str, int] = {}
    for ex in style_examples:
        et = ex["event_type"]
        type_counts[et] = type_counts.get(et, 0) + 1

    style_payload = {
        "schema_version": "1.0",
        "built_at": built_at,
        "total_examples": len(style_examples),
        "event_type_counts": type_counts,
        "examples": style_examples,
    }

    works_list = sorted(works_map.values(), key=lambda item: (-len(item["appearances"]), item["canonical_name"]))
    works_payload = {
        "schema_version": "1.0",
        "built_at": built_at,
        "total_works": len(works_list),
        "works": works_list,
    }

    (out_root / "selection_index.json").write_text(
        json.dumps(selection_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_root / "style_examples.json").write_text(
        json.dumps(style_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_root / "works_index.json").write_text(
        json.dumps(works_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "status": "ok",
        "index_dir": str(out_root.resolve()),
        "files": {
            "selection_index": str((out_root / "selection_index.json").resolve()),
            "style_examples": str((out_root / "style_examples.json").resolve()),
            "works_index": str((out_root / "works_index.json").resolve()),
        },
        "summary": {
            "draft_issues_indexed": len(selection_issues),
            "manual_files_scanned": len(parsed_manuals),
            "x_files_scanned": len(parsed_x_list),
            "title_only_x_files_count": len(title_only_x_files),
            "title_only_x_files": title_only_x_files,
            "style_examples_count": len(style_examples),
            "event_type_counts": type_counts,
            "works_indexed": len(works_list),
        },
    }
