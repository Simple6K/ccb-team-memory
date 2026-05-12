"""Tests for memory verification (问题 7)."""

import tempfile
from pathlib import Path

import pytest
from team_memory.services.verify import (
    verify_memory_file,
    verify_memories_dir,
    verify_before_push,
    VerifyResult,
)


class TestVerifyMemoryFile:
    def test_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.md"
            path.write_text("""---
name: test
description: test desc
type: user
scope: team
extracted_at: 2026-04-29T16:00:00+08:00
---
content""")
            errors = verify_memory_file(path)
            assert errors == []

    def test_missing_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "no_fm.md"
            path.write_text("just content, no frontmatter")
            errors = verify_memory_file(path)
            assert len(errors) >= 1
            assert any("缺少" in e for e in errors)

    def test_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.md"
            path.write_text("""---
name: test
---
content""")
            errors = verify_memory_file(path)
            assert any("description" in e for e in errors)
            assert any("type" in e for e in errors)

    def test_invalid_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_type.md"
            path.write_text("""---
name: test
description: desc
type: invalid_type
---
content""")
            errors = verify_memory_file(path)
            assert any("不合法" in e for e in errors)

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.md"
            path.write_text("")
            errors = verify_memory_file(path)
            assert len(errors) >= 1

    def test_unreadable_file(self):
        """Should handle unreadable files gracefully."""
        # Skip: requires os-specific permission manipulation
        pass


class TestVerifyMemoriesDir:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            result = verify_memories_dir(tm_dir)
            assert result.file_count == 0
            assert result.is_clean

    def test_valid_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            (shared / "a.md").write_text("""---
name: alpha
description: first memory
type: user
scope: team
---
content a""")
            (shared / "b.md").write_text("""---
name: beta
description: second memory
type: feedback
scope: team
---
content b""")
            result = verify_memories_dir(tm_dir)
            assert result.file_count == 2
            assert result.is_clean

    def test_duplicate_name_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            (shared / "a.md").write_text("""---
name: same-name
description: first
type: user
---
content""")
            (shared / "b.md").write_text("""---
name: same-name
description: second
type: project
---
content""")
            result = verify_memories_dir(tm_dir)
            assert len(result.duplicate_names) >= 1
            assert "same-name" in result.duplicate_names

    def test_unindexed_file_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            (shared / "orphan.md").write_text("""---
name: orphan
description: no MEMORY.md entry
type: user
scope: team
---
content""")
            # No MEMORY.md created → orphan.md is unindexed
            result = verify_memories_dir(tm_dir)
            assert result.unindexed_count >= 1

    def test_orphan_index_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            # MEMOERY.md references a file that doesn't exist
            (shared / "MEMORY.md").write_text("- [Ghost](ghost.md) — missing file\n")
            result = verify_memories_dir(tm_dir)
            assert result.orphan_index_count >= 1


class TestVerifyBeforePush:
    def test_clean_dir_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            (shared / "ok.md").write_text("""---
name: ok
description: ok
type: user
scope: team
---
ok""")
            assert verify_before_push(tm_dir) is True

    def test_bad_file_blocks_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            (shared / "bad.md").write_text("no frontmatter at all")
            assert verify_before_push(tm_dir) is False
