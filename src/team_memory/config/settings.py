"""配置数据模型 + settings.json 读写 + 路径工具。

数据类定义和 JSON 配置持久化。不含 YAML 解析 —— 见 annto.py。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


# ─── 数据模型 ─────────────────────────────────────────────────────────

@dataclass
class ExtractConfig:
    mode: str = "instruction"       # "manual" | "instruction" | "auto"
    scope: str = "all"              # "team" | "project" | "all"
    auto_push: bool = True


@dataclass
class LoadConfig:
    auto_load: bool = True
    max_files: int = 10


@dataclass
class ScanConfig:
    enabled: bool = True


@dataclass
class TeamMemoryConfig:
    repo: str = ""
    branch: str = "main"
    enabled: bool = True
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    load: LoadConfig = field(default_factory=LoadConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    # V4.1: optional parsed annto config for multi-repo support
    annto: "AnntoMemoryConfig | None" = None  # type: ignore[name-defined]

    @property
    def team_repo(self) -> str:
        """Team memory repo (from annto or legacy)."""
        if self.annto and self.annto.team.repo:
            return self.annto.team.repo
        return self.repo

    @property
    def team_branch(self) -> str:
        if self.annto and self.annto.team.repo:
            return self.annto.team.branch
        return self.branch

    @property
    def team_path(self) -> str:
        """Subdirectory within repo for team memories."""
        if self.annto and self.annto.team.path:
            return self.annto.team.path
        return "shared/"

    @property
    def project_repo(self) -> str:
        """Project memory repo (may differ from team)."""
        if self.annto and self.annto.project.repo:
            return self.annto.project.repo
        return self.repo

    @property
    def project_branch(self) -> str:
        if self.annto and self.annto.project.repo:
            return self.annto.project.branch
        return self.branch

    @property
    def project_path(self) -> str:
        """Subdirectory within repo for project memories."""
        if self.annto and self.annto.project.path:
            return self.annto.project.path
        return ""  # will be resolved to projects/<name>/

    @classmethod
    def from_dict(cls, d: dict) -> "TeamMemoryConfig":
        extract_raw = d.get("extract") or {}
        load_raw = d.get("load") or {}
        scan_raw = d.get("scan") or {}
        return cls(
            repo=d.get("repo", ""),
            branch=d.get("branch", "main"),
            enabled=d.get("enabled", True),
            extract=ExtractConfig(
                mode=extract_raw.get("mode", "instruction"),
                scope=extract_raw.get("scope", "all"),
                auto_push=extract_raw.get("autoPush", True),
            ),
            load=LoadConfig(
                auto_load=load_raw.get("autoLoad", True),
                max_files=load_raw.get("maxFiles", 10),
            ),
            scan=ScanConfig(
                enabled=scan_raw.get("enabled", True),
            ),
        )

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "branch": self.branch,
            "enabled": self.enabled,
            "extract": {
                "mode": self.extract.mode,
                "scope": self.extract.scope,
                "autoPush": self.extract.auto_push,
            },
            "load": {
                "autoLoad": self.load.auto_load,
                "maxFiles": self.load.max_files,
            },
            "scan": {
                "enabled": self.scan.enabled,
            },
        }


# ─── 路径工具 ──────────────────────────────────────────────────────────

def get_team_memory_dir(project_root: Path | None = None) -> Path:
    """Return the team memory directory: <project>/.claude/team-memory/"""
    from .annto import find_project_root as _find_project_root
    root = project_root or _find_project_root() or Path.cwd()
    return root / ".claude" / "team-memory"


def get_settings_path(project_root: Path | None = None) -> Path:
    """Return the project settings.json path."""
    from .annto import find_project_root as _find_project_root
    root = project_root or _find_project_root() or Path.cwd()
    return root / ".claude" / "settings.json"


# ─── settings.json 读写 ──────────────────────────────────────────────────

def load_settings_json(project_root: Path | None = None) -> dict:
    """Read .claude/settings.json."""
    path = get_settings_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_settings_json(data: dict, project_root: Path | None = None) -> None:
    """Write .claude/settings.json, preserving unknown keys."""
    path = get_settings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _is_self_source_dir(root: Path) -> bool:
    """Check if root is the ccb-team-memory source directory itself.

    Prevents the tool from treating its own source repo as a user project
    when CWD happens to be the source directory and a stale .claude/settings.json
    with teamMemory config exists.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        import tomllib
        data = tomllib.loads(pyproject.read_text())
        return data.get("project", {}).get("name") == "ccb-team-memory"
    except Exception:
        return False


def load_team_memory_config(project_root: Path | None = None) -> TeamMemoryConfig | None:
    """Load team memory config, trying ccb-annto-memory.yaml first (V4.1),
    falling back to settings.json teamMemory section (V4.0 compatibility).

    Returns None if no config is found.
    """
    from .annto import load_annto_yaml

    from .annto import find_project_root as _find_project_root

    root = project_root or _find_project_root() or Path.cwd()

    # Never treat our own source directory as a configured project.
    if _is_self_source_dir(root):
        return None

    # 1. Try ccb-annto-memory.yaml (V4.1 priority)
    annto = load_annto_yaml(root)
    if annto and not annto.is_empty:
        # Merge with settings.json for extract/load/scan config
        settings = load_settings_json(root)
        tm_raw = settings.get("teamMemory") if isinstance(settings.get("teamMemory"), dict) else {}
        config = TeamMemoryConfig.from_dict(tm_raw)
        config.annto = annto
        if not config.repo:
            config.repo = annto.team.repo
        return config

    # 2. Fall back to settings.json teamMemory section (V4.0)
    settings = load_settings_json(root)
    tm_raw = settings.get("teamMemory")
    if not tm_raw or not isinstance(tm_raw, dict):
        return None
    config = TeamMemoryConfig.from_dict(tm_raw)
    if not config.repo:
        return None
    return config


def save_team_memory_config(config: TeamMemoryConfig, project_root: Path | None = None) -> None:
    """Write team memory config to settings.json, merging with existing."""
    settings = load_settings_json(project_root)
    settings["teamMemory"] = config.to_dict()
    save_settings_json(settings, project_root)


def has_team_memory_config(project_root: Path | None = None) -> bool:
    """Check if team memory is configured (repo is set via YAML or settings.json)."""
    config = load_team_memory_config(project_root)
    return config is not None and bool(config.repo or config.team_repo)


def ensure_gitignore(project_root: Path | None = None) -> None:
    """Ensure .claude/team-memory/ is in .gitignore."""
    from .annto import find_project_root as _find_project_root
    root = project_root or _find_project_root() or Path.cwd()
    gi_path = root / ".gitignore"
    pattern = ".claude/team-memory/"
    if gi_path.exists():
        content = gi_path.read_text()
        if pattern not in content:
            gi_path.write_text(
                content.rstrip("\n") + f"\n{pattern}\n"
            )
    else:
        gi_path.write_text(f"{pattern}\n")
