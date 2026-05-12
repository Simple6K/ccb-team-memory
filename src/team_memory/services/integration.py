"""批量整合 _staging → 项目记忆。

由 `team-memory review integrate` 命令调用。
核心流程：pull → diff 获取远程增量 staging → 去重 → 移入 shared/projects → 更新 MEMORY.md → git commit（不 push）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..config import TeamMemoryConfig, get_project_name
from ..services.extract import _parse_frontmatter


# ─── Data types ──────────────────────────────────────────────────────────

@dataclass
class StagingFile:
    """_staging/ 中的单个待整合文件。"""
    path: Path               # staging 目录下的相对路径
    abs_path: Path           # 绝对路径
    name: str = ""           # frontmatter name
    description: str = ""    # frontmatter description
    type: str = ""           # frontmatter type
    scope: str = ""          # frontmatter scope
    contributor: str = ""    # frontmatter contributor


@dataclass
class IntegrateItem:
    """单条整合结果。"""
    staging_file: StagingFile
    action: str                      # "new" | "update" | "skip"
    target_path: str = ""            # 目标相对路径
    reason: str = ""                 # 跳过原因等


@dataclass
class IntegrateResult:
    """整合结果汇总。"""
    items: list[IntegrateItem] = field(default_factory=list)
    pull_head_before: str = ""       # pull 前的 origin/main HEAD
    pull_head_after: str = ""        # pull 后的 HEAD
    staging_files_total: int = 0     # staging 总文件数
    remote_increments: int = 0       # 远程增量 staging 文件数

    @property
    def new_count(self) -> int:
        return sum(1 for i in self.items if i.action == "new")

    @property
    def update_count(self) -> int:
        return sum(1 for i in self.items if i.action == "update")

    @property
    def skip_count(self) -> int:
        return sum(1 for i in self.items if i.action == "skip")

    @property
    def total_actions(self) -> int:
        return self.new_count + self.update_count

    @property
    def has_work(self) -> bool:
        return self.total_actions > 0

    def contributor_stats(self) -> dict[str, dict[str, int]]:
        """按提交人统计: {name: {new: N, update: N, skip: N}}"""
        stats: dict[str, dict[str, int]] = defaultdict(lambda: {"new": 0, "update": 0, "skip": 0})
        for item in self.items:
            c = item.staging_file.contributor or "unknown"
            stats[c][item.action] += 1
        return dict(stats)

    def scope_stats(self) -> dict[str, int]:
        """按 scope 统计涉及的文件数。"""
        scopes: dict[str, int] = defaultdict(int)
        for item in self.items:
            if item.action == "skip":
                continue
            scope = item.staging_file.scope or "team"
            scopes[scope] += 1
        return dict(scopes)


# ─── Git helpers ─────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    """Run a git command, return stripped stdout. On failure return "". """
    import subprocess
    try:
        p = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def _git_pull(memory_dir: Path) -> tuple[str, str, bool]:
    """Pull from origin/main. Returns (pre-pull HEAD, post-pull HEAD, success)."""
    pre = _git(memory_dir, "rev-parse", "HEAD")
    _git(memory_dir, "pull", "--ff-only", "origin", "main")
    post = _git(memory_dir, "rev-parse", "HEAD")
    return pre, post, bool(pre and post)


def _get_remote_incremental_staging(memory_dir: Path, pre_head: str, post_head: str) -> list[str]:
    """Returns list of _staging/*.md files added/changed in remote since pre_head.

    Uses git diff between pre-pull remote HEAD and post-pull HEAD.
    """
    if not pre_head or not post_head or pre_head == post_head:
        return []
    diff = _git(memory_dir, "diff", "--name-only", "--diff-filter=ACMR", pre_head, post_head, "--", "_staging/")
    if not diff:
        return []
    return [f for f in diff.split("\n") if f.endswith(".md")]


# ─── Staging scan ────────────────────────────────────────────────────────

def scan_staging_files(staging_dir: Path, file_paths: list[str] | None = None) -> list[StagingFile]:
    """Parse frontmatter from staging .md files.

    Args:
        staging_dir: _staging/ absolute path
        file_paths: optional list of relative paths to scan. If None, scan all.

    Returns:
        List of StagingFile sorted by contributor then name.
    """
    result: list[StagingFile] = []
    if not staging_dir.is_dir():
        return result

    target = set(file_paths) if file_paths else None

    for md_file in sorted(staging_dir.rglob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        rel = str(md_file.relative_to(staging_dir))
        if target is not None and rel not in target:
            continue
        try:
            content = md_file.read_text()
            fm = _parse_frontmatter(content)
        except Exception:
            continue

        result.append(StagingFile(
            path=Path(rel),
            abs_path=md_file,
            name=fm.get("name", ""),
            description=fm.get("description", ""),
            type=fm.get("type", ""),
            scope=fm.get("scope", ""),
            contributor=fm.get("contributor", ""),
        ))

    result.sort(key=lambda s: (s.contributor, s.name))
    return result


# ─── Dedup ───────────────────────────────────────────────────────────────

def _resolve_target_dir(tm_dir: Path, scope: str, project_name: str) -> Path:
    """根据 scope 决定目标目录。"""
    if scope == "project":
        return tm_dir / "projects" / project_name
    return tm_dir / "shared"


def _find_existing_by_name(target_dir: Path, name: str) -> Path | None:
    """在目标目录中查找 name 匹配的已有记忆文件。"""
    if not target_dir.is_dir():
        return None
    for md_file in target_dir.rglob("*.md"):
        if md_file.name == "MEMORY.md":
            continue
        try:
            fm = _parse_frontmatter(md_file.read_text())
            if fm.get("name") == name:
                return md_file
        except Exception:
            continue
    return None


def classify_items(
    staging_files: list[StagingFile],
    tm_dir: Path,
    project_name: str,
) -> list[IntegrateItem]:
    """对每条 staging 文件判断 action: new / update / skip。

    - name 在目标目录不存在 → new
    - name 在目标目录已存在 → update
    - name 与另一个 staging 文件相同 → skip (后出现的标记为重复)
    """
    seen_names: dict[str, str] = {}  # name → contributor (for dedup within staging)
    items: list[IntegrateItem] = []

    for sf in staging_files:
        name = sf.name
        if not name:
            items.append(IntegrateItem(
                staging_file=sf, action="skip",
                reason="缺少 name 字段",
            ))
            continue

        # 同一批 staging 中的重复
        if name in seen_names:
            items.append(IntegrateItem(
                staging_file=sf, action="skip",
                reason=f"与 {seen_names[name]} 的 staging 文件 name 重复",
            ))
            continue
        seen_names[name] = sf.contributor

        target_dir = _resolve_target_dir(tm_dir, sf.scope, project_name)
        existing = _find_existing_by_name(target_dir, name)
        if existing:
            items.append(IntegrateItem(
                staging_file=sf, action="update",
                target_path=str(existing.relative_to(tm_dir)),
                reason=f"已有: {existing.relative_to(tm_dir)}",
            ))
        else:
            stem = sf.path.stem
            target_rel = str(target_dir.relative_to(tm_dir)) + "/" + stem + ".md"
            items.append(IntegrateItem(
                staging_file=sf, action="new",
                target_path=target_rel,
            ))

    return items


# ─── Execute ─────────────────────────────────────────────────────────────

def _merge_content(existing_content: str, staging_content: str) -> str:
    """合并两个记忆文件的内容。

    策略：将 staging 内容追加到已有文件末尾，添加分隔标记。
    保留已有文件的 frontmatter。
    """
    # 剥离 staging 的 frontmatter
    body = staging_content
    if body.startswith("---"):
        idx = body.find("---", 3)
        if idx != -1:
            body = body[idx + 3:].strip()

    existing = existing_content.rstrip()
    merged = existing + "\n\n---\n\n" + body + "\n"
    return merged


def execute_integration(
    items: list[IntegrateItem],
    staging_dir: Path,
    tm_dir: Path,
    project_name: str,
    dry_run: bool = False,
) -> list[str]:
    """执行整合：移入/合并文件，删除 staging 源文件。

    Returns:
        操作描述列表（用于 commit message / 输出）
    """
    logs: list[str] = []

    for item in items:
        if item.action == "skip":
            # 删除 staging 中的重复/无效文件
            if not dry_run:
                try:
                    item.staging_file.abs_path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        sf = item.staging_file
        target_dir = _resolve_target_dir(tm_dir, sf.scope, project_name)

        if dry_run:
            logs.append(f"[DRY-RUN] [{item.action}] {sf.path} → {item.target_path}")
            continue

        target_dir.mkdir(parents=True, exist_ok=True)

        if item.action == "new":
            # 直接移入
            dst = tm_dir / item.target_path
            try:
                content = sf.abs_path.read_text()
                dst.write_text(content)
                sf.abs_path.unlink(missing_ok=True)
                logs.append(f"[new] {item.target_path}")
            except OSError as e:
                logs.append(f"[error] {item.target_path}: {e}")

        elif item.action == "update":
            # 合并内容
            existing_path = tm_dir / item.target_path
            try:
                existing = existing_path.read_text()
                staging_content = sf.abs_path.read_text()
                merged = _merge_content(existing, staging_content)
                existing_path.write_text(merged)
                sf.abs_path.unlink(missing_ok=True)
                logs.append(f"[update] {item.target_path}")
            except OSError as e:
                logs.append(f"[error] {item.target_path}: {e}")

    return logs


# ─── MEMORY.md update ────────────────────────────────────────────────────

def update_memory_indexes(tm_dir: Path, project_name: str, dry_run: bool = False) -> list[str]:
    """重建 shared/ 和 projects/<name>/ 的 MEMORY.md 索引。

    复用 consolidation.py 的 _repair_index 逻辑。
    """
    from ..services.consolidation import ConsolidationManager, ConsolidationConfig

    manager = ConsolidationManager(tm_dir, ConsolidationConfig())
    updated: list[str] = []

    for dname, dpath in [("shared", tm_dir / "shared"), (f"projects/{project_name}", tm_dir / "projects" / project_name)]:
        if not dpath.is_dir():
            continue
        if dry_run:
            updated.append(f"[DRY-RUN] 更新 {dname}/MEMORY.md")
            continue
        try:
            manager._repair_index()
            updated.append(f"更新 {dname}/MEMORY.md")
        except Exception as e:
            updated.append(f"更新 {dname}/MEMORY.md 失败: {e}")

    return updated


# ─── Commit message ──────────────────────────────────────────────────────

def generate_commit_message(result: IntegrateResult, project_name: str) -> str:
    """生成带统计和提交人信息的 commit message。"""
    date_str = time.strftime("%Y-%m-%d")
    lines = [
        f"team-memory integrate: 整合 {result.total_actions} 条待审核记忆（{date_str}）",
        "",
        f"统计: 新增 {result.new_count} | 更新 {result.update_count} | 跳过 {result.skip_count}",
    ]

    # 涉及目录
    scope_stats = result.scope_stats()
    if scope_stats:
        scope_parts = []
        for s, c in sorted(scope_stats.items()):
            label = "shared/" if s == "team" else f"projects/{project_name}/"
            scope_parts.append(f"{label} {c} 条")
        lines.append(f"涉及: {' | '.join(scope_parts)}")
    lines.append("")

    # 提交人统计
    cstats = result.contributor_stats()
    if cstats:
        lines.append("## 提交人统计")
        for name, counts in sorted(cstats.items()):
            parts = [f"{name}: {sum(counts.values())} 条"]
            detail = []
            if counts["new"]:
                detail.append(f"新增 {counts['new']}")
            if counts["update"]:
                detail.append(f"更新 {counts['update']}")
            if counts["skip"]:
                detail.append(f"跳过 {counts['skip']}")
            if detail:
                parts.append(f"({' | '.join(detail)})")
            lines.append(" ".join(parts))
        lines.append("")

    # 按 action 分组
    grouped: dict[str, list[IntegrateItem]] = {"new": [], "update": [], "skip": []}
    for item in result.items:
        grouped[item.action].append(item)

    for action, label in [("new", "新增"), ("update", "更新"), ("skip", "跳过")]:
        items = grouped[action]
        if not items:
            continue
        lines.append(f"## {label}")
        for item in items:
            sf = item.staging_file
            c = sf.contributor or "?"
            t = sf.type or "?"
            scope = sf.scope or "team"
            desc = f" — {sf.description}" if sf.description else ""
            target_info = item.target_path or "-"

            if action == "new":
                lines.append(f"- [{c}] {sf.path} [{t}→{scope}] {target_info}{desc}")
            elif action == "update":
                lines.append(f"- [{c}] {sf.path} [{target_info}] 合并补充{desc}")
            elif action == "skip":
                reason = item.reason or "未知原因"
                lines.append(f"- [{c}] {sf.path} [{reason}]{desc}")
        lines.append("")

    return "\n".join(lines)


# ─── Git commit ──────────────────────────────────────────────────────────

def commit_integration(memory_dir: Path, message: str) -> tuple[bool, str]:
    """Stage .md files and commit. Returns (success, commit_hash | error)."""
    from ..utils.git import add_md_files, commit

    add_result = add_md_files(memory_dir)
    if not add_result.success:
        return False, f"git add 失败: {add_result.stderr}"

    commit_result = commit(memory_dir, message)
    if not commit_result.success:
        return False, f"git commit 失败: {commit_result.stderr}"

    return True, commit_result.commit_hash


# ─── Main entry point ────────────────────────────────────────────────────

def run_integration(
    config: TeamMemoryConfig,
    project_root: Path,
    memory_dir: Path,
    *,
    dry_run: bool = False,
    skip_pull: bool = False,
) -> IntegrateResult:
    """执行完整的整合流程。

    Returns:
        IntegrateResult 包含所有操作明细。
    """
    result = IntegrateResult()
    project_name = get_project_name(project_root) or "unknown"
    staging_dir = memory_dir / "_staging"

    # 1. Pull
    if not skip_pull:
        pre, post, ok = _git_pull(memory_dir)
        result.pull_head_before = pre
        result.pull_head_after = post
        if not ok:
            # 即使 pull 失败也继续（可能没有 remote 或网络问题）
            pass
    else:
        pre = _git(memory_dir, "rev-parse", "HEAD")
        post = pre
        result.pull_head_before = pre
        result.pull_head_after = post

    # 2. 获取远程增量 staging 文件
    if not skip_pull and result.pull_head_before and result.pull_head_after:
        remote_staging = _get_remote_incremental_staging(
            memory_dir, result.pull_head_before, result.pull_head_after,
        )
    else:
        # --no-pull 或不具备 diff 条件 → 处理所有 staging
        remote_staging = None

    # 3. 扫描 staging
    all_staging = scan_staging_files(staging_dir)
    result.staging_files_total = len(all_staging)

    if remote_staging is not None:
        # 只处理远程增量
        remote_set = set(remote_staging)
        target_staging = [s for s in all_staging if str(s.path) in remote_set]
        result.remote_increments = len(target_staging)
    else:
        target_staging = all_staging
        result.remote_increments = len(all_staging)

    if not target_staging:
        return result

    # 4. 去重分类
    items = classify_items(target_staging, memory_dir, project_name)
    result.items = items

    if dry_run:
        return result

    # 5. 执行移入/合并
    execute_integration(items, staging_dir, memory_dir, project_name, dry_run=False)

    # 6. 更新 MEMORY.md
    update_memory_indexes(memory_dir, project_name, dry_run=False)

    return result
