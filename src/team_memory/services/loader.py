"""Memory loading: auto-load summary and manual search/load."""

import json
from pathlib import Path

from ..config import TeamMemoryConfig, get_team_memory_dir
from .extract import generate_auto_load_summary, strip_metadata_fields


def auto_load(config: TeamMemoryConfig, project_root: Path | None = None) -> str:
    """Generate team memory summary for auto-injection at session start.

    Returns empty string if auto-load is disabled or no memories exist.
    """
    if not config.load.auto_load:
        return ""
    if not config.enabled:
        return ""
    return generate_auto_load_summary(config, project_root)


def manual_load(config: TeamMemoryConfig, project_root: Path | None = None,
                query: str = "", mem_type: str = "") -> str:
    """Search and load specific memories.

    Args:
        config: Team memory config
        project_root: Project root
        query: Search query (matches filenames and content)
        mem_type: Filter by type (user/feedback/project/reference)

    Returns:
        Markdown string with matching memory contents
    """
    tm_dir = get_team_memory_dir(project_root)
    if not tm_dir.is_dir():
        return "未找到团队记忆。请先运行 'team-memory pull'。"

    import glob as _glob_mod

    results: list[tuple[str, str]] = []
    max_files = config.load.max_files

    for md_file in sorted(tm_dir.rglob("*.md")):
        if ".git" in md_file.parts:
            continue
        if md_file.name == "MEMORY.md":
            continue

        try:
            content = md_file.read_text()
        except OSError:
            continue

        # Filter by type if specified
        if mem_type:
            if f"type: {mem_type}" not in content[:200]:
                continue

        # Filter by query if specified
        if query:
            if query.lower() not in md_file.name.lower() and query.lower() not in content.lower():
                continue

        rel_path = str(md_file.relative_to(tm_dir))
        results.append((rel_path, strip_metadata_fields(content)))

        if len(results) >= max_files:
            break

    if not results:
        base_msg = "未找到匹配的记忆"
        if query:
            base_msg += f"（搜索: '{query}'）"
        if mem_type:
            base_msg += f"（类型: '{mem_type}'）"
        return base_msg + "。"

    lines = [
        "# 团队记忆搜索结果",
        "",
    ]
    if query:
        lines.append(f"搜索: `{query}`")
    if mem_type:
        lines.append(f"类型过滤: `{mem_type}`")
    lines.append(f"找到 {len(results)} 个文件:")
    lines.append("")

    for path, content in results:
        lines.append(f"## {path}")
        lines.append("")
        # Truncate long files
        content_lines = content.split("\n")
        if len(content_lines) > 80:
            content_lines = content_lines[:80]
            content_lines.append("... （已截断）")
        lines.extend(content_lines)
        lines.append("")

    return "\n".join(lines)


def list_memory_files(config: TeamMemoryConfig, project_root: Path | None = None) -> list[dict]:
    """List all team memory files with metadata."""
    tm_dir = get_team_memory_dir(project_root)
    if not tm_dir.is_dir():
        return []

    files = []
    for md_file in sorted(tm_dir.rglob("*.md")):
        if ".git" in md_file.parts:
            continue
        rel = str(md_file.relative_to(tm_dir))
        try:
            st = md_file.stat()
            files.append({
                "path": rel,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except OSError:
            files.append({"path": rel, "size": 0, "mtime": 0})

    return files
