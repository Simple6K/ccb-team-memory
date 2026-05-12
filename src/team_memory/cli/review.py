"""team-memory review — 审核 _staging/ 中的待审核记忆。

list: 列出所有待审核记忆
approve: 审核通过，将记忆移到正式目录
reject: 审核拒绝，删除记忆文件
"""

import argparse
import sys
from pathlib import Path

from ..config import find_project_root, get_project_name, get_team_memory_dir, load_team_memory_config
from ..services.extract import ManifestEntry, _parse_frontmatter, scan_manifest


def _get_staging_dir(root: Path | None = None) -> Path:
    tm_dir = get_team_memory_dir(root)
    return tm_dir / "_staging"


def _resolve_target_dir(entry: ManifestEntry, tm_dir: Path, project_name: str) -> Path:
    """根据 frontmatter 中的 scope 决定目标目录。

    scope="team" → shared/
    scope="project" → projects/<name>/
    未指定 → 默认 shared/
    """
    scope = entry.scope
    if scope == "project":
        return tm_dir / "projects" / project_name
    return tm_dir / "shared"


def cmd_review_list(args: argparse.Namespace) -> None:
    """列出 _staging/ 中所有待审核记忆。"""
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        return

    staging_dir = _get_staging_dir(root)
    if not staging_dir.is_dir():
        print("暂无待审核记忆。")
        return

    entries = scan_manifest(staging_dir)
    if not entries:
        print("暂无待审核记忆。")
        return

    project_name = get_project_name(root or Path.cwd()) or "unknown"

    print(f"─── 待审核记忆 ({len(entries)} 条) ───")
    print()
    for i, entry in enumerate(entries, 1):
        target_dir = _resolve_target_dir(entry, get_team_memory_dir(root), project_name)
        print(f"  [{i}] {entry.path}")
        print(f"      类型: {entry.type or '未知'}  →  目标目录: {target_dir}")
        if entry.description:
            print(f"      描述: {entry.description}")
        print()

    print("批准: team-memory review approve <编号>")
    print("拒绝: team-memory review reject <编号>")
    print("全部批准: team-memory review approve --all")


def cmd_review_approve(args: argparse.Namespace) -> None:
    """审核通过，将记忆移到正式目录。"""
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    staging_dir = _get_staging_dir(root)
    tm_dir = get_team_memory_dir(root)
    project_name = get_project_name(root or Path.cwd()) or "unknown"

    entries = scan_manifest(staging_dir)

    if args.all:
        # 批量批准所有
        targets = entries
    elif args.number:
        try:
            idx = int(args.number) - 1
            if idx < 0 or idx >= len(entries):
                print(f"无效编号: {args.number}（共 {len(entries)} 条）", file=sys.stderr)
                sys.exit(1)
            targets = [entries[idx]]
        except ValueError:
            print(f"无效编号: {args.number}", file=sys.stderr)
            sys.exit(1)
    else:
        print("请指定要批准的编号，或使用 --all 批准全部", file=sys.stderr)
        sys.exit(1)

    approved = 0
    for entry in targets:
        src = staging_dir / entry.path
        target_dir = _resolve_target_dir(entry, tm_dir, project_name)
        target_dir.mkdir(parents=True, exist_ok=True)
        dst = target_dir / entry.path

        # 如果目标已存在，追加序号避免覆盖
        if dst.exists():
            stem = dst.stem
            counter = 2
            while (target_dir / f"{stem}_{counter}.md").exists():
                counter += 1
            dst = target_dir / f"{stem}_{counter}.md"

        try:
            src.rename(dst)
            approved += 1
            print(f"  [OK] {entry.path} → {dst.relative_to(tm_dir)}")
        except OSError as e:
            print(f"  [FAIL] {entry.path}: {e}", file=sys.stderr)

    if approved > 0:
        print()
        print(f"已批准 {approved} 条记忆。运行 'team-memory push' 推送到团队仓库。")
        print("运行 'team-memory verify' 检查 MEMORY.md 是否需要更新。")


def cmd_review_reject(args: argparse.Namespace) -> None:
    """审核拒绝，删除 staging 中的记忆文件。"""
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    staging_dir = _get_staging_dir(root)
    entries = scan_manifest(staging_dir)

    if args.all:
        targets = entries
    elif args.number:
        try:
            idx = int(args.number) - 1
            if idx < 0 or idx >= len(entries):
                print(f"无效编号: {args.number}（共 {len(entries)} 条）", file=sys.stderr)
                sys.exit(1)
            targets = [entries[idx]]
        except ValueError:
            print(f"无效编号: {args.number}", file=sys.stderr)
            sys.exit(1)
    else:
        print("请指定要拒绝的编号，或使用 --all 拒绝全部", file=sys.stderr)
        sys.exit(1)

    rejected = 0
    for entry in targets:
        src = staging_dir / entry.path
        try:
            src.unlink()
            rejected += 1
            print(f"  [OK] 已删除 {entry.path}")
        except OSError as e:
            print(f"  [FAIL] {entry.path}: {e}", file=sys.stderr)

    if rejected > 0:
        print()
        print(f"已拒绝 {rejected} 条记忆。")


def register_review_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("review", help="审核待提取记忆")
    sub_review = p.add_subparsers(dest="review_command")

    # list
    pl = sub_review.add_parser("list", help="列出待审核记忆")
    pl.set_defaults(func=cmd_review_list)

    # approve
    pa = sub_review.add_parser("approve", help="批准记忆")
    pa.add_argument("number", nargs="?", help="要批准的编号")
    pa.add_argument("--all", action="store_true", help="批准全部")
    pa.set_defaults(func=cmd_review_approve)

    # reject
    pr = sub_review.add_parser("reject", help="拒绝记忆")
    pr.add_argument("number", nargs="?", help="要拒绝的编号")
    pr.add_argument("--all", action="store_true", help="拒绝全部")
    pr.set_defaults(func=cmd_review_reject)

    # integrate
    from .integrate import register_integrate_parser
    register_integrate_parser(sub_review)
