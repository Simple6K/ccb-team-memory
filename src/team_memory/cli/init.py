"""team-memory init — 项目初始化命令。"""

import argparse
import sys
from pathlib import Path

from ..config import (
    TeamMemoryConfig,
    ensure_gitignore,
    find_annto_yaml,
    find_project_root,
    generate_annto_yaml,
    get_project_name,
    load_team_memory_config,
    save_team_memory_config,
)
from ..services.installer import install_all, _ensure_rules_wrapper
from ..services.sync import do_init


def _print_init_success(project_name: str) -> None:
    print(f"\nTeam memory initialized! New members who clone this project")
    print(f"and open ccb will be prompted to pull team memories.")


def cmd_init(args: argparse.Namespace) -> None:
    from ..cli.install import _apply_config_dir
    _apply_config_dir(args.config_dir)

    # ── --generate-yaml: can run anywhere, no project needed ──
    if args.generate_yaml:
        team_repo = args.team_repo or args.repo
        if not team_repo:
            print("Error: --team-repo (or --repo) is required with --generate-yaml", file=sys.stderr)
            sys.exit(1)
        project_repo = args.project_repo or args.repo or ""
        cwd = Path.cwd()
        yaml_path = generate_annto_yaml(
            cwd,
            team_repo=team_repo,
            project_repo=project_repo,
            team_branch=args.branch,
            team_path=args.team_path or "shared/",
            project_path=args.project_path or "",
        )
        print(f"Created {yaml_path}")
        print("\nRun 'team-memory init' (without --repo) to use this config.")
        return

    root = find_project_root()
    if not root:
        print("Error: 未找到 ccb-annto-memory.yaml", file=sys.stderr)
        print("  在项目目录创建 ccb-annto-memory.yaml，或运行:", file=sys.stderr)
        print("    team-memory init --generate-yaml --team-repo <url>", file=sys.stderr)
        sys.exit(1)

    # ── Determine config source ──
    if args.repo:
        # Explicit repo: use settings.json (legacy mode)
        config = TeamMemoryConfig(repo=args.repo, branch=args.branch)
        project_name = get_project_name(root, config)
        if not project_name:
            print("Error: 无法确定项目名称", file=sys.stderr)
            sys.exit(1)
        print(f"项目: {project_name}")
        print(f"根目录: {root}")
        print(f"仓库: {args.repo}  (settings.json)")
        save_team_memory_config(config, root)
    else:
        # Auto-discover from ccb-annto-memory.yaml
        yaml_path = find_annto_yaml(root)
        if yaml_path:
            config = load_team_memory_config(root)
            if config is None:
                print("Error: 解析 ccb-annto-memory.yaml 失败", file=sys.stderr)
                sys.exit(1)
            project_name = get_project_name(root, config)
            if not project_name:
                print("Error: 无法确定项目名称", file=sys.stderr)
                sys.exit(1)
            print(f"Project: {project_name}")
            print(f"Root: {root}")
            print(f"Config: {yaml_path}")
            print(f"Team repo: {config.team_repo}")
            if config.annto and not config.annto.uses_single_repo:
                print(f"Project repo: {config.project_repo}")
            # Save to settings.json for backward compat
            save_team_memory_config(config, root)
        else:
            # Try existing settings.json
            config = load_team_memory_config(root)
            if config is None:
                print("Error: no config found.", file=sys.stderr)
                print("\nOptions:", file=sys.stderr)
                print("  team-memory init --repo <url>", file=sys.stderr)
                print("  team-memory init --generate-yaml --team-repo <url>", file=sys.stderr)
                print("  Or create ccb-annto-memory.yaml in the project directory.", file=sys.stderr)
                sys.exit(1)
            project_name = get_project_name(root, config)
            if not project_name:
                print("Error: 无法确定项目名称", file=sys.stderr)
                sys.exit(1)
            print(f"Project: {project_name}")
            print(f"Root: {root}")
            print(f"Repo: {config.repo}  (settings.json, legacy)")

    ensure_gitignore(root)

    ok, msg = do_init(config, root)
    if ok:
        print(f"  {msg}")
    else:
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    if not args.no_install:
        ok2, msg2 = install_all(root)
        print(f"  {msg2}")

    # Create .claude/rules/team-memory.md wrapper so ccb discovers team memory
    _ensure_rules_wrapper(root)
    print("  Created .claude/rules/team-memory.md (ccb @include wrapper)")

    _print_init_success(project_name)


def register_init_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="Initialize team memory for this project")
    p.add_argument("--repo", default=None, help="Shared team memory Git repository URL (optional if using ccb-annto-memory.yaml)")
    p.add_argument("--branch", default="main", help="Git branch (default: main)")
    p.add_argument("--no-install", action="store_true", help="Skip installing ccb hooks")
    p.add_argument("--config-dir", default=None, help="ccb config directory (e.g., ~/.ccb-dev or ~/.ccb)")
    # V4.1: YAML generation options
    p.add_argument("--generate-yaml", action="store_true", help="Generate ccb-annto-memory.yaml in project directory")
    p.add_argument("--team-repo", default=None, help="Team memory Git repo URL (for --generate-yaml)")
    p.add_argument("--project-repo", default=None, help="Project memory Git repo URL (for --generate-yaml)")
    p.add_argument("--team-path", default=None, help="Team memory subdirectory within repo (default: shared/)")
    p.add_argument("--project-path", default=None, help="Project memory subdirectory within repo")
    p.set_defaults(func=cmd_init)
