from __future__ import annotations

from pathlib import Path

from labit.paths import discover_repo_root


def test_discover_repo_root_does_not_require_top_level_labit_package(tmp_path: Path) -> None:
    for path in [
        tmp_path / ".git",
        tmp_path / ".labit",
        tmp_path / "configs",
        tmp_path / "vault",
    ]:
        path.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"research-os\"\n", encoding="utf-8")

    nested = tmp_path / "vault" / "projects" / "Labit"
    nested.mkdir(parents=True)

    assert discover_repo_root(start=nested) == tmp_path
