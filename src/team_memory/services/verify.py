"""记忆验证：frontmatter 完整性、MEMORY.md 一致性、目录健康检查。

对应 claude-code extractWrittenPaths()（extractMemories.ts:251）+
MemorySavedMessage（extractMemories.ts:491）的后验证逻辑。

在 push 前置检查和独立 verify 命令中使用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

VALID_TYPES = {"user", "feedback", "project", "reference"}
VALID_SCOPES = {"team", "project"}


@dataclass
class VerifyResult:
    """验证结果。对应 claude-code 提取后的日志 + MemorySavedMessage。"""
    errors: list[str] = field(default_factory=list)    # 阻塞性问题
    warnings: list[str] = field(default_factory=list)  # 非阻塞
    file_count: int = 0
    indexed_count: int = 0
    orphan_index_count: int = 0      # MEMORY.md 中有但文件不存在的条目
    unindexed_count: int = 0         # 文件存在但 MEMORY.md 中无条目
    duplicate_names: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        parts = [
            f"文件数: {self.file_count}",
            f"已索引: {self.indexed_count}",
        ]
        if self.orphan_index_count:
            parts.append(f"孤立索引: {self.orphan_index_count}")
        if self.unindexed_count:
            parts.append(f"未索引: {self.unindexed_count}")
        if self.errors:
            parts.append(f"错误: {len(self.errors)}")
        if self.warnings:
            parts.append(f"警告: {len(self.warnings)}")
        return " | ".join(parts)


# ── 单文件验证 ──────────────────────────────────────────────────

def verify_memory_file(filepath: Path) -> list[str]:
    """验证单个记忆文件。

    检查项（对应 claude-code frontmatter 解析 + 类型校验）：
    - frontmatter 存在且包含必需字段
    - type 值合法
    - extracted_at 格式正确
    - 文件非空

    Returns:
        错误列表（空列表 = 验证通过）
    """
    errors: list[str] = []
    rel = str(filepath)

    try:
        content = filepath.read_text()
    except OSError as e:
        return [f"{rel}: 无法读取 — {e}"]

    if not content.strip():
        errors.append(f"{rel}: 文件为空")
        return errors

    # Parse frontmatter
    frontmatter = _parse_frontmatter(content)
    if not frontmatter:
        errors.append(f"{rel}: 缺少 YAML frontmatter（---...---）")
        return errors

    # 必需字段
    for field in ("name", "description", "type"):
        if field not in frontmatter:
            errors.append(f"{rel}: frontmatter 缺少必需字段 '{field}'")

    # type 合法性
    mem_type = frontmatter.get("type", "")
    if mem_type and mem_type not in VALID_TYPES:
        errors.append(
            f"{rel}: type '{mem_type}' 不合法，应为 {', '.join(sorted(VALID_TYPES))}"
        )

    # scope 合法性
    scope = frontmatter.get("scope", "")
    if scope and scope not in VALID_SCOPES:
        errors.append(
            f"{rel}: scope '{scope}' 不合法，应为 {', '.join(sorted(VALID_SCOPES))}"
        )

    # extracted_at 格式
    extracted_at = frontmatter.get("extracted_at", "")
    if extracted_at and not _is_valid_iso8601(extracted_at):
        errors.append(f"{rel}: extracted_at 格式无效: '{extracted_at}'")

    return errors


def _parse_frontmatter(content: str) -> dict[str, str]:
    """解析 YAML frontmatter。返回 dict 或空 dict。"""
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return {}
    frontmatter: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip()
    return frontmatter


def _is_valid_iso8601(value: str) -> bool:
    """宽松检查 ISO 8601 格式（YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS...）。"""
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}', value))


# ── 目录级验证 ─────────────────────────────────────────────────

def verify_memories_dir(tm_dir: Path) -> VerifyResult:
    """验证团队记忆目录的健康状况。

    检查项：
    - 每个 .md 文件的 frontmatter 完整性
    - MEMORY.md 条目与实际文件一致性
    - 无孤立索引条目
    - 无未索引的记忆文件
    - 无重复 name

    Returns:
        VerifyResult 包含所有检查结果
    """
    result = VerifyResult()

    if not tm_dir.is_dir():
        result.errors.append(f"目录不存在: {tm_dir}")
        return result

    # 收集所有记忆文件（排除 MEMORY.md 和 .git）
    memory_files: dict[str, Path] = {}  # rel_path -> absolute
    all_names: dict[str, list[str]] = {}  # name -> [rel_path, ...]

    for md_file in sorted(tm_dir.rglob("*.md")):
        if ".git" in md_file.parts:
            continue
        if "_staging" in md_file.parts:
            continue
        if md_file.name == "MEMORY.md":
            continue
        rel = str(md_file.relative_to(tm_dir))
        memory_files[rel] = md_file

        # 解析 frontmatter 中的 name
        try:
            content = md_file.read_text()
            fm = _parse_frontmatter(content)
            name = fm.get("name", "")
            if name:
                all_names.setdefault(name, []).append(rel)
        except OSError:
            pass

    result.file_count = len(memory_files)

    # 单文件验证
    for rel, path in sorted(memory_files.items()):
        file_errors = verify_memory_file(path)
        # 将 verify_memory_file 的错误升级为 result 的 error
        result.errors.extend(file_errors)

    # 重复 name 检测
    for name, paths in all_names.items():
        if len(paths) > 1:
            result.duplicate_names.append(name)
            result.warnings.append(
                f"重复的 name '{name}': {', '.join(paths)}"
            )

    # MEMORY.md 一致性检查
    for md_file in sorted(tm_dir.rglob("MEMORY.md")):
        if ".git" in md_file.parts:
            continue
        check_memory_md_consistency(md_file, memory_files, result)

    # 未索引文件检测
    indexed_paths = _collect_indexed_paths(tm_dir)
    for rel in memory_files:
        if rel not in indexed_paths:
            result.unindexed_count += 1
            result.warnings.append(f"未索引的文件: {rel}")

    return result


def check_memory_md_consistency(
    mem_md_path: Path,
    memory_files: dict[str, Path],
    result: VerifyResult,
) -> None:
    """检查单个 MEMORY.md 的条目一致性。"""
    try:
        content = mem_md_path.read_text()
    except OSError:
        return

    # 提取所有 [title](file.md) 引用
    refs = set(re.findall(r'\[([^\]]+)\]\(([^\)]+\.md)\)', content))
    dir_base = mem_md_path.parent

    for title, ref_path in refs:
        result.indexed_count += 1
        # 解析相对路径
        relative_ref = str(
            (dir_base / ref_path).resolve().relative_to(
                mem_md_path.parents[1].resolve()
            )
        ) if ".." in ref_path else ref_path

        # 检查引用文件是否存在（匹配相对路径）
        full_path = dir_base / ref_path
        rel_from_tm = str(mem_md_path.parents[0].relative_to(
            mem_md_path.parents[1]
        )) if len(mem_md_path.parents) > 2 else ""
        # 简化检查：直接看文件名
        ref_filename = Path(ref_path).name
        found = any(
            Path(rp).name == ref_filename
            for rp in memory_files
        )
        if not found:
            result.orphan_index_count += 1
            result.warnings.append(
                f"MEMORY.md 引用不存在的文件: [{title}]({ref_path}) "
                f"(在 {mem_md_path.parent.name}/)"
            )


def _collect_indexed_paths(tm_dir: Path) -> set[str]:
    """收集所有 MEMORY.md 中索引的文件路径。"""
    indexed: set[str] = set()
    for mem_md in tm_dir.rglob("MEMORY.md"):
        if ".git" in mem_md.parts:
            continue
        try:
            content = mem_md.read_text()
        except OSError:
            continue
        refs = re.findall(r'\[([^\]]+)\]\(([^\)]+\.md)\)', content)
        dir_base = mem_md.parent
        for _title, ref_path in refs:
            # 添加相对于 tm_dir 的路径
            try:
                abs_path = (dir_base / ref_path).resolve()
                rel = str(abs_path.relative_to(tm_dir.resolve()))
                indexed.add(rel)
            except ValueError:
                # 路径不在 tm_dir 下
                indexed.add(ref_path)
    return indexed


# ── Push 前置验证 ───────────────────────────────────────────────

def verify_before_push(tm_dir: Path) -> bool:
    """Push 前的快速验证检查。

    只检查阻塞性错误（不检查 warnings），
    返回 True 表示可以安全 push。

    对应 claude-code push 前的 secretScanner 检查。
    """
    result = verify_memories_dir(tm_dir)
    if result.errors:
        return False
    return True
