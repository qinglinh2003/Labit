"""Experiment discovery, launch, and monitoring service.

Experiments live under ``code/experiments/<experiment_id>/`` in each project.
Each experiment directory must contain:
- ``manifest.yaml``: name, description, profile, optional tags
- ``run.sh``: self-contained launch script

Run records are persisted under ``vault/projects/<project>/runs/<experiment_id>/<run_id>.json``.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import urllib.parse
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from labit.models import ComputeProfile
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService
from labit.services.project_service import ProjectService


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass
class ExperimentManifest:
    """Parsed manifest.yaml for an experiment directory."""

    experiment_id: str
    name: str
    description: str = ""
    profile: str = ""
    tags: list[str] = field(default_factory=list)


_DEFAULT_RESULTS_EXCLUDE = [
    "checkpoints/",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.safetensors",
    "wandb/",
    "__pycache__/",
]


@dataclass
class SyncManifest:
    """Tracks the state of log/result sync from remote to local."""

    logs_synced_at: str = ""
    results_synced_at: str = ""
    last_sync_status: str = ""  # ok | failed
    last_sync_error: str = ""
    files_synced: int = 0
    bytes_synced: int = 0
    excluded_patterns: list[str] = field(default_factory=list)
    artifact_sources: list[str] = field(default_factory=list)  # non-empty if fallback to artifact dirs

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncManifest:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class RunRecord:
    """Persisted state of a single experiment run."""

    run_id: str
    experiment_id: str
    name: str
    profile: str
    script: str
    command: str
    status: str  # pending | syncing | running | completed | failed | stopped
    pid: int | None = None
    exit_code: int | None = None
    remote_workdir: str = ""
    remote_run_dir: str = ""
    local_commit: str = ""
    dirty: bool = False
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    notes: str = ""
    error: str = ""
    sync: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ExperimentService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        project_service: ProjectService | None = None,
        compute_service: ComputeService | None = None,
    ):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)
        self.compute_service = compute_service or ComputeService(
            paths, project_service=self.project_service
        )
        self._last_refresh_all: dict[str, float] = {}  # project -> timestamp

    # ── discovery ────────────────────────────────────────────────────

    def _experiments_dir(self, project: str) -> Path:
        return self.project_service.project_code_dir(project) / "experiments"

    def _runs_dir(self, project: str) -> Path:
        return self.project_service.project_dir(project) / "runs"

    def list_experiments(self, project: str) -> list[ExperimentManifest]:
        exp_dir = self._experiments_dir(project)
        if not exp_dir.exists():
            return []
        results: list[ExperimentManifest] = []
        for child in sorted(exp_dir.iterdir()):
            if not child.is_dir():
                continue
            if not _is_safe_id(child.name):
                continue
            manifest_path = child / "manifest.yaml"
            run_sh = child / "run.sh"
            if not manifest_path.exists() or not run_sh.exists():
                continue
            try:
                raw = yaml.safe_load(manifest_path.read_text()) or {}
                results.append(
                    ExperimentManifest(
                        experiment_id=child.name,
                        name=str(raw.get("name", child.name)),
                        description=str(raw.get("description", "")),
                        profile=str(raw.get("profile", "")),
                        tags=_normalize_tags(raw.get("tags", [])),
                    )
                )
            except Exception:
                continue
        return results

    def get_experiment(self, project: str, experiment_id: str) -> ExperimentManifest:
        if not _is_safe_id(experiment_id):
            raise FileNotFoundError(
                f"Experiment '{experiment_id}' not found in project '{project}'."
            )
        exp_dir = self._experiments_dir(project) / experiment_id
        manifest_path = exp_dir / "manifest.yaml"
        run_sh = exp_dir / "run.sh"
        if not manifest_path.exists() or not run_sh.exists():
            raise FileNotFoundError(
                f"Experiment '{experiment_id}' not found in project '{project}'."
            )
        raw = yaml.safe_load(manifest_path.read_text()) or {}
        return ExperimentManifest(
            experiment_id=experiment_id,
            name=str(raw.get("name", experiment_id)),
            description=str(raw.get("description", "")),
            profile=str(raw.get("profile", "")),
            tags=_normalize_tags(raw.get("tags", [])),
        )

    # ── run records ──────────────────────────────────────────────────

    def _experiment_runs_dir(self, project: str, experiment_id: str) -> Path:
        return self._runs_dir(project) / experiment_id

    def _save_run(self, project: str, record: RunRecord) -> None:
        run_dir = self._experiment_runs_dir(project, record.experiment_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{record.run_id}.json"
        path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))

    def _load_run(self, project: str, experiment_id: str, run_id: str) -> RunRecord:
        path = self._experiment_runs_dir(project, experiment_id) / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Run '{run_id}' not found.")
        return RunRecord.from_dict(json.loads(path.read_text()))

    def list_runs(self, project: str, experiment_id: str) -> list[RunRecord]:
        run_dir = self._experiment_runs_dir(project, experiment_id)
        if not run_dir.exists():
            return []
        records: list[RunRecord] = []
        for path in sorted(run_dir.glob("*.json"), reverse=True):
            try:
                records.append(RunRecord.from_dict(json.loads(path.read_text())))
            except Exception:
                continue
        return records

    def latest_run(self, project: str, experiment_id: str) -> RunRecord | None:
        runs = self.list_runs(project, experiment_id)
        return runs[0] if runs else None

    # ── launch ───────────────────────────────────────────────────────

    def launch(
        self,
        project: str,
        experiment_id: str,
        *,
        profile_override: str | None = None,
        notes: str = "",
    ) -> RunRecord:
        experiment = self.get_experiment(project, experiment_id)
        profile_name = profile_override or experiment.profile
        if not profile_name:
            raise ValueError(
                f"No compute profile specified for experiment '{experiment_id}'. "
                "Set 'profile' in manifest.yaml or pass profile_override."
            )

        profile = self.compute_service.get_profile(project, profile_name)

        # Generate run ID
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        run_id = f"run_{ts}"

        script_rel = f"experiments/{experiment_id}/run.sh"
        record = RunRecord(
            run_id=run_id,
            experiment_id=experiment_id,
            name=experiment.name,
            profile=profile_name,
            script=script_rel,
            command=f"bash {script_rel}",
            status="syncing",
            remote_workdir=profile.workdir,
            notes=notes,
            created_at=_now_iso(),
        )

        # Record local git state
        code_dir = self.project_service.project_code_dir(project)
        record.local_commit = self._git_head(code_dir)
        record.dirty = self._git_dirty(code_dir)

        self._save_run(project, record)

        # Step 1: sync code
        try:
            sync_result = self.compute_service.sync_code(project, profile_name)
            if not sync_result.success:
                record.status = "failed"
                record.error = f"Sync failed: {sync_result.stderr}"
                record.finished_at = _now_iso()
                self._save_run(project, record)
                return record
        except Exception as exc:
            record.status = "failed"
            record.error = f"Sync error: {exc}"
            record.finished_at = _now_iso()
            self._save_run(project, record)
            return record

        # Step 2: launch on remote
        workdir = profile.workdir.replace("\uff5e", "~")
        remote_run_dir = f".labit/runs/{run_id}"
        record.remote_run_dir = remote_run_dir

        # Build the remote launch command
        workdir_q = _quote_remote_path(workdir)
        remote_run_dir_q = shlex.quote(remote_run_dir)
        script_rel_q = shlex.quote(script_rel)
        # Resolve cache directory (absolute or relative to workdir)
        cache_dir = profile.cache_dir or ".cache"
        if not cache_dir.startswith("/"):
            cache_abs = f"{workdir}/{cache_dir}"
        else:
            cache_abs = cache_dir
        cache_abs_q = shlex.quote(cache_abs)

        inner_cmd = (
            f"export LABIT_RUN_DIR={shlex.quote(remote_run_dir)}; "
            f"export LABIT_RESULTS_DIR={shlex.quote(remote_run_dir + '/results')}; "
            f"export LABIT_CACHE_DIR={cache_abs_q}; "
            f"export HF_HOME={cache_abs_q}/huggingface; "
            f"export HF_HUB_CACHE={cache_abs_q}/huggingface/hub; "
            f"export TRANSFORMERS_CACHE={cache_abs_q}/huggingface/transformers; "
            f"export TORCH_HOME={cache_abs_q}/torch; "
            f"mkdir -p -- $LABIT_RESULTS_DIR $HF_HOME $TORCH_HOME; "
            f"(bash {script_rel_q}; echo $? > {shlex.quote(remote_run_dir + '/exit_code')}) "
            f"> {shlex.quote(remote_run_dir + '/stdout.log')} "
            f"2> {shlex.quote(remote_run_dir + '/stderr.log')}"
        )
        remote_cmd = (
            f"cd -- {workdir_q} && "
            f"mkdir -p -- {remote_run_dir_q} && "
            f"setsid bash -c {shlex.quote(inner_cmd)} "
            f"> /dev/null 2>&1 < /dev/null & "
            f"echo $!"
        )

        try:
            ssh_cmd = profile.ssh_command()
            result = subprocess.run(
                [*ssh_cmd, remote_cmd],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                record.status = "failed"
                record.error = f"SSH launch failed: {result.stderr.strip()}"
                record.finished_at = _now_iso()
                self._save_run(project, record)
                return record

            pid_str = result.stdout.strip().split("\n")[-1]
            record.pid = int(pid_str)
            record.status = "running"
            record.started_at = _now_iso()
        except Exception as exc:
            record.status = "failed"
            record.error = f"Launch error: {exc}"
            record.finished_at = _now_iso()

        self._save_run(project, record)
        return record

    # ── manual status override ────────────────────────────────────────

    def update_status(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        status: str,
        exit_code: int | None = None,
    ) -> "RunRecord":
        """Manually override a run's status (e.g. when remote is unreachable)."""
        record = self._load_run(project, experiment_id, run_id)
        record.status = status
        if exit_code is not None:
            record.exit_code = exit_code
        record.error = ""
        if not record.finished_at:
            record.finished_at = _now_iso()
        self._save_run(project, record)
        return record

    # ── monitoring ───────────────────────────────────────────────────

    def refresh_status(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        ssh_timeout: int = 10,
        sync_logs_on_finish: bool = True,
    ) -> RunRecord:
        record = self._load_run(project, experiment_id, run_id)

        try:
            profile = self.compute_service.get_profile(project, record.profile)
        except FileNotFoundError:
            # Profile was deleted — can't check remote, return cached status
            return record
        ssh_cmd = profile.ssh_command()

        # If launch failed with no PID (e.g. SSH timeout), try to recover
        # by checking the remote exit_code file — the process may have
        # actually started despite the local timeout.
        if record.status == "failed" and record.pid is None and record.remote_run_dir:
            workdir = record.remote_workdir.replace("\uff5e", "~")
            recover_cmd = (
                f"cd -- {_quote_remote_path(workdir)} && "
                f"cat -- {shlex.quote(record.remote_run_dir + '/exit_code')} "
                "2>/dev/null || echo unknown"
            )
            try:
                r = subprocess.run(
                    [*ssh_cmd, recover_cmd],
                    text=True, capture_output=True, timeout=ssh_timeout, check=False,
                )
                exit_text = r.stdout.strip()
                if exit_text.isdigit():
                    record.exit_code = int(exit_text)
                    record.status = "completed" if record.exit_code == 0 else "failed"
                    record.error = ""
                    record.finished_at = _now_iso()
                    self._save_run(project, record)

                    # Auto-sync logs after recovery
                    if sync_logs_on_finish:
                        try:
                            self.sync_logs(project, experiment_id, run_id)
                            record = self._load_run(project, experiment_id, run_id)
                        except Exception:
                            pass
                elif exit_text != "unknown":
                    # exit_code file exists but content is unexpected
                    pass
                # If "unknown", remote run dir may not exist — keep current status
            except Exception:
                pass
            return record

        if record.status not in ("running",):
            return record

        # Check if PID is alive and read exit_code in a single SSH call
        workdir = record.remote_workdir.replace("\uff5e", "~")
        combined_cmd = (
            f"kill -0 {record.pid} 2>/dev/null && echo alive || "
            f"(echo dead && cd -- {_quote_remote_path(workdir)} && "
            f"cat -- {shlex.quote(record.remote_run_dir + '/exit_code')} "
            "2>/dev/null || echo unknown)"
        )
        try:
            result = subprocess.run(
                [*ssh_cmd, combined_cmd],
                text=True, capture_output=True, timeout=ssh_timeout, check=False,
            )
            lines = result.stdout.strip().split("\n")
            status_text = lines[0].strip() if lines else "alive"
        except Exception:
            return record

        if status_text == "dead":
            exit_text = lines[1].strip() if len(lines) > 1 else "unknown"
            if exit_text.isdigit():
                record.exit_code = int(exit_text)
                record.status = "completed" if record.exit_code == 0 else "failed"
            else:
                record.status = "failed"
                record.exit_code = -1
            record.finished_at = _now_iso()
            self._save_run(project, record)

            # Auto-sync logs on completion
            if sync_logs_on_finish:
                try:
                    self.sync_logs(project, experiment_id, run_id)
                    record = self._load_run(project, experiment_id, run_id)
                except Exception:
                    pass  # Best-effort; don't fail refresh because of sync

        return record

    def refresh_all_running(self, project: str) -> None:
        """Refresh status of all running runs in the project, concurrently.

        Throttled to run at most once every 15 seconds per project.
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        now = time.monotonic()
        last = self._last_refresh_all.get(project, 0.0)
        if now - last < 15:
            return

        # Collect all running runs across all experiments
        running_runs: list[tuple[str, str]] = []  # (experiment_id, run_id)
        runs_dir = self._runs_dir(project)
        if not runs_dir.exists():
            return
        for exp_dir in runs_dir.iterdir():
            if not exp_dir.is_dir():
                continue
            for run_file in exp_dir.glob("*.json"):
                try:
                    data = json.loads(run_file.read_text())
                    if data.get("status") == "running":
                        running_runs.append((data["experiment_id"], data["run_id"]))
                except Exception:
                    continue

        if not running_runs:
            return

        self._last_refresh_all[project] = now
        running_runs = running_runs[:4]

        def _refresh_one(exp_id: str, run_id: str) -> None:
            try:
                self.refresh_status(
                    project,
                    exp_id,
                    run_id,
                    ssh_timeout=8,
                    sync_logs_on_finish=False,
                )
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=min(len(running_runs), 4)) as pool:
            futures = {
                pool.submit(_refresh_one, eid, rid): (eid, rid)
                for eid, rid in running_runs
            }
            for future in as_completed(futures, timeout=12):
                try:
                    future.result()
                except Exception:
                    pass

    def tail_logs(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        stream: str = "stdout",
        tail: int = 200,
    ) -> str:
        if stream not in {"stdout", "stderr"}:
            raise ValueError("stream must be 'stdout' or 'stderr'.")
        tail = max(1, min(int(tail), 10000))
        record = self._load_run(project, experiment_id, run_id)
        if not record.remote_run_dir:
            return _empty_log_message(stream)

        # For terminal runs (completed/failed/stopped), prefer local cache
        local_log = self._local_run_data_dir(
            project, experiment_id, run_id
        ) / f"{stream}.log"

        if record.status in ("completed", "failed", "stopped") and local_log.exists():
            return _tail_local(local_log, tail)

        # Try remote SSH — profile may have been deleted
        try:
            profile = self.compute_service.get_profile(project, record.profile)
        except FileNotFoundError:
            if local_log.exists():
                return _tail_local(local_log, tail)
            return _empty_log_message(stream)
        ssh_cmd = profile.ssh_command()
        workdir = record.remote_workdir.replace("\uff5e", "~")
        log_file = f"{record.remote_run_dir}/{stream}.log"

        try:
            result = subprocess.run(
                [
                    *ssh_cmd,
                    f"cd -- {_quote_remote_path(workdir)} && "
                    f"if [ -f {shlex.quote(log_file)} ]; then "
                    f"tail -n {tail} -- {shlex.quote(log_file)}; "
                    f"else echo {shlex.quote(_empty_log_message(stream))}; fi",
                ],
                text=True, capture_output=True, timeout=15, check=False,
            )
            if result.returncode != 0:
                # Remote failed — fall back to local cache
                if local_log.exists():
                    return _tail_local(local_log, tail)
                return "[Log unavailable: could not connect to the remote machine or run directory.]"
            return result.stdout
        except subprocess.TimeoutExpired:
            if local_log.exists():
                return _tail_local(local_log, tail)
            return "[Log unavailable: SSH connection timed out. The remote machine may be unreachable.]"
        except Exception:
            if local_log.exists():
                return _tail_local(local_log, tail)
            return "[Log unavailable: could not connect to remote machine.]"

    def fetch_events(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
    ) -> dict:
        """Fetch stdout.log, parse structured __labit__ JSONL events."""
        raw, total_lines = self._fetch_structured_stdout(project, experiment_id, run_id)
        events, _ = parse_log(raw)
        plain_count = max(0, total_lines - len(events))
        return {
            "events": events,
            "plain_lines": plain_count,
            "total_lines": total_lines,
        }

    def fetch_metrics(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
    ) -> dict:
        """Fetch stdout.log, extract metric events, return grouped time series."""
        raw, _ = self._fetch_structured_stdout(project, experiment_id, run_id)
        events, _ = parse_log(raw)

        metrics: dict[str, list[dict]] = {}
        steps: list[int] = []
        seen_steps: set[int] = set()

        for ev in events:
            if ev.get("type") != "metric":
                continue
            step = ev.get("step")
            if step is None:
                continue
            data = ev.get("data", {})
            if not isinstance(data, dict):
                continue
            if step not in seen_steps:
                seen_steps.add(step)
                steps.append(step)
            for key, value in data.items():
                if isinstance(value, (int, float)):
                    metrics.setdefault(key, []).append(
                        {"step": step, "value": value}
                    )

        steps.sort()
        return {"metrics": metrics, "steps": steps}

    def _fetch_structured_stdout(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
    ) -> tuple[str, int]:
        """Return all Labit event lines from stdout.log plus the full line count.

        For terminal runs with synced logs, reads from local cache.
        Otherwise reads from remote via SSH.
        """
        record = self._load_run(project, experiment_id, run_id)

        # For terminal runs, prefer local cache
        local_log = self._local_run_data_dir(
            project, experiment_id, run_id
        ) / "stdout.log"

        if record.status in ("completed", "failed", "stopped") and local_log.exists():
            return _parse_structured_from_local(local_log)

        # Can't reach remote if run never started or profile is gone
        if not record.remote_run_dir:
            if local_log.exists():
                return _parse_structured_from_local(local_log)
            return "", 0

        try:
            profile = self.compute_service.get_profile(project, record.profile)
        except FileNotFoundError:
            if local_log.exists():
                return _parse_structured_from_local(local_log)
            return "", 0

        # Remote path
        ssh_cmd = profile.ssh_command()
        workdir = record.remote_workdir.replace("\uff5e", "~")
        log_file = f"{record.remote_run_dir}/stdout.log"
        marker = "__LABIT_TOTAL_LINES__"
        remote_cmd = (
            f"cd -- {_quote_remote_path(workdir)} && "
            f"total=$(wc -l < {shlex.quote(log_file)} 2>/dev/null || echo 0); "
            f"printf '{marker}%s\\n' \"$total\"; "
            f"grep -F '\"__labit__\"' -- {shlex.quote(log_file)} 2>/dev/null || true"
        )

        try:
            result = subprocess.run(
                [*ssh_cmd, remote_cmd],
                text=True, capture_output=True, timeout=15, check=False,
            )
        except Exception:
            # Fall back to local cache
            if local_log.exists():
                return _parse_structured_from_local(local_log)
            return "", 0

        if result.returncode != 0 and local_log.exists():
            return _parse_structured_from_local(local_log)

        lines = result.stdout.splitlines()
        total_lines = 0
        event_lines = lines
        if lines and lines[0].startswith(marker):
            raw_total = lines[0][len(marker):].strip()
            if raw_total.isdigit():
                total_lines = int(raw_total)
            event_lines = lines[1:]
        return "\n".join(event_lines), total_lines

    def stop_run(self, project: str, experiment_id: str, run_id: str) -> RunRecord:
        record = self._load_run(project, experiment_id, run_id)
        if record.status != "running" or record.pid is None:
            return record

        # Mark stopped immediately so the UI reflects the intent even if
        # the remote kill is slow, the SSH connection hangs, or the
        # compute profile has been deleted.
        record.status = "stopped"
        record.finished_at = _now_iso()
        self._save_run(project, record)

        # Best-effort remote kill — profile may have been deleted/renamed.
        try:
            profile = self.compute_service.get_profile(project, record.profile)
            ssh_cmd = profile.ssh_command()
            subprocess.run(
                [
                    *ssh_cmd,
                    f"kill -TERM -- -{record.pid} 2>/dev/null || "
                    f"kill -TERM -- {record.pid} 2>/dev/null; "
                    "sleep 1; "
                    f"kill -KILL -- -{record.pid} 2>/dev/null || "
                    f"kill -KILL -- {record.pid} 2>/dev/null; "
                    "true",
                ],
                text=True, capture_output=True, timeout=15, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # Profile missing or remote kill timed out
        except Exception:
            pass

        # Best-effort sync logs after stop
        try:
            self.sync_logs(project, experiment_id, run_id)
            record = self._load_run(project, experiment_id, run_id)
        except Exception:
            pass

        return record

    # ── log sync ─────────────────────────────────────────────────────

    def _local_run_data_dir(self, project: str, experiment_id: str, run_id: str) -> Path:
        """Local directory for synced run data (logs, results)."""
        return self._experiment_runs_dir(project, experiment_id) / run_id

    def sync_logs(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
    ) -> SyncManifest:
        """Pull stdout.log, stderr.log, and exit_code from remote to local."""
        record = self._load_run(project, experiment_id, run_id)
        if not record.remote_run_dir:
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            manifest = SyncManifest(
                logs_synced_at=prev.logs_synced_at,
                results_synced_at=prev.results_synced_at,
                last_sync_status="failed",
                last_sync_error="No remote run directory recorded.",
                excluded_patterns=prev.excluded_patterns,
            )
            record.sync = manifest.to_dict()
            self._save_run(project, record)
            return manifest

        profile = self.compute_service.get_profile(project, record.profile)
        ssh_cmd = profile.ssh_command()
        workdir = record.remote_workdir.replace("\uff5e", "~")

        local_dir = self._local_run_data_dir(project, experiment_id, run_id)
        local_dir.mkdir(parents=True, exist_ok=True)

        # Build rsync command to pull only log files
        remote_source = f"{profile.connection.target}:{workdir}/{record.remote_run_dir}/"

        cmd = ["rsync", "-az"]
        # Only include log-related files
        cmd.extend(["--include", "stdout.log"])
        cmd.extend(["--include", "stderr.log"])
        cmd.extend(["--include", "exit_code"])
        cmd.extend(["--exclude", "*"])

        # SSH options
        ssh_parts = ["ssh"]
        if profile.connection.identity_file:
            ssh_parts.extend(["-i", str(Path(profile.connection.identity_file).expanduser())])
        if profile.connection.port != 22:
            ssh_parts.extend(["-p", str(profile.connection.port)])
        cmd.extend(["-e", shlex.join(ssh_parts)])

        cmd.append(remote_source)
        cmd.append(str(local_dir) + "/")

        try:
            result = subprocess.run(
                cmd, text=True, capture_output=True, timeout=60, check=False,
            )
            # Preserve prior results sync state
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            if result.returncode == 0:
                # Count synced files and bytes
                files_synced = sum(
                    1 for f in ["stdout.log", "stderr.log", "exit_code"]
                    if (local_dir / f).exists()
                )
                bytes_synced = sum(
                    (local_dir / f).stat().st_size
                    for f in ["stdout.log", "stderr.log", "exit_code"]
                    if (local_dir / f).exists()
                )
                manifest = SyncManifest(
                    logs_synced_at=_now_iso(),
                    results_synced_at=prev.results_synced_at,
                    last_sync_status="ok",
                    files_synced=files_synced,
                    bytes_synced=bytes_synced,
                    excluded_patterns=prev.excluded_patterns,
                )
            else:
                manifest = SyncManifest(
                    logs_synced_at=prev.logs_synced_at,
                    results_synced_at=prev.results_synced_at,
                    last_sync_status="failed",
                    last_sync_error=result.stderr.strip()[:500],
                    excluded_patterns=prev.excluded_patterns,
                )
        except subprocess.TimeoutExpired:
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            manifest = SyncManifest(
                logs_synced_at=prev.logs_synced_at,
                results_synced_at=prev.results_synced_at,
                last_sync_status="failed",
                last_sync_error="Rsync timed out after 60 seconds.",
                excluded_patterns=prev.excluded_patterns,
            )
        except Exception as exc:
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            manifest = SyncManifest(
                logs_synced_at=prev.logs_synced_at,
                results_synced_at=prev.results_synced_at,
                last_sync_status="failed",
                last_sync_error=str(exc)[:500],
                excluded_patterns=prev.excluded_patterns,
            )

        # Persist sync state on the run record
        record.sync = manifest.to_dict()
        self._save_run(project, record)

        return manifest

    def sync_results(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        exclude_patterns: list[str] | None = None,
    ) -> SyncManifest:
        """Pull results/ directory from remote to local.

        Default excludes large files (checkpoints, .pt, wandb, etc.).
        ``exclude_patterns`` are *appended* to defaults, not replacing them.
        """
        record = self._load_run(project, experiment_id, run_id)
        if not record.remote_run_dir:
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            manifest = SyncManifest(
                logs_synced_at=prev.logs_synced_at,
                results_synced_at=prev.results_synced_at,
                last_sync_status="failed",
                last_sync_error="No remote run directory recorded.",
                excluded_patterns=prev.excluded_patterns,
            )
            record.sync = manifest.to_dict()
            self._save_run(project, record)
            return manifest

        profile = self.compute_service.get_profile(project, record.profile)
        workdir = record.remote_workdir.replace("\uff5e", "~")

        local_results = self._local_run_data_dir(project, experiment_id, run_id) / "results"
        local_results.mkdir(parents=True, exist_ok=True)

        # Merge exclude patterns: defaults + user-supplied
        excludes = list(_DEFAULT_RESULTS_EXCLUDE)
        if exclude_patterns:
            excludes.extend(exclude_patterns)

        remote_source = f"{profile.connection.target}:{workdir}/{record.remote_run_dir}/results/"

        cmd = ["rsync", "-az", "--stats"]
        for pat in excludes:
            cmd.extend(["--exclude", pat])

        # SSH options
        ssh_parts = ["ssh"]
        if profile.connection.identity_file:
            ssh_parts.extend(["-i", str(Path(profile.connection.identity_file).expanduser())])
        if profile.connection.port != 22:
            ssh_parts.extend(["-p", str(profile.connection.port)])
        cmd.extend(["-e", shlex.join(ssh_parts)])

        cmd.append(remote_source)
        cmd.append(str(local_results) + "/")

        try:
            result = subprocess.run(
                cmd, text=True, capture_output=True, timeout=300, check=False,
            )
            if result.returncode == 0:
                files_synced, bytes_synced = _parse_rsync_stats(result.stdout)
                artifact_sources: list[str] = []

                # Fallback: if standard results/ was empty, try artifact dirs
                if files_synced == 0:
                    local_log = self._local_run_data_dir(
                        project, experiment_id, run_id,
                    ) / "stdout.log"
                    artifact_dirs = _filter_artifact_dirs_for_workdir(
                        _extract_artifact_dirs(local_log),
                        workdir,
                    )
                    if artifact_dirs:
                        fb_files, fb_bytes = self._sync_artifact_dirs(
                            profile, workdir, artifact_dirs,
                            local_results, excludes,
                        )
                        files_synced += fb_files
                        bytes_synced += fb_bytes
                        artifact_sources = artifact_dirs

                prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
                manifest = SyncManifest(
                    logs_synced_at=prev.logs_synced_at,
                    results_synced_at=_now_iso(),
                    last_sync_status="ok",
                    files_synced=files_synced,
                    bytes_synced=bytes_synced,
                    excluded_patterns=excludes,
                    artifact_sources=artifact_sources,
                )
            else:
                prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
                manifest = SyncManifest(
                    logs_synced_at=prev.logs_synced_at,
                    results_synced_at=prev.results_synced_at,
                    last_sync_status="failed",
                    last_sync_error=result.stderr.strip()[:500],
                    excluded_patterns=excludes,
                )
        except subprocess.TimeoutExpired:
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            manifest = SyncManifest(
                logs_synced_at=prev.logs_synced_at,
                results_synced_at=prev.results_synced_at,
                last_sync_status="failed",
                last_sync_error="Rsync timed out after 300 seconds.",
                excluded_patterns=excludes,
            )
        except Exception as exc:
            prev = SyncManifest.from_dict(record.sync) if record.sync else SyncManifest()
            manifest = SyncManifest(
                logs_synced_at=prev.logs_synced_at,
                results_synced_at=prev.results_synced_at,
                last_sync_status="failed",
                last_sync_error=str(exc)[:500],
                excluded_patterns=excludes,
            )

        record = self._load_run(project, experiment_id, run_id)
        record.sync = manifest.to_dict()
        self._save_run(project, record)
        return manifest

    def _sync_artifact_dirs(
        self,
        profile: Any,
        workdir: str,
        artifact_dirs: list[str],
        local_results: Path,
        excludes: list[str],
    ) -> tuple[int, int]:
        """Rsync artifact directories from remote into local results/.

        Each artifact dir (e.g. /workspace/outputs/phase0_foo) is synced into
        local_results/<dirname>/.  Returns (total_files, total_bytes).
        """
        total_files = 0
        total_bytes = 0

        for remote_dir in artifact_dirs:
            dirname = Path(remote_dir).name
            local_sub = local_results / dirname
            local_sub.mkdir(parents=True, exist_ok=True)

            remote_source = f"{profile.connection.target}:{remote_dir}/"

            cmd = ["rsync", "-az", "--stats"]
            for pat in excludes:
                cmd.extend(["--exclude", pat])

            ssh_parts = ["ssh"]
            if profile.connection.identity_file:
                ssh_parts.extend(["-i", str(Path(profile.connection.identity_file).expanduser())])
            if profile.connection.port != 22:
                ssh_parts.extend(["-p", str(profile.connection.port)])
            cmd.extend(["-e", shlex.join(ssh_parts)])

            cmd.append(remote_source)
            cmd.append(str(local_sub) + "/")

            try:
                result = subprocess.run(
                    cmd, text=True, capture_output=True, timeout=300, check=False,
                )
                if result.returncode == 0:
                    f, b = _parse_rsync_stats(result.stdout)
                    total_files += f
                    total_bytes += b
            except Exception:
                pass  # best-effort per directory

        return total_files, total_bytes

    def sync_all(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        *,
        exclude_patterns: list[str] | None = None,
    ) -> SyncManifest:
        """Sync both logs and results from remote."""
        self.sync_logs(project, experiment_id, run_id)
        return self.sync_results(
            project, experiment_id, run_id,
            exclude_patterns=exclude_patterns,
        )

    # ── result browsing ─────────────────────────────────────────────

    def list_results(
        self, project: str, experiment_id: str, run_id: str,
    ) -> list[dict[str, Any]]:
        """List synced result files as a flat list with relative paths."""
        self._load_run(project, experiment_id, run_id)  # validate run exists
        results_dir = self._local_run_data_dir(project, experiment_id, run_id) / "results"
        if not results_dir.exists():
            return []
        entries: list[dict[str, Any]] = []
        for path in sorted(results_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(results_dir)
            entries.append({
                "path": str(rel),
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds"),
            })
        return entries

    def read_result_file(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        file_path: str,
    ) -> Path:
        """Return the local Path to a synced result file.

        Raises FileNotFoundError if the file doesn't exist or the path
        escapes the results directory.
        """
        self._load_run(project, experiment_id, run_id)  # validate run exists
        results_dir = self._local_run_data_dir(project, experiment_id, run_id) / "results"
        resolved = (results_dir / file_path).resolve()
        # Prevent path traversal
        if not resolved.is_relative_to(results_dir.resolve()):
            raise FileNotFoundError(f"Invalid path: {file_path}")
        if not resolved.is_file():
            raise FileNotFoundError(f"Result file not found: {file_path}")
        return resolved

    # ── result preview ─────────────────────────────────────────────

    _TEXT_EXTENSIONS = frozenset({
        ".txt", ".log", ".md", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".conf", ".sh", ".bash", ".py", ".csv", ".tsv",
    })
    _JSON_EXTENSIONS = frozenset({".json", ".jsonl"})
    _IMAGE_EXTENSIONS = frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    })
    _PREVIEW_MAX_BYTES = 512 * 1024  # 512 KB text preview cap

    def preview_result_file(
        self,
        project: str,
        experiment_id: str,
        run_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Return preview data for a synced result file.

        Returns a dict with keys: kind, path, size, content (str | None),
        truncated, download_url.
        """
        resolved = self.read_result_file(project, experiment_id, run_id, file_path)
        size = resolved.stat().st_size
        suffix = resolved.suffix.lower()

        encoded_path = "/".join(
            urllib.parse.quote(seg, safe="") for seg in file_path.split("/")
        )
        base_url = (
            f"/api/projects/{urllib.parse.quote(project, safe='')}"
            f"/experiments/{urllib.parse.quote(experiment_id, safe='')}"
            f"/runs/{urllib.parse.quote(run_id, safe='')}"
            f"/results/{encoded_path}"
        )

        if suffix in self._IMAGE_EXTENSIONS:
            return {
                "kind": "image",
                "path": file_path,
                "size": size,
                "content": None,
                "truncated": False,
                "download_url": base_url,
            }

        if suffix in self._JSON_EXTENSIONS:
            truncated = size > self._PREVIEW_MAX_BYTES
            raw = resolved.read_bytes()[:self._PREVIEW_MAX_BYTES]
            text = raw.decode("utf-8", errors="replace")
            # Try to pretty-print JSON (only full file, not truncated)
            if not truncated and suffix == ".json":
                import json as _json
                try:
                    obj = _json.loads(text)
                    text = _json.dumps(obj, indent=2, ensure_ascii=False)
                except _json.JSONDecodeError:
                    pass
            return {
                "kind": "json",
                "path": file_path,
                "size": size,
                "content": text,
                "truncated": truncated,
                "download_url": base_url,
            }

        if suffix in self._TEXT_EXTENSIONS:
            truncated = size > self._PREVIEW_MAX_BYTES
            raw = resolved.read_bytes()[:self._PREVIEW_MAX_BYTES]
            text = raw.decode("utf-8", errors="replace")
            kind = "csv" if suffix in (".csv", ".tsv") else "text"
            return {
                "kind": kind,
                "path": file_path,
                "size": size,
                "content": text,
                "truncated": truncated,
                "download_url": base_url,
            }

        # Not previewable — download only
        return {
            "kind": "download",
            "path": file_path,
            "size": size,
            "content": None,
            "truncated": False,
            "download_url": base_url,
        }

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _git_head(code_dir: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=code_dir, text=True, capture_output=True, timeout=None, check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    @staticmethod
    def _git_dirty(code_dir: Path) -> bool:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=code_dir, text=True, capture_output=True, timeout=None, check=False,
            )
            return bool(result.stdout.strip()) if result.returncode == 0 else False
        except Exception:
            return False


def _is_safe_id(value: str) -> bool:
    return bool(_SAFE_ID_RE.fullmatch(value))


def _normalize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for item in raw:
        tag = str(item).strip()
        if tag:
            tags.append(tag)
    return tags


def _empty_log_message(stream: str) -> str:
    return f"[No {stream} log output yet. The experiment may still be starting up.]"


def parse_log(raw: str) -> tuple[list[dict], int]:
    """Parse stdout.log into (structured_events, plain_line_count).

    Lines containing valid JSON with ``__labit__: true`` are extracted as
    structured events.  Everything else counts as a plain log line.
    """
    events: list[dict] = []
    plain_count = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("__labit__"):
                events.append(obj)
                continue
        except (json.JSONDecodeError, ValueError):
            pass
        plain_count += 1
    return events, plain_count


def _tail_local(path: Path, tail: int) -> str:
    """Read the last *tail* lines from a local file."""
    try:
        lines = path.read_text().splitlines()
        return "\n".join(lines[-tail:]) + ("\n" if lines else "")
    except Exception:
        return ""


def _parse_structured_from_local(path: Path) -> tuple[str, int]:
    """Extract __labit__ event lines and total line count from a local log file."""
    try:
        text = path.read_text()
    except Exception:
        return "", 0
    all_lines = text.splitlines()
    total_lines = len(all_lines)
    event_lines = [
        line for line in all_lines
        if '"__labit__"' in line
    ]
    return "\n".join(event_lines), total_lines


def _extract_artifact_dirs(stdout_path: Path) -> list[str]:
    """Parse artifact events from stdout.log and return unique parent directories.

    Artifact events look like:
        {"__labit__": true, "type": "artifact", "path": "/workspace/outputs/foo/bar.json"}

    Returns sorted list of unique parent directories (e.g. ["/workspace/outputs/foo"]).
    """
    dirs: set[str] = set()
    try:
        for line in stdout_path.read_text().splitlines():
            if '"artifact"' not in line:
                continue
            try:
                obj = json.loads(line)
                if (
                    isinstance(obj, dict)
                    and obj.get("__labit__")
                    and obj.get("type") == "artifact"
                    and obj.get("path")
                ):
                    parent = str(Path(obj["path"]).parent)
                    dirs.add(parent)
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:
        pass
    return sorted(dirs)


def _filter_artifact_dirs_for_workdir(artifact_dirs: list[str], workdir: str) -> list[str]:
    """Keep only absolute artifact dirs under the remote workdir."""
    normalized_workdir = PurePosixPath(posixpath.normpath(workdir))
    if not normalized_workdir.is_absolute():
        return []

    filtered: list[str] = []
    for artifact_dir in artifact_dirs:
        normalized_dir = PurePosixPath(posixpath.normpath(artifact_dir))
        if not normalized_dir.is_absolute():
            continue
        try:
            normalized_dir.relative_to(normalized_workdir)
        except ValueError:
            continue
        filtered.append(str(normalized_dir))
    return filtered


def _parse_rsync_stats(stdout: str) -> tuple[int, int]:
    """Extract file count and byte count from rsync --stats output."""
    files = 0
    total_bytes = 0
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("Number of regular files transferred:"):
            try:
                files = int(line.split(":")[-1].strip().replace(",", ""))
            except ValueError:
                pass
        elif line.startswith("Total transferred file size:"):
            try:
                raw = line.split(":")[-1].strip().replace(",", "")
                # Format: "12345 bytes" or just "12345"
                total_bytes = int(raw.split()[0])
            except (ValueError, IndexError):
                pass
    return files, total_bytes


def _quote_remote_path(path: str) -> str:
    """Quote a remote shell path while preserving leading ~/ expansion."""
    if path == "~":
        return "$HOME"
    if path.startswith("~/"):
        return "$HOME/" + shlex.quote(path[2:])
    return shlex.quote(path)
