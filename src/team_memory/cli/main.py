"""CLI 入口 — 顶层解析器，注册子命令，dispatch 到各命令模块。

仅负责 argparse 定义和路由，不包含业务逻辑。

V4.6: 新增 verify、consolidate 子命令。
"""

import argparse
import sys

from ..services.extract import EXTRACT_MODES
from .init import register_init_parser
from .sync import register_sync_parsers
from .extract import register_extract_parsers
from .load import register_load_parsers
from .install import register_install_parsers
from .verify_cmd import register_verify_parsers
from .consolidate_cmd import register_consolidate_parsers
from .review import register_review_parsers
from .knowledge_cmd import register_knowledge_parsers


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="team-memory",
        description="Enterprise team memory sync and extraction for ccb.",
    )
    parser.add_argument("--version", action="version", version="ccb-team-memory 1.0.0")

    sub = parser.add_subparsers(dest="command")

    register_init_parser(sub)
    register_sync_parsers(sub)
    register_extract_parsers(sub)
    register_load_parsers(sub)
    register_install_parsers(sub)
    register_verify_parsers(sub)
    register_consolidate_parsers(sub)
    register_review_parsers(sub)
    register_knowledge_parsers(sub)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
