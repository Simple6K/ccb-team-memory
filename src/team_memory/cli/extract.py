"""team-memory extract prompt / extract status / extract history — 提取管理命令。

V4.6: 新增 extract history 子命令（问题 1, 2）。
"""

import argparse
import json
import sys
from pathlib import Path

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..services.extract import (
    EXTRACT_MODES,
    build_extract_prompt,
    scan_manifest,
)
from ..services.extraction_manager import ExtractionManager
from ..utils.transcript import (
    find_project_dir,
    find_all_session_files,
    find_all_session_files_recursive,
    read_recent_messages,
    format_messages_for_api,
)


def _verbose(args: argparse.Namespace, msg: str) -> None:
    """输出诊断信息到 stderr（仅 --verbose 模式）。"""
    if getattr(args, "verbose", False):
        print(f"[team-memory] {msg}", file=sys.stderr)


def cmd_extract_prompt(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    mode = args.mode or config.extract.mode
    tm_dir = get_team_memory_dir(root)

    # ── V4.6: 初始化 ExtractionManager 用于注入状态上下文 ──
    manager = ExtractionManager(tm_dir)
    text = build_extract_prompt(config, root, mode=mode, extraction_manager=manager)
    if args.output:
        Path(args.output).write_text(text)
        print(f"Prompt written to {args.output}")
    else:
        print(text)


def cmd_extract_status(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("Team memory not configured.")
        return

    tm_dir = get_team_memory_dir(root)
    manager = ExtractionManager(tm_dir)
    state = manager._state

    print("─── Extraction Status ───")
    print(f"  Mode:         {config.extract.mode}")
    print(f"  Scope:        {config.extract.scope}")
    print(f"  Auto push:    {config.extract.auto_push}")
    print(f"  Team dir:     {tm_dir}")

    # 诊断：Stop hook 调用追踪
    if state.last_invocation_at:
        print(f"  Last invoked: {state.last_invocation_at}")
        print(f"  Invoke result: {state.last_invocation_result}")
    else:
        print("  Last invoked: (never — Stop hook 可能未触发)")

    if state.last_extraction_at:
        print(f"  Last extract: {state.last_extraction_at}")
        print(f"  Total:        {state.total_extractions}")
        print(f"  Team count:   {state.team_count}")
        print(f"  Project count: {state.project_count}")
        if state.last_files_written:
            print(f"  Last files:   {', '.join(state.last_files_written[:5])}")
    else:
        print("  Last extract: (never)")

    if tm_dir.is_dir():
        # _staging/ pending review
        staging_dir = tm_dir / "_staging"
        if staging_dir.is_dir():
            staging_entries = scan_manifest(staging_dir)
            print(f"  _staging/: {len(staging_entries)} pending review")
            if staging_entries:
                print(f"    Run 'team-memory review list' to view pending")

        for label, d in [
            ("shared", tm_dir / "shared"),
            ("projects", tm_dir / "projects"),
        ]:
            if d.is_dir():
                manifest = scan_manifest(d)
                type_counts: dict[str, int] = {}
                for entry in manifest:
                    t = entry.type or "unknown"
                    type_counts[t] = type_counts.get(t, 0) + 1
                type_str = ", ".join(f"{t}:{c}" for t, c in sorted(type_counts.items()))
                print(f"  {label}/:   {len(manifest)} files ({type_str})")


def cmd_extract_history(args: argparse.Namespace) -> None:
    """Show extraction history."""
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        return

    tm_dir = get_team_memory_dir(root)
    manager = ExtractionManager(tm_dir)
    state = manager._state

    print("─── Extraction History ───")
    if state.last_extraction_at is None:
        print("  No extraction records.")
        print()
        print("  Run 'team-memory extract prompt' to start first extraction.")
        return

    print(f"  Total extractions: {state.total_extractions}")
    print(f"  Last extraction:   {state.last_extraction_at}")
    print(f"  Team count:        {state.team_count}")
    print(f"  Project count:     {state.project_count}")
    if state.last_files_written:
        print(f"  Last files written ({len(state.last_files_written)}):")
        for f in state.last_files_written:
            print(f"    - {f}")

    new_files = manager.detect_writes_since_last()
    if new_files:
        print(f"\n  New writes detected since last extraction ({len(new_files)}):")
        for f in new_files[:10]:
            print(f"    - {f.relative_to(tm_dir)}")


def cmd_extract_run(args: argparse.Namespace) -> None:
    """自动提取 — 由 Stop hook 触发，完全异步。

    对标 ccb-dev 内置 executeExtractMemories 的 runForkedAgent 模式。
    """
    root = find_project_root()
    _verbose(args, f"project_root={root}")

    config = load_team_memory_config(root)
    if not config:
        _verbose(args, "skip: no team memory config")
        sys.exit(0)

    tm_dir = get_team_memory_dir(root)
    _verbose(args, f"tm_dir={tm_dir}")
    manager = ExtractionManager(tm_dir)

    # 冷却守卫
    if not manager.should_extract():
        manager.record_invocation("cooldown: 距上次提取不足 60 秒")
        _verbose(args, (
            f"skip: cooldown "
            f"(last_extraction_at={manager._state.last_extraction_at}, "
            f"in_progress={manager._in_progress})"
        ))
        sys.exit(0)

    from ..services.agent_loop import run_extraction_loop

    try:
        manager.mark_start()
        _verbose(args, "extraction started...")
        files = run_extraction_loop(config, root, tm_dir, verbose=getattr(args, "verbose", False))
        n = len(files) if files else 0
        _verbose(args, f"extraction done, {n} files written")
        if files:
            manager.mark_done(files)
            print(json.dumps(
                {"systemMessage": f"\u23fa Extracted {n} team memories"},
                ensure_ascii=False,
            ))
        else:
            manager.mark_skipped("无新记忆")
    except Exception as e:
        _verbose(args, f"extraction error: {e}")
        manager.mark_skipped("提取异常")
        sys.exit(0)

    # ── 条件推送 ──
    push_on_count = getattr(args, "push_on_count", None)
    push_on_minutes = getattr(args, "push_on_minutes", None)
    should_push = False

    if push_on_count and push_on_count > 0:
        count_ok = manager.should_push_by_count(push_on_count)
        _verbose(args, f"push_on_count={push_on_count} count_ok={count_ok}")
        if count_ok:
            should_push = True
    if push_on_minutes and push_on_minutes > 0:
        time_ok = manager.should_push_by_time(push_on_minutes)
        has_files = manager.should_push_by_count(1)
        _verbose(args, f"push_on_minutes={push_on_minutes} time_ok={time_ok} has_staging={has_files}")
        if time_ok and has_files:
            should_push = True

    if should_push:
        _verbose(args, "triggering push...")
        try:
            from ..services.sync import do_push
            ok, msg = do_push(config, root, quiet=True)
            _verbose(args, f"push result: ok={ok}")
            if ok:
                manager.mark_pushed()
        except Exception as e:
            _verbose(args, f"push error: {e}")


def cmd_extract_batch(args: argparse.Namespace) -> None:
    """批量历史会话提取：从多个 .jsonl 文件中批量提取记忆。"""
    project_root_arg = getattr(args, "project_root", None)
    root = Path(project_root_arg) if project_root_arg else find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    tm_dir = get_team_memory_dir(root)
    verbose = getattr(args, "verbose", False)

    # 1. 发现 session 文件
    source = getattr(args, "source", None)
    if source:
        source_path = Path(source)
        if source_path.is_file():
            session_files = [source_path]
        elif source_path.is_dir():
            session_files = find_all_session_files_recursive(source_path)
        else:
            print(f"源路径不存在: {source}", file=sys.stderr)
            sys.exit(1)
    else:
        project_dir = find_project_dir(root)
        if not project_dir:
            print("未找到项目 session 目录。使用 --source 指定路径。", file=sys.stderr)
            sys.exit(1)
        session_files = find_all_session_files(project_dir)

    if not session_files:
        print("未找到任何 .jsonl 会话文件。")
        return

    max_sessions = getattr(args, "max_sessions", None)
    if max_sessions is not None:
        session_files = session_files[:max_sessions]

    # 2. dry-run 模式
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print(f"─── 批量提取预览（{len(session_files)} 个会话）───")
        for i, sf in enumerate(session_files, 1):
            msgs = read_recent_messages(sf, max_messages=9999)
            print(f"  {i}. {sf.name} ({len(msgs)} 条消息)")
        return

    # 3. 逐个提取
    from ..services.agent_loop import run_extraction_loop

    total_files: list[str] = []
    skipped = 0
    errors = 0

    print(f"─── 批量提取开始（{len(session_files)} 个会话）───")
    for i, sf in enumerate(session_files, 1):
        _verbose(args, f"[{i}/{len(session_files)}] processing {sf.name}")
        try:
            files = run_extraction_loop(
                config, root, tm_dir,
                verbose=verbose,
                session_file=sf,
            )
            if files:
                total_files.extend(files)
                print(f"  [{i}/{len(session_files)}] {sf.name} → {len(files)} 条记忆")
            else:
                skipped += 1
                _verbose(args, f"[{i}/{len(session_files)}] {sf.name} → 无记忆")
        except Exception as e:
            errors += 1
            _verbose(args, f"[{i}/{len(session_files)}] {sf.name} → 错误: {e}")

    print(f"\n─── 批量提取完成 ───")
    print(f"  处理会话: {len(session_files)}")
    print(f"  提取记忆: {len(total_files)}")
    print(f"  跳过: {skipped}")
    print(f"  错误: {errors}")


def register_extract_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("extract", help="Memory extraction commands")
    sub_extract = p.add_subparsers(dest="extract_command")

    pe = sub_extract.add_parser("prompt", help="Generate extraction prompt")
    pe.add_argument("--mode", choices=EXTRACT_MODES, help="Extraction mode")
    pe.add_argument("--output", help="Write prompt to file")
    pe.set_defaults(func=cmd_extract_prompt)

    pe = sub_extract.add_parser("status", help="Show extraction configuration")
    pe.set_defaults(func=cmd_extract_status)

    ph = sub_extract.add_parser("history", help="Show extraction history")
    ph.set_defaults(func=cmd_extract_history)

    pr = sub_extract.add_parser("run", help="Run auto memory extraction (triggered by Stop hook)")
    pr.add_argument("--push-on-count", type=int, default=5,
                    help="_staging/ pending files threshold for auto push (default 5, 0 to disable)")
    pr.add_argument("--push-on-minutes", type=int, default=30,
                    help="Minutes since last push threshold for auto push (default 30, 0 to disable)")
    pr.add_argument("--verbose", action="store_true",
                    help="Print diagnostic info to stderr")
    pr.set_defaults(func=cmd_extract_run)

    pb = sub_extract.add_parser("batch", help="Batch extract memories from multiple session files")
    pb.add_argument("--project-root", help="Project root directory (default: auto-detect from cwd)")
    pb.add_argument("--source", help="Path to .jsonl file or directory of .jsonl files")
    pb.add_argument("--max-sessions", type=int, default=None,
                    help="Maximum number of sessions to process")
    pb.add_argument("--dry-run", action="store_true",
                    help="Preview sessions without extracting")
    pb.add_argument("--verbose", action="store_true",
                    help="Print diagnostic info to stderr")
    pb.set_defaults(func=cmd_extract_batch)
