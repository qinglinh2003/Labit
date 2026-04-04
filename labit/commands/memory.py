from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel

from labit.memory.models import MemoryKind
from labit.memory.store import MemoryStore
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

memory_app = typer.Typer(help="Inspect and manage project long-term memory records.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_service() -> ProjectService:
    return ProjectService(_paths())


def _memory_store() -> MemoryStore:
    return MemoryStore(_paths())


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
                "No active project. Switch to a project before using memory commands.",
                as_json=as_json,
            )
        )
    return active_project


@memory_app.command("list", help="List long-term memory records for the active project.")
def list_memory(
    kind: str = typer.Option("", "--kind", help="Optional memory kind filter, for example discussion_takeaway."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    store = _memory_store()

    try:
        records = store.list_records(project)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    resolved_kind = kind.strip()
    if resolved_kind:
        try:
            kind_value = MemoryKind(resolved_kind)
        except ValueError:
            allowed = ", ".join(item.value for item in MemoryKind)
            raise typer.Exit(
                code=_fail(f"Unknown memory kind '{resolved_kind}'. Expected one of: {allowed}", as_json=json_output)
            )
        records = [record for record in records if record.kind == kind_value]

    if json_output:
        _emit(
            {
                "project": project,
                "count": len(records),
                "memories": [record.model_dump(mode="json") for record in records],
            },
            as_json=True,
        )
        return

    console.print(f"[bold]Memory[/bold] ({project})")
    if not records:
        console.print("[dim]No memory records yet.[/dim]")
        return

    for record in records:
        refs = f" · refs: {', '.join(record.evidence_refs[:3])}" if record.evidence_refs else ""
        console.print(
            f"- [bold]{record.memory_id}[/bold] [{record.kind.value}/{record.memory_type.value}] "
            f"{record.title} · {record.namespace.render()} · {record.confidence} · score:{record.promotion_score}{refs}"
        )


@memory_app.command("show", help="Show one long-term memory record from the active project.")
def show_memory(
    memory_id: str = typer.Argument(..., help="Memory id, for example abc123de."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    store = _memory_store()

    try:
        record = store.load_record(project, memory_id.strip())
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit({"project": project, "memory": record.model_dump(mode="json")}, as_json=True)
        return

    body = (
        f"[bold]Kind[/bold]: {record.kind.value}\n"
        f"[bold]Type[/bold]: {record.memory_type.value}\n"
        f"[bold]Status[/bold]: {record.status.value}\n"
        f"[bold]Namespace[/bold]: {record.namespace.render()}\n"
        f"[bold]Confidence[/bold]: {record.confidence}\n"
        f"[bold]Promotion score[/bold]: {record.promotion_score}\n"
        f"[bold]Promotion reasons[/bold]: {', '.join(record.promotion_reasons) or '(none)'}\n"
        f"[bold]Updated[/bold]: {record.updated_at}\n"
        f"[bold]Evidence refs[/bold]: {', '.join(record.evidence_refs) or '(none)'}\n"
        f"[bold]Source events[/bold]: {', '.join(record.source_event_ids) or '(none)'}\n"
        f"[bold]Source artifacts[/bold]: {', '.join(record.source_artifact_refs) or '(none)'}\n\n"
        f"{record.summary}"
    )
    console.print(
        Panel(
            body,
            title=f"[bold green]{record.memory_id} · {record.title}[/bold green]",
            border_style="green",
        )
    )


@memory_app.command("delete", help="Delete one long-term memory record from the active project.")
def delete_memory(
    memory_id: str = typer.Argument(..., help="Memory id, for example abc123de."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Delete without confirmation."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _require_active_project(as_json=json_output)
    store = _memory_store()

    try:
        record = store.load_record(project, memory_id.strip())
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if not yes and not json_output:
        confirmed = typer.confirm(
            f"Delete memory '{record.memory_id}' ({record.kind.value}: {record.title})?",
            default=False,
        )
        if not confirmed:
            console.print("[dim]Cancelled.[/dim]")
            return

    try:
        deleted_path = store.delete_record(project, record.memory_id)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "ok": True,
                "project": project,
                "deleted": {
                    "memory_id": record.memory_id,
                    "title": record.title,
                    "kind": record.kind.value,
                    "path": str(deleted_path.relative_to(_paths().root)),
                },
            },
            as_json=True,
        )
        return

    console.print("[bold green]Memory deleted[/bold green]")
    console.print(f"- id: {record.memory_id}")
    console.print(f"- kind: {record.kind.value}")
    console.print(f"- title: {record.title}")
