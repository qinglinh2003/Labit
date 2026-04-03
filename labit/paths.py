from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoPaths:
    root: Path
    configs_dir: Path
    project_configs_dir: Path
    active_project_path: Path
    vault_dir: Path
    vault_projects_dir: Path
    papers_dir: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> "RepoPaths":
        root = discover_repo_root(start=start)
        return cls(
            root=root,
            configs_dir=root / "configs",
            project_configs_dir=root / "configs" / "projects",
            active_project_path=root / "configs" / "active_project",
            vault_dir=root / "vault",
            vault_projects_dir=root / "vault" / "projects",
            papers_dir=root / "vault" / "papers",
        )


def discover_repo_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("LABIT_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = (start or Path.cwd()).expanduser().resolve()
    candidates = [current, *current.parents]

    for candidate in candidates:
        has_markers = (candidate / ".git").exists() or (candidate / ".claude").exists()
        if has_markers and (candidate / "scripts").exists():
            return candidate

    raise RuntimeError(
        "Could not locate the repository root. Run from inside the repo or set LABIT_REPO_ROOT."
    )
