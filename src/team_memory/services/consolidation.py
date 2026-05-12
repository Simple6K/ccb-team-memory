"""记忆整合：检测可合并/过时记忆，定期清理。

对应 claude-code `initAutoDream()` 闭包模式（autoDream.ts:123）：
- 门控链：时间 → 文件数 → 锁
- 扫描节流（SESSION_SCAN_INTERVAL_MS）
- 整合候选检测（merge/archive/repair_index）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from .verify import _parse_frontmatter, verify_memories_dir

LOCK_FILENAME = ".consolidation-lock"
STATE_FILENAME = ".consolidation-state.json"


@dataclass
class ConsolidationConfig:
    """整合配置。对应 autoDream 的门控参数。"""
    min_hours: int = 24           # 对应 minHours
    min_files: int = 10           # 对应 minSessions（无 session 概念，用文件数替代）
    scan_interval_s: int = 600    # 对应 SESSION_SCAN_INTERVAL_MS


@dataclass
class ConsolidationCandidate:
    """整合候选项。"""
    action: str                   # "merge" | "archive" | "repair_index"
    files: list[str]              # 涉及的文件（相对路径）
    reason: str                   # 原因说明


@dataclass
class ConsolidationReport:
    """整合报告。对应 DreamTask 的进度状态。"""
    candidates: list[ConsolidationCandidate] = field(default_factory=list)
    total_files_scanned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        return len(self.candidates) > 0


class ConsolidationManager:
    """管理记忆整合生命周期。

    对应 claude-code initAutoDream() 返回的闭包功能集：
    - 时间门控 + 文件数门控 + 锁管理
    - 扫描节流
    - 整合候选生成
    """

    def __init__(
        self,
        tm_dir: Path,
        config: ConsolidationConfig | None = None,
    ) -> None:
        self._tm_dir = tm_dir
        self._config = config or ConsolidationConfig()
        self._lock_path = tm_dir / LOCK_FILENAME
        self._state_path = tm_dir / STATE_FILENAME
        self._last_scan_at: float = 0.0  # 对应 lastSessionScanAt

    # ── 门控链 ──────────────────────────────────────────────────

    def should_run(self) -> bool:
        """门控检查链（对应 autoDream 门控顺序：时间 → 文件数 → 锁）。

        返回 False 表示门控未通过，本次不运行。
        """
        tm_dir = self._tm_dir
        if not tm_dir.is_dir():
            return False

        # Gate 1: 时间（对应 hoursSince >= minHours）
        last_at = self._read_last_consolidated_at()
        hours_since = (time.time() - last_at) / 3600
        if hours_since < self._config.min_hours:
            return False

        # Gate 2: 扫描节流（对应 SESSION_SCAN_INTERVAL_MS）
        since_scan = time.time() - self._last_scan_at
        if since_scan < self._config.scan_interval_s:
            return False

        # Gate 3: 文件数（对应 minSessions）
        file_count = self._count_memory_files(tm_dir)
        if file_count < self._config.min_files:
            return False

        # Gate 4: 锁（对应 tryAcquireConsolidationLock）
        if not self._acquire_lock():
            return False

        self._last_scan_at = time.time()
        return True

    # ── 扫描 ────────────────────────────────────────────────────

    def scan(self) -> ConsolidationReport:
        """扫描整合候选。

        检测项：
        1. 可合并的记忆（name 前缀相似 + 内容重叠）
        2. 过时记忆（超过 90 天未更新）
        3. MEMORY.md 不一致（孤立条目 / 未索引文件）
        """
        report = ConsolidationReport()

        try:
            verify_result = verify_memories_dir(self._tm_dir)
        except Exception as e:
            report.errors.append(f"验证失败: {e}")
            return report

        # 统计
        report.total_files_scanned = verify_result.file_count

        # 1. MEMORY.md 修复候选
        if verify_result.orphan_index_count > 0:
            report.candidates.append(ConsolidationCandidate(
                action="repair_index",
                files=[],
                reason=f"{verify_result.orphan_index_count} 个孤立索引条目"
            ))
        if verify_result.unindexed_count > 0:
            report.candidates.append(ConsolidationCandidate(
                action="repair_index",
                files=[],
                reason=f"{verify_result.unindexed_count} 个未索引的记忆文件"
            ))

        # 2. 重复 name 合并候选
        for dup_name in verify_result.duplicate_names:
            report.candidates.append(ConsolidationCandidate(
                action="merge",
                files=[],  # verify_result 中收集
                reason=f"重复的 name '{dup_name}'"
            ))

        # 3. 过时记忆检测（> 90 天未更新）
        stale_files = self._find_stale_files(days=90)
        if stale_files:
            report.candidates.append(ConsolidationCandidate(
                action="archive",
                files=stale_files,
                reason=f"{len(stale_files)} 个文件超过 90 天未更新"
            ))

        return report

    # ── 锁管理 ──────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """获取整合锁。对应 tryAcquireConsolidationLock()。

        使用 mtime 而非内容判断锁状态。
        """
        if self._lock_path.exists():
            try:
                lock_age = time.time() - self._lock_path.stat().st_mtime
                # 锁超过 1 小时视为过期
                if lock_age < 3600:
                    return False
            except OSError:
                pass
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(
            json.dumps({"pid": os.getpid(), "time": time.time()})
        )
        return True

    def _release_lock(self) -> None:
        """释放整合锁。对应 rollbackConsolidationLock()。"""
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    # ── 内部工具 ────────────────────────────────────────────────

    def _read_last_consolidated_at(self) -> float:
        """读取上次整合时间。"""
        if not self._state_path.exists():
            return 0.0
        try:
            raw = json.loads(self._state_path.read_text())
            return raw.get("last_consolidated_at", 0.0)
        except (json.JSONDecodeError, KeyError):
            return 0.0

    def _write_last_consolidated_at(self) -> None:
        """写入整合时间。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({
            "last_consolidated_at": time.time()
        }, indent=2) + "\n")

    def _count_memory_files(self, tm_dir: Path) -> int:
        """统计记忆文件数（排除 MEMORY.md 和 .git）。"""
        count = 0
        for md_file in tm_dir.rglob("*.md"):
            if ".git" in md_file.parts:
                continue
            if "_staging" in md_file.parts:
                continue
            if md_file.name == "MEMORY.md":
                continue
            count += 1
        return count

    def _find_stale_files(self, days: int = 90) -> list[str]:
        """查找超过指定天数未更新的记忆文件。"""
        cutoff = time.time() - days * 86400
        stale: list[str] = []
        for md_file in sorted(self._tm_dir.rglob("*.md")):
            if ".git" in md_file.parts:
                continue
            if "_staging" in md_file.parts:
                continue
            if md_file.name == "MEMORY.md":
                continue
            try:
                if md_file.stat().st_mtime < cutoff:
                    stale.append(str(md_file.relative_to(self._tm_dir)))
            except OSError:
                continue
        return stale

    # ── 公共 API ────────────────────────────────────────────────

    def apply(self, candidates: list[ConsolidationCandidate]) -> dict[str, int]:
        """执行整合操作。

        Returns:
            {"merged": N, "archived": N, "repaired": N, "errors": N}
        """
        results = {"merged": 0, "archived": 0, "repaired": 0, "errors": 0}
        for candidate in candidates:
            try:
                if candidate.action == "repair_index":
                    self._repair_index()
                    results["repaired"] += 1
                elif candidate.action == "archive":
                    for file_path in candidate.files:
                        self._archive_file(file_path)
                    results["archived"] += len(candidate.files)
                elif candidate.action == "merge":
                    results["merged"] += 1  # 手动合并，暂不自动执行
            except Exception:
                results["errors"] += 1
        self._release_lock()
        self._write_last_consolidated_at()
        return results

    def _repair_index(self) -> None:
        """重建 MEMORY.md 索引。扫描所有 .md 文件并重写索引。

        通过 verify_memories_dir 的结果写入对应的 MEMORY.md。
        """
        # 收集每个子目录的文件并按类型分组
        dir_files: dict[Path, dict[str, list[str]]] = {}
        for md_file in sorted(self._tm_dir.rglob("*.md")):
            if ".git" in md_file.parts:
                continue
            if "_staging" in md_file.parts:
                continue
            if md_file.name == "MEMORY.md":
                continue
            parent = md_file.parent
            if parent not in dir_files:
                dir_files[parent] = {}
            try:
                fm = _parse_frontmatter(md_file.read_text())
                mem_type = fm.get("type", "other")
            except Exception:
                mem_type = "other"
            dir_files[parent].setdefault(mem_type, []).append(md_file.name)

        # 重写每个目录的 MEMORY.md
        for parent, type_groups in dir_files.items():
            mem_md_path = parent / "MEMORY.md"
            lines = [
                f"# Team Memory — {parent.name}",
                "",
                f"Shared team knowledge applicable across all projects."
                if parent.name == "shared"
                else f"Project-specific memory for {parent.name}.",
                "",
            ]
            for mem_type in ("user", "feedback", "project", "reference"):
                files = type_groups.get(mem_type, [])
                if not files:
                    continue
                lines.append(f"## {mem_type}")
                for fname in sorted(files):
                    full_path = parent / fname
                    try:
                        fm = _parse_frontmatter(full_path.read_text())
                        desc = fm.get("description", "")
                    except Exception:
                        desc = ""
                    lines.append(f"- [{fname}]({fname}) — {desc}")
                lines.append("")
            mem_md_path.write_text("\n".join(lines) + "\n")

    def _archive_file(self, rel_path: str) -> None:
        """归档文件：移动到 _archived/ 子目录。"""
        src = self._tm_dir / rel_path
        if not src.exists():
            return
        archive_dir = src.parent / "_archived"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dst = archive_dir / src.name
        src.rename(dst)
