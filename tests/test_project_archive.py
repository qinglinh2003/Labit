from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from labit.api.app import create_app
from labit.cli import app
from labit.models import ProjectSpec
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


def _paths(root: Path) -> RepoPaths:
    return RepoPaths(
        root=root,
        labit_dir=root / ".labit",
        runs_dir=root / ".labit" / "runs",
        conversations_dir=root / ".labit" / "conversations",
        context_dir=root / ".labit" / "context",
        configs_dir=root / "configs",
        project_configs_dir=root / "configs" / "projects",
        active_project_path=root / "configs" / "active_project",
        vault_dir=root / "vault",
        vault_projects_dir=root / "vault" / "projects",
    )


def _write_project(paths: RepoPaths, name: str, *, archived: bool = False) -> None:
    paths.project_configs_dir.mkdir(parents=True, exist_ok=True)
    paths.vault_projects_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {"name": name}
    if archived:
        data["archived"] = True
    (paths.project_configs_dir / f"{name}.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    (paths.vault_projects_dir / name).mkdir(parents=True, exist_ok=True)


def test_save_project_creates_only_functional_top_level_dirs(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = ProjectService(paths)

    service.save_project(ProjectSpec(name="Research"))

    project_dir = paths.vault_projects_dir / "Research"
    assert (project_dir / "code").is_dir()
    assert (project_dir / "docs").is_dir()
    assert (project_dir / "papers").is_dir()
    assert not (project_dir / "digests").exists()
    assert not (project_dir / "sparks").exists()


def test_api_archive_hides_project_and_clears_active(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_project(paths, "Active")
    _write_project(paths, "Visible")
    paths.configs_dir.mkdir(parents=True, exist_ok=True)
    paths.active_project_path.write_text("Active\n", encoding="utf-8")
    client = TestClient(create_app(paths))

    archive_response = client.put("/api/projects/Active/archive")

    assert archive_response.status_code == 200
    assert archive_response.json() == {"name": "Active", "archived": True, "changed": True}
    assert yaml.safe_load((paths.project_configs_dir / "Active.yaml").read_text(encoding="utf-8"))["archived"] is True
    assert not paths.active_project_path.exists()
    assert client.get("/api/projects").json() == {"projects": ["Visible"], "active_project": None}
    assert client.get("/api/projects?include_archived=true").json() == {
        "projects": ["Active", "Visible"],
        "active_project": None,
    }


def test_api_archive_preserves_legacy_project_config_fields(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_project(paths, "Legacy")
    config_path = paths.project_configs_dir / "Legacy.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "name": "Legacy",
                "description": "legacy config",
                "arxiv_categories": ["cs.AI"],
                "storage_profile": "research-r2",
                "sync_dirs": ["outputs"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(paths))

    archive_response = client.put("/api/projects/Legacy/archive")

    assert archive_response.status_code == 200
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["archived"] is True
    assert saved["arxiv_categories"] == ["cs.AI"]
    assert saved["storage_profile"] == "research-r2"
    assert saved["sync_dirs"] == ["outputs"]


def test_api_unarchive_restores_project_to_default_list(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_project(paths, "Hidden", archived=True)
    client = TestClient(create_app(paths))

    unarchive_response = client.delete("/api/projects/Hidden/archive")

    assert unarchive_response.status_code == 200
    assert unarchive_response.json() == {"name": "Hidden", "archived": False, "changed": True}
    assert "archived" not in yaml.safe_load((paths.project_configs_dir / "Hidden.yaml").read_text(encoding="utf-8"))
    assert client.get("/api/projects").json()["projects"] == ["Hidden"]


def test_active_project_ignores_archived_config(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_project(paths, "Hidden", archived=True)
    paths.configs_dir.mkdir(parents=True, exist_ok=True)
    paths.active_project_path.write_text("Hidden\n", encoding="utf-8")
    client = TestClient(create_app(paths))

    assert client.get("/api/projects").json() == {"projects": [], "active_project": None}


def test_cli_archive_list_and_unarchive(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    _write_project(paths, "HideMe")
    _write_project(paths, "KeepMe")
    monkeypatch.setattr("labit.commands.project.RepoPaths.discover", lambda: paths)
    runner = CliRunner()

    archive_result = runner.invoke(app, ["project", "archive", "HideMe", "--json"])
    assert archive_result.exit_code == 0, archive_result.output
    assert json.loads(archive_result.stdout)["archived"] is True

    default_list = runner.invoke(app, ["project", "list", "--json"])
    assert default_list.exit_code == 0, default_list.output
    assert [item["name"] for item in json.loads(default_list.stdout)["projects"]] == ["KeepMe"]

    archived_list = runner.invoke(app, ["project", "list", "--archived", "--json"])
    assert archived_list.exit_code == 0, archived_list.output
    archived_payload = json.loads(archived_list.stdout)
    assert [(item["name"], item["archived"]) for item in archived_payload["projects"]] == [
        ("HideMe", True),
        ("KeepMe", False),
    ]

    switch_result = runner.invoke(app, ["project", "switch", "HideMe", "--json"])
    assert switch_result.exit_code == 1
    assert "not found" in json.loads(switch_result.stdout)["error"]

    unarchive_result = runner.invoke(app, ["project", "unarchive", "HideMe", "--json"])
    assert unarchive_result.exit_code == 0, unarchive_result.output
    assert json.loads(unarchive_result.stdout)["archived"] is False
