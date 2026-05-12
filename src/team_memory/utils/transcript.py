"""读取 ccb-dev session transcript。

ccb-dev 将对话存储在 JSONL 文件中：
  ~/.ccb-dev/projects/<sanitized-project-path>/<session-id>.jsonl

每条消息是一个 JSON 对象，包含 type、message、timestamp、isSidechain 等字段。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# 最大 sanitized 路径长度（对应 ccb-dev MAX_SANITIZED_LENGTH = 200）
_MAX_SANITIZED_LENGTH = 200


def _djb2_hash(s: str) -> int:
    """djb2 散列算法（对应 ccb-dev djb2Hash）。"""
    h = 5381
    for c in s:
        h = ((h << 5) + h + ord(c)) & 0xFFFFFFFF
    return h


def _to_base36(n: int) -> str:
    """将整数转为 base36 字符串（对应 JS Number.toString(36)）。"""
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while n > 0:
        result = chars[n % 36] + result
        n //= 36
    return result


def sanitize_path(name: str) -> str:
    """将项目路径转为安全的目录名。

    对应 ccb-dev sessionStoragePortable.ts:310 sanitizePath()。

    /path/to/project → -path-to-project
    """
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
    if len(sanitized) <= _MAX_SANITIZED_LENGTH:
        return sanitized
    # 超长路径：截断并附加 djb2 hash
    h = _djb2_hash(name)
    return f"{sanitized[:_MAX_SANITIZED_LENGTH]}-{_to_base36(h & 0x7FFFFFFF)}"


def _get_projects_base() -> Path | None:
    """获取 ccb-dev projects 目录。

    优先级：
    1. ~/.ccb-dev/projects/
    2. ~/.claude/projects/
    """
    for base in (
        Path(os.path.expanduser("~/.ccb-dev/projects")),
        Path(os.path.expanduser("~/.claude/projects")),
    ):
        if base.is_dir():
            return base
    return None


def find_project_dir(project_root: Path) -> Path | None:
    """查找项目对应的 session 存储目录。

    按 sanitize_path 映射项目路径为目录名，
    在 ~/.ccb-dev/projects/ 下查找匹配目录。
    """
    base = _get_projects_base()
    if base is None:
        return None

    resolved = str(project_root.resolve())
    expected = sanitize_path(resolved)

    # 精确匹配
    exact = base / expected
    if exact.is_dir():
        return exact

    # 前缀匹配（处理 hash 不一致，对应 findProjectDir fallback）
    if len(expected) > _MAX_SANITIZED_LENGTH:
        prefix = expected[:_MAX_SANITIZED_LENGTH]
        try:
            for d in base.iterdir():
                if d.is_dir() and d.name.startswith(prefix + "-"):
                    return d
        except OSError:
            return None

    return None


def find_session_dir(project_dir: Path) -> Path | None:
    """在项目目录下找到最近修改的 session 目录。

    返回匹配 *.jsonl 文件且包含对应同名 session 目录的最优 session。
    优先选择非 sidechain 消息最多的 session（主对话）。
    """
    if not project_dir.is_dir():
        return None

    sessions: list[tuple[float, Path]] = []
    try:
        for f in project_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix != ".jsonl":
                continue
            if f.name.endswith(".backup") or f.name.endswith(".backup2"):
                continue
            try:
                st = f.stat()
                sessions.append((st.st_mtime, f))
            except OSError:
                continue
    except OSError:
        return None

    if not sessions:
        return None

    # 按 mtime 降序排列，取最新的
    sessions.sort(key=lambda x: x[0], reverse=True)
    return sessions[0][1]


def read_recent_messages(
    session_file: Path,
    since: str | None = None,
    max_messages: int = 100,
) -> list[dict]:
    """读取 session JSONL 中的最近消息。

    过滤规则：
    - 仅保留 isSidechain == false（主对话，非子 agent）
    - 仅保留 type 为 "user" 或 "assistant"
    - 如果提供 since（ISO 8601 时间戳），仅返回该时间之后的消息
    - 最多返回 max_messages 条

    返回原始消息 dict 列表（包含 type, message, timestamp 等字段）。
    """
    if not session_file.is_file():
        return []

    messages: list[dict] = []
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 仅主对话
                if msg.get("isSidechain") is not False:
                    continue

                msg_type = msg.get("type")
                if msg_type not in ("user", "assistant"):
                    continue

                # 游标过滤
                if since is not None:
                    ts = msg.get("timestamp", "")
                    if ts <= since:
                        continue

                messages.append(msg)
    except OSError:
        return []

    # 返回最近 N 条
    return messages[-max_messages:]


def format_messages_for_api(messages: list[dict]) -> list[dict]:
    """将 transcript 消息转换为 Anthropic API 格式。

    输入：原始 JSONL 消息（含 type, message, timestamp 等）
    输出：[{"role": "user"|"assistant", "content": str|list}, ...]
    """
    formatted: list[dict] = []
    for msg in messages:
        inner = msg.get("message", {})
        role = inner.get("role", "")
        if role not in ("user", "assistant"):
            continue

        content = inner.get("content", "")

        # user 消息：content 可能是字符串或 tool_result 数组
        if role == "user":
            if isinstance(content, str):
                formatted.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # 提取 tool_result 中的文本
                texts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_result":
                            tc = block.get("content", "")
                            if isinstance(tc, list):
                                for item in tc:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        texts.append(str(item.get("text", "")))
                            elif isinstance(tc, str):
                                texts.append(tc)
                formatted.append({"role": "user", "content": "\n".join(texts)})
        else:
            # assistant 消息：content 是数组 [{"type": "text", "text": "..."}, ...]
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(str(block.get("text", "")))
                if texts:
                    formatted.append({"role": "assistant", "content": "\n".join(texts)})

    return formatted
