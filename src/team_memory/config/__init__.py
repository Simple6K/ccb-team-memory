"""配置层 — 数据模型、YAML 解析、项目发现、身份校验。

重导出常用符号，简化外部 import：
    from team_memory.config import TeamMemoryConfig, find_project_root, load_team_memory_config
"""

from .settings import (
    ExtractConfig,
    LoadConfig,
    ScanConfig,
    TeamMemoryConfig,
    ensure_gitignore,
    get_settings_path,
    get_team_memory_dir,
    has_team_memory_config,
    load_settings_json,
    load_team_memory_config,
    save_settings_json,
    save_team_memory_config,
)
from .annto import (
    AnntoMemoryConfig,
    MemorySourceConfig,
    ProjectIdentity,
    find_annto_yaml,
    find_project_root,
    generate_annto_yaml,
    get_git_remote_url,
    get_project_name,
    load_annto_yaml,
    parse_simple_yaml,
    verify_project_identity,
)

__all__ = [
    "ExtractConfig",
    "LoadConfig",
    "ScanConfig",
    "TeamMemoryConfig",
    "AnntoMemoryConfig",
    "MemorySourceConfig",
    "ProjectIdentity",
    "ensure_gitignore",
    "find_annto_yaml",
    "find_project_root",
    "generate_annto_yaml",
    "get_git_remote_url",
    "get_project_name",
    "get_settings_path",
    "get_team_memory_dir",
    "has_team_memory_config",
    "load_annto_yaml",
    "load_settings_json",
    "load_team_memory_config",
    "parse_simple_yaml",
    "save_settings_json",
    "save_team_memory_config",
    "verify_project_identity",
]
