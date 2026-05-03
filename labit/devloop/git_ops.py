from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


def normalize_git_remote(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    if value.startswith("git@"):
        value = value[4:]
        value = value.replace(":", "/", 1)
    value = re.sub(r"^https?://", "", value)
    value = value.removesuffix(".git")
    return value.rstrip("/")


def git_output(*args: str, cwd: Path, timeout: int = 10) -> str:
    result = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=timeout,
    )
    return result.stdout.strip()


def resolve_dev_scope(session) -> tuple[str, list[str], Path]:
    """Choose the git scope that /dev should review.

    Returns (label, pathspecs, git_root). git_root may point to a nested repo
    rather than the outer Research-OS repo.
    """
    paths = RepoPaths.discover()
    project = session.project or ""
    if not project:
        return ("repository", ["."], paths.root)

    project_dir = paths.vault_projects_dir / project
    if project_dir.exists():
        code_dir = project_dir / "code"
        if code_dir.exists() and (code_dir / ".git").exists():
            return (f"project code ({project})", ["."], code_dir)

        try:
            spec = ProjectService(paths).load_project(project)
        except Exception:
            spec = None
        repo_root_remote = normalize_git_remote(
            git_output("git", "config", "--get", "remote.origin.url", cwd=paths.root)
        )
        spec_remote = normalize_git_remote(spec.repo) if spec and spec.repo else ""
        if spec_remote and repo_root_remote and spec_remote == repo_root_remote:
            return (f"repository ({paths.root.name})", ["."], paths.root)
        try:
            project_pathspec = str(project_dir.relative_to(paths.root))
        except ValueError:
            project_pathspec = str(project_dir)
        return (f"project ({project})", [project_pathspec], paths.root)
    return ("repository", ["."], paths.root)


def list_scope_dirty_files(pathspecs: list[str], git_root: Path | None = None) -> list[str]:
    cwd = str(git_root) if git_root else str(RepoPaths.discover().root)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *pathspecs],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
    except Exception:
        return []
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        candidate = line[3:].strip()
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1].strip()
        if candidate and candidate not in dirty:
            dirty.append(candidate)
    return dirty


def create_dev_branch(task: str, git_root: Path | None = None) -> tuple[str, str]:
    """Create a dev branch and return (branch_name, original_branch)."""
    cwd = git_root or RepoPaths.discover().root
    original = git_output("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=cwd) or "main"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task.lower())[:40].strip("-")
    timestamp = datetime.now().strftime("%m%d-%H%M")
    branch_name = f"labit-dev/{slug}-{timestamp}"
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    return branch_name, original


def create_dev_worktree(
    *,
    task: str,
    git_root: Path,
    project: str | None,
) -> tuple[str, str, Path]:
    """Create an isolated git worktree for a /dev run."""
    paths = RepoPaths.discover()
    original = git_output("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=git_root) or "main"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task.lower())[:40].strip("-") or "task"
    timestamp = datetime.now().strftime("%m%d-%H%M%S")
    branch_name = f"labit-dev/{slug}-{timestamp}"
    project_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", project or git_root.name).strip("-") or "repo"
    worktree_path = paths.root / ".labit" / "dev-worktrees" / project_slug / f"{slug}-{timestamp}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(git_root),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git worktree add failed")
    return branch_name, original, worktree_path


def remove_dev_worktree(repo_root: Path, worktree_path: Path) -> tuple[bool, str]:
    """Remove a /dev worktree. Returns (ok, message)."""
    if not str(worktree_path):
        return True, ""
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    subprocess.run(
        ["git", "worktree", "prune"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, ""


def dev_auto_commit(round_num: int, pathspecs: list[str], task: str, git_root: Path | None = None) -> str | None:
    """Stage and commit changes from a dev round. Returns commit hash or None."""
    cwd = git_root or RepoPaths.discover().root
    subprocess.run(
        ["git", "add", "--", *pathspecs],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    status = git_output("git", "diff", "--cached", "--stat", cwd=cwd)
    if not status:
        return None
    msg = f"dev(round {round_num}): {task[:60]}"
    result = subprocess.run(
        ["git", "commit", "-m", msg, "--no-verify"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    if result.returncode != 0:
        return None
    return git_output("git", "rev-parse", "--short", "HEAD", cwd=cwd)


def get_last_commit_diff(git_root: Path | None = None) -> str:
    """Get the diff of the most recent commit."""
    cwd = git_root or RepoPaths.discover().root
    try:
        diff = git_output("git", "diff", "HEAD~1..HEAD", cwd=cwd)
        stat = git_output("git", "diff", "--stat", "HEAD~1..HEAD", cwd=cwd)
    except Exception:
        return "(unable to get commit diff)"
    parts = []
    if stat:
        parts.append(f"Diff stat:\n{stat}")
    if diff:
        if len(diff) > 8000:
            diff = diff[:8000] + "\n... (truncated)"
        parts.append(f"Full diff:\n{diff}")
    return "\n\n".join(parts) if parts else "(no changes in last commit)"


def get_scope_diff(pathspecs: list[str], git_root: Path | None = None) -> str:
    """Get git diff for the selected /dev scope."""
    cwd = git_root or RepoPaths.discover().root
    try:
        stat = git_output("git", "diff", "--stat", "--", *pathspecs, cwd=cwd)
        diff = git_output("git", "diff", "--", *pathspecs, cwd=cwd)
        untracked = git_output("git", "status", "--porcelain", "--", *pathspecs, cwd=cwd)
    except Exception:
        return "(unable to get diff)"
    parts = []
    if stat:
        parts.append(f"Diff stat:\n{stat}")
    if diff:
        if len(diff) > 8000:
            diff = diff[:8000] + "\n... (truncated)"
        parts.append(f"Full diff:\n{diff}")
    if untracked:
        parts.append(f"Untracked/modified:\n{untracked}")
    return "\n\n".join(parts) if parts else "(no changes detected)"
