"""team-memory consolidate — 记忆整合命令。

V4.6: 新增，对应 claude-code autoDream.ts 整合流程。
"""

import argparse
import sys
from pathlib import Path

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..services.consolidation import (
    ConsolidationManager,
    ConsolidationConfig,
    ConsolidationCandidate,
)


def cmd_consolidate(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    tm_dir = get_team_memory_dir(root)
    if not tm_dir.is_dir():
        print(f"团队记忆目录不存在: {tm_dir}")
        print("请先运行 'team-memory pull'。")
        sys.exit(1)

    # 构建配置：--force 跳过门控
    cons_config = ConsolidationConfig(
        min_hours=args.min_hours or 24,
        min_files=args.min_files or 10,
        scan_interval_s=args.scan_interval or 600,
    )
    manager = ConsolidationManager(tm_dir, cons_config)

    if not args.force and not args.dry_run:
        if not manager.should_run():
            print("门控未通过（时间 / 文件数 / 锁）。使用 --force 强制执行。")
            return

    # 扫描
    report = manager.scan()
    print("─── Consolidation Report ───")
    print(f"  扫描文件数: {report.total_files_scanned}")
    print(f"  候选操作数: {len(report.candidates)}")
    print()

    if not report.has_work:
        print("✓ 未发现需要整合的内容。")
        manager._release_lock()
        return

    for i, c in enumerate(report.candidates, 1):
        print(f"  {i}. [{c.action}] {c.reason}")
        if c.files:
            for f in c.files[:5]:
                print(f"       - {f}")
            if len(c.files) > 5:
                print(f"       ... 还有 {len(c.files) - 5} 个")

    if report.errors:
        print()
        print("错误:")
        for e in report.errors:
            print(f"  - {e}")

    if args.dry_run:
        print()
        print("（--dry-run 模式，未执行任何变更）")
        manager._release_lock()
        return

    if args.apply:
        print()
        print("正在执行整合...")
        results = manager.apply(report.candidates)
        print(f"  已合并: {results['merged']}")
        print(f"  已归档: {results['archived']}")
        print(f"  已修复: {results['repaired']}")
        if results["errors"]:
            print(f"  错误: {results['errors']}")
        print()
        print("整合完成。运行 'team-memory push' 推送变更。")
    else:
        print()
        print("使用 --apply 执行以上变更，或 --dry-run 仅预览。")


def register_consolidate_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("consolidate", help="Consolidate and clean up team memories")
    p.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    p.add_argument("--apply", action="store_true", help="Execute consolidation")
    p.add_argument("--force", action="store_true", help="Skip gate checks (time/file count/lock)")
    p.add_argument("--min-hours", type=int, help="Minimum hours since last consolidation")
    p.add_argument("--min-files", type=int, help="Minimum memory files to trigger consolidation")
    p.add_argument("--scan-interval", type=int, help="Minimum seconds between scans")
    p.set_defaults(func=cmd_consolidate)
