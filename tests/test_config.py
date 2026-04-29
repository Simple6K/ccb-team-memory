"""Tests for configuration management."""

import json
import tempfile
from pathlib import Path

import pytest
from team_memory.config import (
    TeamMemoryConfig,
    ExtractConfig,
    LoadConfig,
    ScanConfig,
    load_team_memory_config,
    save_team_memory_config,
    has_team_memory_config,
    find_project_root,
    get_project_name,
    get_team_memory_dir,
)


class TestTeamMemoryConfig:
    def test_defaults(self):
        c = TeamMemoryConfig()
        assert c.repo == ""
        assert c.branch == "main"
        assert c.enabled is True
        assert c.extract.mode == "instruction"
        assert c.extract.scope == "all"
        assert c.extract.auto_push is True
        assert c.load.auto_load is True
        assert c.load.max_files == 10
        assert c.scan.enabled is True

    def test_from_dict_minimal(self):
        c = TeamMemoryConfig.from_dict({"repo": "git@github.com:org/team-mem.git"})
        assert c.repo == "git@github.com:org/team-mem.git"
        assert c.branch == "main"

    def test_from_dict_full(self):
        d = {
            "repo": "git@github.com:org/tm.git",
            "branch": "develop",
            "enabled": False,
            "extract": {"mode": "auto", "scope": "project", "autoPush": False},
            "load": {"autoLoad": False, "maxFiles": 5},
            "scan": {"enabled": False},
        }
        c = TeamMemoryConfig.from_dict(d)
        assert c.repo == "git@github.com:org/tm.git"
        assert c.branch == "develop"
        assert c.enabled is False
        assert c.extract.mode == "auto"
        assert c.extract.scope == "project"
        assert c.extract.auto_push is False
        assert c.load.auto_load is False
        assert c.load.max_files == 5
        assert c.scan.enabled is False

    def test_to_dict_roundtrip(self):
        c = TeamMemoryConfig(repo="git@github.com:org/tm.git", branch="develop")
        d = c.to_dict()
        c2 = TeamMemoryConfig.from_dict(d)
        assert c2.repo == c.repo
        assert c2.branch == c.branch
        assert c2.extract.mode == c.extract.mode
        assert c2.extract.scope == c.extract.scope

    def test_to_dict_contains_keys(self):
        d = TeamMemoryConfig(repo="test").to_dict()
        assert "repo" in d
        assert "branch" in d
        assert "extract" in d
        assert "load" in d
        assert "scan" in d
        assert d["extract"]["mode"] == "instruction"


class TestSettingsJson:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_dir = root / ".claude"
            settings_dir.mkdir(parents=True)

            config = TeamMemoryConfig(repo="git@test:org/tm.git")
            save_team_memory_config(config, root)

            loaded = load_team_memory_config(root)
            assert loaded is not None
            assert loaded.repo == config.repo

    def test_no_config_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = load_team_memory_config(root)
            assert result is None

    def test_empty_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_dir = root / ".claude"
            settings_dir.mkdir(parents=True)
            settings = {"teamMemory": {"repo": ""}}
            (settings_dir / "settings.json").write_text(json.dumps(settings))
            result = load_team_memory_config(root)
            assert result is None

    def test_has_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assert has_team_memory_config(root) is False
            config = TeamMemoryConfig(repo="git@test:org/tm.git")
            save_team_memory_config(config, root)
            assert has_team_memory_config(root) is True

    def test_preserves_existing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_dir = root / ".claude"
            settings_dir.mkdir(parents=True)
            original = {"permissions": {"allow": ["Read(*)"]}, "env": {"FOO": "bar"}}
            (settings_dir / "settings.json").write_text(json.dumps(original))

            config = TeamMemoryConfig(repo="git@test:org/tm.git")
            save_team_memory_config(config, root)

            data = json.loads((settings_dir / "settings.json").read_text())
            assert "permissions" in data
            assert "env" in data
            assert "teamMemory" in data
            assert data["permissions"] == original["permissions"]


class TestGetTeamMemoryDir:
    def test_returns_claude_team_memory(self):
        root = Path("/fake/project")
        result = get_team_memory_dir(root)
        assert result == root / ".claude" / "team-memory"
