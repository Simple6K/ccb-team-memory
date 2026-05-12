"""Tests for ExtractionManager — extraction state tracking (问题 1, 2)."""

import tempfile
import json
import time
from pathlib import Path

import pytest
from team_memory.services.extraction_manager import (
    ExtractionManager,
    ExtractionState,
    STATE_FILENAME,
)


class TestExtractionState:
    def test_defaults(self):
        state = ExtractionState()
        assert state.last_extraction_at is None
        assert state.total_extractions == 0
        assert state.last_files_written == []
        assert state.team_count == 0
        assert state.project_count == 0


class TestExtractionManager:
    def test_init_creates_state_file_on_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.save_state()
            state_path = tm_dir / STATE_FILENAME
            assert state_path.exists()
            raw = json.loads(state_path.read_text())
            assert raw["total_extractions"] == 0
            assert raw["last_extraction_at"] is None

    def test_mark_done_updates_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.mark_done(["a.md", "b.md"])
            assert manager._state.total_extractions == 1
            assert manager._state.last_extraction_at is not None
            assert manager._state.last_files_written == ["a.md", "b.md"]
            # 状态已持久化
            state_path = tm_dir / STATE_FILENAME
            assert state_path.exists()
            raw = json.loads(state_path.read_text())
            assert raw["total_extractions"] == 1

    def test_load_state_restores_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            state_path = tm_dir / STATE_FILENAME
            tm_dir.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "last_extraction_at": "2026-04-29T16:00:00+08:00",
                "total_extractions": 5,
                "last_files_written": ["x.md"],
                "team_count": 3,
                "project_count": 2,
            }))
            manager = ExtractionManager(tm_dir)
            assert manager._state.total_extractions == 5
            assert manager._state.team_count == 3
            assert manager._state.project_count == 2

    def test_should_run_auto_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            (tm_dir / "shared").mkdir(parents=True)
            manager = ExtractionManager(tm_dir)
            assert manager.should_run("auto") is True
            assert manager.should_run("instruction") is True
            assert manager.should_run("manual") is True

    def test_should_run_auto_blocks_when_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            (tm_dir / "shared").mkdir(parents=True)
            manager = ExtractionManager(tm_dir)
            manager.mark_start()
            assert manager.in_progress is True
            assert manager.should_run("auto") is False

    def test_detect_writes_since_last_no_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            assert manager.detect_writes_since_last() == []

    def test_detect_writes_since_last_finds_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            # 先标记一次提取
            manager = ExtractionManager(tm_dir)
            manager.mark_done([])
            # 然后创建新文件（模拟主模型直接写入）
            time.sleep(0.1)
            shared = tm_dir / "shared"
            shared.mkdir(parents=True)
            (shared / "new_memory.md").write_text("---\ntype: user\n---\ncontent")
            new_files = manager.detect_writes_since_last()
            assert len(new_files) == 1
            assert new_files[0].name == "new_memory.md"

    def test_detect_writes_since_last_excludes_memory_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.mark_done([])
            time.sleep(0.1)
            (tm_dir / "MEMORY.md").write_text("- [a](a.md)")
            new_files = manager.detect_writes_since_last()
            assert len(new_files) == 0  # MEMORY.md is excluded

    def test_mark_skipped_advances_cursor_without_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.mark_done(["a.md"])
            assert manager._state.total_extractions == 1
            manager.mark_skipped("main model wrote")
            assert manager._state.total_extractions == 1  # 不计数
            assert manager._in_progress is False

    def test_stash_and_pop_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.stash_pending({"key": "value"})
            ctx = manager.pop_pending()
            assert ctx == {"key": "value"}
            assert manager.pop_pending() is None  # 已消费

    def test_get_summary_for_prompt_first_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            summary = manager.get_summary_for_prompt()
            assert "首次提取" in summary

    def test_get_summary_for_prompt_with_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.mark_done(["a.md", "b.md"])
            summary = manager.get_summary_for_prompt()
            assert "累计提取次数: 1" in summary
            assert "a.md" in summary
            assert "b.md" in summary

    def test_update_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tm_dir = Path(tmp)
            manager = ExtractionManager(tm_dir)
            manager.update_counts(team_count=10, project_count=5)
            assert manager._state.team_count == 10
            assert manager._state.project_count == 5
