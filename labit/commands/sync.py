from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from labit.paths import RepoPaths
from labit.services.project_service import ProjectService
from labit.services.storage_service import StorageService
from labit.sync.models import SyncTransferEntry
from labit.sync.service import SyncService

sync_app = typer.Typer(
    help="Sync configured project directories between the compute node and the project's storage profile.",
    invoke_without_command=True,
)
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_service() -> ProjectService:
    return ProjectService(_paths())


def _sync_service() -> SyncService:
    return SyncService(_paths())


def _storage_service() -> StorageService:
    return StorageService(_paths())


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
                "No active project. Switch to a project before using sync commands.",
                as_json=as_json,
            )
        )
    return active_project


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "(unknown)"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(value)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.1f} {units[idx]}"


def _render_status(project: str, entries, *, storage_label: str) -> None:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Dir", style="bold")
    table.add_column("Compute")
    table.add_column(storage_label)
    for entry in entries:
        compute_text = (
            f"{_format_bytes(entry.compute.bytes)} · {entry.compute.count or 0} files"
            if not entry.compute.error
            else f"error: {entry.compute.error}"
        )
        remote_text = (
            f"{_format_bytes(entry.remote.bytes)} · {entry.remote.count or 0} files"
            if not entry.remote.error
            else f"error: {entry.remote.error}"
        )
        table.add_row(entry.dir_name, compute_text, remote_text)
    console.print(Panel(table, title=f"[bold green]Sync Status · {project}[/bold green]", border_style="green"))


def _render_transfer(project: str, entries: list[SyncTransferEntry], *, title: str) -> None:
    console.print(f"[bold]{title}[/bold] ({project})")
    for item in entries:
        color = "green" if item.ok else "red"
        body = (
            f"[bold]Direction[/bold]: {item.direction.value}\n"
            f"[bold]Compute[/bold]: {item.compute_path}\n"
            f"[bold]Remote[/bold]: {item.remote_path}\n"
            f"[bold]Exit[/bold]: {item.exit_code if item.exit_code is not None else '(none)'}\n"
            f"[bold]stdout[/bold]: {item.stdout_tail or '(blank)'}\n"
            f"[bold]stderr[/bold]: {item.stderr_tail or '(blank)'}"
        )
        console.print(Panel(body, title=f"[bold {color}]{item.dir_name}[/bold {color}]", border_style=color))


@sync_app.callback(invoke_without_command=True)
def sync_root(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    project = _require_active_project(as_json=json_output)
    service = _sync_service()
    project_spec = _project_service().load_project(project)
    storage = _storage_service().load_storage(project_spec.storage_profile)
    try:
        project_spec = _project_service().load_project(project)
        storage = _storage_service().load_storage(project_spec.storage_profile)
        entries = service.status(project)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "project": project,
                "storage_profile": storage.name,
                "storage_backend": storage.backend.value,
                "entries": [item.model_dump(mode="json") for item in entries],
            },
            as_json=True,
        )
        return
    _render_status(project, entries, storage_label=storage.name)


@sync_app.command("push", help="Copy configured sync_dirs from compute to the project's storage profile.")
def push(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    service = _sync_service()
    try:
        entries = service.push(project)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(
            {
                "project": project,
                "direction": "push",
                "entries": [item.model_dump(mode="json") for item in entries],
            },
            as_json=True,
        )
        return
    _render_transfer(project, entries, title="Sync Push")


@sync_app.command("pull", help="Copy configured sync_dirs from the project's storage profile back to compute.")
def pull(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    service = _sync_service()
    try:
        entries = service.pull(project)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(
            {
                "project": project,
                "direction": "pull",
                "entries": [item.model_dump(mode="json") for item in entries],
            },
            as_json=True,
        )
        return
    _render_transfer(project, entries, title="Sync Pull")
