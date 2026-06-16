"""Tests for ExperimentService discovery and run record management."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import labit.services.experiment_service as experiment_service_module
from labit.services.compute_service import SyncResult
from labit.services.experiment_service import (
    ExperimentService, RunRecord, SyncManifest, parse_log,
    _extract_artifact_dirs,
)


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


# ── sync tests ──────────────────────────────────────────────────────


def _make_run_with_local_logs(svc, experiment_tree, *, status="completed"):
    """Create a run record and write local cached logs."""
    record = RunRecord(
        run_id="run_sync_01",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status=status,
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_sync_01",
    )
    svc._save_run("TestProj", record)

    # Write local cached log files
    local_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_sync_01")
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "stdout.log").write_text("line1\nline2\nline3\n")
    (local_dir / "stderr.log").write_text("warn1\n")
    (local_dir / "exit_code").write_text("0\n")
    return record


def test_tail_logs_reads_local_for_completed_run(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    _make_run_with_local_logs(svc, experiment_tree, status="completed")

    # Should read from local without SSH
    content = svc.tail_logs("TestProj", "exp_01", "run_sync_01")
    assert "line1" in content
    assert "line3" in content


def test_tail_logs_reads_local_for_failed_run(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    _make_run_with_local_logs(svc, experiment_tree, status="failed")

    content = svc.tail_logs("TestProj", "exp_01", "run_sync_01")
    assert "line1" in content


def test_tail_logs_falls_back_to_local_on_ssh_failure(
    monkeypatch: pytest.MonkeyPatch, experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    _make_run_with_local_logs(svc, experiment_tree, status="running")

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Connection refused")

    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)

    content = svc.tail_logs("TestProj", "exp_01", "run_sync_01")
    assert "line1" in content


def test_fetch_events_reads_local_for_completed_run(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_ev_local",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_ev_local",
    )
    svc._save_run("TestProj", record)

    local_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_ev_local")
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "stdout.log").write_text(
        'Starting...\n'
        '{"__labit__": true, "type": "metric", "step": 10, "data": {"loss": 2.5}}\n'
        'Done.\n'
    )

    result = svc.fetch_events("TestProj", "exp_01", "run_ev_local")
    assert len(result["events"]) == 1
    assert result["total_lines"] == 3
    assert result["plain_lines"] == 2


def test_fetch_metrics_reads_local_for_completed_run(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_m_local",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_m_local",
    )
    svc._save_run("TestProj", record)

    local_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_m_local")
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "stdout.log").write_text(
        '{"__labit__": true, "type": "metric", "step": 10, "data": {"loss": 2.5}}\n'
        '{"__labit__": true, "type": "metric", "step": 20, "data": {"loss": 1.8}}\n'
    )

    result = svc.fetch_metrics("TestProj", "exp_01", "run_m_local")
    assert "loss" in result["metrics"]
    assert len(result["metrics"]["loss"]) == 2
    assert result["steps"] == [10, 20]


def test_sync_logs_no_remote_run_dir(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_no_remote",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir="",
    )
    svc._save_run("TestProj", record)

    manifest = svc.sync_logs("TestProj", "exp_01", "run_no_remote")
    assert manifest.last_sync_status == "failed"
    assert "No remote run directory" in manifest.last_sync_error

    loaded = svc._load_run("TestProj", "exp_01", "run_no_remote")
    assert loaded.sync is not None
    assert loaded.sync["last_sync_status"] == "failed"
    assert "No remote run directory" in loaded.sync["last_sync_error"]


def test_stop_run_returns_record_with_sync_state(
    monkeypatch: pytest.MonkeyPatch, experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_stop_sync",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="running",
        pid=4242,
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_stop_sync",
    )
    svc._save_run("TestProj", record)

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)

    stopped = svc.stop_run("TestProj", "exp_01", "run_stop_sync")
    assert stopped.status == "stopped"
    assert stopped.sync is not None
    assert stopped.sync["last_sync_status"] == "ok"


def test_sync_manifest_roundtrip():
    m = SyncManifest(
        logs_synced_at="2026-06-15T10:00:00+00:00",
        last_sync_status="ok",
        files_synced=3,
        bytes_synced=12345,
    )
    d = m.to_dict()
    m2 = SyncManifest.from_dict(d)
    assert m2.logs_synced_at == m.logs_synced_at
    assert m2.files_synced == 3


def test_sync_field_persisted_on_run_record(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_sync_field",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir="",
        sync={"logs_synced_at": "2026-06-15T10:00:00+00:00", "last_sync_status": "ok"},
    )
    svc._save_run("TestProj", record)
    loaded = svc._load_run("TestProj", "exp_01", "run_sync_field")
    assert loaded.sync is not None
    assert loaded.sync["last_sync_status"] == "ok"


def test_launch_injects_labit_env_vars(
    monkeypatch: pytest.MonkeyPatch, experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    captured: dict[str, object] = {}

    def fake_sync_code(project, profile_name):
        return SyncResult(
            success=True, profile_name=profile_name,
            local_path="/local/code", remote_path="/workspace",
        )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="4242\n", stderr="")

    monkeypatch.setattr(svc.compute_service, "sync_code", fake_sync_code)
    monkeypatch.setattr(ExperimentService, "_git_head", staticmethod(lambda code_dir: "abc123"))
    monkeypatch.setattr(ExperimentService, "_git_dirty", staticmethod(lambda code_dir: False))
    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)

    svc.launch("TestProj", "exp_01")

    remote_cmd = captured["cmd"][-1]
    assert "LABIT_RUN_DIR=" in remote_cmd
    assert "LABIT_RESULTS_DIR=" in remote_cmd
    assert "/results" in remote_cmd


# ── V2: results sync + browsing ────────────────────────────────────


def test_sync_results_no_remote_run_dir(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_no_remote_r",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir="",
    )
    svc._save_run("TestProj", record)

    manifest = svc.sync_results("TestProj", "exp_01", "run_no_remote_r")
    assert manifest.last_sync_status == "failed"
    assert "No remote run directory" in manifest.last_sync_error


def test_sync_results_preserves_logs_synced_at(
    monkeypatch: pytest.MonkeyPatch, experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_preserve",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_preserve",
        sync={"logs_synced_at": "2026-06-15T10:00:00+00:00", "last_sync_status": "ok"},
    )
    svc._save_run("TestProj", record)

    # Mock rsync to fail (simulates remote unavailable)
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Connection refused")

    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)

    manifest = svc.sync_results("TestProj", "exp_01", "run_preserve")
    # Even though results sync failed, logs_synced_at should be preserved
    assert manifest.logs_synced_at == "2026-06-15T10:00:00+00:00"
    assert manifest.last_sync_status == "failed"


def test_sync_results_falls_back_to_artifact_dirs(
    monkeypatch: pytest.MonkeyPatch, experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_artifacts",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_artifacts",
    )
    svc._save_run("TestProj", record)
    local_log_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_artifacts")
    local_log_dir.mkdir(parents=True, exist_ok=True)
    (local_log_dir / "stdout.log").write_text(
        '{"__labit__": true, "type": "artifact", "name": "summary", '
        '"path": "/workspace/outputs/phase0/summary.csv"}\n'
    )

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "Number of regular files transferred: 0\n"
                "Total transferred file size: 0 bytes\n"
            ),
            stderr="",
        )

    captured: dict[str, object] = {}

    def fake_sync_artifact_dirs(profile, workdir, artifact_dirs, local_results, excludes):
        captured["workdir"] = workdir
        captured["artifact_dirs"] = artifact_dirs
        captured["local_results"] = local_results
        return 2, 1234

    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)
    monkeypatch.setattr(svc, "_sync_artifact_dirs", fake_sync_artifact_dirs)

    manifest = svc.sync_results("TestProj", "exp_01", "run_artifacts")

    assert manifest.last_sync_status == "ok"
    assert manifest.files_synced == 2
    assert manifest.bytes_synced == 1234
    assert manifest.artifact_sources == ["/workspace/outputs/phase0"]
    assert captured["workdir"] == "/workspace"
    assert captured["artifact_dirs"] == ["/workspace/outputs/phase0"]


def test_sync_results_ignores_artifact_dirs_outside_workdir(
    monkeypatch: pytest.MonkeyPatch, experiment_tree: Path,
):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_artifacts_outside",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_artifacts_outside",
    )
    svc._save_run("TestProj", record)
    local_log_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_artifacts_outside")
    local_log_dir.mkdir(parents=True, exist_ok=True)
    (local_log_dir / "stdout.log").write_text(
        '{"__labit__": true, "type": "artifact", "name": "safe", '
        '"path": "/workspace/outputs/phase0/summary.csv"}\n'
        '{"__labit__": true, "type": "artifact", "name": "unsafe", '
        '"path": "/etc/passwd"}\n'
    )

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "Number of regular files transferred: 0\n"
                "Total transferred file size: 0 bytes\n"
            ),
            stderr="",
        )

    captured: dict[str, object] = {}

    def fake_sync_artifact_dirs(profile, workdir, artifact_dirs, local_results, excludes):
        captured["artifact_dirs"] = artifact_dirs
        return 1, 100

    monkeypatch.setattr(experiment_service_module.subprocess, "run", fake_run)
    monkeypatch.setattr(svc, "_sync_artifact_dirs", fake_sync_artifact_dirs)

    manifest = svc.sync_results("TestProj", "exp_01", "run_artifacts_outside")

    assert manifest.artifact_sources == ["/workspace/outputs/phase0"]
    assert captured["artifact_dirs"] == ["/workspace/outputs/phase0"]


def test_sync_manifest_v2_fields():
    m = SyncManifest(
        logs_synced_at="2026-06-15T10:00:00+00:00",
        results_synced_at="2026-06-15T11:00:00+00:00",
        last_sync_status="ok",
        files_synced=42,
        bytes_synced=123456,
        excluded_patterns=["checkpoints/", "*.pt"],
    )
    d = m.to_dict()
    m2 = SyncManifest.from_dict(d)
    assert m2.results_synced_at == "2026-06-15T11:00:00+00:00"
    assert m2.excluded_patterns == ["checkpoints/", "*.pt"]


def test_list_results_empty(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_no_results",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_no_results",
    )
    svc._save_run("TestProj", record)
    assert svc.list_results("TestProj", "exp_01", "run_no_results") == []


def test_list_results_with_files(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_with_results",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_with_results",
    )
    svc._save_run("TestProj", record)

    results_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_with_results") / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "metrics.json").write_text('{"loss": 0.5}')
    sub = results_dir / "figures"
    sub.mkdir()
    (sub / "train_loss.png").write_bytes(b"\x89PNG")

    entries = svc.list_results("TestProj", "exp_01", "run_with_results")
    assert len(entries) == 2
    paths = {e["path"] for e in entries}
    assert "metrics.json" in paths
    assert "figures/train_loss.png" in paths
    # Each entry has size and modified
    for e in entries:
        assert e["size"] > 0
        assert e["modified"]


def test_read_result_file(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_read_file",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_read_file",
    )
    svc._save_run("TestProj", record)

    results_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_read_file") / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "metrics.json").write_text('{"loss": 0.5}')

    path = svc.read_result_file("TestProj", "exp_01", "run_read_file", "metrics.json")
    assert path.read_text() == '{"loss": 0.5}'


def test_read_result_file_path_traversal(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_traversal",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_traversal",
    )
    svc._save_run("TestProj", record)

    results_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_traversal") / "results"
    results_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        svc.read_result_file("TestProj", "exp_01", "run_traversal", "../../run_traversal.json")


def test_read_result_file_not_found(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    record = RunRecord(
        run_id="run_no_file",
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=".labit/runs/run_no_file",
    )
    svc._save_run("TestProj", record)

    results_dir = svc._local_run_data_dir("TestProj", "exp_01", "run_no_file") / "results"
    results_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        svc.read_result_file("TestProj", "exp_01", "run_no_file", "nonexistent.json")


def test_parse_rsync_stats():
    from labit.services.experiment_service import _parse_rsync_stats

    output = """
Number of files: 15 (reg: 10, dir: 5)
Number of created files: 8
Number of regular files transferred: 7
Total file size: 2,345,678 bytes
Total transferred file size: 1,234,567 bytes
"""
    files, total_bytes = _parse_rsync_stats(output)
    assert files == 7
    assert total_bytes == 1234567


def test_default_results_exclude():
    from labit.services.experiment_service import _DEFAULT_RESULTS_EXCLUDE

    assert "checkpoints/" in _DEFAULT_RESULTS_EXCLUDE
    assert "*.pt" in _DEFAULT_RESULTS_EXCLUDE
    assert "wandb/" in _DEFAULT_RESULTS_EXCLUDE
    assert "__pycache__/" in _DEFAULT_RESULTS_EXCLUDE


# ── preview tests ───────────────────────────────────────────────────

def _make_preview_run(svc, run_id="run_preview"):
    record = RunRecord(
        run_id=run_id,
        experiment_id="exp_01",
        name="Test Exp",
        profile="gpu1",
        script="experiments/exp_01/run.sh",
        command="bash experiments/exp_01/run.sh",
        status="completed",
        remote_workdir="/workspace",
        remote_run_dir=f".labit/runs/{run_id}",
    )
    svc._save_run("TestProj", record)
    results_dir = svc._local_run_data_dir("TestProj", "exp_01", run_id) / "results"
    results_dir.mkdir(parents=True)
    return results_dir


def test_preview_json(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc)
    (results_dir / "metrics.json").write_text('{"loss": 0.5}')

    p = svc.preview_result_file("TestProj", "exp_01", "run_preview", "metrics.json")
    assert p["kind"] == "json"
    assert '"loss"' in p["content"]
    assert not p["truncated"]


def test_preview_csv(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc, "run_csv")
    (results_dir / "data.csv").write_text("a,b\n1,2\n3,4\n")

    p = svc.preview_result_file("TestProj", "exp_01", "run_csv", "data.csv")
    assert p["kind"] == "csv"
    assert "a,b" in p["content"]


def test_preview_image(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc, "run_img")
    (results_dir / "plot.png").write_bytes(b"\x89PNG\r\n")

    p = svc.preview_result_file("TestProj", "exp_01", "run_img", "plot.png")
    assert p["kind"] == "image"
    assert p["content"] is None
    assert "plot.png" in p["download_url"]


def test_preview_download_fallback(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc, "run_dl")
    (results_dir / "model.pt").write_bytes(b"\x00" * 100)

    p = svc.preview_result_file("TestProj", "exp_01", "run_dl", "model.pt")
    assert p["kind"] == "download"
    assert p["content"] is None


def test_preview_text(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc, "run_txt")
    (results_dir / "notes.txt").write_text("hello world")

    p = svc.preview_result_file("TestProj", "exp_01", "run_txt", "notes.txt")
    assert p["kind"] == "text"
    assert p["content"] == "hello world"


def test_preview_truncation(experiment_tree: Path):
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc, "run_big")
    (results_dir / "big.log").write_text("x" * (600 * 1024))

    p = svc.preview_result_file("TestProj", "exp_01", "run_big", "big.log")
    assert p["kind"] == "text"
    assert p["truncated"] is True
    assert len(p["content"]) == 512 * 1024


def test_extract_artifact_dirs(tmp_path: Path):
    """_extract_artifact_dirs parses artifact events from stdout.log."""
    log = tmp_path / "stdout.log"
    log.write_text(
        'some plain log line\n'
        '{"__labit__": true, "type": "artifact", "name": "results", "path": "/workspace/outputs/foo/bar.json"}\n'
        '{"__labit__": true, "type": "artifact", "name": "chart", "path": "/workspace/outputs/foo/baz.png"}\n'
        '{"__labit__": true, "type": "artifact", "name": "other", "path": "/workspace/outputs/qux/data.csv"}\n'
        '{"__labit__": true, "type": "metric", "name": "loss", "value": 0.5}\n'
    )
    dirs = _extract_artifact_dirs(log)
    assert dirs == ["/workspace/outputs/foo", "/workspace/outputs/qux"]


def test_extract_artifact_dirs_no_artifacts(tmp_path: Path):
    """Returns empty list when no artifact events in stdout."""
    log = tmp_path / "stdout.log"
    log.write_text("just plain output\n")
    assert _extract_artifact_dirs(log) == []


def test_extract_artifact_dirs_missing_file(tmp_path: Path):
    """Returns empty list when stdout.log doesn't exist."""
    assert _extract_artifact_dirs(tmp_path / "nonexistent.log") == []


def test_sync_manifest_artifact_sources():
    """SyncManifest roundtrips artifact_sources field."""
    m = SyncManifest(
        logs_synced_at="2026-01-01T00:00:00+00:00",
        results_synced_at="2026-01-01T00:00:00+00:00",
        last_sync_status="ok",
        artifact_sources=["/workspace/outputs/foo"],
    )
    d = m.to_dict()
    assert d["artifact_sources"] == ["/workspace/outputs/foo"]
    m2 = SyncManifest.from_dict(d)
    assert m2.artifact_sources == ["/workspace/outputs/foo"]


def test_preview_url_encoding(experiment_tree: Path):
    """download_url must encode special chars (#, ?, space) in file paths."""
    svc = _make_service(experiment_tree)
    results_dir = _make_preview_run(svc, "run_enc")
    figures_dir = results_dir / "figures"
    figures_dir.mkdir()
    (figures_dir / "loss #1.png").write_bytes(b"\x89PNG\r\n")

    p = svc.preview_result_file("TestProj", "exp_01", "run_enc", "figures/loss #1.png")
    assert p["kind"] == "image"
    # The URL must not contain a raw space or #
    assert " " not in p["download_url"]
    assert "#" not in p["download_url"]
    assert "loss%20%231.png" in p["download_url"]


# ── auto-refresh & graceful profile handling ───────────────────────


def test_refresh_status_missing_profile(experiment_tree: Path):
    """refresh_status should return cached record if profile is deleted."""
    svc = _make_service(experiment_tree)
    # Create a run with a non-existent profile
    import json
    run_dir = experiment_tree / "vault" / "projects" / "TestProj" / "runs" / "exp_01"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_data = {
        "run_id": "run_missing_profile",
        "experiment_id": "exp_01",
        "name": "Test",
        "profile": "deleted_gpu",
        "script": "experiments/exp_01/run.sh",
        "command": "bash run.sh",
        "status": "running",
        "pid": 99999,
        "exit_code": None,
        "remote_workdir": "/workspace",
        "remote_run_dir": ".labit/runs/run_missing_profile",
        "local_commit": "",
        "dirty": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:00:01+00:00",
        "finished_at": "",
        "notes": "",
        "error": "",
    }
    (run_dir / "run_missing_profile.json").write_text(json.dumps(record_data))

    # Should not raise, should return cached record
    result = svc.refresh_status("TestProj", "exp_01", "run_missing_profile")
    assert result.status == "running"  # unchanged, since profile is gone


def test_refresh_all_running_throttle(experiment_tree: Path):
    """refresh_all_running should be throttled to avoid redundant SSH calls."""
    import json, time
    svc = _make_service(experiment_tree)
    run_dir = experiment_tree / "vault" / "projects" / "TestProj" / "runs" / "exp_01"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_data = {
        "run_id": "run_throttle",
        "experiment_id": "exp_01",
        "name": "Test",
        "profile": "deleted_gpu",
        "script": "experiments/exp_01/run.sh",
        "command": "bash run.sh",
        "status": "running",
        "pid": 99999,
        "exit_code": None,
        "remote_workdir": "/workspace",
        "remote_run_dir": ".labit/runs/run_throttle",
        "local_commit": "",
        "dirty": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:00:01+00:00",
        "finished_at": "",
        "notes": "",
        "error": "",
    }
    (run_dir / "run_throttle.json").write_text(json.dumps(record_data))

    # First call should proceed (sets timestamp)
    svc.refresh_all_running("TestProj")
    # Second call within 15s should be throttled (no-op)
    svc.refresh_all_running("TestProj")
    # Record should still be running (profile doesn't exist, so no SSH)
    record = svc._load_run("TestProj", "exp_01", "run_throttle")
    assert record.status == "running"


def test_refresh_all_running_limits_runs_per_sweep(experiment_tree: Path, monkeypatch: pytest.MonkeyPatch):
    """A list poll should not attempt unbounded SSH refreshes."""
    svc = _make_service(experiment_tree)
    for idx in range(6):
        svc._save_run(
            "TestProj",
            RunRecord(
                run_id=f"run_many_{idx}",
                experiment_id="exp_01",
                name="Test",
                profile="gpu1",
                script="experiments/exp_01/run.sh",
                command="bash run.sh",
                status="running",
                pid=1000 + idx,
                remote_workdir="/workspace",
                remote_run_dir=f".labit/runs/run_many_{idx}",
            ),
        )

    refreshed: list[str] = []

    def fake_refresh(
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        ssh_timeout: int = 10,
        sync_logs_on_finish: bool = True,
    ):
        refreshed.append(run_id)
        return svc._load_run(project, experiment_id, run_id)

    monkeypatch.setattr(svc, "refresh_status", fake_refresh)

    svc.refresh_all_running("TestProj")

    assert len(refreshed) == 4


def test_refresh_all_running_does_not_sync_logs(experiment_tree: Path, monkeypatch: pytest.MonkeyPatch):
    """Automatic list polling should update status without running rsync."""
    svc = _make_service(experiment_tree)
    svc._save_run(
        "TestProj",
        RunRecord(
            run_id="run_no_log_sync",
            experiment_id="exp_01",
            name="Test",
            profile="gpu1",
            script="experiments/exp_01/run.sh",
            command="bash run.sh",
            status="running",
            pid=1000,
            remote_workdir="/workspace",
            remote_run_dir=".labit/runs/run_no_log_sync",
        ),
    )

    called_with: list[bool] = []

    def fake_refresh(
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        ssh_timeout: int = 10,
        sync_logs_on_finish: bool = True,
    ):
        called_with.append(sync_logs_on_finish)
        return svc._load_run(project, experiment_id, run_id)

    monkeypatch.setattr(svc, "refresh_status", fake_refresh)

    svc.refresh_all_running("TestProj")

    assert called_with == [False]


def test_tail_logs_missing_profile(experiment_tree: Path):
    """tail_logs should return empty message if profile is deleted."""
    import json
    svc = _make_service(experiment_tree)
    run_dir = experiment_tree / "vault" / "projects" / "TestProj" / "runs" / "exp_01"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_data = {
        "run_id": "run_no_profile",
        "experiment_id": "exp_01",
        "name": "Test",
        "profile": "gone_gpu",
        "script": "experiments/exp_01/run.sh",
        "command": "bash run.sh",
        "status": "running",
        "pid": 99999,
        "exit_code": None,
        "remote_workdir": "/workspace",
        "remote_run_dir": ".labit/runs/run_no_profile",
        "local_commit": "",
        "dirty": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:00:01+00:00",
        "finished_at": "",
        "notes": "",
        "error": "",
    }
    (run_dir / "run_no_profile.json").write_text(json.dumps(record_data))

    # Should not raise
    result = svc.tail_logs("TestProj", "exp_01", "run_no_profile")
    assert "No stdout" in result or result == ""


def test_fetch_events_no_remote_run_dir(experiment_tree: Path):
    """fetch_events should return empty when remote_run_dir is empty."""
    import json
    svc = _make_service(experiment_tree)
    run_dir = experiment_tree / "vault" / "projects" / "TestProj" / "runs" / "exp_01"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_data = {
        "run_id": "run_no_dir",
        "experiment_id": "exp_01",
        "name": "Test",
        "profile": "gpu1",
        "script": "experiments/exp_01/run.sh",
        "command": "bash run.sh",
        "status": "failed",
        "pid": None,
        "exit_code": None,
        "remote_workdir": "/workspace",
        "remote_run_dir": "",
        "local_commit": "",
        "dirty": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "",
        "finished_at": "2026-01-01T00:00:01+00:00",
        "notes": "",
        "error": "Sync failed",
    }
    (run_dir / "run_no_dir.json").write_text(json.dumps(record_data))

    result = svc.fetch_events("TestProj", "exp_01", "run_no_dir")
    assert result["events"] == []
