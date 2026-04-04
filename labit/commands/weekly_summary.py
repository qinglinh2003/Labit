from __future__ import annotations

import json
from datetime import date

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from labit.paths import RepoPaths
from labit.reports.service import WeeklySummaryService
from labit.services.project_service import ProjectService

console = Console()
weekly_summary_app = typer.Typer(help="Generate an end-of-week project summary from daily summaries and weekly LABIT activity.")


def weekly_summary_command(
    summary_date: str = typer.Option("", "--date", help="Date in YYYY-MM-DD. Uses the ISO week containing that date. Defaults to today in local timezone."),
    provider: str = typer.Option("auto", "--provider", help="Agent provider: auto, claude, or codex."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    paths = RepoPaths.discover()
    project_service = ProjectService(paths)
    project = project_service.active_project_name()
    if project is None:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": "No active project. Switch to a project before generating a weekly summary."}, indent=2))
        else:
            console.print("[bold red]Error:[/bold red] No active project. Switch to a project before generating a weekly summary.")
        raise typer.Exit(code=1)

    target_date: date | None = None
    if summary_date.strip():
        try:
            target_date = date.fromisoformat(summary_date.strip())
        except ValueError:
            if json_output:
                typer.echo(json.dumps({"ok": False, "error": "Invalid --date. Expected YYYY-MM-DD."}, indent=2))
            else:
                console.print("[bold red]Error:[/bold red] Invalid --date. Expected YYYY-MM-DD.")
            raise typer.Exit(code=1)

    service = WeeklySummaryService(paths)
    try:
        result = service.generate(project=project, target_date=target_date, provider=provider.strip() or "auto")
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "project": result.project,
                    "week_label": result.week_label,
                    "week_start": result.week_start,
                    "week_end": result.week_end,
                    "timezone": result.timezone,
                    "markdown_path": result.markdown_path,
                    "yaml_path": result.yaml_path,
                    "markdown": result.markdown,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    console.print(
        Panel(
            (
                f"[bold]Project[/bold]: {result.project}\n"
                f"[bold]Week[/bold]: {result.week_label} ({result.week_start} to {result.week_end})\n"
                f"[bold]Markdown[/bold]: {result.markdown_path}\n"
                f"[bold]Inputs[/bold]: {result.yaml_path}"
            ),
            title="[bold green]Weekly Summary Written[/bold green]",
            border_style="green",
        )
    )
    console.print("")
    console.print(Markdown(result.markdown))


weekly_summary_app.command("weekly-summary", help="Generate a project weekly summary artifact.")(weekly_summary_command)
