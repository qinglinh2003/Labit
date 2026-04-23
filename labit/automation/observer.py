from __future__ import annotations

import json
from collections import Counter

from labit.automation.models import AutoObservation, ExperimentObservation
from labit.experiments.executors.ssh import SSHExecutor
from labit.experiments.service import ExperimentService
from labit.paths import RepoPaths


class AutomationObserver:
    def __init__(self, paths: RepoPaths, experiment_service: ExperimentService):
        self.paths = paths
        self.experiment_service = experiment_service
        self.executor = SSHExecutor(paths)

    def observe(self, *, project: str, experiment_ids: list[str] | None = None) -> AutoObservation:
        summaries = self.experiment_service.list_experiments(project)
        if experiment_ids:
            wanted = {item.strip() for item in experiment_ids if item.strip()}
            summaries = [item for item in summaries if item.experiment_id in wanted]
        else:
            summaries = summaries[:8]

        experiments: list[ExperimentObservation] = []
        for summary in summaries:
            detail = self.experiment_service.load_experiment(project, summary.experiment_id)
            counts = Counter(task.status.value for task in detail.tasks)

            runtime_signals: list[str] = []
            runtime_statuses: list[str] = []
            results_available = False
            for task in detail.tasks:
                if not task.latest_launch_id:
                    continue
                latest_task = self.experiment_service.load_task(project, detail.record.experiment_id, task.task_id)
                if not latest_task.latest_launch_id:
                    continue
                artifact = self.experiment_service.load_launch_artifact(
                    project,
                    detail.record.experiment_id,
                    latest_task.latest_launch_id,
                )
                if artifact.submission and artifact.submission.accepted:
                    try:
                        collected = self.executor.collect(artifact)
                    except Exception as exc:
                        collected = {"status": "unknown", "stderr": str(exc)}
                    runtime_status = str(collected.get("status", "")).strip()
                    signal = self._signal_from_collect(collected)
                    if runtime_status:
                        runtime_statuses.append(runtime_status)
                    if signal:
                        runtime_signals.append(f"{task.task_id}: {signal}")
                    results_available = results_available or self._has_results(collected)

            if detail.record.status.value == "completed":
                results_available = True

            latest_runtime_status = self._merge_runtime_statuses(runtime_statuses)
            latest_signal = " | ".join(runtime_signals[:3])

            experiments.append(
                ExperimentObservation(
                    experiment_id=detail.record.experiment_id,
                    title=detail.record.title,
                    status=detail.record.status.value,
                    assessment=detail.record.assessment.value,
                    task_status_counts=dict(counts),
                    latest_runtime_status=latest_runtime_status,
                    latest_signal=latest_signal,
                    results_available=results_available,
                )
            )

        has_running = any(
            item.status in {"running", "approved"}
            or (item.status == "queued" and item.latest_runtime_status in {"running"})
            for item in experiments
        )
        has_failures = any(item.status == "failed" or "error" in item.latest_signal.lower() for item in experiments)
        has_results = any(item.results_available for item in experiments)
        summary = self._render_summary(project, experiments, has_running=has_running, has_failures=has_failures, has_results=has_results)
        return AutoObservation(
            project=project,
            summary=summary,
            experiments=experiments,
            has_running=has_running,
            has_failures=has_failures,
            has_results=has_results,
        )

    def _has_results(self, collected: dict) -> bool:
        files = collected.get("files") or {}
        if any(str(path).endswith("experiment_results.json") for path in files):
            return True
        if any(str(path).endswith("results.json") for path in files):
            return True
        return bool(collected.get("output_dir_exists")) and bool(collected.get("artifact_refs"))

    def _signal_from_collect(self, collected: dict) -> str:
        files = collected.get("files") or {}
        result_file = next((content for path, content in files.items() if str(path).endswith("experiment_results.json")), "")
        if result_file:
            try:
                payload = json.loads(result_file)
                conclusion = str(payload.get("conclusion", "")).strip()
                if conclusion:
                    return conclusion[:240]
            except Exception:
                pass
        log_tail = str(collected.get("log_tail", "")).strip()
        if log_tail:
            return log_tail.rsplit("\n", 1)[-1][:240]
        stderr = str(collected.get("stderr", "")).strip()
        return stderr[:240]

    def _merge_runtime_statuses(self, statuses: list[str]) -> str:
        ordered = [status for status in statuses if status]
        if not ordered:
            return ""
        if "running" in ordered:
            return "running"
        if "stopped" in ordered:
            return "stopped"
        return ordered[-1]

    def _render_summary(
        self,
        project: str,
        experiments: list[ExperimentObservation],
        *,
        has_running: bool,
        has_failures: bool,
        has_results: bool,
    ) -> str:
        if not experiments:
            return f"Project {project}: no registered experiments found."
        lines = [
            f"Project {project}: {len(experiments)} tracked experiments.",
            f"Signals: running={str(has_running).lower()}, failures={str(has_failures).lower()}, results={str(has_results).lower()}",
        ]
        for item in experiments[:5]:
            counts = ", ".join(f"{key}:{value}" for key, value in sorted(item.task_status_counts.items())) or "no tasks"
            signal = f" | signal: {item.latest_signal}" if item.latest_signal else ""
            runtime = f" | runtime: {item.latest_runtime_status}" if item.latest_runtime_status else ""
            lines.append(
                f"- {item.experiment_id} [{item.status}/{item.assessment}] tasks({counts}){runtime}{signal}"
            )
        return "\n".join(lines)
