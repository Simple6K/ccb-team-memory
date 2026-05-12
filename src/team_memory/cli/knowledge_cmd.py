"""team-memory knowledge — 知识模块系统。

extract: 运行知识提取（从 _staging/ 生成知识文档）
pull: 拉取知识文档，注入 shared/ 和 projects/
list: 列出知识模块
show: 显示指定模块完整内容
status: 模块统计
clean: 清理模块
"""

import argparse
import sys
from pathlib import Path

from ..config import find_project_root, get_team_memory_dir, load_team_memory_config
from ..knowledge.runner import (
    run_knowledge_extract,
    run_knowledge_pull,
    run_knowledge_list,
    run_knowledge_show,
    run_knowledge_status,
    run_knowledge_clean,
    run_knowledge_review_list,
    run_knowledge_review_show,
    run_knowledge_review_approve,
    run_knowledge_review_reject,
    _get_knowledge_config,
)


def register_knowledge_parsers(subparsers: argparse._SubParsersAction) -> None:
    """注册 knowledge 子命令组。"""
    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="知识模块系统",
        description="知识模块系统：提取、拉取、管理结构化知识文档。",
    )
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_command")

    _register_extract(knowledge_sub)
    _register_pull(knowledge_sub)
    _register_review(knowledge_sub)
    _register_list(knowledge_sub)
    _register_show(knowledge_sub)
    _register_status(knowledge_sub)
    _register_clean(knowledge_sub)


def _register_extract(sub) -> None:
    p = sub.add_parser("extract", help="运行知识提取（从 _staging/ 生成知识文档）")
    p.add_argument("--extractor", dest="extractor_name", default=None,
                   help="指定提取器名称（不指定则运行所有）")
    p.add_argument("--force", action="store_true",
                   help="强制覆盖已有文档")
    p.add_argument("--dry-run", action="store_true",
                   help="仅预览，不实际写入")
    p.set_defaults(func=_cmd_extract)


def _register_pull(sub) -> None:
    p = sub.add_parser("pull", help="拉取知识文档，注入 shared/ 和 projects/")
    p.add_argument("--tags", default=None,
                   help="按标签过滤（逗号分隔，如 '支付,后端'）")
    p.add_argument("--domain", default=None,
                   help="按领域过滤")
    p.add_argument("--doc-id", dest="doc_id", default=None,
                   help="按 doc_id 精确拉取")
    p.add_argument("--all", dest="all_docs", action="store_true",
                   help="拉取全部知识（无过滤）")
    p.add_argument("--dry-run", action="store_true",
                   help="仅预览，不实际写入")
    p.set_defaults(func=_cmd_pull)


def _register_review(sub) -> None:
    p = sub.add_parser("review", help="审核知识文档变更（查看/发布/撤销待审 commit）")
    review_sub = p.add_subparsers(dest="review_command")

    lp = review_sub.add_parser("list", help="列出未发布的知识 commit")
    lp.set_defaults(func=_cmd_review_list)

    sp = review_sub.add_parser("show", help="显示指定 commit 详情")
    sp.add_argument("commit_hash", help="commit hash")
    sp.add_argument("--full", action="store_true", help="显示完整 diff")
    sp.set_defaults(func=_cmd_review_show)

    ap = review_sub.add_parser("approve", help="发布所有待审知识 commit（push）")
    ap.set_defaults(func=_cmd_review_approve)

    rp = review_sub.add_parser("reject", help="撤销指定知识 commit（git revert）")
    rp.add_argument("commit_hash", help="要撤销的 commit hash")
    rp.add_argument("--message", default=None, help="revert 原因说明")
    rp.set_defaults(func=_cmd_review_reject)


def _register_list(sub) -> None:
    p = sub.add_parser("list", help="列出知识模块")
    p.add_argument("--stale", action="store_true",
                   help="仅列出过期文档")
    p.set_defaults(func=_cmd_list)


def _register_show(sub) -> None:
    p = sub.add_parser("show", help="显示指定模块完整内容")
    p.add_argument("doc_id", help="文档 doc_id")
    p.set_defaults(func=_cmd_show)


def _register_status(sub) -> None:
    p = sub.add_parser("status", help="模块统计")
    p.set_defaults(func=_cmd_status)


def _register_clean(sub) -> None:
    p = sub.add_parser("clean", help="清理模块")
    p.add_argument("--stale-only", action="store_true",
                   help="仅清理过期文档")
    p.set_defaults(func=_cmd_clean)


# ─── Command handlers ───────────────────────────────────────────────────

def _cmd_extract(args) -> None:
    config = load_team_memory_config()
    if config is None:
        print("未找到团队记忆配置。", file=sys.stderr)
        sys.exit(1)

    result = run_knowledge_extract(
        config,
        extractor_name=args.extractor_name,
        force=args.force,
        dry_run=args.dry_run,
    )
    print(result)


def _cmd_pull(args) -> None:
    config = load_team_memory_config()
    if config is None:
        print("未找到团队记忆配置。", file=sys.stderr)
        sys.exit(1)

    tags = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    result = run_knowledge_pull(
        config,
        tags=tags,
        domain=args.domain,
        doc_id=args.doc_id,
        all_docs=args.all_docs,
        dry_run=args.dry_run,
    )
    print(result)


def _cmd_review_list(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    knowledge_config = _get_knowledge_config(config)
    kpath = knowledge_config.get("path", "knowledge/")
    result = run_knowledge_review_list(tm_dir, kpath)
    print(result)


def _cmd_review_show(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    knowledge_config = _get_knowledge_config(config)
    kpath = knowledge_config.get("path", "knowledge/")
    result = run_knowledge_review_show(tm_dir, args.commit_hash, kpath, full=args.full)
    print(result)


def _cmd_review_approve(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    result = run_knowledge_review_approve(tm_dir)
    print(result)


def _cmd_review_reject(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    result = run_knowledge_review_reject(tm_dir, args.commit_hash, message=args.message)
    print(result)


def _cmd_list(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    knowledge_dir = tm_dir / kpath
    result = run_knowledge_list(knowledge_dir, stale=args.stale)
    print(result)


def _cmd_show(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    knowledge_dir = tm_dir / kpath
    result = run_knowledge_show(knowledge_dir, args.doc_id)
    print(result)


def _cmd_status(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    knowledge_dir = tm_dir / kpath
    result = run_knowledge_status(knowledge_dir)
    print(result)


def _cmd_clean(args) -> None:
    config = load_team_memory_config()
    tm_dir = get_team_memory_dir(find_project_root()) if config else None
    if tm_dir is None:
        print("未找到团队记忆目录。", file=sys.stderr)
        sys.exit(1)

    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    knowledge_dir = tm_dir / kpath
    result = run_knowledge_clean(knowledge_dir, stale_only=args.stale_only)
    print(result)
