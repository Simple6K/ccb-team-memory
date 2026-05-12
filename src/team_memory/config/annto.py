"""ccb-annto-memory.yaml 解析与生成 + 项目发现 + 身份校验。

包含简易 YAML 解析器（零外部依赖），项目根目录发现，
git remote URL 获取，以及 push 前的项目身份校验。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import TeamMemoryConfig


# ─── 数据模型 ─────────────────────────────────────────────────────────

@dataclass
class MemorySourceConfig:
    """Config for a single memory source (team or project)."""
    repo: str = ""
    branch: str = "main"
    path: str = ""  # subdirectory within the repo


@dataclass
class ProjectIdentity:
    """Project identity for verification (from ccb-annto-memory.yaml)."""
    url: str = ""    # expected git remote URL
    name: str = ""   # optional project name override


@dataclass
class KnowledgeConfig:
    """知识模块配置（V4.10 新增，从 ccb-annto-memory.yaml 的 knowledge 段解析）。"""
    repo: str = ""               # 知识文档远程仓库（空=用 team_memory.repo）
    path: str = "knowledge/"     # 远程仓库内路径
    extractors: dict = field(default_factory=dict)  # enabled, default_tags
    load: dict = field(default_factory=dict)        # auto_tags, auto_domains, doc_types, time_range, max_docs
    tags: dict = field(default_factory=dict)         # 项目级标签扩展


@dataclass
class AnntoMemoryConfig:
    """Parsed from ccb-annto-memory.yaml (V4.1)."""
    team: MemorySourceConfig = field(default_factory=MemorySourceConfig)
    project: MemorySourceConfig = field(default_factory=MemorySourceConfig)
    identity: ProjectIdentity = field(default_factory=ProjectIdentity)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    source_path: Path | None = None  # where the YAML was found

    @property
    def is_empty(self) -> bool:
        return not self.team.repo and not self.project.repo

    @property
    def uses_single_repo(self) -> bool:
        """True if team and project share the same repo."""
        return bool(self.team.repo) and self.team.repo == self.project.repo


# ─── 简易 YAML 解析 ────────────────────────────────────────────────────

_ANTO_YAML_FILENAME = "ccb-annto-memory.yaml"


def parse_simple_yaml(text: str) -> dict:
    """Minimal YAML parser for ccb-annto-memory.yaml.

    Handles comments, scalar key:value pairs, and nested mappings
    (indentation-based). Sufficient for config files — not a full
    YAML 1.2 parser.
    """
    result: dict = {}
    stack: list[tuple[int, dict]] = [(0, result)]

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if ":" not in stripped:
            continue

        # Strip inline comment (but preserve # in values as they're unlikely in git URLs)
        if "#" in stripped:
            colon_pos = stripped.index(":")
            hash_pos = stripped.index("#")
            if hash_pos > colon_pos:
                stripped = stripped[:hash_pos].rstrip()

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        # Pop stack to correct indent level
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        if value:
            # Scalar value
            stack[-1][1][key] = value
        else:
            # Nested mapping
            new_dict: dict = {}
            stack[-1][1][key] = new_dict
            stack.append((indent, new_dict))

    return result


# ─── YAML 发现与解析 ────────────────────────────────────────────────────

def find_annto_yaml(start: Path | None = None) -> Path | None:
    """Search for ccb-annto-memory.yaml in start directory (default: cwd)."""
    current = (start or Path.cwd()).resolve()
    candidate = current / _ANTO_YAML_FILENAME
    return candidate if candidate.is_file() else None


def _normalize_yaml_value(value):
    """将 parse_simple_yaml 的字符串值转为 Python 原生类型。

    YAML 行内列表 '[a, b]' → ['a', 'b']
    引号包裹 '"str"' → 'str'
    整数 '8' → 8
    """
    if isinstance(value, dict):
        return {k: _normalize_yaml_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_value(v) for v in value]
    if isinstance(value, str):
        s = value.strip()
        # 行内列表: [a, b, c]
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1]
            items = [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]
            return [_normalize_yaml_value(item) for item in items]
        # 整数
        try:
            return int(s)
        except ValueError:
            pass
        # 去掉引号包裹
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            return s[1:-1]
    return value


def load_annto_yaml(project_root: Path | None = None) -> AnntoMemoryConfig | None:
    """Find and parse ccb-annto-memory.yaml.

    Returns AnntoMemoryConfig if found, None otherwise.
    """
    yaml_path = find_annto_yaml(project_root)
    if yaml_path is None:
        return None

    try:
        raw = parse_simple_yaml(yaml_path.read_text())
    except Exception:
        return None

    team_raw = raw.get("team_memory") or {}
    project_raw = raw.get("project_memory") or {}
    identity_raw = raw.get("project") or {}
    knowledge_raw = raw.get("knowledge") or {}

    if isinstance(team_raw, str):
        team_raw = {"repo": team_raw}
    if isinstance(project_raw, str):
        project_raw = {"repo": project_raw}
    if isinstance(identity_raw, str):
        identity_raw = {"url": identity_raw}

    return AnntoMemoryConfig(
        team=MemorySourceConfig(
            repo=team_raw.get("repo", ""),
            branch=team_raw.get("branch", "main"),
            path=team_raw.get("path", "shared/"),
        ),
        project=MemorySourceConfig(
            repo=project_raw.get("repo", ""),
            branch=project_raw.get("branch", "main"),
            path=project_raw.get("path", ""),
        ),
        identity=ProjectIdentity(
            url=identity_raw.get("url", ""),
            name=identity_raw.get("name", ""),
        ),
        knowledge=KnowledgeConfig(
            repo=knowledge_raw.get("repo", "") if isinstance(knowledge_raw, dict) else "",
            path=_normalize_yaml_value(knowledge_raw.get("path", "knowledge/")) if isinstance(knowledge_raw, dict) else "knowledge/",
            extractors=_normalize_yaml_value(knowledge_raw.get("extractors", {})) if isinstance(knowledge_raw, dict) else {},
            load=_normalize_yaml_value(knowledge_raw.get("load", {})) if isinstance(knowledge_raw, dict) else {},
            tags=_normalize_yaml_value(knowledge_raw.get("tags", {})) if isinstance(knowledge_raw, dict) else {},
        ),
        source_path=yaml_path,
    )


def generate_annto_yaml(project_root: Path,
                         team_repo: str,
                         project_repo: str = "",
                         team_branch: str = "main",
                         project_branch: str = "main",
                         team_path: str = "shared/",
                         project_path: str = "") -> Path:
    """Generate a ccb-annto-memory.yaml template in the project directory.

    Auto-fills project.url from local git remote if available.
    Returns the path to the created file.
    """
    root = project_root.resolve()
    yaml_path = root / _ANTO_YAML_FILENAME

    local_url = get_git_remote_url(root) or ""

    lines = [
        "# ccb-annto-memory.yaml — Auto-discovery config for ccb-team-memory",
        f"# Generated for project: {root.name}",
        "",
        "team_memory:",
        f"  repo: {team_repo}",
        f"  branch: {team_branch}",
        f"  path: {team_path}",
        "",
        "project_memory:",
        f"  repo: {project_repo or team_repo}",
        f"  branch: {project_branch}",
        f"  path: {project_path or f'projects/{root.name}/'}",
        "",
        "# Project identity — used to verify local git remote before push",
        "project:",
        f"  url: {local_url or 'git@github.com:owner/repo.git'}",
        "",
        "# Knowledge module config — controls knowledge extraction, filtering, and review (V4.10)",
        "knowledge:",
        "  # repo: git@github.com:org/team-memories.git  # 知识文档远程仓库（不配则用 team_memory.repo）",
        '  path: "knowledge/"                         # 远程仓库内路径',
        "  extractors:",
        "    enabled: [qa, architecture, workflow, requirements]",
        "  load:",
        "    auto_tags: [研发]",
        "    auto_domains: [architecture, qa]",
        "    doc_types: [knowledge, qa_pair]",
        "    time_range:",
        "      since: \"7d\"",
        "    max_docs: 8",
        "  tags: {}",
        "",
    ]

    yaml_path.write_text("\n".join(lines))
    return yaml_path


# ─── 项目发现与身份校验 ─────────────────────────────────────────────────

def find_project_root(start: str | Path | None = None) -> Path | None:
    """Find project root via ccb-annto-memory.yaml only.

    Checks start directory (default: cwd) for ccb-annto-memory.yaml.
    Returns CWD if found, None otherwise. No git dependency.
    """
    cwd = Path(start).resolve() if start else Path.cwd()
    if find_annto_yaml(cwd) is not None:
        return cwd
    return None


def get_project_name(project_root: Path, config: TeamMemoryConfig | None = None) -> str | None:
    """Derive project name.

    Priority:
    1. YAML project.name (if set)
    2. git remote get-url origin → owner--repo
    3. directory name (fallback)
    """
    # 1. YAML name override
    if config and config.annto and config.annto.identity.name:
        return config.annto.identity.name

    # 2. Try git remote
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=project_root,
            timeout=10,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            if url:
                url = url.removesuffix(".git")
                if ":" in url:
                    parts = url.rsplit(":", 1)
                    if len(parts) == 2 and "/" in parts[1]:
                        candidate = parts[1]
                        if "/" in candidate:
                            return candidate.replace("/", "--")
                if "/" in url:
                    parts = url.rstrip("/").split("/")
                    if len(parts) >= 2:
                        candidate = "/".join(parts[-2:])
                        if "/" in candidate:
                            return candidate.replace("/", "--")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 3. Fall back to directory name
    return project_root.resolve().name


def get_git_remote_url(project_root: Path) -> str | None:
    """Get the git remote origin URL for a directory.

    Returns the URL string (with .git suffix removed) or None if not a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=project_root,
            timeout=10,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            if url:
                return url.removesuffix(".git")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def verify_project_identity(config: TeamMemoryConfig | None, project_root: Path) -> tuple[bool, str]:
    """Verify project identity: YAML project.url must match local git remote.

    Returns (allowed_to_push, message).
    Only git projects with a matching project.url in YAML may push.
    """
    if config is None:
        return False, "未配置团队记忆"

    annto = config.annto
    if annto is None:
        # Legacy settings.json — use git check
        local_url = get_git_remote_url(project_root)
        if local_url is None:
            return False, "当前目录不是 git 项目，禁止 push。请配置 ccb-annto-memory.yaml"
        return True, "使用 settings.json 配置（向后兼容），校验通过"

    identity_url = annto.identity.url.removesuffix(".git")
    if not identity_url:
        return False, "ccb-annto-memory.yaml 未配置 project.url，禁止 push"

    local_url = get_git_remote_url(project_root)
    if local_url is None:
        return False, f"当前目录不是 git 项目，禁止 push。YAML 期望项目: {identity_url}"

    if local_url != identity_url:
        return False, f"项目不匹配 — YAML 期望: {identity_url}，本地: {local_url}"

    return True, "项目身份校验通过"
