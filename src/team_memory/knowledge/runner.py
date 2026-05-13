"""知识提取与拉取编排。

knowledge extract:
  git pull → 扫描 _staging/ → 提取器筛选 → AI 归纳 → 写入 knowledge/ → commit

knowledge pull:
  git pull → 扫描 knowledge/ → 过滤匹配 → 注入 shared/projects → 更新 MEMORY.md
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import TeamMemoryConfig, get_team_memory_dir, get_project_name
from ..services.extract import _parse_frontmatter, scan_manifest
from ..services.api_client import get_api_credentials, call_anthropic_api
from .store import (
    scan_knowledge_docs,
    write_knowledge_doc,
    update_knowledge_index,
    filter_knowledge_docs,
)
from .registry import discover_extractors, get_extractor_by_name


# ─── knowledge extract ──────────────────────────────────────────────────

def run_knowledge_extract(
    config: TeamMemoryConfig,
    project_root: Path | None = None,
    *,
    extractor_name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """执行知识提取。

    Args:
        config: 团队记忆配置
        project_root: 项目根目录
        extractor_name: 指定提取器名称（None = 所有启用的提取器）
        force: 强制覆盖已有文档
        dry_run: 仅预览，不实际写入

    Returns:
        操作结果摘要文本
    """
    tm_dir = get_team_memory_dir(project_root)
    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    knowledge_dir = tm_dir / kpath
    staging_dir = tm_dir / "_staging"

    # 1. 检查 _staging/ 是否有文件
    if not staging_dir.is_dir() or not list(staging_dir.rglob("*.md")):
        return "无待审核记忆，跳过知识提取。"

    # 2. Git pull（获取远端增量）
    _git_pull(tm_dir)

    # 3. 扫描 _staging/ 中的记忆文件（含 frontmatter 元数据）
    staging_files = _scan_staging_with_metadata(staging_dir)
    if not staging_files:
        return "无待审核记忆，跳过知识提取。"

    # 4. 获取要运行的提取器
    if extractor_name:
        cls = get_extractor_by_name(extractor_name)
        if cls is None:
            return f"未找到提取器: {extractor_name}"
        extractor_classes = [cls]
    else:
        extractor_classes = discover_extractors()

    if not extractor_classes:
        return "未找到任何提取器。"

    # 5. 加载标签字典
    tag_dict = _load_tag_dict(tm_dir, config)

    # 6. 获取 API 凭证
    creds = get_api_credentials()
    if creds is None:
        return "未找到 API 凭证。请检查 ANTHROPIC_AUTH_TOKEN 和 ANTHROPIC_BASE_URL。"

    api_key, base_url, model = creds

    # 7. 逐个提取器执行
    results: list[str] = []
    for cls in extractor_classes:
        extractor = cls()
        # 注入默认标签
        extractor.tags = _get_extractor_tags(cls.name, config)

        # 筛选记忆
        matched = extractor.input_filter(staging_files)
        if not matched:
            results.append(f"提取器 {extractor.name}: 无匹配记忆")
            continue

        # 预生成 doc_id
        doc_id = extractor.generate_doc_id(matched)

        if dry_run:
            title_preview = _preview_title(matched)
            results.append(
                f"[DRY RUN] 提取器 {extractor.name}: "
                f"匹配 {len(matched)} 条记忆 → doc_id={doc_id} "
                f"预计标题: {title_preview}"
            )
            continue

        # 构建 prompt
        prompt = extractor.build_prompt(matched, tag_dict)

        # 调用 AI
        api_result = call_anthropic_api(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个知识管理专家。请根据提示词分析原始记忆，生成结构化知识文档。",
            tools=[],
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_tokens=8192,
        )

        if isinstance(api_result, dict) and api_result.get("error"):
            results.append(f"提取器 {extractor.name}: API 调用失败 - {api_result.get('body', '')}")
            continue

        # 提取 AI 返回的内容
        ai_content = _extract_ai_content(api_result)
        if not ai_content:
            results.append(f"提取器 {extractor.name}: AI 返回内容为空")
            continue

        # 写入知识文档
        title = _extract_title_from_content(ai_content) or f"{extractor.name}-{doc_id}"
        filepath = write_knowledge_doc(knowledge_dir, doc_id, title, ai_content)
        results.append(
            f"提取器 {extractor.name}: "
            f"{len(matched)} 条记忆 → {filepath.name}"
        )

    # 8. 更新 KNOWLEDGE.md
    if not dry_run and results:
        update_knowledge_index(knowledge_dir)

    # 9. Git commit（不 push）
    if not dry_run and any("→" in r for r in results):
        _git_commit(tm_dir, "知识提取：归纳 _staging/ 记忆为知识文档", kpath)

    return "\n".join(results) + "\n\n知识提取完成。运行 team-memory knowledge review list 查看变更。" if results else "知识提取完成，无变更。"


# ─── knowledge pull ────────────────────────────────────────────────────

def run_knowledge_pull(
    config: TeamMemoryConfig,
    project_root: Path | None = None,
    *,
    tags: list[str] | None = None,
    domain: str | None = None,
    doc_id: str | None = None,
    all_docs: bool = False,
    dry_run: bool = False,
) -> str:
    """执行知识拉取：将知识文档注入 shared/ 和 projects/。

    Args:
        config: 团队记忆配置
        project_root: 项目根目录
        tags: 按标签过滤（覆盖配置）
        domain: 按领域过滤（覆盖配置）
        doc_id: 精确拉取指定文档
        all_docs: 拉取全部知识
        dry_run: 仅预览

    Returns:
        操作结果
    """
    tm_dir = get_team_memory_dir(project_root)
    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    knowledge_dir = tm_dir / kpath
    project_name = get_project_name(project_root, config) or "unknown"

    # Git pull 同步远端
    _git_pull(tm_dir)

    # 扫描知识文档
    all_knowledge_docs = scan_knowledge_docs(knowledge_dir)
    if not all_knowledge_docs:
        return "knowledge/ 中无知识文档。请先运行 knowledge extract。"

    # 过滤
    if doc_id:
        matched = [d for d in all_knowledge_docs if d.doc_id == doc_id]
        if not matched:
            return f"未找到 doc_id={doc_id} 的文档。"
    elif all_docs:
        matched = all_knowledge_docs
    else:
        # 从配置读取过滤条件
        knowledge_config = _get_knowledge_config(config)
        load_config = knowledge_config.get("load", {})

        filter_tags = tags or load_config.get("auto_tags", [])
        filter_domains = [domain] if domain else load_config.get("auto_domains", [])
        filter_types = load_config.get("doc_types", ["knowledge", "qa_pair"])
        since = load_config.get("time_range", {}).get("since")
        max_docs = load_config.get("max_docs")

        matched = filter_knowledge_docs(
            all_knowledge_docs,
            tags=filter_tags if filter_tags else None,
            domains=filter_domains if filter_domains else None,
            doc_types=filter_types,
            since=since,
            max_docs=max_docs,
        )

    # 分离 shared/ 知识（始终全量）
    shared_dir = knowledge_dir / "shared"
    shared_docs: list = []
    regular_docs: list = []
    for doc in matched:
        # shared/ 下的文档始终全量
        if str(doc.path).startswith("shared/"):
            shared_docs.append(doc)
        else:
            regular_docs.append(doc)

    if not shared_docs and not regular_docs:
        return "无匹配的知识文档。"

    if dry_run:
        return _format_dry_run_output(shared_docs, regular_docs)

    # 注入到目标目录
    injected: list[str] = []

    # shared/ 知识 → 注入到 shared/
    tm_shared = tm_dir / "shared"
    tm_shared.mkdir(parents=True, exist_ok=True)
    for doc in shared_docs:
        _inject_knowledge_doc(knowledge_dir, doc, tm_shared)
        name = doc.path.name if doc.path.name.startswith("kn-") else f"kn-{doc.path.name}"
        injected.append(f"shared/{name}")

    # 其他知识 → 按 Public 标签决定目标
    tm_projects = tm_dir / "projects" / project_name
    tm_projects.mkdir(parents=True, exist_ok=True)
    for doc in regular_docs:
        name = doc.path.name if doc.path.name.startswith("kn-") else f"kn-{doc.path.name}"
        if "Public" in doc.tags:
            _inject_knowledge_doc(knowledge_dir, doc, tm_shared)
            injected.append(f"shared/{name}")
        else:
            _inject_knowledge_doc(knowledge_dir, doc, tm_projects)
            injected.append(f"projects/{project_name}/{name}")

    # 更新 MEMORY.md
    _update_memory_index(tm_shared)
    _update_memory_index(tm_projects)

    return f"知识拉取完成: {len(injected)} 篇文档\n" + "\n".join(f"  - {p}" for p in injected)


def _inject_knowledge_doc(knowledge_dir: Path, doc, target_dir: Path) -> Path:
    """将知识文档注入目标目录（加 kn- 前缀，已有则不重复加）。"""
    src = knowledge_dir / doc.path
    name = doc.path.name
    target_name = name if name.startswith("kn-") else f"kn-{name}"
    target = target_dir / target_name

    content = src.read_text()
    target.write_text(content)
    return target


def _update_memory_index(target_dir: Path) -> None:
    """更新目标目录的 MEMORY.md 索引。

    在现有索引基础上追加知识文档条目。
    """
    index_file = target_dir / "MEMORY.md"
    existing = ""
    if index_file.exists():
        existing = index_file.read_text()

    # 扫描目录下所有 .md 文件（含知识文档）
    entries: list[str] = []
    for md_file in sorted(target_dir.rglob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        if ".git" in md_file.parent.parts:
            continue
        try:
            content = md_file.read_text()
            fm = _parse_frontmatter(content)
            name = fm.get("name", "") or fm.get("title", "")
            desc = fm.get("description", "") or name
            rel = str(md_file.relative_to(target_dir))
            if len(desc) > 150:
                desc = desc[:147] + "..."
            entries.append(f"- [{rel}]({rel}) — {desc}")
        except OSError:
            continue

    new_index = "# 团队记忆索引\n\n" + "\n".join(entries) + "\n"
    index_file.write_text(new_index)


# ─── 辅助函数 ────────────────────────────────────────────────────────────

def _git_pull(tm_dir: Path) -> None:
    """在执行目录执行 git pull。"""
    import subprocess
    import os
    try:
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(tm_dir),
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except Exception:
        pass


def _git_commit(tm_dir: Path, message: str, kpath: str = "knowledge/") -> None:
    """git add + commit（不 push）。

    Args:
        tm_dir: 团队记忆仓库目录
        message: commit 消息
        kpath: 知识文档目录的相对路径（git add 目标）
    """
    import subprocess
    import os
    try:
        subprocess.run(
            ["git", "add", kpath],
            cwd=str(tm_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(tm_dir),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except Exception:
        pass


def _scan_staging_with_metadata(staging_dir: Path) -> list[dict]:
    """扫描 _staging/ 目录，返回含 frontmatter 元数据的文件列表。"""
    entries = scan_manifest(staging_dir)
    result = []
    for entry in entries:
        filepath = staging_dir / entry.path
        result.append({
            "path": str(entry.path),
            "name": entry.name,
            "description": entry.description,
            "type": entry.type,
            "scope": entry.scope,
        })
    return result


def _load_tag_dict(tm_dir: Path, config: TeamMemoryConfig) -> dict:
    """加载合并后的标签字典。

    优先级：框架默认 → 团队仓库 .tag-dict.yaml → 项目 knowledge 配置
    """
    from ..config.annto import parse_simple_yaml

    merged: dict = {}

    # 1. 框架默认
    try:
        from importlib.resources import files
        default_path = files("team_memory.knowledge.tags").joinpath("default.yaml")
        if default_path.is_file():
            default_data = parse_simple_yaml(Path(default_path))
            if isinstance(default_data, dict):
                merged.update(default_data)
    except Exception:
        pass

    # 2. 团队仓库级 .tag-dict.yaml
    kpath = _get_knowledge_config(config).get("path", "knowledge/")
    team_tag_dict = tm_dir / kpath / ".tag-dict.yaml"
    if team_tag_dict.is_file():
        try:
            team_data = parse_simple_yaml(team_tag_dict)
            if isinstance(team_data, dict):
                merged.update(team_data)
        except Exception:
            pass

    # 3. 项目级（从配置中获取）
    knowledge_config = _get_knowledge_config(config)
    project_tags = knowledge_config.get("tags", {})
    if project_tags and isinstance(project_tags, dict):
        merged.update(project_tags)

    return merged


def _get_knowledge_config(config: TeamMemoryConfig) -> dict:
    """从配置中提取 knowledge 段。

    将 KnowledgeConfig dataclass 转为普通 dict，
    便于下游代码统一使用 .get() 访问。
    """
    if config.annto and hasattr(config.annto, 'knowledge') and config.annto.knowledge is not None:
        kc = config.annto.knowledge
        return {
            "extractors": kc.extractors or {},
            "load": kc.load or {},
            "tags": kc.tags or {},
            "path": kc.path or "knowledge/",
        }
    return {}


def _get_extractor_tags(name: str, config: TeamMemoryConfig) -> list[str]:
    """获取提取器的默认标签。"""
    knowledge_config = _get_knowledge_config(config)
    extractors_config = knowledge_config.get("extractors", {})
    return extractors_config.get("default_tags", [])


def _extract_ai_content(api_result: dict) -> str | None:
    """从 Anthropic API 响应中提取文本内容。"""
    if api_result.get("error"):
        return None
    try:
        for block in api_result.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
    except Exception:
        pass
    return None


def _extract_title_from_content(content: str) -> str | None:
    """从 Markdown 内容中提取标题。"""
    import re
    for line in content.split("\n"):
        m = re.match(r'^#\s+(.+)', line)
        if m:
            return m.group(1).strip()
    return None


def _preview_title(memories: list[dict]) -> str:
    """从记忆中预览可能的标题。"""
    names = [m.get("name", "") for m in memories[:3]]
    return " / ".join(n for n in names if n)


def _format_dry_run_output(shared_docs: list, regular_docs: list) -> str:
    """格式化 dry-run 输出。"""
    lines = ["[DRY RUN] 将注入以下知识文档：", ""]
    if shared_docs:
        lines.append("## shared/（始终全量）")
        for doc in shared_docs:
            tag_str = " ".join(f"#{t}" for t in doc.tags)
            lines.append(f"  - {doc.title} {tag_str}")
        lines.append("")
    if regular_docs:
        lines.append("## 按过滤条件匹配")
        for doc in regular_docs:
            target = "shared/" if "Public" in doc.tags else "projects/"
            tag_str = " ".join(f"#{t}" for t in doc.tags)
            lines.append(f"  - [{target}] {doc.title} {tag_str}")
        lines.append("")
    return "\n".join(lines)


def run_knowledge_list(knowledge_dir: Path, stale: bool = False) -> str:
    """列出知识模块。"""
    docs = scan_knowledge_docs(knowledge_dir)
    if not docs:
        return "knowledge/ 中暂无知识文档。"

    lines = [f"共 {len(docs)} 篇知识文档:", ""]
    for doc in docs:
        tag_str = " ".join(f"#{t}" for t in doc.tags)
        lines.append(f"  [{doc.domain}] {doc.title}")
        lines.append(f"    doc_id: {doc.doc_id} | {tag_str}")
        lines.append(f"    源记忆: {doc.source_count} 条 | 生成: {doc.generated_at}")
        lines.append("")
    return "\n".join(lines)


def run_knowledge_show(knowledge_dir: Path, doc_id: str) -> str:
    """显示指定知识模块的完整内容。"""
    docs = scan_knowledge_docs(knowledge_dir)
    for doc in docs:
        if doc.doc_id == doc_id:
            filepath = knowledge_dir / doc.path
            try:
                return filepath.read_text()
            except OSError:
                return f"无法读取文档: {doc.path}"
    return f"未找到 doc_id={doc_id} 的文档。"


def run_knowledge_status(knowledge_dir: Path) -> str:
    """显示知识模块统计。"""
    docs = scan_knowledge_docs(knowledge_dir)
    if not docs:
        return "knowledge/ 中暂无知识文档。"

    # 按 domain 统计
    domain_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for doc in docs:
        domain_counts[doc.domain] = domain_counts.get(doc.domain, 0) + 1
        type_counts[doc.doc_type] = type_counts.get(doc.doc_type, 0) + 1

    lines = [
        f"知识模块统计:",
        f"  总文档数: {len(docs)}",
        "",
        "按领域:",
    ]
    for domain, count in sorted(domain_counts.items()):
        lines.append(f"  {domain}: {count}")
    lines.append("")
    lines.append("按类型:")
    for dtype, count in sorted(type_counts.items()):
        lines.append(f"  {dtype}: {count}")

    return "\n".join(lines)


def run_knowledge_clean(knowledge_dir: Path, stale_only: bool = False) -> str:
    """清理知识模块。"""
    # V1 简单实现：仅列出可清理项
    docs = scan_knowledge_docs(knowledge_dir)
    if not docs:
        return "无知识文档。"

    if stale_only:
        # 查找 90 天未更新的文档
        cutoff = time.time() - 90 * 86400
        stale = [d for d in docs if _doc_timestamp(d) < cutoff]
        if not stale:
            return "无过期知识文档（90天阈值）。"
        lines = [f"{len(stale)} 篇过期文档（90天未更新）:", ""]
        for doc in stale:
            lines.append(f"  - {doc.title} (最后更新: {doc.generated_at})")
        return "\n".join(lines)

    return f"共 {len(docs)} 篇知识文档。使用 --stale-only 查找过期文档。"


def _doc_timestamp(doc) -> float:
    """获取文档时间戳（Unix）。"""
    try:
        import time as _time
        t = _time.strptime(doc.generated_at[:19], "%Y-%m-%dT%H:%M:%S")
        return _time.mktime(t)
    except Exception:
        return 0


# ─── knowledge review ────────────────────────────────────────────────────

def run_knowledge_review_list(tm_dir: Path, kpath: str = "knowledge/") -> str:
    """列出未发布的知识 commit。

    Args:
        tm_dir: 团队记忆仓库目录
        kpath: 知识文档目录的相对路径

    Returns:
        格式化的 commit 列表
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", "origin/HEAD..HEAD", "--oneline", "--", kpath],
            cwd=str(tm_dir),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return "无法获取 commit 列表。"
        if not result.stdout.strip():
            return "所有知识已发布。"
        lines = ["未发布的知识 commit:", ""]
        for commit_line in result.stdout.strip().split("\n"):
            lines.append(f"  {commit_line}")
        return "\n".join(lines)
    except Exception:
        return "无法获取 commit 列表。"


def run_knowledge_review_show(tm_dir: Path, commit_hash: str, kpath: str = "knowledge/",
                               full: bool = False) -> str:
    """显示指定 commit 的详情。

    Args:
        tm_dir: 团队记忆仓库目录
        commit_hash: commit hash
        kpath: 知识文档目录的相对路径
        full: 是否显示完整 diff

    Returns:
        commit 详情文本
    """
    import subprocess
    try:
        cmd = ["git", "show", "--stat", commit_hash, "--", kpath]
        if full:
            cmd = ["git", "show", commit_hash, "--", kpath]
        result = subprocess.run(
            cmd, cwd=str(tm_dir), capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return f"未找到 commit: {commit_hash}"
        return result.stdout.strip()
    except Exception:
        return f"无法查看 commit: {commit_hash}"


def run_knowledge_review_approve(tm_dir: Path) -> str:
    """发布所有待审 commit（push）。

    Args:
        tm_dir: 团队记忆仓库目录

    Returns:
        push 结果文本
    """
    import subprocess
    import os
    try:
        # 获取当前分支名
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tm_dir), capture_output=True, text=True, timeout=10,
        )
        branch = branch_result.stdout.strip()

        push_result = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            cwd=str(tm_dir), capture_output=True, text=True, timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if push_result.returncode == 0:
            return "知识已发布。"
        stderr = push_result.stderr.strip()
        if "rejected" in stderr or "fetch first" in stderr:
            return "推送被拒绝。请先执行 team-memory pull 同步远端变更后重试。"
        return f"推送失败:\n{stderr}"
    except Exception as e:
        return f"推送异常: {e}"


def run_knowledge_review_reject(tm_dir: Path, commit_hash: str,
                                 message: str | None = None) -> str:
    """撤销指定 commit（git revert）。

    Args:
        tm_dir: 团队记忆仓库目录
        commit_hash: 要撤销的 commit hash
        message: revert 原因（可选）

    Returns:
        revert 结果文本
    """
    import subprocess
    import os
    try:
        if message:
            # 自定义消息：先用 --no-commit 暂存，再单独 commit
            revert_cmd = ["git", "revert", commit_hash, "--no-commit"]
            subprocess.run(
                revert_cmd, cwd=str(tm_dir), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            commit_cmd = ["git", "commit", "-m", message]
            result = subprocess.run(
                commit_cmd, cwd=str(tm_dir), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        else:
            cmd = ["git", "revert", commit_hash, "--no-edit"]
            result = subprocess.run(
                cmd, cwd=str(tm_dir), capture_output=True, text=True, timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        if result.returncode == 0:
            lines = [f"commit {commit_hash} 已撤销。"]
            lines.append("运行 team-memory knowledge review approve 发布回退。")
            return "\n".join(lines)
        return f"撤销失败:\n{result.stderr.strip()}"
    except Exception as e:
        return f"撤销异常: {e}"
