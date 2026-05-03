from __future__ import annotations

from labit.commands.context import ChatContext
from labit.commands.rendering import render_memory_detail, render_memory_records
from labit.memory.models import MemoryKind
from labit.memory.store import MemoryStore


def handle_memory_command(
    *,
    ctx: ChatContext,
    argument: str,
) -> None:
    console = ctx.console
    current_session = ctx.session
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return

    store = MemoryStore(ctx.paths)
    try:
        if not argument:
            records = store.list_records(current_session.project)[:10]
            render_memory_records(console, records)
            return
        token = argument.strip()
        try:
            kind = MemoryKind(token)
        except ValueError:
            kind = None
        if kind is not None:
            records = [record for record in store.list_records(current_session.project) if record.kind == kind][:10]
            render_memory_records(console, records)
            return
        record = store.load_record(current_session.project, token)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return
    render_memory_detail(console, record)
