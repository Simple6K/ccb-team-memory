"""微型 Agent Loop — 异步记忆提取的核心。

对标 ccb-dev 内置 executeExtractMemories 的 runForkedAgent 模式：
独立调用 Anthropic API，执行 tool-use 循环，完成记忆提取。

路径沙箱：所有工具操作限制在 .claude/team-memory/ 目录内。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..config import TeamMemoryConfig
from ..services.extract import build_extract_prompt
from ..utils.transcript import (
    find_project_dir,
    find_session_dir,
    format_messages_for_api,
    read_recent_messages,
)
from .api_client import call_anthropic_api, get_api_credentials
from .extraction_manager import ExtractionManager

# 最多 API 调用轮数（对应 ccb-dev maxTurns: 5）
_MAX_TURNS = 5

# Agent Loop 的 system prompt（中文）
_SYSTEM_PROMPT = """\
你是记忆提取子 agent。你的任务是分析对话内容，提取可持久化的记忆，
写入团队记忆目录的 _staging/ 子目录（待审核区）。

你有三个工具可用：
- read_memory_file：读取记忆目录中的文件
- write_memory_file：在 _staging/ 中创建新的记忆文件
- edit_memory_file：编辑已有的记忆文件

重要规则：
- **所有新记忆必须写入 _staging/ 目录**，不要直接写 shared/ 或 projects/
- shared/ 和 projects/ 中的已有记忆可以读取用于去重，但不要修改它们
- _staging/ 下不需要维护 MEMORY.md 索引
- **文件名会自动添加时间戳和提交人前缀**（如 user_prefs.md → 20260507T153045-zhangsan-user_prefs.md），
  你使用简短描述性文件名即可，系统会自动处理命名唯一性

工作策略：
- 第 1 轮：并行调用 read_memory_file 读取 _staging/、shared/、projects/ 中可能需要参考的文件
- 第 2 轮：并行调用 write_memory_file 写入 _staging/ 目录
- 完成后不要继续调用工具，直接输出文本说明完成

所有文件操作仅限于团队记忆目录内。"""


def _path_sandbox_ok(file_path: str, memory_dir: Path) -> bool:
    """检查路径是否在沙箱内。

    规则：
    - 必须是 .md 文件
    - 不能包含路径穿越 (..)
    - 解析后的绝对路径必须在 memory_dir 下
    """
    if not file_path.endswith(".md"):
        return False
    if ".." in file_path:
        return False
    if file_path.startswith("/"):
        return False

    resolved = (memory_dir / file_path).resolve()
    try:
        resolved.relative_to(memory_dir.resolve())
        return True
    except ValueError:
        return False


def _get_contributor(project_root: Path | None = None) -> str:
    """获取提交人名称。

    优先级：项目目录 git config user.name → 环境变量 USER → "unknown"
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=project_root or Path.cwd(),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return os.environ.get("USER", "unknown")


def _force_staging_path(file_path: str) -> str:
    """强制文件写入 _staging/ 目录，忽略模型指定的路径。

    提取文件名部分，加 <timestamp>-<contributor>- 前缀，
    强制放入 _staging/ 下。
    如 projects/foo/bar.md → _staging/20260507T153045-zhangsan-bar.md
    如已含时间戳前缀则跳过（幂等）。
    """
    import re

    name = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    # 已有时间戳前缀? 格式: YYYYmmddTHHMMSS-xxx-xxx.md
    if re.match(r"^\d{8}T\d{6}-", name):
        return f"_staging/{name}"

    prefix = time.strftime("%Y%m%dT%H%M%S") + "-" + _get_contributor()
    return f"_staging/{prefix}-{name}"


def _execute_tool(tool_use: dict, memory_dir: Path) -> str:
    """执行单个工具调用。

    Args:
        tool_use: {"name": "...", "id": "...", "input": {...}}
        memory_dir: 团队记忆根目录

    Returns:
        工具执行结果字符串
    """
    name = tool_use.get("name", "")
    input_data = tool_use.get("input", {})

    if name == "read_memory_file":
        file_path = input_data.get("file_path", "")
        if not _path_sandbox_ok(file_path, memory_dir):
            return "错误：文件路径不在允许范围内"
        full_path = memory_dir / file_path
        if not full_path.is_file():
            return f"错误：文件不存在 — {file_path}"
        try:
            return full_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"错误：读取文件失败 — {e}"

    elif name == "write_memory_file":
        file_path = input_data.get("file_path", "")
        content = input_data.get("content", "")
        if not _path_sandbox_ok(file_path, memory_dir):
            return "错误：文件路径不在允许范围内"
        # 强制写入 _staging/ 并加时间戳前缀，防止多人同名冲突
        original_path = file_path
        file_path = _force_staging_path(file_path)
        full_path = memory_dir / file_path
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return f"文件已写入：{file_path}"
        except Exception as e:
            return f"错误：写入文件失败 — {e}"

    elif name == "edit_memory_file":
        file_path = input_data.get("file_path", "")
        old_string = input_data.get("old_string", "")
        new_string = input_data.get("new_string", "")
        if not _path_sandbox_ok(file_path, memory_dir):
            return "错误：文件路径不在允许范围内"
        full_path = memory_dir / file_path
        if not full_path.is_file():
            return f"错误：文件不存在 — {file_path}"
        try:
            current = full_path.read_text(encoding="utf-8")
            if old_string not in current:
                return "错误：old_string 未在文件中找到"
            # 仅替换第一次出现
            updated = current.replace(old_string, new_string, 1)
            full_path.write_text(updated, encoding="utf-8")
            return f"文件已编辑：{file_path}"
        except Exception as e:
            return f"错误：编辑文件失败 — {e}"

    return f"未知工具：{name}"


def _build_tools() -> list[dict]:
    """构建 Anthropic API 工具定义。"""
    return [
        {
            "name": "read_memory_file",
            "description": "读取团队记忆目录中的记忆文件。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于团队记忆根目录的文件路径，如 _staging/user_prefs.md 或 shared/user_prefs.md",
                    }
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "write_memory_file",
            "description": "在 _staging/ 目录中创建新的待审核记忆文件。新记忆必须写入 _staging/ 下。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于团队记忆根目录的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件的完整内容，含 YAML frontmatter 和 Markdown 正文",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
        {
            "name": "edit_memory_file",
            "description": "编辑团队记忆目录中已有的记忆文件。仅用于更新 _staging/ 中本次已写入的文件。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "相对于团队记忆根目录的文件路径",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要被替换的文本",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    ]


def _extract_written_paths(
    assistant_messages: list[dict],
) -> list[str]:
    """从 assistant 响应中提取 Write/Edit 的文件路径。

    对应 ccb-dev extractWrittenPaths()。
    """
    paths: list[str] = []
    for msg in assistant_messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            if name not in ("write_memory_file", "edit_memory_file"):
                continue
            input_data = block.get("input", {})
            fp = input_data.get("file_path", "")
            if fp:
                paths.append(fp)
    # 去重保持顺序
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def run_extraction_loop(
    config: TeamMemoryConfig,
    project_root: Path,
    memory_dir: Path,
    verbose: bool = False,
) -> list[str]:
    """运行记忆提取 agent loop。

    1. 读取 session transcript 获取最近对话
    2. 构建提取提示词
    3. Agent Loop：API 调用 → tool_use → 执行 → 继续
    4. 返回已写入的文件路径列表

    Returns:
        已写入/编辑的文件路径列表
    """
    import sys as _sys

    def _v(msg: str) -> None:
        if verbose:
            print(f"[team-memory] {msg}", file=_sys.stderr)

    # 1. 获取 API 凭证
    creds = get_api_credentials()
    if not creds:
        _v("agent_loop: no API credentials")
        return []
    api_key, base_url, model = creds
    _v(f"agent_loop: model={model}")

    # 2. 读取 transcript
    project_dir = find_project_dir(project_root)
    if not project_dir:
        _v(f"agent_loop: project_dir not found for root={project_root}")
        return []

    session_file = find_session_dir(project_dir)
    if not session_file:
        _v(f"agent_loop: no session file in {project_dir}")
        return []

    recent_messages = read_recent_messages(session_file)
    if not recent_messages:
        _v(f"agent_loop: no messages read from {session_file}")
        return []

    formatted_messages = format_messages_for_api(recent_messages)
    if not formatted_messages:
        _v(f"agent_loop: no messages after formatting ({len(recent_messages)} raw)")
        return []

    _v(f"agent_loop: {len(formatted_messages)} formatted messages, calling API...")

    # 3. 构建提取提示词
    extraction_prompt = build_extract_prompt(config, project_root, mode="auto")

    # 4. 构建初始消息列表
    # 系统提示词 + 最近对话 + 提取指令作为最后一条 user 消息
    messages = list(formatted_messages)
    messages.append({"role": "user", "content": extraction_prompt})

    tools = _build_tools()
    assistant_messages: list[dict] = []

    # 5. Agent Loop
    for _ in range(_MAX_TURNS):
        response = call_anthropic_api(
            messages=messages,
            system=_SYSTEM_PROMPT,
            tools=tools,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

        if response.get("error"):
            _v(f"agent_loop: API error: {response.get('status', '?')} {str(response.get('body', ''))[:300]}")
            break

        content = response.get("content", [])
        if not isinstance(content, list):
            _v(f"agent_loop: unexpected content type: {type(content).__name__}")
            break

        # 记录本轮响应类型
        content_types = [b.get("type", "?") for b in content if isinstance(b, dict)]
        _v(f"agent_loop turn: content_types={content_types}, stop_reason={response.get('stop_reason', '?')}")

        # 记录 assistant 消息
        assistant_msg = {"role": "assistant", "content": content}
        messages.append(assistant_msg)
        assistant_messages.append(assistant_msg)

        # 提取 tool_use
        tool_uses = [
            b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

        if not tool_uses:
            # 没有工具调用 → 模型完成了提取
            break

        # 执行工具并收集结果
        tool_results: list[dict] = []
        for tu in tool_uses:
            result = _execute_tool(tu, memory_dir)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.get("id", ""),
                "content": result,
            })

        # 追加 tool_result 作为 user 消息
        messages.append({"role": "user", "content": tool_results})

    # 6. 统计写入文件
    return _extract_written_paths(assistant_messages)
