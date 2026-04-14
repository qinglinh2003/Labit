from __future__ import annotations

import json

import typer
from rich.console import Console
from labit.rendering import LaTeXMarkdown as Markdown
from rich.panel import Panel

from labit.hypotheses.service import HypothesisService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

hypothesis_app = typer.Typer(help="Inspect structured project hypotheses created from LABIT sessions.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _hypothesis_service() -> HypothesisService:
    return HypothesisService(_paths())


def _project_service() -> ProjectService:
    return ProjectService(_paths())


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
                "No active project. Switch to a project before using hypothesis commands.",
                as_json=as_json,
            )
        )
    return active_project


@hypothesis_app.command("list", help="List hypotheses for the active project.")
def list_hypotheses(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    service = _hypothesis_service()
    try:
        hypotheses = service.list_hypotheses(project)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "project": project,
                "count": len(hypotheses),
                "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
            },
            as_json=True,
        )
        return

    console.print(f"[bold]Hypotheses[/bold] ({project})")
    if not hypotheses:
        console.print("[dim]No hypotheses yet. Create one from chat or paper focus with /hypothesis.[/dim]")
        return

    for item in hypotheses:
        legacy_suffix = " [dim](legacy)[/dim]" if item.legacy else ""
        source_papers = f" · papers: {', '.join(item.source_paper_ids)}" if item.source_paper_ids else ""
        result_summary = f" · result: {item.result_summary}" if item.result_summary else ""
        console.print(
            f"- [bold]{item.hypothesis_id}[/bold] [{item.state.value}/{item.resolution.value}] {item.title}{legacy_suffix}{source_papers}{result_summary}"
        )


@hypothesis_app.command("show", help="Show one hypothesis from the active project.")
def show_hypothesis(
    hypothesis_id: str = typer.Argument(..., help="Hypothesis id, for example h-1a2b3c4d."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    service = _hypothesis_service()
    try:
        detail = service.load_hypothesis(project, hypothesis_id.strip())
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "project": project,
                "hypothesis": detail.model_dump(mode="json"),
            },
            as_json=True,
        )
        return

    record = detail.record
    body = (
        f"[bold]State[/bold]: {record.state.value}\n"
        f"[bold]Resolution[/bold]: {record.resolution.value}\n"
        f"[bold]Legacy status[/bold]: {record.status.value}\n"
        f"[bold]Claim[/bold]: {record.claim}\n"
        f"[bold]Independent variable[/bold]: {record.independent_variable or '(blank)'}\n"
        f"[bold]Dependent variable[/bold]: {record.dependent_variable or '(blank)'}\n"
        f"[bold]Success criteria[/bold]: {record.success_criteria or '(blank)'}\n"
        f"[bold]Failure criteria[/bold]: {record.failure_criteria or '(blank)'}\n"
        f"[bold]Result summary[/bold]: {record.result_summary or '(blank)'}\n"
        f"[bold]Decision rationale[/bold]: {record.decision_rationale or '(blank)'}\n"
        f"[bold]Supporting experiments[/bold]: {', '.join(record.supporting_experiment_ids) or '(none)'}\n"
        f"[bold]Contradicting experiments[/bold]: {', '.join(record.contradicting_experiment_ids) or '(none)'}\n"
        f"[bold]Closed at[/bold]: {record.closed_at or '(open)'}\n"
        f"[bold]Source session[/bold]: {record.source_session_id or '(none)'}\n"
        f"[bold]Source papers[/bold]: {', '.join(record.source_paper_ids) or '(none)'}\n"
        f"[bold]Path[/bold]: {detail.path}"
    )
    console.print(
        Panel(
            body,
            title=f"[bold green]{record.hypothesis_id} · {record.title}[/bold green]",
            border_style="green",
        )
    )
    if detail.rationale_markdown:
        console.print(Panel(Markdown(detail.rationale_markdown), title="Rationale", border_style="blue"))
    if detail.experiment_plan_markdown:
        console.print(Panel(Markdown(detail.experiment_plan_markdown), title="Experiment Plan", border_style="magenta"))
