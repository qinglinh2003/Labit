from __future__ import annotations

import json

import typer
from rich.console import Console

from labit.chat.models import ChatMode, ContextBinding
from labit.chat.service import ChatService
from labit.commands.chat import run_chat_shell
from labit.paths import RepoPaths
from labit.papers.service import PaperService
from labit.services.project_service import ProjectService

focus_app = typer.Typer(help="Open paper-focused conversation sessions on top of LABIT chat.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _chat_service() -> ChatService:
    return ChatService(_paths())


def _project_service() -> ProjectService:
    return ProjectService(_paths())


def _paper_service() -> PaperService:
    return PaperService(_paths())


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
                "No active project. Switch to a project before using focus.",
                as_json=as_json,
            )
        )
    return active_project


def _focus_binding(paper_id: str) -> ContextBinding:
    return ContextBinding(provider="paper_focus", config={"paper_id": paper_id})


def _session_matches_paper(session, *, project: str, paper_id: str) -> bool:
    if session.project != project:
        return False
    for binding in session.context_bindings:
        if binding.provider != "paper_focus":
            continue
        if str(binding.config.get("paper_id", "")).strip() == paper_id:
            return True
    return False


def _find_existing_focus_session(service: ChatService, *, project: str, paper_id: str):
    for session in service.list_sessions():
        if _session_matches_paper(session, project=project, paper_id=paper_id):
            return session
    return None


def _open_focus_session(
    *,
    paper_id: str,
    mode: ChatMode,
    provider: str,
    second_provider: str,
    force_new: bool,
    as_json: bool,
):
    project = _require_active_project(as_json=as_json)
    paper_service = _paper_service()
    service = _chat_service()

    global_record = paper_service.load_global_record(paper_id)

    if not force_new:
        existing = _find_existing_focus_session(service, project=project, paper_id=paper_id)
        if existing is not None:
            return existing, False, global_record.meta.title

    title = f"Focus · {global_record.meta.title}"
    session = service.open_session(
        title=title,
        mode=mode,
        provider=provider,
        second_provider=second_provider,
        project=project,
        context_bindings=[_focus_binding(paper_id)],
    )
    return session, True, global_record.meta.title


@focus_app.command("open")
def open_focus_session(
    paper_id: str = typer.Argument(..., help="Canonical paper id to focus on."),
    mode: ChatMode = typer.Option(ChatMode.SINGLE, "--mode", help="single, round_robin, or parallel."),
    provider: str = typer.Option("auto", "--provider", help="Primary provider."),
    second_provider: str = typer.Option("auto", "--second-provider", help="Secondary provider for multi-agent modes."),
    force_new: bool = typer.Option(False, "--new", help="Create a new focus session even if one already exists."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output instead of entering the shell."),
) -> None:
    try:
        session, created, title = _open_focus_session(
            paper_id=paper_id.strip(),
            mode=mode,
            provider=provider,
            second_provider=second_provider,
            force_new=force_new,
            as_json=json_output,
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "session": session.model_dump(mode="json"),
        "paper_id": paper_id.strip(),
        "paper_title": title,
        "created": created,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    if created:
        console.print(f"[dim]Opened a new focus session for {paper_id.strip()}.[/dim]")
    else:
        console.print(f"[dim]Resuming existing focus session for {paper_id.strip()}.[/dim]")
    run_chat_shell(session=session, service=_chat_service())


@focus_app.command("list")
def list_focus_sessions(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    active_project = _require_active_project(as_json=json_output)
    sessions = [
        session
        for session in _chat_service().list_sessions()
        if any(binding.provider == "paper_focus" for binding in session.context_bindings)
    ]
    if json_output:
        _emit([session.model_dump(mode="json") for session in sessions], as_json=True)
        return
    if not sessions:
        console.print("[dim]No focus sessions yet.[/dim]")
        return

    console.print(f"[bold]Focus Sessions[/bold] ({active_project})")
    for session in sessions:
        paper_ids = [
            str(binding.config.get("paper_id", "")).strip()
            for binding in session.context_bindings
            if binding.provider == "paper_focus"
        ]
        paper_label = ", ".join(paper_id for paper_id in paper_ids if paper_id) or "(unknown paper)"
        console.print(
            f"- {session.session_id}: {paper_label} [dim]({session.mode.value}, {session.project or '(none)'})[/dim]"
        )
