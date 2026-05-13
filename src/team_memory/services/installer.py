"""ccb integration: register hooks in settings.json and install Skill."""

import json
import os
import shutil
import sys
from pathlib import Path

from ..config import (
    get_settings_path,
    load_settings_json,
    save_settings_json,
)


SKILL_NAME = "team-memory"
SKILL_MARKER = f"<!-- {SKILL_NAME} managed -->"

# ─── Hook definitions ──────────────────────────────────────────────────

def _get_bin_path() -> str:
    """Get absolute path to team-memory CLI."""
    return shutil.which("team-memory") or sys.executable + " -m team_memory"


def _build_hooks(bin_path: str) -> dict:
    """Build the hooks configuration for settings.json."""
    return {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{bin_path} pull --quiet && {bin_path} knowledge pull && {bin_path} load auto",
                        "shell": "bash",
                        "async": True,
                        "statusMessage": "Syncing team memory...",
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Write|Edit",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{bin_path} push --quiet",
                        "shell": "bash",
                        "async": True,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{bin_path} extract run",
                        "shell": "bash",
                        "async": True,
                    }
                ],
            }
        ],
        "SessionEnd": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{bin_path} push --quiet",
                        "shell": "bash",
                        "timeout": 10,
                    }
                ],
            }
        ],
    }


def _get_global_settings_path() -> Path:
    """Get the global ccb settings.json path.

    Resolution order:
    1. CLAUDE_CONFIG_DIR env var
    2. ~/.ccb-dev/settings.json (if it exists — ccb-dev default)
    3. ~/.claude/settings.json
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / "settings.json"

    # ccb-dev is the primary target; use its config dir if present.
    ccb_dev = Path(os.path.expanduser("~/.ccb-dev"))
    if ccb_dev.is_dir():
        return ccb_dev / "settings.json"

    return Path(os.path.expanduser("~/.claude")) / "settings.json"


def _load_global_settings() -> dict:
    """Read the global ccb settings.json."""
    path = _get_global_settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _save_global_settings(data: dict) -> None:
    """Write the global ccb settings.json."""
    path = _get_global_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_hooks(project_root: str | Path | None = None, bin_path: str | None = None, global_hooks: bool = True) -> tuple[bool, str]:
    """Register team-memory hooks in ccb settings.json.

    By default installs to the global ccb settings (CLAUDE_CONFIG_DIR or ~/.claude/)
    so hooks fire for every session. The team-memory CLI detects per-project config
    and silently no-ops when team memory is not configured for the current project.

    Returns (success, message).
    """
    bp = bin_path or _get_bin_path()
    hooks_to_add = _build_hooks(bp)

    if global_hooks:
        settings = _load_global_settings()
        settings_path = _get_global_settings_path()
    else:
        root = Path(project_root) if project_root else Path.cwd()
        settings = load_settings_json(root)
        settings_path = get_settings_path(root)

    # Merge hooks — preserve existing hooks for other events
    existing_hooks = settings.get("hooks", {})

    for event, entries in hooks_to_add.items():
        if event not in existing_hooks:
            existing_hooks[event] = []

        # 移除旧的 team-memory hooks（确保 install 幂等，
        # 避免新旧命令并存，如 extract prompt 和 extract run）
        existing_hooks[event] = [
            e for e in existing_hooks[event]
            if not any(
                "team-memory" in h.get("command", "")
                for h in e.get("hooks", [])
            )
        ]

        # 添加新的 hooks
        for entry in entries:
            existing_hooks[event].append(entry)

    settings["hooks"] = existing_hooks

    if global_hooks:
        _save_global_settings(settings)
    else:
        save_settings_json(settings, root if not global_hooks else None)

    return True, f"Hooks registered in {settings_path}"


def install_skill(project_root: str | Path | None = None, bin_path: str | None = None, global_skill: bool = False) -> tuple[bool, str]:
    """Create the team-memory skill file.

    If global_skill is True, installs to ~/.ccb-dev/skills/ (or ~/.claude/skills/).
    Otherwise installs to the project's .claude/skills/.

    Returns (success, message).
    """
    bp = bin_path or _get_bin_path()

    # Find the SKILL.md template
    template_paths = [
        Path(__file__).parent.parent / "templates" / "SKILL.md",
        Path(sys.prefix) / "share" / "ccb-team-memory" / "templates" / "SKILL.md",
    ]

    template = None
    for tp in template_paths:
        if tp.exists():
            template = tp.read_text()
            break

    if template is None:
        # Use embedded template
        template = _get_embedded_skill_template()

    # Replace bin path placeholder
    template = template.replace("%%BIN_PATH%%", bp)

    if global_skill:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        if config_dir:
            base = Path(config_dir)
        else:
            ccb_dev = Path(os.path.expanduser("~/.ccb-dev"))
            if ccb_dev.is_dir():
                base = ccb_dev
            else:
                base = Path(os.path.expanduser("~/.claude"))
        skills_dir = base / "skills" / SKILL_NAME
    else:
        root = Path(project_root) if project_root else Path.cwd()
        skills_dir = root / ".claude" / "skills" / SKILL_NAME

    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(template)

    return True, f"Skill registered at {skill_file}"


def _get_embedded_skill_template() -> str:
    """Fallback embedded skill template."""
    return """---
name: team-memory
description: 企业团队记忆同步与提取。通过 Git 同步管理共享团队知识，从对话中提取记忆，扫描密钥，加载团队上下文。
user-invocable: true
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep]
argument-hint: "<命令> [参数]"
---

# 团队记忆

管理 ccb 的企业团队记忆。可用命令：

- `/team-memory init --repo <url>` — 初始化项目团队记忆
- `/team-memory pull` — 拉取最新团队记忆
- `/team-memory push` — 推送本地变更（自动扫描密钥）
- `/team-memory scan` — 扫描密钥
- `/team-memory status` — 查看同步状态
- `/team-memory extract` — 从本次对话提取记忆
- `/team-memory load [查询]` — 搜索并加载团队记忆
- `/team-memory install` — 安装 hooks 和自动同步

## 使用方式

调用时通过 Bash 执行对应的 `team-memory` CLI 命令：

```bash
team-memory <命令> <参数>
```

`extract`：CLI 输出提取提示词，按提示从当前对话中识别、分类、保存记忆。

`load`：CLI 输出已加载的记忆内容，用于指导当前工作。

团队记忆存储在 `.claude/team-memory/`，通过 Git 同步。
"""


def install_all(project_root: str | Path | None = None, bin_path: str | None = None, global_hooks: bool = True) -> tuple[bool, str]:
    """Install both hooks and skill.

    Hooks are installed globally by default so they fire for every session.
    The skill is installed in the project's .claude/skills/ (or CWD if no
    project root is found).

    Returns (success, combined message).
    """
    ok_hooks, msg_hooks = install_hooks(project_root, bin_path, global_hooks=global_hooks)
    ok_skill, msg_skill = install_skill(project_root, bin_path, global_skill=global_hooks)

    parts = []
    if ok_hooks:
        parts.append(f"[OK] {msg_hooks}")
    else:
        parts.append(f"[FAIL] {msg_hooks}")
    if ok_skill:
        parts.append(f"[OK] {msg_skill}")
    else:
        parts.append(f"[FAIL] {msg_skill}")

    return (ok_hooks and ok_skill), "\n".join(parts)


def uninstall_hooks(project_root: str | Path | None = None, global_hooks: bool = True) -> tuple[bool, str]:
    """Remove team-memory hooks from settings.json."""
    if global_hooks:
        settings = _load_global_settings()
        settings_path = _get_global_settings_path()
    else:
        root = Path(project_root) if project_root else Path.cwd()
        settings = load_settings_json(root)
        settings_path = get_settings_path(root)

    existing_hooks = settings.get("hooks", {})
    removed = False

    for event in list(existing_hooks.keys()):
        entries = existing_hooks[event]
        filtered = []
        for entry in entries:
            commands = [h.get("command", "") for h in entry.get("hooks", [])]
            if any("team-memory" in c for c in commands):
                removed = True
                continue
            filtered.append(entry)
        if filtered:
            existing_hooks[event] = filtered
        else:
            del existing_hooks[event]

    if existing_hooks:
        settings["hooks"] = existing_hooks
    elif "hooks" in settings:
        del settings["hooks"]

    if global_hooks:
        _save_global_settings(settings)
    else:
        save_settings_json(settings, root if not global_hooks else Path.cwd())

    return True, "Hooks removed" if removed else "No hooks to remove"


def uninstall_skill(project_root: str | Path | None = None, global_skill: bool = True) -> tuple[bool, str]:
    """Remove the team-memory skill file.

    Mirrors install_skill() — removes from the same locations.
    """
    import shutil

    removed = []

    if global_skill:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        if config_dir:
            base = Path(config_dir)
        else:
            ccb_dev = Path(os.path.expanduser("~/.ccb-dev"))
            if ccb_dev.is_dir():
                base = ccb_dev
            else:
                base = Path(os.path.expanduser("~/.claude"))
        skill_dir = base / "skills" / SKILL_NAME
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            removed.append(str(skill_dir))

    # Also remove project-level skill
    root = Path(project_root) if project_root else Path.cwd()
    proj_skill = root / ".claude" / "skills" / SKILL_NAME
    if proj_skill.exists():
        shutil.rmtree(proj_skill)
        removed.append(str(proj_skill))

    if removed:
        return True, f"Skill removed: {', '.join(removed)}"
    return True, "No skill to remove"


def _ensure_rules_wrapper(root: Path) -> None:
    """Create .claude/rules/team-memory.md @include wrapper so ccb discovers
    team memory via its standard .claude/rules/*.md loading mechanism."""
    rules_dir = root / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    wrapper = rules_dir / "team-memory.md"
    wrapper_text = (
        "<!-- team-memory managed -->\n"
        "# 团队记忆\n\n"
        "通过 ccb-team-memory 同步的团队共享知识。\n\n"
        "@../team-memory/MEMORY.md\n"
    )
    wrapper.write_text(wrapper_text)
