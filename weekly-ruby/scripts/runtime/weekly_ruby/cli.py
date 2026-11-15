"""CLI implementation for the weekly-ruby skill."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import sys

from .core import annotate_document, annotate_text, normalize_pairs, resolve_candidates
from .io_handlers import (
    _read_docx_raw,
    load_input_document,
    render_annotated_markdown,
    render_clean_markdown,
    write_ruby_docx,
    write_ruby_evidence_json,
    write_ruby_report_md,
)

FORBIDDEN_AUTHOR_TAG_RE = re.compile(r"#[^#\s]{1,20}#")


def _safe_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "_", name).strip("_")
    return slug[:40] or "draft"


def doctor_command(
    input_path: Path | None = None,
    pairs_path: Path | None = None,
) -> dict[str, Any]:
    py_ok = sys.version_info >= (3, 11)
    checks: dict[str, Any] = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_311_plus": py_ok,
        "ooxml_zip_reader": True,
    }
    paths_info: dict[str, Any] = {}
    status = "ok" if py_ok else "error"

    if input_path is not None:
        exists = input_path.is_file()
        paths_info["input"] = {"path": str(input_path), "exists": exists}
        if not exists:
            status = "error"
    if pairs_path is not None:
        exists = pairs_path.is_file()
        valid_json = False
        if exists:
            try:
                json.loads(pairs_path.read_text(encoding="utf-8"))
                valid_json = True
            except json.JSONDecodeError:
                valid_json = False
        paths_info["pairs"] = {"path": str(pairs_path), "exists": exists, "valid_json": valid_json}
        if not exists or not valid_json:
            status = "error"

    return {
        "status": status,
        "skill": "weekly-ruby",
        "checks": checks,
        "paths": paths_info,
    }


def _load_pairs_file(pairs_path: Path | None) -> dict[str, str]:
    if pairs_path is None:
        return {}
    if not pairs_path.is_file():
        raise ValueError(f"词典文件不存在: {pairs_path}")
    raw = json.loads(pairs_path.read_text(encoding="utf-8"))
    return dict(normalize_pairs(raw))


def prepare_ruby_run(
    *,
    input_path: Path,
    output_root: Path,
    pairs_path: Path | None = None,
    use_vndb: bool = True,
    strip_urls: bool = True,
) -> dict[str, Any]:
    doc = load_input_document(input_path, strip_urls=strip_urls)
    file_pairs = _load_pairs_file(pairs_path)

    merged_seed_pairs: dict[str, str] = {}
    merged_seed_pairs.update(doc.get("ruby_pairs") or {})
    merged_seed_pairs.update(file_pairs)

    chunks: list[str] = []
    for entry in doc["entries"]:
        if entry.get("title"):
            chunks.append(entry["title"])
        chunks.extend(entry.get("body_paragraphs", []))

    resolved = resolve_candidates(
        "\n".join(chunks),
        extra_pairs=merged_seed_pairs,
        use_vndb=use_vndb,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"ruby_{_safe_slug(input_path.stem)}_{timestamp}"
    suffix = 1
    while run_dir.exists():
        run_dir = output_root / f"ruby_{_safe_slug(input_path.stem)}_{timestamp}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)

    candidates_payload = {
        "source_path": doc["source_path"],
        "source_type": doc["source_type"],
        "doc_title": doc["doc_title"],
        "candidates": resolved["candidates"],
        "vndb_hits": resolved["vndb_hits"],
        "local_hits": resolved["local_hits"],
        "extra_hits": resolved["extra_hits"],
        "unresolved_misses": resolved["unresolved_misses"],
        "vndb_service_error": resolved["vndb_service_error"],
    }
    run_meta = {
        "status": "prepared",
        "skill": "weekly-ruby",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir.resolve()),
        "source_path": doc["source_path"],
        "source_type": doc["source_type"],
        "strip_urls": strip_urls,
        "entry_count": len(doc["entries"]),
    }

    (run_dir / "input_normalized.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "candidates.json").write_text(json.dumps(candidates_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "ruby_pairs.json").write_text(json.dumps(merged_seed_pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "prepared",
        "run_dir": str(run_dir.resolve()),
        "source_type": doc["source_type"],
        "entry_count": len(doc["entries"]),
        "candidate_count": len(resolved["candidates"]),
        "vndb_hit_count": len(resolved["vndb_hits"]),
        "local_hit_count": len(resolved["local_hits"]),
        "unresolved_misses": resolved["unresolved_misses"],
        "vndb_service_error": resolved["vndb_service_error"],
        "files": {
            "input_normalized": str((run_dir / "input_normalized.json").resolve()),
            "candidates": str((run_dir / "candidates.json").resolve()),
            "ruby_pairs": str((run_dir / "ruby_pairs.json").resolve()),
        },
        "message": (
            "存在未命中含假名专名，请在 ruby_pairs.json 中补齐后再运行 render。"
            if resolved["unresolved_misses"]
            else "候选词已全部解析完成，可直接运行 render 导出带注音口播稿。"
        ),
    }


def verify_ruby_run(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir)
    errors: list[str] = []
    warnings: list[str] = []

    norm_path = root / "input_normalized.json"
    voice_path = root / "口播注音稿.md"
    report_path = root / "注音报告.md"
    ev_path = root / "ruby_evidence.json"
    run_path = root / "run.json"

    for label, p in (
        ("input_normalized.json", norm_path),
        ("口播注音稿.md", voice_path),
        ("注音报告.md", report_path),
        ("ruby_evidence.json", ev_path),
    ):
        if not p.is_file():
            errors.append(f"缺少必需产物文件: {label}")

    if errors:
        return {"status": "failed", "errors": errors, "warnings": warnings}

    doc = json.loads(norm_path.read_text(encoding="utf-8"))
    evidence = json.loads(ev_path.read_text(encoding="utf-8"))
    run_meta = json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {}
    strip_urls = bool(run_meta.get("strip_urls", True))

    voice_text = voice_path.read_text(encoding="utf-8")
    clean_baseline = render_clean_markdown(doc)

    if strip_urls and re.search(r"https?://", voice_text, re.I):
        errors.append("口播注音稿.md 中残留了 URL 链接")
    if FORBIDDEN_AUTHOR_TAG_RE.search(voice_text):
        errors.append("口播注音稿.md 中残留了 #作者# 标签")
    if voice_text.count("【") != voice_text.count("】"):
        errors.append("口播注音稿.md 中方括号【】数量不匹配")
    if re.search(r"《[^》]*【[^】]*】[^》]*》", voice_text):
        errors.append("口播注音稿.md 中存在注音【】误插入书名号《》内部的情况")

    # Verify exact 1:1 restoration when stripping 【...】 rubies (provided baseline itself had no 【】)
    if "【" not in clean_baseline:
        restored = re.sub(r"【[^】]*】", "", voice_text)
        if re.sub(r"\s+", "", restored) != re.sub(r"\s+", "", clean_baseline):
            errors.append("1:1 校验失败：去除【】注音后的文本与输入底稿不一致")

    docx_path = root / "口播注音版.docx"
    if docx_path.is_file():
        try:
            _read_docx_raw(docx_path)
        except ValueError as exc:
            errors.append(f"导出的 口播注音版.docx 无法读取: {exc}")

    misses = evidence.get("unresolved_misses", [])
    if misses:
        warnings.append(
            "仍有含假名日文专名未能自动注音: "
            + ", ".join(misses)
            + "（可在 ruby_pairs.json 中补充后重跑 render --force）"
        )

    status = "verified" if not errors else "failed"
    verification = {
        "status": status,
        "entry_count": len(doc.get("entries", [])),
        "applied_count": len(evidence.get("applied", [])),
        "unresolved_misses": misses,
        "errors": errors,
        "warnings": warnings,
    }
    (root / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    return verification


def render_ruby_run(
    run_dir: Path | str,
    *,
    pairs_path: Path | None = None,
    use_vndb: bool = True,
    export_docx: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(run_dir)
    if not root.is_dir():
        raise ValueError(f"运行目录不存在: {root}")
    run_meta_file = root / "run.json"
    norm_file = root / "input_normalized.json"
    if not run_meta_file.is_file() or not norm_file.is_file():
        raise ValueError(f"运行目录缺少 run.json 或 input_normalized.json: {root}")

    ver_file = root / "verification.json"
    if ver_file.is_file() and not force:
        if json.loads(ver_file.read_text(encoding="utf-8")).get("status") == "verified":
            raise ValueError("该注音批次已通过验证 (verified)，如需重新导出请显式传入 --force")

    doc = json.loads(norm_file.read_text(encoding="utf-8"))
    run_pairs_file = root / "ruby_pairs.json"
    merged_pairs: dict[str, str] = {}
    if run_pairs_file.is_file():
        merged_pairs.update(_load_pairs_file(run_pairs_file))
    if pairs_path is not None:
        merged_pairs.update(_load_pairs_file(pairs_path))

    ruby_res = annotate_document(doc, extra_pairs=merged_pairs, use_vndb=use_vndb)
    voice_md_path = root / "口播注音稿.md"
    voice_md_path.write_text(render_annotated_markdown(ruby_res), encoding="utf-8")

    report_path = write_ruby_report_md(ruby_res, doc, root / "注音报告.md")
    ev_path = write_ruby_evidence_json(ruby_res, doc, root / "ruby_evidence.json")

    outputs: dict[str, str] = {
        "voiceover_ruby_md": str(voice_md_path.resolve()),
        "ruby_report_md": str(report_path.resolve()),
        "ruby_evidence_json": str(ev_path.resolve()),
    }
    if export_docx:
        docx_out = write_ruby_docx(ruby_res, root / "口播注音版.docx")
        outputs["voiceover_ruby_docx"] = str(docx_out.resolve())

    verification = verify_ruby_run(root)

    run_meta = json.loads(run_meta_file.read_text(encoding="utf-8"))
    run_meta["status"] = "verified" if verification["status"] == "verified" else "exported_with_errors"
    run_meta["outputs"] = outputs
    run_meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    run_meta_file.write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": run_meta["status"],
        "run_dir": str(root.resolve()),
        "outputs": outputs,
        "ruby_summary": {
            "candidate_count": len(ruby_res["candidates"]),
            "vndb_hit_count": len(ruby_res["vndb_hits"]),
            "local_hit_count": len(ruby_res["local_hits"]),
            "extra_hit_count": len(ruby_res["extra_hits"]),
            "applied_count": len(ruby_res["applied"]),
            "unresolved_misses": ruby_res["unresolved_misses"],
            "vndb_service_error": ruby_res["vndb_service_error"],
        },
        "verification": verification,
    }


def annotate_quick_command(
    *,
    text: str | None = None,
    input_path: Path | None = None,
    pairs_path: Path | None = None,
    use_vndb: bool = True,
    strip_urls: bool = True,
) -> dict[str, Any]:
    extra = _load_pairs_file(pairs_path)
    if text is not None:
        res = annotate_text(text, extra_pairs=extra, use_vndb=use_vndb)
        return {"status": "ok" if res["ok"] else "warning", **res}
    if input_path is not None:
        doc = load_input_document(input_path, strip_urls=strip_urls)
        res = annotate_document(doc, extra_pairs=extra, use_vndb=use_vndb)
        return {
            "status": "ok" if res["ok"] else "warning",
            "annotated_markdown": render_annotated_markdown(res),
            "candidates": res["candidates"],
            "vndb_hits": res["vndb_hits"],
            "local_hits": res["local_hits"],
            "extra_hits": res["extra_hits"],
            "unresolved_misses": res["unresolved_misses"],
            "applied": res["applied"],
            "vndb_service_error": res["vndb_service_error"],
        }
    raise ValueError("请提供 --text 或 --input")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Galgame 周报口播日文注音工具 (weekly-ruby)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="静态检查环境与可选输入文件")
    p_doc.add_argument("--input", type=Path, default=None, help="可选的待注音文件 (.docx / .md / .txt / .json)")
    p_doc.add_argument("--pairs", type=Path, default=None, help="可选的 ruby_pairs.json 词典")

    p_prep = sub.add_parser("prepare", help="解析输入文档、抽取日文专名并创建独立注音批次")
    p_prep.add_argument("--input", type=Path, required=True, help="待注音文件 (周报初稿.docx / 口播稿.md / .txt / draft_plan.json)")
    p_prep.add_argument("--output", type=Path, required=True, help="注音批次输出根目录")
    p_prep.add_argument("--pairs", type=Path, default=None, help="可选的预设 ruby_pairs.json 词典")
    p_prep.add_argument("--offline", action="store_true", help="离线模式，跳过 VNDB 在线查询")
    p_prep.add_argument("--keep-urls", action="store_true", help="保留输入中的 URL（默认剔除，便于直接配音）")

    p_ren = sub.add_parser("render", help="应用注音、导出 口播注音稿.md 与 注音报告.md 并执行 1:1 核验")
    p_ren.add_argument("run_dir", type=Path, help="prepare 创建的批次目录")
    p_ren.add_argument("--pairs", type=Path, default=None, help="可选的额外 ruby_pairs.json 词典")
    p_ren.add_argument("--offline", action="store_true", help="离线模式，跳过 VNDB 在线查询")
    p_ren.add_argument("--export-docx", action="store_true", help="同步导出带注音的 Word 文件 (口播注音版.docx)")
    p_ren.add_argument("--force", action="store_true", help="允许重新导出已验证批次")

    p_ver = sub.add_parser("verify", help="对注音运行目录执行 1:1 保真与格式核验")
    p_ver.add_argument("run_dir", type=Path, help="注音批次目录")

    p_ann = sub.add_parser("annotate", help="单句或文件快速注音直出（不创建批次目录）")
    p_ann.add_argument("--text", default=None, help="待注音文本")
    p_ann.add_argument("--input", type=Path, default=None, help="待注音文件 (.docx / .md / .txt / .json)")
    p_ann.add_argument("--pairs", type=Path, default=None, help="可选的 ruby_pairs.json 词典")
    p_ann.add_argument("--offline", action="store_true", help="离线模式，跳过 VNDB 查询")
    p_ann.add_argument("--keep-urls", action="store_true", help="保留 URL")

    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor_command(args.input, args.pairs)
        elif args.command == "prepare":
            result = prepare_ruby_run(
                input_path=args.input,
                output_root=args.output,
                pairs_path=args.pairs,
                use_vndb=not args.offline,
                strip_urls=not args.keep_urls,
            )
        elif args.command == "render":
            result = render_ruby_run(
                args.run_dir,
                pairs_path=args.pairs,
                use_vndb=not args.offline,
                export_docx=args.export_docx,
                force=args.force,
            )
        elif args.command == "verify":
            result = verify_ruby_run(args.run_dir)
        elif args.command == "annotate":
            result = annotate_quick_command(
                text=args.text,
                input_path=args.input,
                pairs_path=args.pairs,
                use_vndb=not args.offline,
                strip_urls=not args.keep_urls,
            )
        else:
            raise ValueError(f"未知命令: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") not in {"failed", "error", "exported_with_errors"} else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
