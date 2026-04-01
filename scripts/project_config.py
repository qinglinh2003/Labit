"""project_config.py — Load and manage per-project configuration."""

import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = REPO_ROOT / "configs" / "projects"
PROJECTS_DIR = REPO_ROOT / "vault" / "projects"


def _resolve_name(name: str) -> str:
    """Case-insensitive match against available config filenames."""
    if not CONFIGS_DIR.exists():
        return name
    for f in CONFIGS_DIR.glob("*.yaml"):
        if f.stem.lower() == name.lower():
            return f.stem
    return name


def load_project(name: str) -> dict:
    """Load a single project config by name (case-insensitive)."""
    name = _resolve_name(name)
    path = CONFIGS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Project config not found: {path}")
    return yaml.safe_load(path.read_text())


def load_all_projects() -> list[dict]:
    """Load all project configs from configs/projects/."""
    if not CONFIGS_DIR.exists():
        return []
    return [yaml.safe_load(f.read_text()) for f in sorted(CONFIGS_DIR.glob("*.yaml"))]


def list_project_names() -> list[str]:
    """Return sorted list of project names."""
    if not CONFIGS_DIR.exists():
        return []
    return sorted(f.stem for f in CONFIGS_DIR.glob("*.yaml"))


def project_dir(name: str) -> Path:
    """Return the overlay directory for a project."""
    name = _resolve_name(name)
    return PROJECTS_DIR / name


def ensure_project_dirs(name: str):
    """Create overlay subdirectories for a project."""
    base = project_dir(name)
    for sub in ["digests", "sparks", "hypotheses", "tasks"]:
        (base / sub).mkdir(parents=True, exist_ok=True)


def find_hypothesis(hid: str, legacy_dir: Path) -> Path | None:
    """Find a hypothesis YAML in legacy dir or any project overlay."""
    legacy = legacy_dir / f"{hid}.yaml"
    if legacy.exists():
        return legacy
    if PROJECTS_DIR.exists():
        matches = list(PROJECTS_DIR.glob(f"*/hypotheses/{hid}.yaml"))
        if matches:
            return matches[0]
    return None


def find_task(hid: str, legacy_dir: Path) -> Path | None:
    """Find a SkyPilot task YAML in legacy dir or any project overlay."""
    legacy = legacy_dir / f"{hid}.yaml"
    if legacy.exists():
        return legacy
    if PROJECTS_DIR.exists():
        matches = list(PROJECTS_DIR.glob(f"*/tasks/{hid}.yaml"))
        if matches:
            return matches[0]
    return None
