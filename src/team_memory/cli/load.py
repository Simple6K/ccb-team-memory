"""team-memory load auto / load search / load list — 记忆加载命令。"""

import argparse
import sys

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..services.installer import _ensure_rules_wrapper
from ..services.loader import auto_load, list_memory_files, manual_load


def cmd_load_auto(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        return
    text = auto_load(config, root)
    if not text:
        return
    print(text)

    # Write entrypoint to .claude/team-memory/MEMORY.md
    tm_dir = get_team_memory_dir(root)
    tm_dir.mkdir(parents=True, exist_ok=True)
    entrypoint = tm_dir / "MEMORY.md"
    entrypoint.write_text(text)

    # Ensure the @include wrapper exists so ccb picks it up.
    _ensure_rules_wrapper(root)


def cmd_load_search(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    text = manual_load(config, root, query=args.query or "", mem_type=args.type or "")
    print(text)


def cmd_load_list(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        return

    files = list_memory_files(config, root)
    if not files:
        print("未找到团队记忆文件。")
        return
    print(f"团队记忆文件 ({len(files)} 个):")
    for f in files:
        size_kb = f["size"] / 1024
        print(f"  {f['path']}  ({size_kb:.1f} KB)")


def register_load_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("load", help="Memory loading commands")
    sub_load = p.add_subparsers(dest="load_command")

    pl = sub_load.add_parser("auto", help="Auto-load memory summary")
    pl.set_defaults(func=cmd_load_auto)

    pl = sub_load.add_parser("search", help="Search and load memories")
    pl.add_argument("query", nargs="?", help="Search query")
    pl.add_argument("--type", dest="type", choices=["user", "feedback", "project", "reference"],
                    help="Filter by type")
    pl.set_defaults(func=cmd_load_search)

    pl = sub_load.add_parser("list", help="List all memory files")
    pl.set_defaults(func=cmd_load_list)
