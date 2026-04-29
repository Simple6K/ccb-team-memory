"""Memory extraction: prompt generation, manifest scanning, mode dispatch.

Generates extraction prompts that the ccb model uses to identify and save
memories from conversation context. Independent of ccb's native auto-memory.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import TeamMemoryConfig, get_team_memory_dir, get_project_name


# ─── Constants ─────────────────────────────────────────────────────────

EXTRACT_MODES = ("manual", "instruction", "auto")
EXTRACT_SCOPES = ("team", "project", "all")

MEMORY_TYPES_HELP = """
## 记忆类型

可以保存四种类型的记忆：

<types>
<type>
    <name>user</name>
    <scope>默认: team</scope>
    <description>团队成员的角色、偏好和知识背景。
    帮助定制协作方式。避免负面评价。</description>
    <example>
    用户："我们团队所有项目统一用 bun 而非 npm"
    助手：[保存 user 类型团队记忆：团队偏好使用 bun]
    </example>
</type>
<type>
    <name>feedback</name>
    <scope>team > project</scope>
    <description>工作中的经验教训和纠正。成功和失败都要记录。
    包含 *原因* 以便判断边界情况。</description>
    <example>
    用户："不要 mock 数据库——上次被坑过，模拟测试通过但生产迁移失败"
    助手：[保存 feedback 类型团队记忆：集成测试必须连接真实数据库]
    </example>
</type>
<type>
    <name>project</name>
    <scope>project > team</scope>
    <description>本项目专属的架构决策、约束、里程碑。
    始终将相对日期转换为绝对日期。</description>
    <example>
    用户："周四以后冻结所有非紧急合并，准备移动端发版"
    助手：[保存 project 类型项目记忆：合并冻结从 2026-XX-XX 开始]
    </example>
</type>
<type>
    <name>reference</name>
    <scope>team</scope>
    <description>外部系统中的信息指针（文档、看板、监控）。</description>
    <example>
    用户："管道问题都在 Linear 的 INGEST 项目里跟踪"
    助手：[保存 reference 类型团队记忆：管道问题在 Linear INGEST 跟踪]
    </example>
</type>
</types>
"""

WHAT_NOT_TO_SAVE = """
## 不应保存的内容

- 代码片段或源文件内容
- 会话特定的临时上下文（临时状态、当前任务细节）
- 已在 CLAUDE.md 中存在的信息
- 敏感数据（API 密钥、令牌、密码）
- 临时调试状态（断点、变量值）
- 会过时的 Git 分支名和 PR 号
- 可通过链接引用的文档原文
"""

FILE_FORMAT = """
## 文件格式

每条记忆是一个带 YAML frontmatter 的 Markdown 文件：

```markdown
---
name: 简短名称
description: 一句话描述，用于相关性匹配
type: user|feedback|project|reference
scope: team|project
created: YYYY-MM-DD
extracted_at: YYYY-MM-DDTHH:mm:ss+08:00
contributor: 提取人姓名或标识
---

记忆内容。feedback/project 类型的结构：
规则/事实，然后是 **原因：** 和 **如何应用：** 的说明。
```

**字段说明**：
- `extracted_at`：提取时间，ISO 8601 格式。由模型根据当前时间自动填入
- `contributor`：提取人，即当前对话中的用户。从对话中识别用户身份填入

文件名：小写、连字符分隔、具描述性（如 `react-preferences.md`）。
"""

HOW_TO_SAVE = """
## 如何保存记忆

保存分为两步：
1. 将记忆文件写入对应目录，确保 frontmatter 中包含 `extracted_at`（当前时间）和 `contributor`（当前对话用户）
2. 在该目录的 MEMORY.md 索引中添加或更新条目

每个目录有自己的 MEMORY.md 索引。条目格式：
`- [文件名](文件名.md) — 一句话概要`
索引中不体现提取人和时间，这些信息仅存储在文件 frontmatter 中用于追溯。

- 保持 name、description、type、extracted_at、contributor 字段为最新状态
- 按主题而非时间顺序组织
- 更新过时记忆而非创建重复项
- 写入新记忆前检查是否已有相关记忆
"""

EXTRACTION_INSTRUCTIONS = """
## 任务

1. 回顾上述对话，找出可提取的知识
2. 将每条发现归类为四种记忆类型之一
3. 确定范围（team 还是 project）
4. 将记忆文件写入对应目录
5. 更新相应的 MEMORY.md 索引
"""


@dataclass
class ManifestEntry:
    path: str
    mtime: float
    size: int


def scan_manifest(base_dir: Path, max_files: int = 200) -> list[ManifestEntry]:
    """Scan directory for existing .md memory files, sorted by mtime (newest first)."""
    entries: list[ManifestEntry] = []
    if not base_dir.is_dir():
        return entries

    for md_file in sorted(base_dir.rglob("*.md")):
        if ".git" in md_file.parts:
            continue
        if md_file.name == "MEMORY.md":
            continue
        try:
            st = md_file.stat()
            entries.append(ManifestEntry(
                path=str(md_file.relative_to(base_dir)),
                mtime=st.st_mtime,
                size=st.st_size,
            ))
        except OSError:
            continue

    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries[:max_files]


def _get_scope_dirs(base_dir: Path, scope: str, project_name: str) -> list[tuple[str, Path]]:
    """Get the directories to scan/save based on scope."""
    dirs: list[tuple[str, Path]] = []
    if scope in ("team", "all"):
        shared = base_dir / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        dirs.append(("team (shared/)", shared))
    if scope in ("project", "all") and project_name:
        proj = base_dir / "projects" / project_name
        proj.mkdir(parents=True, exist_ok=True)
        dirs.append((f"project (projects/{project_name}/)", proj))
    return dirs


def build_extract_prompt(
    config: TeamMemoryConfig,
    project_root: Path | None = None,
    mode: str = "instruction",
) -> str:
    """Build the extraction prompt based on mode and scope.

    Args:
        config: Team memory configuration
        project_root: Project root directory
        mode: "manual" | "instruction" | "auto"

    Returns:
        Markdown prompt text to be injected into model context
    """
    tm_dir = get_team_memory_dir(project_root)
    project_name = get_project_name(project_root or Path.cwd()) or "unknown"

    scope = config.extract.scope
    scope_dirs = _get_scope_dirs(tm_dir, scope, project_name)

    lines = [
        "# 团队记忆提取",
        "",
        f"模式: **{mode}** | 范围: **{scope}**",
        "",
    ]

    # Directory info
    lines.append("## 目标目录")
    lines.append("")
    for label, d in scope_dirs:
        lines.append(f"- **{label}**: `{d}`")
    lines.append("")

    # Memory type taxonomy
    lines.append(MEMORY_TYPES_HELP)

    # What NOT to save
    lines.append(WHAT_NOT_TO_SAVE)

    # File format
    lines.append(FILE_FORMAT)

    # Existing memories manifest
    lines.append("## 已有记忆（最新优先，避免重复）")
    lines.append("")
    has_existing = False
    for label, d in scope_dirs:
        manifest = scan_manifest(d)
        if manifest:
            has_existing = True
            lines.append(f"### {label}")
            lines.append("")
            for entry in manifest[:50]:  # Cap at 50 per directory
                lines.append(f"- `{entry.path}`")
            if len(manifest) > 50:
                lines.append(f"  ... 还有 {len(manifest) - 50} 条")
            lines.append("")
    if not has_existing:
        lines.append("（暂无已有记忆）")
        lines.append("")

    # How to save
    lines.append(HOW_TO_SAVE)

    # Extraction instructions
    lines.append(EXTRACTION_INSTRUCTIONS)

    if mode == "auto":
        lines.append("")
        lines.append(
            "**自动模式**: 提取完成后，更新 MEMORY.md 并写入所有文件。"
            "变更将自动推送到共享团队仓库。"
        )
    elif mode == "instruction":
        lines.append("")
        lines.append(
            "**指令模式**: 用户要求你提取记忆。"
            "完成提取并确认保存的内容。"
        )

    return "\n".join(lines)


def strip_metadata_fields(content: str) -> str:
    """Remove extracted_at and contributor lines from frontmatter.

    These fields are stored for audit traceability but should not
    consume tokens when the memory is loaded into model context.
    """
    import re
    return re.sub(
        r'^(extracted_at|contributor):.*\n',
        '',
        content,
        flags=re.MULTILINE,
    )


def generate_auto_load_summary(config: TeamMemoryConfig, project_root: Path | None = None) -> str:
    """Generate a brief summary of team memories for auto-load at session start."""
    tm_dir = get_team_memory_dir(project_root)
    if not tm_dir.is_dir():
        return ""

    lines = [
        "# 团队记忆摘要",
        "",
        "以下团队记忆可用，请在工作中参考使用。",
        "",
    ]

    for dname, dpath in [("shared", tm_dir / "shared"), ("projects", tm_dir / "projects")]:
        if not dpath.is_dir():
            continue
        index_file = dpath / "MEMORY.md"
        if index_file.exists():
            content = strip_metadata_fields(index_file.read_text())
            # Truncate to reasonable size
            content_lines = content.split("\n")
            if len(content_lines) > 100:
                content_lines = content_lines[:100]
                content_lines.append(f"> ... truncated ({len(content.split(chr(10))) - 100} more lines)")
            lines.append(f"## {dname}/MEMORY.md")
            lines.append("")
            lines.extend(content_lines)
            lines.append("")

    return "\n".join(lines) if len(lines) > 3 else ""
