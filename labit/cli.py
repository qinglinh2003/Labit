from __future__ import annotations

import shutil

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from labit import __version__
from labit.commands.chat import chat_app
from labit.commands.compute import compute_app
from labit.commands.daily_summary import daily_summary_app
from labit.commands.experiment import experiment_app
from labit.commands.hypothesis import hypothesis_app
from labit.commands.memory import memory_app
from labit.commands.palace import palace_app
from labit.commands.paper import paper_app
from labit.commands.project import project_app
from labit.commands.storage import storage_app
from labit.commands.sync import sync_app
from labit.commands.weekly_summary import weekly_summary_app
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

app = typer.Typer(help="LABIT: local-first control plane for research workflows.", invoke_without_command=True)
app.add_typer(project_app, name="project")
app.add_typer(compute_app, name="compute")
app.add_typer(storage_app, name="storage")
app.add_typer(paper_app, name="paper")
app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(experiment_app, name="experiment")
app.add_typer(memory_app, name="memory")
app.add_typer(palace_app, name="palace")
app.add_typer(sync_app, name="sync")
app.add_typer(chat_app, name="chat")
app.add_typer(daily_summary_app)
app.add_typer(weekly_summary_app)

console = Console()


def _project_service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _tool_status(name: str) -> str:
    return "ready" if shutil.which(name) else "missing"


def _render_home() -> None:
    paths = RepoPaths.discover()
    service = _project_service()
    active_project = service.active_project_name()
    project_summaries = service.list_project_summaries()

    console.print(
        Panel(
            "[bold]LABIT[/bold]\n"
            "[dim]Local-first research workspace for papers, discussion, hypotheses, experiments, and review.[/dim]",
            title="[bold green]Welcome[/bold green]",
            border_style="green",
        )
    )

    status = Table(title="Workspace Status", show_header=True, header_style="bold cyan")
    status.add_column("Item")
    status.add_column("Status")
    status.add_row("Repo root", str(paths.root))
    status.add_row("Active project", active_project or "(none)")
    status.add_row("Projects", str(len(project_summaries)))
    status.add_row("Claude CLI", _tool_status("claude"))
    status.add_row("Codex CLI", _tool_status("codex"))
    console.print(status)

    if not project_summaries:
        next_steps = [
            "Create your first research workspace with `labit project new`.",
            "Then open a session with `labit chat`.",
            "Use `labit setup` any time to revisit this checklist.",
        ]
    elif not active_project:
        next_steps = [
            "Choose a project with `labit project switch <name>`.",
            "Inspect available workspaces with `labit project list`.",
            "Then open a session with `labit chat`.",
        ]
    else:
        next_steps = [
            f"Continue the active project with `labit chat`.",
            f"Inspect project state with `labit project show {active_project}`.",
            "Pull in a paper with `labit paper ingest <arxiv-id-or-url>`.",
        ]

    console.print("[bold]Next Steps[/bold]")
    for step in next_steps:
        console.print(f"- {step}")


@app.command("setup", help="Show first-run setup and current LABIT status.")
def setup() -> None:
    _render_home()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the LABIT version and exit.",
        is_eager=True,
    ),
) -> None:
    """LABIT CLI."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _render_home()
        raise typer.Exit()
