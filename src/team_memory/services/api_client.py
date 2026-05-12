"""Anthropic Messages API 客户端。

使用 Python stdlib urllib，零外部依赖。
认证凭证从环境变量读取（由 ccb-dev 的 managedEnv.ts 注入）。
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def get_api_credentials() -> tuple[str, str, str] | None:
    """获取 API 凭证。

    返回 (api_key, base_url, model) 或 None。

    认证优先级：
    1. 环境变量 ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL
    2. 回退读取 ~/.ccb-dev/settings.json 的 env 段
    """
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")

    # 环境变量不存在则尝试读取 settings.json
    if not token or not base_url:
        for settings_path in (
            Path(os.path.expanduser("~/.ccb-dev/settings.json")),
            Path(os.path.expanduser("~/.claude/settings.json")),
        ):
            try:
                if settings_path.is_file():
                    settings = json.loads(settings_path.read_text())
                    env = settings.get("env", {})
                    if not token:
                        token = env.get("ANTHROPIC_AUTH_TOKEN", "")
                    if not base_url:
                        base_url = env.get("ANTHROPIC_BASE_URL", "")
            except (json.JSONDecodeError, OSError):
                continue

    if not token or not base_url:
        return None

    # 模型选择优先级：
    # 1. TEAM_MEMORY_EXTRACT_MODEL（专用于记忆提取的模型）
    # 2. ANTHROPIC_DEFAULT_SONNET_MODEL（已知可用的通用模型）
    # 3. deepseek-v4-pro（硬编码兜底，flash 模型在 annto API 不可用）
    model = os.environ.get("TEAM_MEMORY_EXTRACT_MODEL", "")
    if not model:
        model = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
    if not model:
        model = "deepseek-v4-pro"
    if not model:
        # 尝试从 settings.json 读取
        for settings_path in (
            Path(os.path.expanduser("~/.ccb-dev/settings.json")),
            Path(os.path.expanduser("~/.claude/settings.json")),
        ):
            try:
                if settings_path.is_file():
                    settings = json.loads(settings_path.read_text())
                    env = settings.get("env", {})
                    model = (
                        env.get("TEAM_MEMORY_EXTRACT_MODEL", "")
                        or env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
                        or "deepseek-v4-pro"
                    )
                    if model:
                        break
            except (json.JSONDecodeError, OSError):
                continue

    if not model:
        return None

    return token, base_url, model


def call_anthropic_api(
    messages: list[dict],
    system: str,
    tools: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 4096,
) -> dict:
    """单次 Anthropic Messages API 调用。

    Args:
        messages: [{"role": "user"|"assistant", "content": ...}, ...]
        system: 系统提示词
        tools: 工具定义列表
        api_key: API 认证 token
        base_url: API 端点（如 https://api.deepseek.com/anthropic）
        model: 模型名称
        max_tokens: 最大输出 token 数

    Returns:
        完整的 API 响应 dict（含 content、usage 等）
    """
    url = base_url.rstrip("/") + "/v1/messages"

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "tools": tools,
    }

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 返回错误信息而非抛出异常
        try:
            error_body = e.read().decode("utf-8")
            return {"error": True, "status": e.code, "body": error_body}
        except Exception:
            return {"error": True, "status": e.code, "body": str(e)}
    except Exception as e:
        return {"error": True, "body": str(e)}
