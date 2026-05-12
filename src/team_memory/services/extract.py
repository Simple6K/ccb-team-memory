"""Memory extraction: prompt generation, manifest scanning, mode dispatch.

Generates extraction prompts that the ccb model uses to identify and save
memories from conversation context. Independent of ccb's native auto-memory.

V4.6: Manifest 升级为解析 frontmatter（问题 3），按类型分组展示（问题 4），
提取状态上下文注入（问题 1, 2），指令约束精确化（问题 6, 10）。
"""

import os
import re
import time
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
- 与当前项目无关的通用讨论（纯技术问答、闲聊、不涉及项目决策/约束/流程的对话）

这些排除规则**即使用户明确要求保存也适用**。如果用户要求保存 PR 列表或活动摘要，
询问其中哪些是*意外*或*非显而易见*的——只有那部分值得保留。
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
系统会自动添加时间戳和提交人前缀（如 `20260507T153045-zhangsan-react-preferences.md`），
以确保多人提取时文件名不冲突。
"""

HOW_TO_SAVE = """
## 如何保存记忆

**指令模式**（本任务）：
1. 将新记忆文件写入对应目录（team scope → shared/，project scope → projects/<name>/）
2. 更新目标目录的 MEMORY.md 索引
3. 确保 frontmatter 中包含 `extracted_at`（当前时间）和 `contributor`（当前对话用户）

**MEMORY.md 约束**：
- 每条索引不超过 150 字符
- 按类型分组（user / feedback / project / reference）
- 索引总行数不超过 200 行，超过会导致后续内容被截断

- 保持 name、description、type、extracted_at、contributor 字段为最新状态
- 按主题而非时间顺序组织
- 更新过时记忆而非创建重复项
- **写入新记忆前，先检查 shared/ 和 projects/ 中是否存在描述相同主题的已有记忆。如有，更新已有文件；如无，再创建新文件**
"""

HOW_TO_SAVE_AUTO = """
## 如何保存记忆（自动模式）

**自动提取阶段**（本任务）：
1. **所有新记忆文件必须写入 `_staging/` 目录**，不要写 shared/ 或 projects/
2. `_staging/` 下不需要创建或更新 MEMORY.md 索引
3. 确保 frontmatter 中包含 `extracted_at`（当前时间）和 `contributor`（当前对话用户）
4. 可以读取 shared/ 和 projects/ 中的已有记忆用于去重参考，但**不要修改它们**

**审核通过后**（由用户通过 `team-memory review` 命令手动完成）：
- 记忆文件会从 `_staging/` 移到 `shared/` 或 `projects/<name>/`
- 目标目录的 MEMORY.md 索引会由 review 命令自动更新

**去重策略**：
- 如果 shared/ 或 projects/ 中已有相同主题的记忆，仍然在 _staging/ 中创建新文件
- 审核时管理员会判断是合并还是替换
- 不要因为"已有文件"而跳过提取或直接修改已有文件
"""

EXTRACTION_INSTRUCTIONS = """
## 任务

0. **先判断对话是否与当前项目相关**：如果对话内容与项目无关（如通用技术问答、闲聊、不涉及当前项目代码/决策/流程），直接回复"无项目相关记忆"并停止，不要读取或写入任何文件。判断标准：
   - 是否涉及当前项目目录下的代码、配置、架构?
   - 是否涉及项目的决策、约束、进度、人员?
   - 是否涉及跨项目共享的团队规范或工具链?
   以上皆否 → 无项目相关记忆，跳过提取
1. 回顾上述对话，找出可提取的知识
2. 将每条发现归类为四种记忆类型之一
3. 确定范围（team 还是 project），写入 frontmatter 的 scope 字段
4. **先检查已有记忆清单**：如发现描述相同主题的已有记忆，更新该文件而非创建新文件
5. 将记忆文件写入对应目录
6. 更新相应的 MEMORY.md 索引
"""

EXTRACTION_INSTRUCTIONS_AUTO = """
## 任务（自动模式）

0. **先判断对话是否与当前项目相关**：如果对话内容与项目无关（如通用技术问答、闲聊、不涉及当前项目代码/决策/流程），直接回复"无项目相关记忆"并停止，不要读取或写入任何文件。判断标准：
   - 是否涉及当前项目目录下的代码、配置、架构?
   - 是否涉及项目的决策、约束、进度、人员?
   - 是否涉及跨项目共享的团队规范或工具链?
   以上皆否 → 无项目相关记忆，跳过提取
1. 回顾上述对话，找出可提取的知识
2. 将每条发现归类为四种记忆类型之一
3. 确定范围（team 还是 project），写入 frontmatter 的 scope 字段
4. 读取 _staging/、shared/、projects/ 中的已有记忆用于去重判断，但**不要修改它们**
5. **所有新记忆一律写入 _staging/ 目录**，不要写 shared/ 或 projects/
6. **不要创建或更新 shared/ 或 projects/ 下的 MEMORY.md**，审核通过后会自动处理
"""


# ─── Manifest ──────────────────────────────────────────────────────

@dataclass
class ManifestEntry:
    """记忆文件清单条目。

    对应 claude-code memoryScan.ts MemoryHeader:
    - filename, filePath, mtimeMs, description, type
    """
    path: str          # 相对于 base_dir 的路径
    mtime: float       # 修改时间
    size: int          # 文件大小
    name: str = ""     # frontmatter name（对应 MemoryHeader.description 的去重键）
    description: str = ""  # frontmatter description
    type: str = ""     # frontmatter type (user/feedback/project/reference)
    scope: str = ""    # frontmatter scope (team/project)


def _strip_yaml_quotes(s: str) -> str:
    """去掉 YAML 值的引号包裹。'\"abc\"' → 'abc'，''abc'' → 'abc'。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _parse_frontmatter(content: str) -> dict[str, str]:
    """解析 YAML frontmatter 为 dict。"""
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return {}
    result: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = _strip_yaml_quotes(value)
    return result


def scan_manifest(base_dir: Path, max_files: int = 200) -> list[ManifestEntry]:
    """扫描目录中的 .md 记忆文件，解析 frontmatter 返回完整清单。

    对应 claude-code scanMemoryFiles()（memoryScan.ts:35）：
    - 读取每个 .md 文件的前 30 行（frontmatter 部分）
    - 解析 name / description / type
    - 按 mtime 降序排列
    - 上限 max_files（默认 200）

    排除 .git/ 目录和 MEMORY.md 自身。
    """
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
            # 读取 frontmatter（前 30 行足够覆盖）
            content = md_file.read_text()
            fm = _parse_frontmatter(content)
            entries.append(ManifestEntry(
                path=str(md_file.relative_to(base_dir)),
                mtime=st.st_mtime,
                size=st.st_size,
                name=fm.get("name", ""),
                description=fm.get("description", ""),
                type=fm.get("type", ""),
                scope=fm.get("scope", ""),
            ))
        except OSError:
            continue

    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries[:max_files]


def get_scope_dirs(base_dir: Path, scope: str, project_name: str) -> list[tuple[str, Path]]:
    """获取指定 scope 对应的目录列表。

    对应 claude-code getTeamMemPath() + getAutoMemPath() 的目录解析逻辑。
    从 _get_scope_dirs 重命名为公共函数（问题 10）。

    Args:
        base_dir: 团队记忆根目录（.claude/team-memory/）
        scope: "team" | "project" | "all"
        project_name: 项目名称

    Returns:
        [(标签, 目录路径), ...] 列表
    """
    dirs: list[tuple[str, Path]] = []
    if scope in ("team", "all"):
        shared = base_dir / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        dirs.append(("团队 (shared/)", shared))
    if scope in ("project", "all") and project_name:
        proj = base_dir / "projects" / project_name
        proj.mkdir(parents=True, exist_ok=True)
        dirs.append((f"项目 (projects/{project_name}/)", proj))
    return dirs


# Backward compat alias
_get_scope_dirs = get_scope_dirs


# ─── Extraction Prompt ──────────────────────────────────────────────

def _format_manifest_grouped(entries: list[ManifestEntry]) -> str:
    """按类型分组格式化已有记忆清单。

    对应 claude-code formatMemoryManifest()（memoryScan.ts:84）：
    [type] filename (timestamp): description
    """
    if not entries:
        return "（暂无已有记忆）"

    grouped: dict[str, list[ManifestEntry]] = {}
    for entry in entries:
        t = entry.type or "unknown"
        grouped.setdefault(t, []).append(entry)

    lines: list[str] = []
    type_order = ["user", "feedback", "project", "reference"]
    for t in type_order:
        if t not in grouped:
            continue
        lines.append(f"### {t}")
        for entry in grouped[t]:
            desc = f" — {entry.description}" if entry.description else ""
            lines.append(f"- `{entry.path}`{desc}")
        lines.append("")
    # 其他类型
    for t, group in grouped.items():
        if t in type_order:
            continue
        lines.append(f"### {t}")
        for entry in group:
            desc = f" — {entry.description}" if entry.description else ""
            lines.append(f"- `{entry.path}`{desc}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_extract_prompt(
    config: TeamMemoryConfig,
    project_root: Path | None = None,
    mode: str = "instruction",
    extraction_manager=None,  # Optional[ExtractionManager]
) -> str:
    """构建提取 prompt。

    对应 claude-code buildExtractAutoOnlyPrompt() / buildExtractCombinedPrompt()
    （prompts.ts:50, 101）。

    V4.6 增强（问题 1-6, 10）：
    - 注入提取状态上下文（session_id, 上次提取时间）
    - 按类型分组展示已有记忆（含 description 用于去重）
    - 不再截断 manifest（每目录最多 200）
    - MEMORY.md 约束精确化
    """
    tm_dir = get_team_memory_dir(project_root)
    project_name = get_project_name(project_root or Path.cwd()) or "unknown"

    scope = config.extract.scope
    scope_dirs = get_scope_dirs(tm_dir, scope, project_name)

    lines = [
        "# 团队记忆提取",
        "",
        f"模式: **{mode}** | 范围: **{scope}** | 项目: **{project_name}**",
        "",
    ]

    # ── 提取状态上下文（问题 1, 2） ──
    if extraction_manager is not None:
        try:
            summary = extraction_manager.get_summary_for_prompt()
            if summary:
                lines.append("## 提取状态")
                lines.append("")
                lines.append(summary)
                lines.append("")
        except Exception:
            pass

    # 本次提取范围说明（问题 1 — session 上下文）
    now_str = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    session_id = f"{project_name}@{now_str[:19]}"
    lines.append(f"会话标识: `{session_id}`")
    lines.append(f"分析范围: 从上次提取时间至今的新对话内容。如果是首次提取，分析全部可用对话。")
    lines.append("")

    # ── 目标目录 ──
    lines.append("## 目标目录")
    lines.append("")
    # 自动提取写入 _staging/ 待审核区
    staging_dir = tm_dir / "_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    lines.append(f"- **待审核 (_staging/)**: `{staging_dir}` （自动提取写入此目录）")
    for label, d in scope_dirs:
        lines.append(f"- **{label}**: `{d}` （审核通过后移入）")
    lines.append("")

    # ── 记忆类型 ──
    lines.append(MEMORY_TYPES_HELP)

    # ── 排除规则 ──
    lines.append(WHAT_NOT_TO_SAVE)

    # ── 文件格式 ──
    lines.append(FILE_FORMAT)

    # ── 已有记忆清单（问题 3, 4: 按类型分组 + 含描述 + 不限截断） ──
    lines.append("## 已有记忆（按类型分组，含描述 — 用于去重判断）")
    lines.append("")
    has_existing = False
    # 也展示 _staging/ 中已有的待审核记忆，用于去重
    all_dirs: list[tuple[str, Path]] = [
        ("待审核 (_staging/)", staging_dir)
    ] + scope_dirs
    for label, d in all_dirs:
        manifest = scan_manifest(d)
        if manifest:
            has_existing = True
            lines.append(f"### {label}（{len(manifest)} 条）")
            lines.append("")
            lines.append(_format_manifest_grouped(manifest))
            lines.append("")
    if not has_existing:
        lines.append("（暂无已有记忆）")
        lines.append("")

    # ── 保存方法（auto 模式用专门的指令，禁止更新 shared/projects）──
    if mode == "auto":
        lines.append(HOW_TO_SAVE_AUTO)
    else:
        lines.append(HOW_TO_SAVE)

    # ── 提取任务（auto 模式用专门的指令，禁止直接写 shared/projects）──
    if mode == "auto":
        lines.append(EXTRACTION_INSTRUCTIONS_AUTO)
    else:
        lines.append(EXTRACTION_INSTRUCTIONS)

    # ── 模式特定提示 ──
    if mode == "auto":
        lines.append("")
        lines.append(
            "**自动模式**: 提取完成后，将新记忆写入 `_staging/` 目录。"
            "`_staging/` 中的文件会被推送到团队仓库，"
            "但不会自动加载到模型上下文。"
            "用户通过 `team-memory review` 审核后，记忆才会移到 shared/ 或 projects/ 并生效。"
        )
    elif mode == "instruction":
        lines.append("")
        lines.append(
            "**指令模式**: 用户要求你提取记忆。"
            "完成提取并确认保存的内容。"
        )

    return "\n".join(lines)


# ─── Metadata stripping ─────────────────────────────────────────────

def strip_metadata_fields(content: str) -> str:
    """移除 frontmatter 中的 extracted_at 和 contributor 行。

    These fields are stored for audit traceability but should not
    consume tokens when the memory is loaded into model context.
    """
    return re.sub(
        r'^(extracted_at|contributor):.*\n',
        '',
        content,
        flags=re.MULTILINE,
    )


# ─── Auto-load summary ──────────────────────────────────────────────

def generate_auto_load_summary(config: TeamMemoryConfig, project_root: Path | None = None) -> str:
    """生成团队记忆摘要，用于 session 启动时自动加载。

    对应 claude-code loadMemoryPrompt()（memdir.ts:419）的 MEMORY.md 注入。
    仅加载 shared/ + 当前项目的项目目录，不加载其他项目的记忆。

    项目目录优先从 config.project_path 推导（与远程拉取路径保持一致），
    为空时回退到 get_project_name()。
    """
    from ..config import get_project_name

    tm_dir = get_team_memory_dir(project_root)
    if not tm_dir.is_dir():
        return ""

    # 项目目录：优先从 config.project_path 推导，与拉取路径一致
    # project_path 例如 "projects/owner--repo/project_name.md"
    # → 父目录 "projects/owner--repo" 即为项目记忆目录
    project_dir: Path | None = None
    project_label: str = ""
    project_path_str = config.project_path
    if project_path_str:
        p = Path(project_path_str)
        # 如果是指向 .md 文件，取其父目录；否则直接使用
        if p.suffix == ".md":
            project_dir = tm_dir / p.parent
        else:
            project_dir = tm_dir / p
        project_label = str(p.parent if p.suffix == ".md" else p)
    else:
        project_name = get_project_name(project_root, config) or ""
        if project_name:
            project_dir = tm_dir / "projects" / project_name
            project_label = f"projects/{project_name}"

    lines = [
        "# 团队记忆摘要",
        "",
        "以下团队记忆可用，请在工作中参考使用。",
        "",
    ]

    # shared/ — 所有项目共享的团队记忆
    shared_dir = tm_dir / "shared"
    shared_index = shared_dir / "MEMORY.md"
    if shared_index.exists():
        content = strip_metadata_fields(shared_index.read_text())
        content_lines = content.split("\n")
        if len(content_lines) > 100:
            content_lines = content_lines[:100]
            content_lines.append(f"> ... 截断 ({len(content.split(chr(10))) - 100} 行)")
        lines.append("## shared/")
        lines.append("")
        lines.extend(content_lines)
        lines.append("")

    # 项目记忆 — 路径与 config.project_path 一致
    if project_dir is not None:
        project_index = project_dir / "MEMORY.md"
        if project_index.exists():
            content = strip_metadata_fields(project_index.read_text())
            content_lines = content.split("\n")
            if len(content_lines) > 100:
                content_lines = content_lines[:100]
                content_lines.append(f"> ... 截断 ({len(content.split(chr(10))) - 100} 行)")
            lines.append(f"## {project_label}/")
            lines.append("")
            lines.extend(content_lines)
            lines.append("")

    return "\n".join(lines) if len(lines) > 3 else ""
