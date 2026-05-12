"""team-memory review integrate — 批量整合 _staging/ 到项目记忆。

pull → diff 获取远程增量 → 去重 → 移入 shared/projects → MEMORY.md → git commit（不 push）
"""

import argparse
import sys

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..services.integration import (
    IntegrateResult,
    generate_commit_message,
    commit_integration,
    run_integration,
)


def _print_dry_run(result: IntegrateResult, project_name: str) -> None:
    """打印 dry-run 预览。"""
    print()
    print("─── 整合预览（--dry-run）───")
    print(f"  Pull 前 HEAD: {result.pull_head_before[:8] if result.pull_head_before else 'N/A'}")
    print(f"  Pull 后 HEAD: {result.pull_head_after[:8] if result.pull_head_after else 'N/A'}")
    print(f"  _staging/ 总文件数: {result.staging_files_total}")
    print(f"  远程增量: {result.remote_increments}")
    print(f"  预计新增: {result.new_count}")
    print(f"  预计更新: {result.update_count}")
    print(f"  预计跳过: {result.skip_count}")
    print()

    if not result.items:
        print("（无待整合的远程增量 staging 文件）")
        return

    # 提交人统计
    cstats = result.contributor_stats()
    if cstats:
        print("提交人统计:")
        for name, counts in sorted(cstats.items()):
            print(f"  {name}: {sum(counts.values())} 条 (新增 {counts['new']}, 更新 {counts['update']}, 跳过 {counts['skip']})")
        print()

    for item in result.items:
        sf = item.staging_file
        c = sf.contributor or "?"
        desc = f" — {sf.description}" if sf.description else ""
        if item.action == "new":
            print(f"  [新增] [{c}] {sf.path} → {item.target_path}{desc}")
        elif item.action == "update":
            print(f"  [更新] [{c}] {sf.path} → {item.target_path}{desc}")
        elif item.action == "skip":
            print(f"  [跳过] [{c}] {sf.path} ({item.reason}){desc}")

    if result.has_work:
        print()
        commit_msg = generate_commit_message(result, project_name)
        print("─── 预计提交信息 ───")
        print(commit_msg)

    print()
    print("以上为预览。去掉 --dry-run 执行实际整合。")


def _print_result(result: IntegrateResult, commit_hash: str) -> None:
    """打印执行结果。"""
    print()
    print(f"整合完成: 新增 {result.new_count} | 更新 {result.update_count} | 跳过 {result.skip_count}")

    cstats = result.contributor_stats()
    if cstats:
        print()
        print("提交人统计:")
        for name, counts in sorted(cstats.items()):
            print(f"  {name}: {sum(counts.values())} 条")

    if commit_hash:
        print(f"\n已提交: {commit_hash}")
        print("审核确认后运行 'team-memory push' 推送到团队仓库。")
    else:
        print("\n未创建提交（无变更或提交失败）。")


def cmd_review_integrate(args: argparse.Namespace) -> None:
    """批量整合 _staging/ → shared/ + projects/。"""
    root = find_project_root()
    config = load_team_memory_config(root)
    if not config:
        print("未配置团队记忆。", file=sys.stderr)
        sys.exit(1)

    tm_dir = get_team_memory_dir(root)
    if not tm_dir.is_dir():
        print("团队记忆目录不存在。请先运行 'team-memory init' 或 'team-memory pull'。", file=sys.stderr)
        sys.exit(1)

    staging_dir = tm_dir / "_staging"
    if not staging_dir.is_dir() or not any(staging_dir.rglob("*.md")):
        print("_staging/ 中没有待整合文件。")
        return

    dry_run = getattr(args, "dry_run", False)
    no_pull = getattr(args, "no_pull", False)

    result = run_integration(
        config, root, tm_dir,
        dry_run=dry_run,
        skip_pull=no_pull,
    )

    if dry_run:
        from ..config import get_project_name
        _print_dry_run(result, get_project_name(root) or "unknown")
        return

    if not result.has_work:
        print("无远程增量 staging 文件需要整合。")
        return

    # 生成提交信息并提交
    from ..config import get_project_name
    project_name = get_project_name(root) or "unknown"
    commit_msg = generate_commit_message(result, project_name)
    ok, commit_hash = commit_integration(tm_dir, commit_msg)

    _print_result(result, commit_hash if ok else "")


def register_integrate_parser(sub_review: argparse._SubParsersAction) -> None:
    pi = sub_review.add_parser("integrate", help="批量整合 _staging/ 待审核记忆")
    pi.add_argument("--dry-run", action="store_true", help="预览整合操作，不实际执行")
    pi.add_argument("--no-pull", action="store_true", help="跳过 git pull，直接处理本地 _staging/")
    pi.set_defaults(func=cmd_review_integrate)
