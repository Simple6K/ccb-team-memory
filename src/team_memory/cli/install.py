"""team-memory install / uninstall — 集成安装命令。"""

import argparse
import os
import sys

from ..config import find_project_root
from ..services.installer import install_all, uninstall_hooks


def _apply_config_dir(config_dir: str | None) -> None:
    """Set CLAUDE_CONFIG_DIR from --config-dir argument."""
    if config_dir:
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)


def cmd_install(args: argparse.Namespace) -> None:
    _apply_config_dir(args.config_dir)
    root = find_project_root()

    # Hooks are always installed globally so they fire for every ccb session.
    # Skill is installed in project (or CWD as fallback).
    ok, msg = install_all(root, args.bin, global_hooks=True)
    print(msg)
    if not ok:
        sys.exit(1)
    if not root:
        print("  (skill installed in current directory — run inside a git project for project-level skill)")
    print("\nTeam memory installed. Restart ccb to activate.")


def cmd_uninstall(args: argparse.Namespace) -> None:
    _apply_config_dir(args.config_dir)
    root = find_project_root()
    ok, msg = uninstall_hooks(root, global_hooks=True)
    print(msg)


def register_install_parsers(sub: argparse._SubParsersAction) -> None:
    # install
    p = sub.add_parser("install", help="Install hooks and skill to ccb")
    p.add_argument("--bin", help="Path to team-memory binary")
    p.add_argument("--config-dir", default=None, help="ccb config directory (e.g., ~/.ccb-dev or ~/.ccb)")
    p.set_defaults(func=cmd_install)

    # uninstall
    p = sub.add_parser("uninstall", help="Remove hooks from ccb")
    p.add_argument("--config-dir", default=None, help="ccb config directory (e.g., ~/.ccb-dev or ~/.ccb)")
    p.set_defaults(func=cmd_uninstall)
