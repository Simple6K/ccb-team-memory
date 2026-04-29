"""Tests for memory extraction."""

import tempfile
from pathlib import Path

import pytest
from team_memory.config import TeamMemoryConfig
from team_memory.services.extract import (
    build_extract_prompt,
    scan_manifest,
    generate_auto_load_summary,
)


class TestBuildExtractPrompt:
    def test_returns_text(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="instruction")
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_memory_types(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="instruction")
        assert "user" in text
        assert "feedback" in text
        assert "project" in text
        assert "reference" in text

    def test_contains_exclusion_rules(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="instruction")
        # Chinese localization in V4.3 changed these to Chinese
        assert "不应保存的内容" in text or "What NOT to save" in text

    def test_contains_frontmatter_format(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="instruction")
        assert "---" in text
        assert "type:" in text
        assert "scope:" in text

    def test_manual_mode(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="manual")
        assert "manual" in text.lower()

    def test_auto_mode(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="auto")
        assert "auto" in text.lower()

    def test_instruction_mode(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        text = build_extract_prompt(config, mode="instruction")
        assert "instruction" in text.lower()

    def test_scope_all_shows_both_dirs(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        config.extract.scope = "all"
        text = build_extract_prompt(config, mode="instruction")
        assert "shared" in text.lower()

    def test_scope_team_only(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        config.extract.scope = "team"
        text = build_extract_prompt(config, mode="instruction")
        assert "shared" in text.lower()

    def test_scope_project_only(self):
        config = TeamMemoryConfig(repo="git@test:org/tm.git")
        config.extract.scope = "project"
        text = build_extract_prompt(config, mode="instruction")
        assert "project" in text.lower()


class TestScanManifest:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_manifest(Path(tmp))
            assert result == []

    def test_with_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "MEMORY.md").write_text("- [a.md](a.md)")
            (base / "a.md").write_text("---\ntype: user\n---\ncontent")
            (base / "b.md").write_text("---\ntype: project\n---\ncontent")

            result = scan_manifest(base)
            # MEMORY.md is excluded
            assert len(result) == 2
            paths = {e.path for e in result}
            assert "a.md" in paths
            assert "b.md" in paths

    def test_max_files_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for i in range(10):
                f = base / f"mem_{i}.md"
                f.write_text(f"---\ntype: user\n---\ncontent {i}")

            result = scan_manifest(base, max_files=5)
            assert len(result) == 5

    def test_skips_git_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            git_dir = base / ".git"
            git_dir.mkdir()
            (git_dir / "leak.md").write_text("secret")
            (base / "ok.md").write_text("---\ntype: user\n---\nok")

            result = scan_manifest(base)
            assert len(result) == 1
            assert result[0].path == "ok.md"


class TestAutoLoadSummary:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = TeamMemoryConfig(repo="git@test:org/tm.git")
            text = generate_auto_load_summary(config, project_root=Path(tmp))
            # Without actual team-memory dir, summary should be empty
            assert text == ""
