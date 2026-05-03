from __future__ import annotations

from collections import Counter
from typing import Any

from labit.automation.models import AutoIterationEntry, AutoSessionRecord
from labit.automation.store import AutomationStore
from labit.experiments.models import ExperimentSummary
from labit.experiments.service import ExperimentService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


def _load_streamlit():
    import streamlit as st

    return st


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_names(paths: RepoPaths) -> list[str]:
    service = ProjectService(paths)
    names = service.list_project_names()
    active = service.active_project_name()
    if active and active in names:
        names = [active, *[name for name in names if name != active]]
    return names


def _load_experiments(paths: RepoPaths, project: str) -> list[ExperimentSummary]:
    try:
        return ExperimentService(paths).list_experiments(project)
    except Exception:
        return []


def _load_auto_session(store: AutomationStore, project: str) -> AutoSessionRecord | None:
    try:
        return store.load_session(project)
    except Exception:
        return None


def _load_iterations(store: AutomationStore, project: str, limit: int = 20) -> list[AutoIterationEntry]:
    try:
        return store.recent_iterations(project, limit=limit)
    except Exception:
        return []


def _load_latest_markdown(store: AutomationStore, project: str) -> str:
    path = store.snapshot_path(project)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _experiment_status_counts(experiments: list[ExperimentSummary]) -> Counter[str]:
    return Counter(str(item.status.value if hasattr(item.status, "value") else item.status) for item in experiments)


def _task_counts(summary: ExperimentSummary) -> str:
    evidence = summary.evidence_task_count
    total = summary.task_count
    if total <= 0:
        return "0"
    if evidence:
        return f"{evidence}/{total} evidence"
    return str(total)


def _status_badge(status: str) -> str:
    colors = {
        "completed": "green",
        "failed": "red",
        "running": "blue",
        "queued": "orange",
        "planned": "gray",
        "blocked": "orange",
    }
    color = colors.get(status.lower(), "gray")
    return f":{color}[{status}]"


def _render_overview(st: Any, project: str, experiments: list[ExperimentSummary], session: AutoSessionRecord | None) -> None:
    counts = _experiment_status_counts(experiments)
    cols = st.columns(5)
    cols[0].metric("Project", project)
    cols[1].metric("Experiments", len(experiments))
    cols[2].metric("Running", counts.get("running", 0))
    cols[3].metric("Failed", counts.get("failed", 0))
    cols[4].metric("Auto", session.status.value if session else "none")

    if session:
        st.subheader("Auto Session")
        st.write(f"Supervisor: `{session.supervisor_agent}`")
        st.write(f"Iterations: `{session.current_iteration}` / `{session.max_iterations}`")
        with st.expander("Constraint", expanded=False):
            st.markdown(session.constraint or "_No constraint recorded._")
        with st.expander("Success Criteria", expanded=False):
            st.markdown(session.success_criteria or "_No success criteria recorded._")
        if session.last_decision_summary:
            st.info(session.last_decision_summary)
    else:
        st.info("No automation session found for this project.")


def _render_experiment_board(st: Any, experiments: list[ExperimentSummary]) -> None:
    st.subheader("Experiment Board")
    if not experiments:
        st.info("No finalized experiments found.")
        return

    buckets = ["planned", "queued", "running", "completed", "failed"]
    grouped: dict[str, list[ExperimentSummary]] = {status: [] for status in buckets}
    grouped["other"] = []
    for item in experiments:
        status = str(item.status.value if hasattr(item.status, "value") else item.status)
        if status in grouped:
            grouped[status].append(item)
        else:
            grouped["other"].append(item)

    columns = st.columns(len(buckets))
    for column, status in zip(columns, buckets, strict=True):
        with column:
            st.markdown(f"#### {status.title()}")
            for item in grouped.get(status, []):
                with st.container(border=True):
                    st.markdown(f"**`{item.experiment_id}`**")
                    st.caption(item.title)
                    st.write(_status_badge(status))
                    st.write(f"Tasks: `{_task_counts(item)}`")
                    st.caption(f"Updated: {item.updated_at}")

    if grouped.get("other"):
        st.markdown("#### Other")
        for item in grouped["other"]:
            st.write(f"`{item.experiment_id}` {item.title} ({item.status})")


def _entry_to_dict(entry: AutoIterationEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json")


def _render_auto_timeline(st: Any, iterations: list[AutoIterationEntry]) -> None:
    st.subheader("Auto Timeline")
    if not iterations:
        st.info("No auto iterations recorded yet.")
        return

    for entry in reversed(iterations):
        action = entry.action.value if hasattr(entry.action, "value") else str(entry.action)
        title = f"Iteration {entry.iteration} · {action} · {entry.created_at}"
        with st.expander(title, expanded=entry is iterations[-1]):
            st.markdown("**Observation**")
            st.text(entry.observation_summary)
            st.markdown("**Decision**")
            st.write(entry.decision_summary or "_No decision summary._")

            if entry.worker_tasks:
                st.markdown("**Worker Tasks**")
                for task in entry.worker_tasks:
                    st.write(f"- `{task.worker}` **{task.title}**: {task.instructions}")

            if entry.worker_results:
                st.markdown("**Worker Results**")
                for result in entry.worker_results:
                    with st.container(border=True):
                        st.write(f"`{result.worker}` · `{result.status}`")
                        st.write(result.summary)
                        if result.outputs:
                            st.caption("Outputs")
                            for output in result.outputs:
                                st.write(f"- {output}")
                        if result.follow_up:
                            st.caption(f"Follow-up: {result.follow_up}")

            if entry.discussion:
                st.markdown("**Discussion**")
                for note in entry.discussion:
                    st.write(f"- `{note.actor}`: {note.summary}")

            with st.expander("Raw JSON", expanded=False):
                st.json(_entry_to_dict(entry))


def _render_latest_snapshot(st: Any, markdown: str) -> None:
    st.subheader("Latest Snapshot")
    if not markdown:
        st.info("No latest.md snapshot found.")
        return
    st.markdown(markdown)


def _render_files(st: Any, paths: RepoPaths, project: str) -> None:
    st.subheader("Backing Files")
    project_dir = paths.vault_projects_dir / project
    files = [
        project_dir / "automation" / "session.yaml",
        project_dir / "automation" / "iterations.jsonl",
        project_dir / "automation" / "latest.md",
    ]
    for path in files:
        exists = path.exists()
        marker = "ok" if exists else "-"
        st.write(f"{marker} `{path}`")
        if exists and path.suffix in {".yaml", ".yml"}:
            with st.expander(path.name, expanded=False):
                st.code(path.read_text(encoding="utf-8"), language="yaml")
        elif exists and path.suffix == ".jsonl":
            with st.expander(path.name, expanded=False):
                lines = path.read_text(encoding="utf-8").splitlines()[-20:]
                st.code("\n".join(lines), language="json")


def main() -> None:
    st = _load_streamlit()
    st.set_page_config(page_title="LABIT Dashboard", layout="wide")
    st.title("LABIT Dashboard")

    paths = _paths()
    names = _project_names(paths)
    if not names:
        st.warning("No LABIT projects found.")
        return

    with st.sidebar:
        project = st.selectbox("Project", names)
        st.caption(f"Vault: {paths.vault_projects_dir}")
        if st.button("Refresh"):
            st.rerun()

    store = AutomationStore(paths)
    experiments = _load_experiments(paths, project)
    session = _load_auto_session(store, project)
    iterations = _load_iterations(store, project, limit=20)
    latest_markdown = _load_latest_markdown(store, project)

    overview, experiments_tab, auto_tab, files_tab = st.tabs(["Overview", "Experiments", "Auto", "Files"])
    with overview:
        _render_overview(st, project, experiments, session)
    with experiments_tab:
        _render_experiment_board(st, experiments)
    with auto_tab:
        _render_auto_timeline(st, iterations)
        _render_latest_snapshot(st, latest_markdown)
    with files_tab:
        _render_files(st, paths, project)


if __name__ == "__main__":
    main()
