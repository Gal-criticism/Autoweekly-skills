"""Retrieve work background, style examples, and selection preferences with holdout filtering."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def _issue_allowed(
    issue_id: str | int | None,
    max_issue: int | None = None,
    exclude_issues: set[str] | None = None,
) -> bool:
    if issue_id is None:
        return True
    s_issue = str(issue_id).strip()
    if exclude_issues and s_issue in exclude_issues:
        return False
    if max_issue is not None and s_issue.isdigit():
        if int(s_issue) > max_issue:
            return False
    return True


def load_indices(index_dir: Path | str) -> dict[str, Any]:
    root = Path(index_dir)
    for required in ("selection_index.json", "style_examples.json", "works_index.json"):
        if not (root / required).is_file():
            raise ValueError(f"索引文件缺失 ({root / required})，请先运行 build-index")
    return {
        "selection": json.loads((root / "selection_index.json").read_text(encoding="utf-8")),
        "style": json.loads((root / "style_examples.json").read_text(encoding="utf-8")),
        "works": json.loads((root / "works_index.json").read_text(encoding="utf-8")),
    }


def retrieve_context(
    index_dir: Path | str,
    *,
    query: str | None = None,
    event_type: str | None = None,
    section: str | None = None,
    account: str | None = None,
    max_issue: int | None = None,
    exclude_issues: list[str] | set[str] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Retrieve filtered work background, style examples, and historical selection patterns."""
    indices = load_indices(index_dir)
    excl_set = {str(i).strip() for i in (exclude_issues or []) if str(i).strip()}
    q_norm = (query or "").strip().casefold()
    acc_norm = (account or "").strip().lstrip("@").casefold()

    # 1. Search works_index with strict holdout filtering on appearances, staff, and clues
    matched_works: list[dict[str, Any]] = []
    for work in indices["works"].get("works", []):
        allowed_appearances = [
            app for app in work.get("appearances", [])
            if _issue_allowed(app.get("issue_id"), max_issue, excl_set)
        ]
        if not allowed_appearances:
            continue

        score = 0
        names_low = [n.casefold() for n in work.get("aliases", [])]
        brands_low = [b.casefold() for b in work.get("brands", [])]
        accs_low = [a.casefold() for a in work.get("accounts", [])]

        if q_norm:
            if any(q_norm == n for n in names_low):
                score += 100
            elif any(q_norm in n or n in q_norm for n in names_low if len(n) >= 2):
                score += 60
            elif any(q_norm in b for b in brands_low):
                score += 40
            elif any(q_norm in app.get("title", "").casefold() or q_norm in app.get("clean_body", "").casefold() for app in allowed_appearances):
                score += 20
        if acc_norm and acc_norm in accs_low:
            score += 50

        if (q_norm or acc_norm) and score == 0:
            continue

        allowed_staff = [
            st for st in work.get("staff", [])
            if _issue_allowed(st.get("source_issue"), max_issue, excl_set)
        ]
        allowed_clues = [
            cl for cl in work.get("historical_clues", [])
            if _issue_allowed(cl.get("source_issue"), max_issue, excl_set)
        ]

        matched_works.append(
            {
                "score": score,
                "canonical_name": work["canonical_name"],
                "aliases": work["aliases"],
                "brands": work["brands"],
                "platforms": work["platforms"],
                "accounts": work["accounts"],
                "staff": allowed_staff,
                "historical_clues": allowed_clues,
                "appearances": allowed_appearances[-limit:],
            }
        )

    matched_works.sort(key=lambda w: (-w["score"], -len(w["appearances"]), w["canonical_name"]))
    matched_works = matched_works[:limit]

    # 2. Search style_examples with holdout filtering
    candidate_examples: list[tuple[int, dict[str, Any]]] = []
    for ex in indices["style"].get("examples", []):
        if not _issue_allowed(ex.get("issue_id"), max_issue, excl_set):
            continue
        if section and ex.get("section") != section:
            continue
        ex_score = 0
        if event_type and ex.get("event_type") == event_type:
            ex_score += 50
        if q_norm:
            if any(q_norm in g.casefold() for g in ex.get("game_names", [])):
                ex_score += 40
            elif any(q_norm in b.casefold() for b in ex.get("brands", [])):
                ex_score += 25
            elif q_norm in ex.get("title", "").casefold():
                ex_score += 15
        if event_type and ex_score == 0:
            continue
        if not event_type and not q_norm and not section:
            ex_score = 1
        if ex_score > 0:
            candidate_examples.append((ex_score, ex))

    candidate_examples.sort(key=lambda pair: (-pair[0], -int(pair[1]["issue_id"]) if str(pair[1]["issue_id"]).isdigit() else 0))
    selected_examples = [pair[1] for pair in candidate_examples[:limit]]

    # 3. Summarize historical selection patterns for this query/account/event_type
    selected_hits: list[dict[str, Any]] = []
    unselected_hits: list[dict[str, Any]] = []
    section_distribution: dict[str, int] = {}

    for issue_rec in indices["selection"].get("issues", []):
        if not _issue_allowed(issue_rec.get("issue_id"), max_issue, excl_set):
            continue
        for ev in issue_rec.get("selected_events", []):
            match_ev = False
            if acc_norm and any(acc_norm == a.casefold() for a in ev.get("status_accounts", [])):
                match_ev = True
            if q_norm and (
                q_norm in ev.get("title", "").casefold()
                or any(q_norm in g.casefold() for g in ev.get("game_names", []))
                or any(q_norm in b.casefold() for b in ev.get("brands", []))
            ):
                match_ev = True
            if event_type and ev.get("event_type") == event_type and not (q_norm or acc_norm):
                match_ev = True
            if match_ev:
                sec = ev.get("section", "新作")
                section_distribution[sec] = section_distribution.get(sec, 0) + 1
                selected_hits.append(
                    {
                        "issue_id": issue_rec["issue_id"],
                        "section": sec,
                        "event_type": ev.get("event_type"),
                        "title": ev.get("title"),
                        "merged_tweet_count": ev.get("merged_tweet_count", 1),
                    }
                )
        for unsel in issue_rec.get("unselected_manual_items", []):
            match_un = False
            if acc_norm and any(acc_norm == a.casefold() for a in unsel.get("status_accounts", [])):
                match_un = True
            if q_norm and q_norm in unsel.get("text_preview", "").casefold():
                match_un = True
            if match_un:
                unselected_hits.append(
                    {
                        "issue_id": issue_rec["issue_id"],
                        "section": unsel.get("section"),
                        "status_accounts": unsel.get("status_accounts", []),
                        "text_preview": unsel.get("text_preview", ""),
                        "reference_only": True,
                    }
                )

    return {
        "status": "ok",
        "filters_applied": {
            "query": query,
            "event_type": event_type,
            "section": section,
            "account": account,
            "max_issue": max_issue,
            "exclude_issues": sorted(excl_set),
        },
        "usage_rules": {
            "background_rule": "作品背景（原版发售时间、原画/剧本阵容、系列关系）需结合当周信息核实，历史发售日/价格/语言仅作核实线索，严禁把范例中的背景直接套进新稿。",
            "style_rule": "保留“本周的新变化 → 必要的作品背景 → 发售、预约或活动信息 → 简短引导语”结构，不复制旧稿署名、编辑批注或残留日文宣传语。",
        },
        "matched_works": matched_works,
        "style_examples": selected_examples,
        "selection_patterns": {
            "historical_selected_count": len(selected_hits),
            "section_distribution": section_distribution,
            "recent_selected": selected_hits[-limit:],
            "historical_unselected_reference": unselected_hits[-limit:],
        },
    }
