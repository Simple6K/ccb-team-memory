"""提取状态管理：游标追踪、互斥检测、持久化。

对应 claude-code `initExtractMemories()` 闭包模式（extractMemories.ts:296）：
- ExtractionState → lastMemoryMessageUuid + 统计字段
- should_run() → 门控检查链
- detect_writes_since_last() → hasMemoryWritesSince()
- mark_done() → 游标推进（lastMemoryMessageUuid = lastMessage.uuid）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_FILENAME = ".extract-state.json"


@dataclass
class ExtractionState:
    """对应 claude-code 闭包中的 mutable state。

    last_extraction_at 作为游标：提取只考虑该时间点之后的对话。
    在 ccb-team-memory 中没有消息 UUID 概念，用 ISO 8601 时间戳替代。
    """
    last_extraction_at: str | None = None   # ISO 8601，对应 lastMemoryMessageUuid
    last_push_at: str | None = None          # ISO 8601，上次推送时间
    total_extractions: int = 0               # 累计提取次数
    last_files_written: list[str] = field(default_factory=list)
    team_count: int = 0
    project_count: int = 0
    # 诊断字段：追踪每次 extract run 调用
    last_invocation_at: str | None = None    # ISO 8601，最近一次 extract run 被调用
    last_invocation_result: str | None = None  # "extracted N" | "skipped: 原因" | "error: 原因"


class ExtractionManager:
    """管理提取生命周期。

    对应 claude-code initExtractMemories() 返回的闭包功能集：
    - 游标追踪（lastMemoryMessageUuid）
    - 互斥检测（hasMemoryWritesSince）
    - 并发控制（inProgress）
    - 合并暂存（pendingContext）
    """

    def __init__(self, tm_dir: Path) -> None:
        self._tm_dir = tm_dir
        self._state_path = tm_dir / STATE_FILENAME
        self._state: ExtractionState = self._load_state()
        self._in_progress = False         # 对应 inProgress
        self._pending: dict | None = None  # 对应 pendingContext

    # ── 状态持久化 ──────────────────────────────────────────────

    def _load_state(self) -> ExtractionState:
        """从 .extract-state.json 恢复状态。"""
        if not self._state_path.exists():
            return ExtractionState()
        try:
            raw = json.loads(self._state_path.read_text())
            return ExtractionState(
                last_extraction_at=raw.get("last_extraction_at"),
                last_push_at=raw.get("last_push_at"),
                total_extractions=raw.get("total_extractions", 0),
                last_files_written=raw.get("last_files_written", []),
                team_count=raw.get("team_count", 0),
                project_count=raw.get("project_count", 0),
                last_invocation_at=raw.get("last_invocation_at"),
                last_invocation_result=raw.get("last_invocation_result"),
            )
        except (json.JSONDecodeError, KeyError):
            return ExtractionState()

    def save_state(self) -> None:
        """持久化状态到 .extract-state.json。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({
            "last_extraction_at": self._state.last_extraction_at,
            "last_push_at": self._state.last_push_at,
            "total_extractions": self._state.total_extractions,
            "last_files_written": self._state.last_files_written,
            "team_count": self._state.team_count,
            "project_count": self._state.project_count,
            "last_invocation_at": self._state.last_invocation_at,
            "last_invocation_result": self._state.last_invocation_result,
        }, indent=2, ensure_ascii=False) + "\n")

    # ── 门控检查 ────────────────────────────────────────────────

    def should_run(self, mode: str) -> bool:
        """门控检查链（对应 extractMemories.ts 的条件判断）。

        当前实现：mode == "auto" 且未在进行中 且 目录存在。
        manual/instruction 模式不受门控限制（用户显式触发）。
        """
        if mode == "auto":
            if self._in_progress:
                return False
            if not self._tm_dir.is_dir():
                return False
        return True

    @property
    def in_progress(self) -> bool:
        return self._in_progress

    # ── 冷却门控 ──────────────────────────────────────────────────

    def should_extract(self, cooldown_seconds: int = 60) -> bool:
        """检查是否应该执行提取。

        对应 ccb-dev extractMemories.ts 的门控检查链：
        - 不在进行中
        - 目录存在
        - 距上次提取 ≥ cooldown_seconds
        """
        if self._in_progress:
            return False
        if not self._tm_dir.is_dir():
            return False
        if self._state.last_extraction_at is None:
            return True

        try:
            last = datetime.fromisoformat(self._state.last_extraction_at)
            now = datetime.now(timezone.utc)
            # 处理无时区信息的时间戳
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds()
            return elapsed >= cooldown_seconds
        except (ValueError, OSError):
            return True

    # ── 互斥检测 ────────────────────────────────────────────────

    def detect_writes_since_last(self) -> list[Path]:
        """检查 team-memory 目录中是否有比 last_extraction_at 更新的 .md 文件。

        对应 claude-code hasMemoryWritesSince()（extractMemories.ts:121）：
        当主模型已自行写入记忆时，跳过本次提取，推进游标。
        在 ccb-team-memory 中没有 assistant message 概念，
        用文件 mtime 与上次提取时间对比替代。

        Returns:
            自上次提取以来新创建/修改的 .md 文件列表（排除 MEMORY.md）
        """
        if self._state.last_extraction_at is None:
            return []

        try:
            cursor_time = time.mktime(
                time.strptime(
                    self._state.last_extraction_at[:19],
                    "%Y-%m-%dT%H:%M:%S",
                )
            )
        except (ValueError, IndexError):
            return []

        new_files: list[Path] = []
        for md_file in sorted(self._tm_dir.rglob("*.md")):
            if ".git" in md_file.parts:
                continue
            if "_staging" in md_file.parts:
                continue
            if md_file.name == "MEMORY.md":
                continue
            try:
                if md_file.stat().st_mtime > cursor_time:
                    new_files.append(md_file)
            except OSError:
                continue
        return new_files

    # ── 游标管理 ────────────────────────────────────────────────

    def mark_start(self) -> None:
        """标记提取开始（对应设置 inProgress = true）。"""
        self._in_progress = True

    def mark_done(self, files: list[str]) -> None:
        """标记提取完成，推进游标。

        对应 claude-code:
            lastMemoryMessageUuid = lastMessage.uuid
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._state.last_extraction_at = now
        self._state.total_extractions += 1
        self._state.last_files_written = files
        self._state.last_invocation_at = now
        self._state.last_invocation_result = f"extracted {len(files)}"
        self._in_progress = False
        self.save_state()

    def mark_skipped(self, reason: str = "") -> None:
        """标记跳过（主模型已写入等原因）。

        对应 claude-code hasMemoryWritesSince() 返回 true 时的处理：
            只推进游标，不计数。
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._state.last_extraction_at = now
        self._state.last_invocation_at = now
        self._state.last_invocation_result = f"skipped: {reason}" if reason else "skipped"
        self._in_progress = False
        self.save_state()

    def record_invocation(self, result: str) -> None:
        """记录一次 extract run 调用（不修改 last_extraction_at，不影响冷却）。

        用于诊断 Stop hook 是否触发。冷却跳过等场景调用此方法。
        """
        self._state.last_invocation_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        self._state.last_invocation_result = result
        self.save_state()

    def mark_pushed(self) -> None:
        """标记推送完成，更新 last_push_at。"""
        self._state.last_push_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        self.save_state()

    def should_push_by_time(self, minutes: int) -> bool:
        """检查距上次推送是否已超过指定分钟数。

        last_push_at 为 None（从未推送）时返回 True。
        """
        if self._state.last_push_at is None:
            return True
        try:
            last = datetime.fromisoformat(self._state.last_push_at)
            now = datetime.now(timezone.utc)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (now - last).total_seconds()
            return elapsed >= minutes * 60
        except (ValueError, OSError):
            return True

    def should_push_by_count(self, threshold: int) -> bool:
        """检查 _staging/ 中待审核文件数是否达到阈值。"""
        staging_dir = self._tm_dir / "_staging"
        if not staging_dir.is_dir():
            return False
        count = len([
            f for f in staging_dir.iterdir()
            if f.suffix == ".md" and f.name != "MEMORY.md"
        ])
        return count >= threshold

    # ── 合并暂存 ────────────────────────────────────────────────

    def stash_pending(self, context: dict) -> None:
        """暂存待处理上下文（对应 pendingContext = {context, ...}）。"""
        self._pending = context

    def pop_pending(self) -> dict | None:
        """取出暂存上下文并清除（对应 trailing run 模式）。"""
        ctx = self._pending
        self._pending = None
        return ctx

    # ── Prompt 上下文生成 ────────────────────────────────────────

    def get_summary_for_prompt(self) -> str:
        """生成提取 prompt 中注入的状态上下文。

        对应 claude-code runExtraction() prompt 中的 newMessageCount。
        """
        state = self._state

        if state.last_extraction_at is None:
            return "（首次提取，无历史状态）"

        lines = [
            f"- 上次提取时间: {state.last_extraction_at}",
            f"- 累计提取次数: {state.total_extractions}",
            f"- 团队记忆数: {state.team_count}，项目记忆数: {state.project_count}",
        ]
        if state.last_files_written:
            files = ", ".join(state.last_files_written[:10])
            extra = (
                f" ... 还有 {len(state.last_files_written) - 10} 个"
                if len(state.last_files_written) > 10
                else ""
            )
            lines.append(f"- 上次提取写入: {files}{extra}")

        return "\n".join(lines)

    def update_counts(self, team_count: int, project_count: int) -> None:
        """更新记忆计数（在 push 后调用）。"""
        self._state.team_count = team_count
        self._state.project_count = project_count
