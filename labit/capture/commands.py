from __future__ import annotations

from rich.panel import Panel

from labit.capture.drafter import IdeaDrafter
from labit.capture.service import CaptureService
from labit.commands.context import ChatContext, session_evidence_refs
from labit.commands.rendering import render_capture_records, render_idea_preview
from labit.context.events import SessionEventKind


def handle_capture_command(
    *,
    ctx: ChatContext,
    command: str,
    argument: str,
) -> None:
    console = ctx.console
    current_session = ctx.session
    kind_map = {
        "/idea": "idea",
        "/todo": "todo",
    }
    kind = kind_map[command]
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return

    capture_service = CaptureService(ctx.paths)
    if not argument:
        try:
            if kind == "idea":
                records = capture_service.list_ideas(current_session.project)
            else:
                records = capture_service.list_todos(current_session.project)
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
        render_capture_records(console, kind, records)
        return

    if kind == "idea":
        try:
            with console.status("[bold blue]Drafting idea from current session...[/bold blue]"):
                draft = IdeaDrafter(ctx.paths).draft_from_session(
                    session=current_session,
                    transcript=ctx.service.transcript(current_session.session_id),
                    context_snapshot=ctx.service.context_snapshot(current_session.session_id),
                    user_intent=argument,
                    provider=current_session.participants[0].provider,
                )
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return

        console.print("")
        render_idea_preview(console, draft)
        if not _confirm(console, "Save this idea?", default=True):
            console.print("[dim]Cancelled idea capture.[/dim]")
            return

        try:
            record = capture_service.save_idea(
                project=current_session.project,
                draft=draft,
                session=current_session,
                intent=argument,
            )
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
    else:
        try:
            record = capture_service.save_todo(
                project=current_session.project,
                content=argument,
                session=current_session,
            )
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return

    console.print(
        Panel(
            (
                f"[bold]Saved[/bold]: {record.title}\n"
                f"[bold]Path[/bold]: {record.path}\n"
                f"[bold]Source[/bold]: {record.source}"
            ),
            title=f"[bold green]{kind.title()} captured[/bold green]",
            border_style="green",
        )
    )
    event_kind_map = {
        "idea": SessionEventKind.ARTIFACT_IDEA_CREATED,
        "todo": SessionEventKind.ARTIFACT_TODO_CREATED,
    }
    try:
        ctx.service.record_session_event(
            session_id=current_session.session_id,
            kind=event_kind_map[kind],
            actor="labit",
            summary=f"{kind.title()} captured: {record.title}",
            payload={
                "kind": kind,
                "title": record.title,
                "path": record.path,
                "source": record.source,
                "created_at": record.created_at,
            },
            evidence_refs=session_evidence_refs(current_session) + [f"{kind}:{record.path}"],
        )
    except Exception:
        pass


def _confirm(console, prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}
