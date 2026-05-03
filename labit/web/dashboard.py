from __future__ import annotations

from collections import Counter
from typing import Any

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


def _render_overview(st: Any, project: str, experiments: list[ExperimentSummary]) -> None:
    counts = _experiment_status_counts(experiments)
    cols = st.columns(4)
    cols[0].metric("Project", project)
    cols[1].metric("Experiments", len(experiments))
    cols[2].metric("Running", counts.get("running", 0))
    cols[3].metric("Failed", counts.get("failed", 0))


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


def _render_files(st: Any, paths: RepoPaths, project: str) -> None:
    st.subheader("Backing Files")
    project_dir = paths.vault_projects_dir / project
    files = [
        project_dir / "project.yaml",
        project_dir / "todos.yaml",
        project_dir / "ideas.yaml",
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

    experiments = _load_experiments(paths, project)

    overview, experiments_tab, files_tab = st.tabs(["Overview", "Experiments", "Files"])
    with overview:
        _render_overview(st, project, experiments)
    with experiments_tab:
        _render_experiment_board(st, experiments)
    with files_tab:
        _render_files(st, paths, project)


if __name__ == "__main__":
    main()
