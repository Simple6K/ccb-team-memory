"""team-memory pull / push / scan / status — 同步与状态命令。"""

import argparse
import sys
from pathlib import Path

from ..config import (
    find_project_root,
    get_team_memory_dir,
    load_team_memory_config,
    verify_project_identity,
)
from ..services.scanner import scan_directory
from ..services.sync import do_pull, do_push, do_status


def cmd_pull(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。创建 ccb-annto-memory.yaml 或运行 'team-memory init --repo <url>'。", file=sys.stderr)
        sys.exit(1)

    # ── Identity check: warn but don't block pull (read-only) ──
    allowed, verify_msg = verify_project_identity(config, root or Path.cwd())
    if not allowed and not args.quiet:
        print(f"  警告: {verify_msg}（pull 不受限制）", file=sys.stderr)

    ok, msg = do_pull(config, root, quiet=args.quiet)
    if not args.quiet:
        print(msg)
    if not ok:
        sys.exit(1)


def cmd_push(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    # ── Identity verification (V4.2) ──
    allowed, verify_msg = verify_project_identity(config, root or Path.cwd())
    if not allowed:
        print(f"Push 被拒绝: {verify_msg}", file=sys.stderr)
        sys.exit(1)
    if not args.quiet:
        print(f"  身份校验: {verify_msg}")

    if config.scan.enabled and not args.force:
        tm_dir = get_team_memory_dir(root)
        if tm_dir.is_dir():
            findings = scan_directory(str(tm_dir))
            if findings:
                print("密钥扫描检测到潜在凭据:", file=sys.stderr)
                for path, matches in findings.items():
                    labels = ", ".join(m.label for m in matches)
                    print(f"  {path}: {labels}", file=sys.stderr)
                print("\nUse --force to skip scanning.", file=sys.stderr)
                sys.exit(1)

    ok, msg = do_push(config, root, force_skip_scan=args.force, quiet=args.quiet)
    if not args.quiet:
        print(msg)
    if not ok:
        sys.exit(1)


def cmd_scan(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    tm_dir = get_team_memory_dir(root)
    if not tm_dir.is_dir():
        print(f"团队记忆目录不存在: {tm_dir}")
        print("请先运行 'team-memory pull'。")
        return

    print(f"正在扫描 {tm_dir}...")
    findings = scan_directory(str(tm_dir))
    if not findings:
        print("未检测到密钥。")
    else:
        print(f"在 {len(findings)} 个文件中检测到密钥:")
        for path, matches in findings.items():
            labels = ", ".join(m.label for m in matches)
            print(f"  {path}: {labels}")
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("此项目未配置团队记忆。")
        print("\nOptions:")
        print("  team-memory init --repo <url>")
        print("  team-memory init --generate-yaml --team-repo <url>")
        return
    print(do_status(config, root))


def register_sync_parsers(sub: argparse._SubParsersAction) -> None:
    # pull
    p = sub.add_parser("pull", help="Pull latest team memories")
    p.add_argument("--quiet", action="store_true", help="Suppress output")
    p.set_defaults(func=cmd_pull)

    # push
    p = sub.add_parser("push", help="Push local changes")
    p.add_argument("--force", action="store_true", help="Skip secret scanning")
    p.add_argument("--quiet", action="store_true", help="Suppress output")
    p.set_defaults(func=cmd_push)

    # scan
    p = sub.add_parser("scan", help="Scan team memories for secrets")
    p.set_defaults(func=cmd_scan)

    # status
    p = sub.add_parser("status", help="Show configuration and sync status")
    p.set_defaults(func=cmd_status)
