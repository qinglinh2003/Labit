from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel

from labit.experiments.service import ExperimentService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

experiment_app = typer.Typer(help="Inspect structured experiments and their task graphs for the active project.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_service() -> ProjectService:
    return ProjectService(_paths())


def _experiment_service() -> ExperimentService:
    return ExperimentService(_paths())


def _emit(data: object, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    console.print(data)


def _fail(message: str, *, as_json: bool) -> int:
    if as_json:
        _emit({"ok": False, "error": message}, as_json=True)
    else:
        console.print(f"[bold red]Error:[/bold red] {message}")
    return 1


def _require_active_project(*, as_json: bool) -> str:
    active_project = _project_service().active_project_name()
    if active_project is None:
        raise typer.Exit(
            code=_fail(
                "No active project. Switch to a project before using experiment commands.",
                as_json=as_json,
            )
        )
    return active_project


@experiment_app.command("list", help="List experiments for the active project.")
def list_experiments(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    service = _experiment_service()
    try:
        experiments = service.list_experiments(project)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "project": project,
                "count": len(experiments),
                "experiments": [item.model_dump(mode="json") for item in experiments],
            },
            as_json=True,
        )
        return

    console.print(f"[bold]Experiments[/bold] ({project})")
    if not experiments:
        console.print("[dim]No experiments yet.[/dim]")
        return

    for item in experiments:
        console.print(
            f"- [bold]{item.experiment_id}[/bold] [{item.status.value}/{item.assessment.value}] "
            f"{item.title} · parent:{item.parent_id} · tasks:{item.task_count} · evidence:{item.evidence_task_count}"
        )


@experiment_app.command("show", help="Show one experiment from the active project.")
def show_experiment(
    experiment_id: str = typer.Argument(..., help="Experiment id, for example e001."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    service = _experiment_service()
    try:
        detail = service.load_experiment(project, experiment_id.strip())
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit({"project": project, "experiment": detail.model_dump(mode="json")}, as_json=True)
        return

    record = detail.record
    body = (
        f"[bold]Parent[/bold]: {record.parent_type.value}:{record.parent_id}\n"
        f"[bold]Status[/bold]: {record.status.value}\n"
        f"[bold]Assessment[/bold]: {record.assessment.value}\n"
        f"[bold]Objective[/bold]: {record.objective}\n"
        f"[bold]Execution[/bold]: {record.execution.backend.value} / {record.execution.profile}\n"
        f"[bold]Host[/bold]: {record.execution.host or '(blank)'}\n"
        f"[bold]Workdir[/bold]: {record.execution.workdir or '(blank)'}\n"
        f"[bold]Result summary[/bold]: {record.result_summary or '(blank)'}\n"
        f"[bold]Decision rationale[/bold]: {record.decision_rationale or '(blank)'}\n"
        f"[bold]Evidence tasks[/bold]: {', '.join(record.evidence_task_ids) or '(none)'}\n"
        f"[bold]Prerequisite tasks[/bold]: {', '.join(record.prerequisite_task_ids) or '(none)'}\n"
        f"[bold]Source session[/bold]: {record.source_session_id or '(none)'}\n"
        f"[bold]Source papers[/bold]: {', '.join(record.source_paper_ids) or '(none)'}\n"
        f"[bold]Path[/bold]: {detail.path}"
    )
    console.print(
        Panel(
            body,
            title=f"[bold green]{record.experiment_id} · {record.title}[/bold green]",
            border_style="green",
        )
    )
    console.print("[bold]Tasks[/bold]")
    if not detail.tasks:
        console.print("[dim]No tasks yet.[/dim]")
    else:
        for task in detail.tasks:
            launch_suffix = f" · launch:{task.latest_launch_id}" if task.latest_launch_id else ""
            console.print(
                f"- [bold]{task.task_id}[/bold] [{task.status.value}] {task.title} "
                f"· {task.task_kind.value}/{task.research_role.value}{launch_suffix}"
            )
