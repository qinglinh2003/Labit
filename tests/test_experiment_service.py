"""Tests for ExperimentService discovery and run record management."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import labit.services.experiment_service as experiment_service_module
from labit.services.compute_service import SyncResult
from labit.services.experiment_service import ExperimentService, RunRecord, parse_log


@pytest.fixture()
def experiment_tree(tmp_path: Path):
    """Create a minimal project + experiment directory tree."""
    # configs/projects/TestProj.yaml
    configs = tmp_path / "configs" / "projects"
    configs.mkdir(parents=True)
    (configs / "TestProj.yaml").write_text(
        yaml.safe_dump({
            "name": "TestProj",
            "compute_profiles": [{
                "name": "gpu1",
                "connection": {"user": "root", "host": "10.0.0.1", "port": 22},
                "workdir": "/workspace",
            }],
        })
    )
    # vault/projects/TestProj/code/experiments/exp_01/
    exp_dir = tmp_path / "vault" / "projects" / "TestProj" / "code" / "experiments" / "exp_01"
    exp_dir.mkdir(parents=True)
    (exp_dir / "manifest.yaml").write_text(
        yaml.safe_dump({"name": "Test Exp", "description": "A test", "profile": "gpu1", "tags": ["test"]})
    )
    (exp_dir / "run.sh").write_text("#!/bin/bash\necho hello\n")

    # Create marker files for RepoPaths
    (tmp_path / ".git").mkdir()
    (tmp_path / "configs").mkdir(exist_ok=True)

    return tmp_path


def _make_service(root: Path) -> ExperimentService:
    from labit.paths import RepoPaths

    paths = RepoPaths(
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
    return ExperimentService(paths)


def test_list_experiments(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    exps = svc.list_experiments("TestProj")
    assert len(exps) == 1
    assert exps[0].experiment_id == "exp_01"
    assert exps[0].name == "Test Exp"
    assert exps[0].profile == "gpu1"
    assert exps[0].tags == ["test"]


def test_get_experiment(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    exp = svc.get_experiment("TestProj", "exp_01")
    assert exp.name == "Test Exp"


def test_get_experiment_not_found(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    with pytest.raises(FileNotFoundError):
        svc.get_experiment("TestProj", "nonexistent")


def test_list_experiments_empty(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    exps = svc.list_experiments("TestProj")
    # Remove run.sh from exp_01 to make it invalid
    (experiment_tree / "vault" / "projects" / "TestProj" / "code" / "experiments" / "exp_01" / "run.sh").unlink()
    exps = svc.list_experiments("TestProj")
    assert len(exps) == 0


def test_invalid_experiment_ids_are_ignored(experiment_tree: Path):
    bad_dir = experiment_tree / "vault" / "projects" / "TestProj" / "code" / "experiments" / "bad id"
    bad_dir.mkdir()
    (bad_dir / "manifest.yaml").write_text(yaml.safe_dump({"name": "Bad"}))
    (bad_dir / "run.sh").write_text("#!/bin/bash\necho bad\n")

    svc = _make_service(experiment_tree)
    assert [exp.experiment_id for exp in svc.list_experiments("TestProj")] == ["exp_01"]

    with pytest.raises(FileNotFoundError):
        svc.get_experiment("TestProj", "bad id")


def test_manifest_tags_are_normalized(experiment_tree: Path):
    manifest = (
        experiment_tree
        / "vault"
        / "projects"
        / "TestProj"
        / "code"
        / "experiments"
        / "exp_01"
        / "manifest.yaml"
    )
    manifest.write_text(
        yaml.safe_dump({
            "name": "Test Exp",
            "profile": "gpu1",
            "tags": "not-a-list",
        })
    )

    svc = _make_service(experiment_tree)
    exp = svc.get_experiment("TestProj", "exp_01")
    assert exp.tags == []


def test_run_record_roundtrip(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_test_001",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="running",
        pid=12345,
        local_commit="abc123",
        dirty=True,
        created_at="2026-06-07T10:00:00+00:00",
        started_at="2026-06-07T10:00:05+00:00",
    )
    svc._save_run("TestProj", record)
    loaded = svc._load_run("TestProj", "exp_01", "run_test_001")
    assert loaded.run_id == "run_test_001"
    assert loaded.pid == 12345
    assert loaded.dirty is True

    runs = svc.list_runs("TestProj", "exp_01")
    assert len(runs) == 1

    latest = svc.latest_run("TestProj", "exp_01")
    assert latest is not None
    assert latest.run_id == "run_test_001"


def test_latest_run_none(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    assert svc.latest_run("TestProj", "exp_01") is None


def test_launch_uses_tracked_process_group(monkeypatch: pytest.MonkeyPatch, experiment_tree: Path):
    svc = _make_service(experiment_tree)
    captured: dict[str, object] = {}

    def fake_sync_code(project: str, profile_name: str) -> SyncResult:
        return SyncResult(
            success=True,
            profile_name=profile_name,
            local_path="/local/code",
            remote_path="/workspace",
        )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="4242\n", stderr="")

    monkeypatch.setattr(svc.compute_service, "sync_code", fake_sync_code)
    monkeypatch.setattr(ExperimentService, "_git_head", staticmethod(lambda code_dir: "abc123"))
    monkeypatch.setattr(ExperimentService, "_git_dirty", staticmethod(lambda code_dir: True))
    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)

    record = svc.launch("TestProj", "exp_01")

    assert record.status == "running"
    assert record.pid == 4242
    assert record.dirty is True
    remote_cmd = captured["cmd"][-1]  # type: ignore[index]
    assert "setsid bash -c" in remote_cmd
    assert "experiments/exp_01/run.sh" in remote_cmd
    assert ".labit/runs/" in remote_cmd


# ── parse_log tests ─────────────────────────────────────────────────


def test_parse_log_extracts_labit_events():
    raw = (
        'Starting training...\n'
        '{"__labit__": true, "type": "config", "data": {"model": "Qwen2.5-VL-3B"}}\n'
        'Epoch 1\n'
        '{"__labit__": true, "type": "metric", "step": 10, "data": {"loss": 2.3, "lr": 0.0001}}\n'
        '{"__labit__": true, "type": "metric", "step": 20, "data": {"loss": 1.8, "lr": 9e-05}}\n'
        'Done.\n'
    )
    events, plain_count = parse_log(raw)
    assert len(events) == 3
    assert events[0]["type"] == "config"
    assert events[1]["type"] == "metric"
    assert events[1]["step"] == 10
    assert events[2]["data"]["loss"] == 1.8
    assert plain_count == 3  # "Starting training...", "Epoch 1", "Done."


def test_parse_log_handles_empty():
    events, plain_count = parse_log("")
    assert events == []
    assert plain_count == 0


def test_parse_log_ignores_non_labit_json():
    raw = '{"some": "json"}\nplain text\n{"__labit__": true, "type": "status", "status": "done", "message": "ok"}\n'
    events, plain_count = parse_log(raw)
    assert len(events) == 1
    assert events[0]["type"] == "status"
    assert plain_count == 2


def test_fetch_metrics_groups_by_key(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    # Save a run record to satisfy _load_run
    record = RunRecord(
        run_id="run_m1",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="running",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_m1",
    )
    svc._save_run("TestProj", record)

    fake_log = (
        '{"__labit__": true, "type": "metric", "step": 10, "data": {"loss": 2.5, "lr": 1e-4}}\n'
        '{"__labit__": true, "type": "metric", "step": 20, "data": {"loss": 2.0, "lr": 9e-5}}\n'
    )

    # Structured parsing should not depend on a tail window.
    svc._fetch_structured_stdout = lambda *a, **kw: (fake_log, 2)  # type: ignore[method-assign]

    result = svc.fetch_metrics("TestProj", "exp_01", "run_m1")
    assert "loss" in result["metrics"]
    assert "lr" in result["metrics"]
    assert len(result["metrics"]["loss"]) == 2
    assert result["steps"] == [10, 20]


def test_fetch_events_uses_full_line_count(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_e1",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="running",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_e1",
    )
    svc._save_run("TestProj", record)

    fake_events = (
        '{"__labit__": true, "type": "config", "data": {"model": "x"}}\n'
        '{"__labit__": true, "type": "status", "status": "training", "message": "ok"}\n'
    )
    svc._fetch_structured_stdout = lambda *a, **kw: (fake_events, 25)  # type: ignore[method-assign]

    result = svc.fetch_events("TestProj", "exp_01", "run_e1")
    assert len(result["events"]) == 2
    assert result["plain_lines"] == 23
    assert result["total_lines"] == 25


def test_tail_logs_without_remote_run_dir_returns_placeholder(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_no_log",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="failed",
        remote_workdir="/workspace",
        remote_run_dir="",
    )
    svc._save_run("TestProj", record)

    assert svc.tail_logs("TestProj", "exp_01", "run_no_log") == (
        "[No stdout log output yet. The experiment may still be starting up.]"
    )


def test_tail_logs_hides_remote_tail_errors(
    monkeypatch: pytest.MonkeyPatch,
    experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_tail_error",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="failed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_tail_error",
    )
    svc._save_run("TestProj", record)

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="tail: cannot open '/stdout.log' for reading: No such file or directory",
        )

    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)

    assert svc.tail_logs("TestProj", "exp_01", "run_tail_error") == (
        "[Log unavailable: could not connect to the remote machine or run directory.]"
    )
