"""team-memory extract prompt / extract status — 提取管理命令。"""

import argparse
import sys
from pathlib import Path

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..services.extract import (
    EXTRACT_MODES,
    build_extract_prompt,
    scan_manifest,
)


def cmd_extract_prompt(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    mode = args.mode or config.extract.mode
    text = build_extract_prompt(config, root, mode=mode)
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
    print("─── Extraction Status ───")
    print(f"  Mode:         {config.extract.mode}")
    print(f"  Scope:        {config.extract.scope}")
    print(f"  Auto push:    {config.extract.auto_push}")
    print(f"  Team dir:     {tm_dir}")

    if tm_dir.is_dir():
        for label, d in [
            ("shared", tm_dir / "shared"),
            ("projects", tm_dir / "projects"),
        ]:
            if d.is_dir():
                manifest = scan_manifest(d)
                print(f"  {label}/:   {len(manifest)} memory files")


def register_extract_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("extract", help="Memory extraction commands")
    sub_extract = p.add_subparsers(dest="extract_command")

    pe = sub_extract.add_parser("prompt", help="Generate extraction prompt")
    pe.add_argument("--mode", choices=EXTRACT_MODES, help="Extraction mode")
    pe.add_argument("--output", help="Write prompt to file")
    pe.set_defaults(func=cmd_extract_prompt)

    pe = sub_extract.add_parser("status", help="Show extraction configuration")
    pe.set_defaults(func=cmd_extract_status)
