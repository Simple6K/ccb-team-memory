"""Git operations via subprocess — zero external Python dependencies."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


# ─── Result types ──────────────────────────────────────────────────────

@dataclass
class GitResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1


@dataclass
class CommitResult(GitResult):
    files_changed: int = 0
    commit_hash: str = ""


@dataclass
class PushResult(GitResult):
    conflict: bool = False
    retries: int = 0


@dataclass
class StatusInfo:
    branch: str = ""
    last_commit: str = ""
    last_commit_date: str = ""
    has_changes: bool = False
    changed_files: list[str] = None

    def __post_init__(self):
        if self.changed_files is None:
            self.changed_files = []


# ─── Core operations ───────────────────────────────────────────────────

def _run(cwd: Path, *args: str, timeout: int = 60) -> GitResult:
    """Run a git command and return structured result."""
    cmd = ["git", *args]
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout,
        )
        return GitResult(
            success=p.returncode == 0,
            stdout=p.stdout.strip(),
            stderr=p.stderr.strip(),
            exit_code=p.returncode,
        )
    except subprocess.TimeoutExpired:
        return GitResult(
            success=False,
            stderr=f"git {' '.join(args)} timed out after {timeout}s",
        )
    except FileNotFoundError:
        return GitResult(
            success=False,
            stderr="git not found; please install git",
        )


def is_git_repo(path: Path) -> bool:
    """Check if path is within a git repository (has its own .git)."""
    return (path / ".git").is_dir()


def clone(repo_url: str, target: Path, depth: int = 1, branch: str = "main",
          sparse: bool = False) -> GitResult:
    """Shallow clone a git repository."""
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--depth", str(depth), "--branch", branch]
    if sparse:
        args.append("--sparse")
    args.extend([repo_url, str(target.name)])
    return _run(target.parent, *args)


def sparse_checkout_set(path: Path, dirs: list[str]) -> GitResult:
    """Set sparse-checkout to only include specified directories (cone mode)."""
    args = ["sparse-checkout", "set", "--cone"] + dirs
    return _run(path, *args)


def sparse_checkout_exclude(path: Path, exclude_dirs: list[str]) -> GitResult:
    """Set sparse-checkout to exclude specific directories.

    Uses non-cone mode with negation patterns.
    """
    # Write sparse-checkout file manually for negation patterns
    info_dir = path / ".git" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    lines = ["/*"]
    for d in exclude_dirs:
        lines.append(f"!{d}/")
    (info_dir / "sparse-checkout").write_text("\n".join(lines) + "\n")
    # Enable sparse-checkout
    _run(path, "config", "core.sparseCheckout", "true")
    return GitResult(success=True)


def init(target: Path) -> GitResult:
    """Initialize a git repository and add a remote."""
    target.mkdir(parents=True, exist_ok=True)
    r = _run(target, "init")
    if not r.success:
        return r
    return _run(target, "-C", str(target), "checkout", "-b", "main")


def set_remote(path: Path, remote_name: str, url: str) -> GitResult:
    """Set or add a git remote."""
    # Check if remote exists
    r = _run(path, "remote", "get-url", remote_name)
    if r.success:
        return _run(path, "remote", "set-url", remote_name, url)
    return _run(path, "remote", "add", remote_name, url)


def pull(path: Path, remote: str = "origin", branch: str = "main") -> GitResult:
    """Fast-forward pull from remote."""
    return _run(path, "pull", "--ff-only", remote, branch)


def pull_rebase(path: Path, remote: str = "origin", branch: str = "main") -> GitResult:
    """Pull with rebase (for conflict resolution)."""
    return _run(path, "pull", "--rebase", remote, branch)


def fetch_unshallow(path: Path) -> GitResult:
    """Convert a shallow clone to a full clone (needed for rebase)."""
    return _run(path, "fetch", "--unshallow")


def status(path: Path) -> StatusInfo:
    """Get detailed git status."""
    info = StatusInfo()

    # Branch
    r = _run(path, "rev-parse", "--abbrev-ref", "HEAD")
    if r.success:
        info.branch = r.stdout

    # Last commit
    r = _run(path, "log", "-1", "--format=%H")
    if r.success:
        info.last_commit = r.stdout

    r = _run(path, "log", "-1", "--format=%aI")
    if r.success:
        info.last_commit_date = r.stdout

    # Changed files (unstaged + staged)
    r = _run(path, "status", "--porcelain")
    if r.success and r.stdout:
        info.has_changes = True
        info.changed_files = [
            line[3:] for line in r.stdout.split("\n") if line
        ]

    return info


def add_md_files(path: Path) -> GitResult:
    """Stage all .md files (recursively), excluding .git directory."""
    return _run(path, "add", "--", "*.md")


def commit(path: Path, message: str) -> CommitResult:
    """Commit staged changes. Returns CommitResult with files_changed count."""
    r = _run(path, "commit", "-m", message)
    if not r.success:
        return CommitResult(success=False, stderr=r.stderr, exit_code=r.exit_code)

    # Count changed files
    count_r = _run(path, "diff-tree", "--no-commit-id", "--numstat", "-r", "HEAD")
    files = 0
    if count_r.success and count_r.stdout:
        files = len([l for l in count_r.stdout.split("\n") if l])

    hash_r = _run(path, "rev-parse", "--short", "HEAD")
    return CommitResult(
        success=True,
        files_changed=files,
        commit_hash=hash_r.stdout if hash_r.success else "",
    )


def push(path: Path, remote: str = "origin", branch: str = "main") -> GitResult:
    """Push to remote. Returns result with conflict flag."""
    r = _run(path, "push", remote, branch)
    result = GitResult(
        success=r.success,
        stdout=r.stdout,
        stderr=r.stderr,
        exit_code=r.exit_code,
    )
    return result


def push_with_retry(path: Path, remote: str = "origin", branch: str = "main", max_retries: int = 3) -> PushResult:
    """Push with automatic pull-rebase retry on conflict."""
    r = push(path, remote, branch)
    if r.success:
        return PushResult(success=True, stdout=r.stdout)

    # Check if conflict (rejected)
    is_rejected = "rejected" in r.stderr.lower() or "[remote rejected]" in r.stderr.lower()

    retries = 0
    while is_rejected and retries < max_retries:
        retries += 1
        # Pull with rebase
        pr = pull_rebase(path, remote, branch)
        if not pr.success:
            # Try unshallow then rebase
            _run(path, "fetch", "--unshallow")
            pr = pull_rebase(path, remote, branch)
            if not pr.success:
                return PushResult(success=False, conflict=True, retries=retries,
                                  stderr=f"rebase failed after {retries} retries: {pr.stderr}")

        # Retry push
        r = push(path, remote, branch)
        if r.success:
            return PushResult(success=True, retries=retries, stdout=r.stdout)
        is_rejected = "rejected" in r.stderr.lower()

    if not r.success:
        return PushResult(success=False, conflict=is_rejected, retries=retries,
                          stderr=r.stderr)
    return PushResult(success=True, retries=retries)


def has_changes(path: Path) -> bool:
    """Check if there are any staged or unstaged changes."""
    r = _run(path, "status", "--porcelain")
    return r.success and bool(r.stdout)


def list_md_files(path: Path) -> list[str]:
    """List all .md files recursively (relative paths)."""
    r = _run(path, "ls-files", "--", "*.md")
    if not r.success or not r.stdout:
        return []
    return [f for f in r.stdout.split("\n") if f]


def last_log_entry(path: Path, n: int = 1) -> str:
    """Get the last n log entries."""
    r = _run(path, "log", f"-{n}", "--oneline")
    return r.stdout if r.success else ""


def diff_name_only(path: Path, base: str = "HEAD~1") -> list[str]:
    """Get list of files changed since a base commit."""
    r = _run(path, "diff", "--name-only", base, "HEAD")
    if not r.success or not r.stdout:
        return []
    return [f for f in r.stdout.split("\n") if f]
