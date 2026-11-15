"""CLI implementation for the weekly-script skill."""

from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import sys

from .exporter import export_all_deliverables
from .indexer import build_indices
from .parsers import (
    parse_editorial_draft_docx,
    parse_manual_material_docx,
    parse_x_summary_docx,
)
from .retriever import retrieve_context
from .verifier import verify_run_deliverables

JP_BRACKET_TITLE_RE = re.compile(r"『([^』]{2,60})』|「([^」]{2,60})」|《([^》]{2,60})》|【([^】]{2,40})】")
HIGH_SIGNAL_RE = re.compile(
    r"マスターアップ|Master\s*Up|制作完成|体験版|体验版|Demo|発売決定|発売日|発売|发售|予約|预约|预订|"
    r"ティザー|公式サイト|サイト公開|サイト更新|ホームページ更新|オープン|公開|オープニング|ムービー|"
    r"新規CG|サンプルCG|CGを\d+枚|背景画像|ギャラリー|サンプルボイス|キャスト|声優|"
    r"Switch|PS4|PS5|Steam|官中|中文|漢化|汉化|"
    r"now\s+available|available\s+now|launch\s+discount|wish\s*list|coming\s+to\s+Steam|officially\s+released|"
    r"Chinese|Simplified\s+Chinese|Traditional\s+Chinese|"
    r"グッズ|ポップアップ|POP\s*UP|コラボカフェ|抱き枕|クラウドファンディング|イベント|"
    r"フィギュア|ねんどろいど|ぬいぐるみ|ドール|アクリル|タペストリー|通販|再販|復刻|受注|"
    r"オンラインくじ|くじ|ASMR|ボイスドラマ|サウンドトラック|OST|配信開始|キャラクターソング|キャラソン|"
    r"ラジオ|ショートストーリー|連載決定|コラボ|フェア|美少女コレクション",
    re.IGNORECASE,
)
NOISE_SIGNAL_RE = re.compile(
    r"当選者発表|プレゼントキャンペーン|フォロー＆リポスト|フォロー&RT|フォロー＆RP|フォロー&RP|送料無料|おはよう|おやすみ|"
    r"発売中(?:です|！)|好評発売中|カウントダウン|あと\d+日",
    re.IGNORECASE,
)
SUBSTANTIVE_UPDATE_RE = re.compile(
    r"体験版|マスターアップ|サイト公開|サンプルCG|新規CG|CGを\d+枚|ギャラリー|サンプルボイス|背景画像|"
    r"発売日決定|予約開始|予約受付開始|通販|再販|復刻|受注開始|フィギュア化|ねんどろいど",
    re.IGNORECASE,
)
CN_MONTH_NAMES = {
    1: "一月",
    2: "二月",
    3: "三月",
    4: "四月",
    5: "五月",
    6: "六月",
    7: "七月",
    8: "八月",
    9: "九月",
    10: "十月",
    11: "十一月",
    12: "十二月",
}


def _last_friday_of_month(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    offset = (d.weekday() - calendar.FRIDAY) % 7
    return date(year, month, last_day - offset)


def _detect_monthly_release_window(date_range_str: str) -> dict[str, Any]:
    dates = re.findall(r"(\d{4})-(\d{2})-(\d{2})", date_range_str)
    if len(dates) >= 2:
        try:
            start_d = date(int(dates[0][0]), int(dates[0][1]), int(dates[0][2]))
            end_d = date(int(dates[1][0]), int(dates[1][1]), int(dates[1][2]))
            for y, m in {(start_d.year, start_d.month), (end_d.year, end_d.month)}:
                last_fri = _last_friday_of_month(y, m)
                if start_d <= last_fri <= end_d:
                    m_label = CN_MONTH_NAMES.get(m, f"{m}月")
                    return {
                        "applicable": True,
                        "year": y,
                        "month": m,
                        "month_label": m_label,
                        "release_friday": last_fri.isoformat(),
                        "recommended_title": f"{m_label}新作本周发售",
                        "note": (
                            f"本期覆盖 {y} 年 {m} 月最后一个周五集中发售日（{last_fri.isoformat()}）。"
                            f"按往期成稿惯例，应在「新作」栏首条合并撰写《{m_label}新作本周发售》汇总所有本周发售的 PC/主机新作，"
                            "避免将清单内的常规发售作品重复拆分为多条独立的「正式发售」长新闻。"
                        ),
                    }
        except ValueError:
            pass
    return {"applicable": False}


def _extract_jp_candidates(text: str) -> list[str]:
    titles: list[str] = []
    for m in JP_BRACKET_TITLE_RE.finditer(text):
        val = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip()
        if not val:
            continue
        # Ignore generic announcement tags in 【...】
        if m.group(4) and re.search(r"お知らせ|情報|告知|更新|予約|発売|特典|紹介|キャンペーン|マスターアップ", val):
            continue
        if val not in titles:
            titles.append(val)
    return titles[:3]


def _classify_tweet_signal(tw: dict[str, Any]) -> tuple[str, str]:
    flags = tw.get("flags", {})
    text = tw.get("clean_text", "") or tw.get("text", "")
    if flags.get("old_retweet_hint"):
        return "old_retweet_warning", "转发的原推文发布时间明显早于本期统计范围，需确认是否有本周新增事实"
    if flags.get("image_only_hint") and not HIGH_SIGNAL_RE.search(text):
        return "image_only_warning", "正文极短或仅有图片/链接提示，无法仅凭文字确认核心事实"
    if NOISE_SIGNAL_RE.search(text) and not SUBSTANTIVE_UPDATE_RE.search(text):
        return "routine_promo_or_chat", "疑似常规转发抽奖、倒计时、已发售重复宣传或日常互动"
    if HIGH_SIGNAL_RE.search(text):
        return "candidate_news", "包含新作/体验版/制作完成/官网更新/预约发售/汉化/周边等关键词"
    return "low_signal", "未命中显著进度关键词，建议结合账号与上下文复核"


def doctor_command(
    reports_dir: Path | None = None,
    drafts_dir: Path | None = None,
    index_dir: Path | None = None,
) -> dict[str, Any]:
    py_ok = sys.version_info >= (3, 11)
    checks: dict[str, Any] = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_311_plus": py_ok,
        "ooxml_zip_parser": True,
    }
    paths_info: dict[str, Any] = {}
    if reports_dir:
        paths_info["reports_dir"] = {"path": str(reports_dir), "exists": reports_dir.is_dir()}
    if drafts_dir:
        paths_info["drafts_dir"] = {"path": str(drafts_dir), "exists": drafts_dir.is_dir()}
    if index_dir:
        paths_info["index_dir"] = {
            "path": str(index_dir),
            "exists": index_dir.is_dir(),
            "ready": all((index_dir / f).is_file() for f in ("selection_index.json", "style_examples.json", "works_index.json")),
        }
    return {
        "status": "ok" if py_ok else "error",
        "skill": "weekly-script",
        "checks": checks,
        "paths": paths_info,
    }


def prepare_run(
    *,
    issue_id: str,
    date_range: str,
    x_docx_paths: list[Path],
    manual_docx_paths: list[Path],
    output_root: Path,
    index_dir: Path | None = None,
    max_issue: int | None = None,
    exclude_issues: list[str] | None = None,
) -> dict[str, Any]:
    clean_issue = str(issue_id).strip()
    clean_range = str(date_range).strip()
    if not clean_issue or clean_issue.lower() in {"x", "unknown", "none", "?"}:
        raise ValueError("期号 (--issue) 必须明确指定，不能凭文件排序猜测")
    if not clean_range or clean_range.lower() in {"x.x-x.x", "unknown", "none", "?"}:
        raise ValueError("报道起止日期 (--date-range) 必须明确指定，不能凭文件排序猜测")
    if not x_docx_paths and not manual_docx_paths:
        raise ValueError("至少需要提供一份 X 汇总 DOCX (--x-docx) 或人工补充素材 (--manual-docx)")

    for p in x_docx_paths + manual_docx_paths:
        if not p.is_file():
            raise ValueError(f"输入文件不存在: {p}")

    # Parse start date if YYYY-MM-DD appears in date_range
    m_start = re.search(r"(\d{4}-\d{2}-\d{2})", clean_range)
    period_start = m_start.group(1) if m_start else None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"issue_{clean_issue}_{timestamp}"
    suffix = 1
    while run_dir.exists():
        run_dir = output_root / f"issue_{clean_issue}_{timestamp}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)

    # 1. Parse X summary files
    x_file_reports: list[dict[str, Any]] = []
    title_only_files: list[str] = []
    raw_tweets: list[dict[str, Any]] = []

    for x_path in x_docx_paths:
        parsed_x = parse_x_summary_docx(x_path, period_start=period_start)
        x_file_reports.append(
            {
                "file_name": parsed_x["file_name"],
                "file_path": parsed_x["file_path"],
                "tweet_count": parsed_x["tweet_count"],
                "is_title_only": parsed_x["is_title_only"],
                "status": parsed_x["status"],
                "header_range": parsed_x["header_range"],
            }
        )
        if parsed_x["is_title_only"]:
            title_only_files.append(parsed_x["file_name"])
        raw_tweets.extend(parsed_x["tweets"])

    # 2. Deduplicate tweets by status_id (fallback to account + published_at + clean_text)
    dedup_map: dict[str, dict[str, Any]] = {}
    cross_file_duplicates = 0

    for tw in raw_tweets:
        sid = tw.get("status_id")
        key = f"sid:{sid}" if sid else f"txt:{tw['account']}:{tw['published_at']}:{tw['clean_text'][:80]}"
        if key in dedup_map:
            existing = dedup_map[key]
            if tw["source_file"] not in existing["source_files"]:
                existing["source_files"].append(tw["source_file"])
                cross_file_duplicates += 1
            for u in tw.get("external_urls", []):
                if u not in existing["external_urls"]:
                    existing["external_urls"].append(u)
        else:
            signal, signal_reason = _classify_tweet_signal(tw)
            detected_titles = _extract_jp_candidates(tw.get("clean_text", ""))
            entry_copy = dict(tw)
            entry_copy["source_files"] = [tw["source_file"]]
            entry_copy["detected_titles"] = detected_titles
            entry_copy["signal"] = signal
            entry_copy["signal_reason"] = signal_reason
            dedup_map[key] = entry_copy

    unique_tweets = list(dedup_map.values())

    # 3. Parse optional manual supplement files
    manual_reports: list[dict[str, Any]] = []
    manual_items: list[dict[str, Any]] = []
    for m_path in manual_docx_paths:
        parsed_m = parse_manual_material_docx(m_path)
        manual_reports.append(
            {
                "file_name": parsed_m["file_name"],
                "file_path": parsed_m["file_path"],
                "item_count": parsed_m["item_count"],
                "status_id_count": len(parsed_m["status_ids"]),
            }
        )
        manual_items.extend(parsed_m["items"])

    # 4. Group candidate clusters by (account, primary detected title) to assist event merging
    clusters_map: dict[str, dict[str, Any]] = {}
    for tw in unique_tweets:
        if tw["signal"] in {"routine_promo_or_chat", "old_retweet_warning"}:
            continue
        primary_title = tw["detected_titles"][0] if tw["detected_titles"] else ""
        cluster_key = f"{tw['account'].casefold()}::{primary_title.casefold()}" if primary_title else f"{tw['account'].casefold()}::sid_{tw.get('status_id') or tw['paragraph_index']}"
        if cluster_key not in clusters_map:
            clusters_map[cluster_key] = {
                "cluster_key": cluster_key,
                "account": tw["account"],
                "primary_title_hint": primary_title or None,
                "tweet_count": 0,
                "status_ids": [],
                "external_urls": [],
                "tweets": [],
            }
        c = clusters_map[cluster_key]
        c["tweet_count"] += 1
        if tw.get("status_id") and tw["status_id"] not in c["status_ids"]:
            c["status_ids"].append(tw["status_id"])
        for u in tw.get("external_urls", []):
            if u not in c["external_urls"]:
                c["external_urls"].append(u)
        c["tweets"].append(
            {
                "status_id": tw.get("status_id"),
                "published_at": tw["published_at"],
                "tweet_type": tw["tweet_type"],
                "signal": tw["signal"],
                "clean_text": tw["clean_text"],
                "status_url": tw.get("status_url"),
            }
        )

    candidate_clusters = sorted(
        clusters_map.values(),
        key=lambda c: (-c["tweet_count"], c["account"]),
    )

    # 5. Determine overall input coverage status
    if len(unique_tweets) == 0 and len(manual_items) == 0:
        coverage_status = "empty_input_blocked"
        run_status = "empty_input_blocked"
    elif title_only_files:
        coverage_status = "partial_input_missing"
        run_status = "prepared"
    else:
        coverage_status = "ok"
        run_status = "prepared"

    holdout_constraints = {
        "max_issue": max_issue,
        "exclude_issues": sorted({str(x).strip() for x in (exclude_issues or []) if str(x).strip()}),
    }
    monthly_release_hint = _detect_monthly_release_window(clean_range)

    input_audit = {
        "issue_id": clean_issue,
        "date_range": clean_range,
        "coverage_status": coverage_status,
        "monthly_release_roundup_hint": monthly_release_hint,
        "x_files": x_file_reports,
        "title_only_files": title_only_files,
        "manual_files": [m["file_name"] for m in manual_reports],
        "manual_reports": manual_reports,
        "raw_tweet_count": len(raw_tweets),
        "unique_tweet_count": len(unique_tweets),
        "cross_file_duplicates": cross_file_duplicates,
        "candidate_cluster_count": len(candidate_clusters),
        "manual_item_count": len(manual_items),
        "signal_breakdown": {
            sig: sum(1 for t in unique_tweets if t["signal"] == sig)
            for sig in ("candidate_news", "low_signal", "routine_promo_or_chat", "old_retweet_warning", "image_only_warning")
        },
    }

    parsed_payload = {
        "issue_id": clean_issue,
        "date_range": clean_range,
        "coverage_status": coverage_status,
        "monthly_release_roundup_hint": monthly_release_hint,
        "holdout_constraints": holdout_constraints,
        "candidate_clusters": candidate_clusters,
        "manual_items": manual_items,
        "tweets": unique_tweets,
    }

    run_meta = {
        "status": run_status,
        "issue_id": clean_issue,
        "date_range": clean_range,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir.resolve()),
        "index_dir": str(index_dir.resolve()) if index_dir else None,
        "holdout_constraints": holdout_constraints,
        "monthly_release_roundup_hint": monthly_release_hint,
        "coverage_status": coverage_status,
        "files": {
            "input_audit": str((run_dir / "input_audit.json").resolve()),
            "parsed_tweets": str((run_dir / "parsed_tweets.json").resolve()),
            "draft_plan": str((run_dir / "draft_plan.json").resolve()),
        },
    }

    (run_dir / "input_audit.json").write_text(json.dumps(input_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "parsed_tweets.json").write_text(json.dumps(parsed_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": run_status,
        "coverage_status": coverage_status,
        "run_dir": str(run_dir.resolve()),
        "input_audit": input_audit,
        "holdout_constraints": holdout_constraints,
        "message": (
            "所有输入文件均仅含标题或无有效内容，已按输入缺失停止生成正文。"
            if run_status == "empty_input_blocked"
            else "输入解析与去重完成，请审阅 parsed_tweets.json、检索背景并核实当周事实后写入 draft_plan.json。"
        ),
    }


def render_run(
    run_dir: Path | str,
    plan_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(run_dir)
    if not root.is_dir():
        raise ValueError(f"运行目录不存在: {root}")
    run_meta_file = root / "run.json"
    if not run_meta_file.is_file():
        raise ValueError(f"缺少 run.json: {run_meta_file}")

    run_meta = json.loads(run_meta_file.read_text(encoding="utf-8"))
    if run_meta.get("status") == "empty_input_blocked":
        raise ValueError("该批次输入为空（仅含标题），禁止生成周报正文")

    verification_file = root / "verification.json"
    if verification_file.is_file() and not force:
        existing_ver = json.loads(verification_file.read_text(encoding="utf-8"))
        if existing_ver.get("status") == "verified":
            raise ValueError("该批次已通过验证 (verified)，如需重新导出请显式传入 --force")

    target_plan = plan_path or (root / "draft_plan.json")
    if not target_plan.is_file():
        raise ValueError(f"未找到写作计划文件: {target_plan}")

    plan = json.loads(target_plan.read_text(encoding="utf-8"))
    if target_plan.resolve() != (root / "draft_plan.json").resolve():
        (root / "draft_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_file = root / "input_audit.json"
    input_audit = json.loads(audit_file.read_text(encoding="utf-8")) if audit_file.is_file() else None

    export_result = export_all_deliverables(
        plan,
        root,
        input_audit=input_audit,
    )
    verification = verify_run_deliverables(root)

    run_meta["status"] = "verified" if verification["status"] == "verified" else "exported_with_errors"
    run_meta["outputs"] = export_result
    run_meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    run_meta_file.write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": run_meta["status"],
        "run_dir": str(root.resolve()),
        "outputs": export_result,
        "verification": verification,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Galgame 周报文案生成与核验工具 (weekly-script)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="静态检查环境与可选语料/索引路径")
    p_doc.add_argument("--reports-dir", type=Path, default=None)
    p_doc.add_argument("--drafts-dir", type=Path, default=None)
    p_doc.add_argument("--index-dir", type=Path, default=None)

    p_idx = sub.add_parser("build-index", help="从本地历史分类素材、X汇总与中文成稿构建三类索引")
    p_idx.add_argument("--reports-dir", type=Path, required=True, help="包含 周报/ 与 x新闻/ 的目录")
    p_idx.add_argument("--drafts-dir", type=Path, required=True, help="中文周报成稿 DOCX 目录")
    p_idx.add_argument("--output", type=Path, required=True, help="本地索引输出目录")

    p_prep = sub.add_parser("prepare", help="检查输入、解析去重推文并创建独立运行批次")
    p_prep.add_argument("--issue", required=True, help="明确的期号（如 267）")
    p_prep.add_argument("--date-range", required=True, help="明确的报道范围（如 2026-10-24~2026-10-30）")
    p_prep.add_argument("--x-docx", type=Path, nargs="*", default=[], help="一份或多份 X 汇总 DOCX 路径")
    p_prep.add_argument("--manual-docx", type=Path, nargs="*", default=[], help="可选的人工分类或补充素材 DOCX 路径")
    p_prep.add_argument("--index-dir", type=Path, default=None, help="可选的本地索引目录")
    p_prep.add_argument("--output", type=Path, required=True, help="运行批次输出根目录")
    p_prep.add_argument("--max-issue", type=int, default=None, help="留出测试：允许检索的最大历史期号")
    p_prep.add_argument("--exclude-issue", nargs="*", default=[], help="留出测试：禁止检索的特定期号")

    p_ret = sub.add_parser("retrieve", help="按作品、品牌、账号或事件类型检索背景、范例与历史选题偏好")
    p_ret.add_argument("--index-dir", type=Path, required=True, help="本地索引目录")
    p_ret.add_argument("--query", default=None, help="作品名、品牌名或关键词")
    p_ret.add_argument("--event-type", default=None, help="事件类型（如 体验版、制作完成、官网更新、主机移植、正式发售、预约与发售日、新作发表、汉化与海外发行、周边与活动）")
    p_ret.add_argument("--section", default=None, help="栏目（新作、汉化、周边）")
    p_ret.add_argument("--account", default=None, help="X 账号名")
    p_ret.add_argument("--max-issue", type=int, default=None, help="留出测试：最大允许期号")
    p_ret.add_argument("--exclude-issue", nargs="*", default=[], help="留出测试：排除的期号")
    p_ret.add_argument("--limit", type=int, default=5, help="每类返回条数上限")

    p_ren = sub.add_parser("render", help="根据 draft_plan.json 导出四件套产物并执行结构核验")
    p_ren.add_argument("run_dir", type=Path, help="prepare 创建的批次目录")
    p_ren.add_argument("--plan", type=Path, default=None, help="可选的 draft_plan.json 路径")
    p_ren.add_argument("--force", action="store_true", help="允许重新导出已验证批次")

    p_ver = sub.add_parser("verify", help="对运行目录中的四件套产物进行结构与可追溯性核验")
    p_ver.add_argument("run_dir", type=Path, help="运行批次目录")

    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor_command(args.reports_dir, args.drafts_dir, args.index_dir)
        elif args.command == "build-index":
            result = build_indices(args.reports_dir, args.drafts_dir, args.output)
        elif args.command == "prepare":
            result = prepare_run(
                issue_id=args.issue,
                date_range=args.date_range,
                x_docx_paths=args.x_docx,
                manual_docx_paths=args.manual_docx,
                output_root=args.output,
                index_dir=args.index_dir,
                max_issue=args.max_issue,
                exclude_issues=args.exclude_issue,
            )
        elif args.command == "retrieve":
            result = retrieve_context(
                args.index_dir,
                query=args.query,
                event_type=args.event_type,
                section=args.section,
                account=args.account,
                max_issue=args.max_issue,
                exclude_issues=args.exclude_issue,
                limit=args.limit,
            )
        elif args.command == "render":
            result = render_run(
                args.run_dir,
                plan_path=args.plan,
                force=args.force,
            )
        elif args.command == "verify":
            result = verify_run_deliverables(args.run_dir)
        else:
            raise ValueError(f"未知命令: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in {"failed", "error", "exported_with_errors"} else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
