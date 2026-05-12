"""team-memory verify — 记忆验证命令。

V4.6: 新增，对应 claude-code extractWrittenPaths() + frontmatter validation。
"""

import argparse
import sys
from pathlib import Path

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..services.verify import verify_memories_dir


def cmd_verify(args: argparse.Namespace) -> None:
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

    result = verify_memories_dir(tm_dir)
    print("─── Team Memory Verification ───")
    print(f"  {result.summary()}")
    print()

    if result.errors:
        print("❌ 错误:")
        for e in result.errors:
            print(f"  - {e}")
        print()

    if result.warnings:
        print("⚠ 警告:")
        for w in result.warnings[:20]:
            print(f"  - {w}")
        if len(result.warnings) > 20:
            print(f"  ... 还有 {len(result.warnings) - 20} 条")
        print()

    if result.duplicate_names:
        print("🔄 重复 name:")
        for name in result.duplicate_names:
            print(f"  - {name}")

    if result.is_clean and not result.warnings:
        print("✓ 所有验证通过。")
    elif result.is_clean:
        print("✓ 无阻塞性错误（仅有警告）。")
    else:
        print("✗ 验证发现错误，push 将被阻止。")
        sys.exit(1)


def register_verify_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("verify", help="Verify team memory file integrity")
    p.set_defaults(func=cmd_verify)
