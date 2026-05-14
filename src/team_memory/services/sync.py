"""Sync orchestration: pull and push operations for team memory.

V4.1: Supports separate team/project memory repos via ccb-annto-memory.yaml.
V4.6: Push 前置验证集成（verify_before_push）。
"""

import time
from pathlib import Path

from ..config import TeamMemoryConfig, get_team_memory_dir, get_project_name
from ..utils.git import (
    add_md_files,
    clone,
    commit,
    has_changes,
    is_git_repo,
    last_log_entry,
    list_md_files,
    pull,
    push_with_retry,
    set_remote,
    sparse_checkout_exclude,
    status,
)
from .verify import verify_before_push


def _get_project_memory_dir(project_root: Path | None = None) -> Path:
    """Get the project memory subdirectory path."""
    tm_dir = get_team_memory_dir(project_root)
    project_name = get_project_name(project_root or Path.cwd()) or "unknown"
    return tm_dir / "projects" / project_name


def _init_single_repo(repo: str, branch: str, target_dir: Path) -> tuple[bool, str]:
    """Clone or verify a single git repo into target_dir."""
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if is_git_repo(target_dir):
        _ensure_sparse_checkout(target_dir)
        r = set_remote(target_dir, "origin", repo)
        if r.success:
            return True, f"Already initialized at {target_dir}"
        return False, f"Failed to set remote: {r.stderr}"

    r = clone(repo, target_dir, branch=branch, sparse=True)
    if not r.success:
        return False, f"Clone failed: {r.stderr}"
    sparse_checkout_exclude(target_dir, ["_staging"])
    return True, f"Initialized at {target_dir}"


def _ensure_sparse_checkout(repo_dir: Path) -> None:
    """Ensure sparse-checkout is configured to exclude _staging/."""
    info_dir = repo_dir / ".git" / "info"
    sc_file = info_dir / "sparse-checkout"
    if sc_file.exists():
        return
    sparse_checkout_exclude(repo_dir, ["_staging"])


def do_init(config: TeamMemoryConfig, project_root: Path | None = None, quiet: bool = False) -> tuple[bool, str]:
    """Initialize team memory: clone repo(s) to .claude/team-memory/.

    V4.1: Supports separate team/project repos. When repos differ, the
    project repo is cloned into .claude/team-memory/projects/<name>/.

    Returns (success, message).
    """
    root = project_root or Path.cwd()
    tm_dir = get_team_memory_dir(root)
    tm_dir.parent.mkdir(parents=True, exist_ok=True)

    # Determine repos to clone
    if config.annto and not config.annto.uses_single_repo and config.annto.project.repo:
        # Multi-repo: clone team and project separately
        ok1, msg1 = _init_single_repo(config.team_repo, config.team_branch, tm_dir)
        proj_dir = _get_project_memory_dir(root)
        ok2, msg2 = _init_single_repo(config.project_repo, config.project_branch, proj_dir)
        if ok1 and ok2:
            return True, f"Team: {msg1}\n  Project: {msg2}"
        return False, f"Team: {msg1}\n  Project: {msg2}"
    else:
        # Single repo (or legacy): clone once
        return _init_single_repo(config.team_repo, config.team_branch, tm_dir)


def do_pull(config: TeamMemoryConfig, project_root: Path | None = None, quiet: bool = False) -> tuple[bool, str]:
    """Pull latest team memory from shared repo(s).

    V4.1: Pulls both team and project repos when they differ.

    Returns (success, message).
    """
    root = project_root or Path.cwd()
    tm_dir = get_team_memory_dir(root)

    # Pull team repo
    if not is_git_repo(tm_dir):
        return do_init(config, root, quiet)

    _ensure_sparse_checkout(tm_dir)
    r = pull(tm_dir, "origin", config.team_branch)
    team_msg = r.stdout.strip() if r.success else f"Pull failed: {r.stderr}"
    if not r.success:
        return False, team_msg

    # If multi-repo, also pull project repo
    if config.annto and not config.annto.uses_single_repo and config.annto.project.repo:
        proj_dir = _get_project_memory_dir(root)
        if is_git_repo(proj_dir):
            r2 = pull(proj_dir, "origin", config.project_branch)
            proj_msg = r2.stdout.strip() if r2.success else f"Project pull failed: {r2.stderr}"
            if r2.success:
                return True, f"Team: {team_msg or 'up to date'}\n  Project: {proj_msg or 'up to date'}"
            return False, f"Team: {team_msg}\n  Project: {proj_msg}"
        else:
            ok, msg = _init_single_repo(config.project_repo, config.project_branch, proj_dir)
            if ok:
                return True, f"Team: {team_msg or 'up to date'}\n  Project: {msg}"
            return False, f"Team: {team_msg}\n  Project: {msg}"

    if team_msg:
        return True, f"Pulled: {team_msg}"
    return True, "Already up to date"


def _push_repo(repo_dir: Path, branch: str) -> tuple[bool, str]:
    """Push changes in a single repo. Returns (success, message)."""
    if not is_git_repo(repo_dir):
        return True, ""  # not a repo, nothing to push

    if not has_changes(repo_dir):
        return True, ""

    md_files = list_md_files(repo_dir)
    if not md_files:
        return True, ""

    r = add_md_files(repo_dir)
    if not r.success:
        return False, f"Failed to stage files: {r.stderr}"

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    msg = f"team-memory: update at {timestamp}"
    r = commit(repo_dir, msg)
    if not r.success:
        if "nothing to commit" in r.stderr.lower():
            return True, ""
        return False, f"Commit failed: {r.stderr}"

    files = r.files_changed  # from CommitResult
    r = push_with_retry(repo_dir, "origin", branch, max_retries=3)
    if r.success:
        n = r.retries
        extra = f" (after {n} retry)" if n else ""
        return True, f"Pushed {files} file(s){extra}"
    return False, f"Push failed{' after retries' if r.retries else ''}: {r.stderr}"


def do_push(config: TeamMemoryConfig, project_root: Path | None = None,
            force_skip_scan: bool = False, quiet: bool = False) -> tuple[bool, str]:
    """Push local team memory changes to shared repo(s).

    V4.1: Pushes to both team and project repos when they differ.

    Returns (success, message).
    """
    root = project_root or Path.cwd()
    tm_dir = get_team_memory_dir(root)

    if not is_git_repo(tm_dir):
        return False, f"Team memory not initialized at {tm_dir}. Run 'team-memory init' first."

    # ── V4.6: Push 前置验证（问题 7） ──
    if not force_skip_scan:
        if not verify_before_push(tm_dir):
            return False, (
                "Push 被阻止: 记忆文件验证失败。"
                "运行 'team-memory verify' 查看详情，"
                "或使用 --force 跳过验证。"
            )

    # Push team repo
    ok, msg = _push_repo(tm_dir, config.team_branch)
    if not ok:
        return False, msg

    # If multi-repo, also push project repo
    proj_msg = ""
    if config.annto and not config.annto.uses_single_repo and config.annto.project.repo:
        proj_dir = _get_project_memory_dir(root)
        ok2, msg2 = _push_repo(proj_dir, config.project_branch)
        if not ok2:
            return False, f"Team: {msg}\n  Project: {msg2}"
        proj_msg = msg2

    parts = [m for m in [msg, proj_msg] if m]
    if parts:
        return True, "\n  ".join(parts)
    return True, "No changes to push"


def do_status(config: TeamMemoryConfig, project_root: Path | None = None) -> str:
    """Generate human-readable status output."""
    tm_dir = get_team_memory_dir(project_root)
    lines = [
        "─── Team Memory Status ───",
    ]

    # Show config source
    if config.annto and config.annto.source_path:
        lines.append(f"  Config:      ccb-annto-memory.yaml ({config.annto.source_path})")
    else:
        lines.append(f"  Config:      .claude/settings.json (legacy)")

    lines += [
        f"  Team repo:   {config.team_repo}",
        f"  Team branch: {config.team_branch}",
    ]
    if config.annto and not config.annto.uses_single_repo and config.annto.project.repo:
        lines += [
            f"  Project repo:   {config.project_repo}",
            f"  Project branch: {config.project_branch}",
        ]
    lines += [
        f"  Local dir:   {tm_dir}",
        f"  Extract mode:  {config.extract.mode}",
        f"  Extract scope: {config.extract.scope}",
        f"  Auto load:   {config.load.auto_load}",
        f"  Secret scan: {config.scan.enabled}",
    ]

    if is_git_repo(tm_dir):
        st = status(tm_dir)
        lines.append(f"  Branch:      {st.branch}")
        if st.last_commit:
            lines.append(f"  Last commit: {st.last_commit[:12]} ({st.last_commit_date})")

        md_files = list_md_files(tm_dir)
        lines.append(f"  Memory files:  {len(md_files)}")
        if md_files:
            for f in md_files[:10]:
                lines.append(f"    - {f}")
            if len(md_files) > 10:
                lines.append(f"    ... and {len(md_files) - 10} more")

        log = last_log_entry(tm_dir, 5)
        if log:
            lines.append("")
            lines.append("  Recent sync history:")
            for entry in log.split("\n"):
                lines.append(f"    {entry}")

        if st.has_changes:
            lines.append("")
            lines.append(f"  Unpushed changes: {len(st.changed_files)} file(s)")
    else:
        lines.append("  (not yet initialized — run 'team-memory init')")

    # Multi-repo: show project repo status
    if config.annto and not config.annto.uses_single_repo and config.annto.project.repo:
        proj_dir = _get_project_memory_dir(project_root)
        lines.append("")
        lines.append(f"  Project repo ({proj_dir}):")
        if is_git_repo(proj_dir):
            st2 = status(proj_dir)
            lines.append(f"    Branch: {st2.branch}")
            if st2.last_commit:
                lines.append(f"    Last commit: {st2.last_commit[:12]}")
            proj_files = list_md_files(proj_dir)
            lines.append(f"    Memory files: {len(proj_files)}")
        else:
            lines.append("    (not initialized)")

    return "\n".join(lines)
