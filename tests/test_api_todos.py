from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from labit.api.app import create_app
from labit.paths import RepoPaths


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


def _create_project(root: Path, name: str = "Labit") -> RepoPaths:
    paths = _paths(root)
    paths.project_configs_dir.mkdir(parents=True)
    paths.vault_projects_dir.mkdir(parents=True)
    paths.configs_dir.mkdir(exist_ok=True)
    (paths.project_configs_dir / f"{name}.yaml").write_text(
        yaml.safe_dump({"name": name}),
        encoding="utf-8",
    )
    paths.active_project_path.write_text(f"{name}\n", encoding="utf-8")
    (paths.vault_projects_dir / name).mkdir()
    return paths


def test_daily_todo_priority_round_trips_and_carries_over(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    todo_response = client.post(
        "/api/projects/Labit/todos",
        json={"title": "Fix reader offset", "priority": "high"},
    )
    assert todo_response.status_code == 201
    todo = todo_response.json()

    daily_response = client.post(
        "/api/projects/Labit/daily/2026-06-01/items",
        json={"todo_id": todo["id"], "title": todo["title"], "priority": todo["priority"]},
    )
    assert daily_response.status_code == 201
    daily_item = daily_response.json()
    assert daily_item["priority"] == "high"

    patch_response = client.patch(
        f"/api/projects/Labit/daily/2026-06-01/items/{daily_item['id']}",
        json={"priority": "low"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["priority"] == "low"

    carry_response = client.post(
        "/api/projects/Labit/daily/2026-06-02/carry-over",
        json={"from_date": "2026-06-01", "to_date": "2026-06-02"},
    )
    assert carry_response.status_code == 200
    assert carry_response.json()["items"][0]["priority"] == "low"
