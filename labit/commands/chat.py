from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.pretty import Pretty
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from labit.capture.commands import handle_capture_command
from labit.commands.context import ChatContext
from labit.commands.dispatch import SlashCommandDispatcher
from labit.commands.rendering import (
    CHAT_SHELL_COMMANDS,
    COMMAND_COLOR,
    CODE_THEME,
    LABIT_THEME,
    PROVIDER_STYLES,
    ThinkingIndicator,
    agent_panel,
    box_bottom,
    box_line,
    box_top,
    box_width,
    clip_box_text,
    md,
    message_body,
    print_doc_mode_hints,
    render_compact_transcript,
    render_console_header,
    render_message_block,
    render_recent_messages,
    render_session_summary,
    render_shell_header,
    render_shell_help,
    render_transcript,
    render_user_shell_message,
    sanitize_markdown,
    transcript_preview_text,
)
from labit.chat.composer import ComposerResult, prompt_toolkit_available, prompt_with_clipboard_image
from labit.chat.models import ChatMode
from labit.chat.service import ChatService
from labit.context.events import SessionEventKind
from labit.documents.commands import handle_document_command
from labit.documents.drafter import DocDrafter
from labit.documents.models import DocSession
from labit.documents.service import DocumentService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

chat_app = typer.Typer(
    help="Shared free conversation sessions with one or more agent backends.",
    invoke_without_command=True,
)

console = Console(theme=LABIT_THEME)


def _chat_service() -> ChatService:
    return ChatService(RepoPaths.discover())


def _project_service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _doc_drafter() -> DocDrafter:
    return DocDrafter(RepoPaths.discover())


def _document_service() -> DocumentService:
    return DocumentService(RepoPaths.discover())


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


def _prompt_text(label: str) -> str:
    while True:
        value = typer.prompt(label).strip()
        if value:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_optional(label: str, default: str = "") -> str:
    return typer.prompt(label, default=default, show_default=bool(default)).strip()


def _prompt_mode() -> ChatMode:
    choices = [mode.value for mode in ChatMode]
    normalized = {choice.lower(): choice for choice in choices}
    while True:
        value = typer.prompt(
            f"Mode [{' / '.join(choices)}]",
            default=ChatMode.SINGLE.value,
            show_default=True,
        ).strip().lower()
        if value in normalized:
            return ChatMode(normalized[value])
        console.print(f"[bold red]Choose one of:[/bold red] {', '.join(choices)}")




def _prompt_in_box(session) -> ComposerResult:
    width = box_width(console)
    inner_width = width - 2
    project = session.project or "no-project"
    mode = session.mode.value
    participants = ", ".join(f"{item.name}:{item.provider.value}" for item in session.participants)
    prompt_prefix = " › "
    if not console.is_terminal:
        render_console_header(console, project=project, mode=mode, participants=participants)
        console.print(f"[yellow]{box_top('Input', width)}[/yellow]")
        console.print(f"[yellow]{box_line('', width)}[/yellow]")
        console.print(f"[yellow]│[/yellow]{prompt_prefix}", end="")
        raw = console.input("")
        console.print(f"[yellow]{box_line('', width)}[/yellow]")
        console.print(f"[yellow]{box_bottom(width)}[/yellow]")
        return ComposerResult(text=raw)

    if prompt_toolkit_available():
        render_console_header(console, project=project, mode=mode, participants=participants)
        return prompt_with_clipboard_image(
            console=console,
            paths=RepoPaths.discover(),
            session_id=session.session_id,
            prompt_prefix=prompt_prefix,
            slash_commands=CHAT_SHELL_COMMANDS,
        )

    top = box_top("Input", width)
    empty = box_line("", width)
    prompt_fill = " " * max(0, inner_width - len(prompt_prefix))
    prompt_line = f"│{prompt_prefix}{prompt_fill}│"
    bottom = box_bottom(width)

    stream = console.file
    yellow = "\x1b[33m"
    reset = "\x1b[0m"

    render_console_header(console, project=project, mode=mode, participants=participants)
    stream.write(f"{yellow}{top}{reset}\n")
    stream.write(f"{yellow}{empty}{reset}\n")
    stream.write(f"{yellow}{prompt_line}{reset}\n")
    stream.write(f"{yellow}{empty}{reset}\n")
    stream.write(f"{yellow}{bottom}{reset}\n")
    stream.write("\n")
    stream.write(f"\x1b[4A\r\x1b[{len(prompt_prefix) + 1}C")
    stream.flush()

    raw_result = prompt_with_clipboard_image(
        console=console,
        paths=RepoPaths.discover(),
        session_id=session.session_id,
        prompt_prefix="",
    )

    stream.write("\x1b[2B\r")
    stream.flush()
    return raw_result


def _open_default_session(
    *,
    service: ChatService,
    title: str | None,
    mode: ChatMode | None,
    provider: str,
    second_provider: str,
):
    sessions = service.list_sessions()
    if sessions:
        return sessions[0], False
    session = service.open_session(
        title=title or "Free Conversation",
        mode=mode or ChatMode.SINGLE,
        provider=provider,
        second_provider=second_provider,
        project=_project_service().active_project_name(),
    )
    return session, True


def _confirm_in_shell(prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}




def _session_evidence_refs(session) -> list[str]:
    refs: list[str] = []
    if session.project:
        refs.append(f"project:{session.project}")
    return refs


def _run_streaming_turn(
    *,
    service: ChatService,
    session,
    query: str,
    attachments: list | None = None,
    force_deep_context: bool = False,
    reasoning_effort: str | None = None,
    skip_participants: set[str] | None = None,
    cwd_override: str | None = None,
) -> object | None:
    render_user_shell_message(console, query, attachments=attachments)
    # Temporarily filter out muted participants for this turn
    effective_session = session
    if skip_participants:
        filtered = [p for p in session.participants if p.name not in skip_participants]
        if filtered:
            effective_session = session.model_copy(update={"participants": filtered})
    participant_state = {
        participant.name: {
            "provider": participant.provider.value,
            "content": "",
            "status": "queued",
            "started_at": None,
            "thinking": None,
        }
        for participant in effective_session.participants
    }
    state_lock = threading.Lock()
    live_lock = threading.Lock()
    cancel_event = threading.Event()

    def _render_live() -> Group:
        panels: list[Panel] = []
        with state_lock:
            snapshot = {
                name: dict(values)
                for name, values in participant_state.items()
            }
        for participant in effective_session.participants:
            state = snapshot[participant.name]
            status = state["status"]
            if status == "queued":
                continue
            started_at = state["started_at"]
            status_text = status
            if started_at is not None and status in {"thinking", "streaming"}:
                status_text = f"{status} · {time.monotonic() - started_at:.1f}s"
            panels.append(
                agent_panel(
                    participant.name,
                    state["provider"],
                    state["content"],
                    thinking=state["thinking"],
                    status_text=status_text,
                )
            )
        return Group(*panels)

    def _refresh_live() -> None:
        with live_lock:
            live.update(_render_live(), refresh=True)

    def _on_reply_start(participant) -> None:
        with state_lock:
            participant_state[participant.name]["content"] = ""
            participant_state[participant.name]["status"] = "thinking"
            participant_state[participant.name]["started_at"] = time.monotonic()
            participant_state[participant.name]["thinking"] = ThinkingIndicator()
        _refresh_live()

    def _on_reply_delta(participant, content: str) -> None:
        with state_lock:
            participant_state[participant.name]["content"] = content
            participant_state[participant.name]["status"] = "streaming" if content.strip() else "thinking"
        _refresh_live()

    def _on_reply_complete(participant, content: str) -> None:
        with state_lock:
            participant_state[participant.name]["content"] = content
            participant_state[participant.name]["status"] = "done"
            participant_state[participant.name]["thinking"] = None
        _refresh_live()

    cancelled = False
    result = None
    with Live(_render_live(), console=console, refresh_per_second=8, transient=True) as live:
        try:
            result = service.ask_stream(
                session_id=session.session_id,
                content=query,
                attachments=attachments,
                force_deep_context=force_deep_context,
                reasoning_effort=reasoning_effort,
                on_reply_start=_on_reply_start,
                on_reply_delta=_on_reply_delta,
                on_reply_complete=_on_reply_complete,
                cancel_event=cancel_event,
                skip_participants=skip_participants,
                cwd_override=cwd_override,
            )
        except KeyboardInterrupt:
            cancel_event.set()
            cancelled = True
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return None

    if result is not None and result.replies:
        for reply in result.replies:
            console.print(
                agent_panel(
                    reply.participant.name,
                    reply.participant.provider.value,
                    reply.message.content,
                    turn_index=reply.message.turn_index,
                )
            )
            console.print("")

    if cancelled:
        with state_lock:
            for state in participant_state.values():
                if state["status"] in {"thinking", "streaming"}:
                    state["status"] = "interrupted"
                    state["thinking"] = None
        console.print("[dim italic]Interrupted.[/dim italic]")
    return result


def run_chat_shell(
    *,
    session,
    service: ChatService,
) -> None:
    render_shell_header(console,session)
    transcript = service.transcript(session.session_id)
    console.print("")
    render_recent_messages(console,transcript, count=8)
    console.print("")

    current_session = session
    active_doc: DocSession | None = None
    muted_next_turn: set[str] = set()  # agent names to skip on next turn only
    dispatcher = SlashCommandDispatcher()

    for capture_command in ("/idea", "/todo"):
        dispatcher.register(
            capture_command,
            lambda ctx, arg, command=capture_command: handle_capture_command(
                ctx=ctx,
                command=command,
                argument=arg,
            ),
        )

    def _handle_document(ctx: ChatContext, arg: str) -> None:
        nonlocal active_doc
        result = handle_document_command(
            ctx=ctx,
            argument=arg,
            active_doc=active_doc,
        )
        active_doc = result.active_doc

    dispatcher.register("/doc", _handle_document)
    while True:
        try:
            composer_result = _prompt_in_box(current_session)
        except KeyboardInterrupt:
            console.print("\n[dim]Leaving chat shell.[/dim]")
            return
        raw = composer_result.text.strip()
        attachments = composer_result.attachments
        if not raw:
            continue
        raw_lower = raw.lower()
        is_doc_edit = active_doc is not None and (raw_lower == "/edit" or raw_lower.startswith("/edit "))
        if raw.startswith("/") and not is_doc_edit:
            parts = raw.split(maxsplit=1)
            command = parts[0]
            argument = parts[1].strip() if len(parts) > 1 else ""
            ctx = ChatContext(
                console=console,
                paths=RepoPaths.discover(),
                service=service,
                session=current_session,
            )

            if command == "/exit":
                console.print("[dim]Leaving chat shell.[/dim]")
                return
            if dispatcher.handle(command, ctx, argument):
                continue
            if command == "/help":
                render_shell_help(console)
                continue
            if command == "/list":
                list_chats(json_output=False)
                continue
            if command == "/show":
                render_session_summary(console,current_session)
                console.print("")
                render_transcript(console,service.transcript(current_session.session_id))
                continue
            if command == "/mode":
                if not argument:
                    render_session_summary(console,current_session)
                    continue
                mode_str = argument.strip().lower()
                try:
                    new_mode = ChatMode(mode_str)
                except ValueError:
                    console.print(f"[bold red]Invalid mode:[/bold red] {mode_str}. Use single, round_robin, or parallel.")
                    continue
                if new_mode == current_session.mode:
                    console.print(f"[dim]Already in {new_mode.value} mode.[/dim]")
                    continue
                current_session = service.update_mode(current_session.session_id, new_mode)
                console.print(f"[bold #0080ff]Switched to {new_mode.value} mode.[/bold #0080ff]")
                if new_mode != ChatMode.SINGLE:
                    names = ", ".join(p.name for p in current_session.participants)
                    console.print(f"[dim]Participants: {names}[/dim]")
                continue
            if command == "/swap":
                if len(current_session.participants) < 2:
                    console.print("[dim]Need at least 2 participants to swap.[/dim]")
                    continue
                old_order = ", ".join(p.name for p in current_session.participants)
                current_session = service.swap_participants(current_session.session_id)
                new_order = ", ".join(p.name for p in current_session.participants)
                console.print(f"[bold #0080ff]Swapped participant order:[/bold #0080ff] {old_order} → {new_order}")
                continue
            if command == "/mute":
                if not argument:
                    if muted_next_turn:
                        console.print(f"[dim]Muted for next turn: {', '.join(muted_next_turn)}[/dim]")
                    else:
                        names = ", ".join(p.name for p in current_session.participants)
                        console.print(f"[dim]Usage: /mute <agent_name>  (participants: {names})[/dim]")
                    continue
                target = argument.strip().lower()
                matched = [p for p in current_session.participants if p.name.lower() == target]
                if not matched:
                    names = ", ".join(p.name for p in current_session.participants)
                    console.print(f"[bold red]Unknown agent:[/bold red] {target}. Participants: {names}")
                    continue
                agent_name = matched[0].name
                if agent_name in muted_next_turn:
                    muted_next_turn.discard(agent_name)
                    console.print(f"[bold #0080ff]Unmuted {agent_name}.[/bold #0080ff]")
                else:
                    # Don't allow muting all participants
                    active_count = len([p for p in current_session.participants if p.name not in muted_next_turn])
                    if active_count <= 1:
                        console.print("[bold red]Error:[/bold red] Cannot mute all participants.")
                        continue
                    muted_next_turn.add(agent_name)
                    console.print(f"[bold #0080ff]{agent_name} muted for next turn.[/bold #0080ff] (auto-unmutes after one turn)")
                continue
            if command == "/new":
                title = _prompt_optional("Title", default="Free Conversation")
                mode = _prompt_mode()
                provider = _prompt_optional("Primary provider", default="auto") or "auto"
                second_provider = "auto"
                if mode != ChatMode.SINGLE:
                    second_provider = _prompt_optional("Secondary provider", default="auto") or "auto"
                try:
                    current_session = service.open_session(
                        title=title,
                        mode=mode,
                        provider=provider,
                        second_provider=second_provider,
                        project=_project_service().active_project_name(),
                    )
                    active_doc = None
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                render_shell_header(console,current_session)
                continue
            if command == "/switch":
                if not argument:
                    console.print("[bold red]Usage:[/bold red] /switch <session_id>")
                    continue
                try:
                    current_session = service.load_session(argument)
                    active_doc = None
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                render_shell_header(console,current_session)
                console.print("")
                render_recent_messages(console,service.transcript(current_session.session_id), count=8)
                continue

            console.print(f"[bold red]Unknown command:[/bold red] {command}")
            continue

        # Doc mode: /edit triggers revision; normal input remains conversation.
        if is_doc_edit:
            edit_instruction = raw[len("/edit"):].strip()
            if not edit_instruction:
                console.print("[bold red]Usage:[/bold red] /edit <instruction>")
                continue
            if attachments:
                console.print("[bold red]Error:[/bold red] Document edit does not support image attachments yet.")
                continue
            doc_service = _document_service()
            drafter = _doc_drafter()
            author = current_session.participants[0]
            reviewer = (
                current_session.participants[1]
                if current_session.mode == ChatMode.ROUND_ROBIN and len(current_session.participants) >= 2
                else None
            )
            try:
                old_markdown = doc_service.read_document(active_doc)

                with console.status(f"[bold blue]{author.name} updating document...[/bold blue]"):
                    update = drafter.revise_document(
                        session=current_session,
                        transcript=service.transcript(current_session.session_id),
                        context_snapshot=service.context_snapshot(current_session.session_id),
                        doc_title=active_doc.title,
                        current_markdown=old_markdown,
                        user_instruction=edit_instruction,
                        interaction_log=doc_service.interaction_excerpt(active_doc),
                        author_name=author.name,
                        provider=author.provider,
                    )
                    active_doc = doc_service.revise_document(
                        doc_session=active_doc,
                        update=update,
                        user_instruction=edit_instruction,
                    )

                console.print(
                    Panel(
                        (
                            f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                            f"[bold]Document[/bold]: {active_doc.document_path}\n"
                            f"[bold]Iteration[/bold]: {active_doc.iteration}\n"
                            f"[bold]Summary[/bold]: {update.summary}"
                        ),
                        title=f"[bold green]{author.name} · Document updated[/bold green]",
                        border_style="green",
                    )
                )

                if reviewer is not None:
                    from labit.documents.drafter import compute_changed_sections

                    new_markdown = doc_service.read_document(active_doc)
                    changed_sections = compute_changed_sections(old_markdown, new_markdown)

                    with console.status(f"[bold cyan]{reviewer.name} reviewing document...[/bold cyan]"):
                        review_update = drafter.review_document(
                            current_markdown=new_markdown,
                            revision_summary=update.summary,
                            user_instruction=edit_instruction,
                            reviewer_name=reviewer.name,
                            changed_sections=changed_sections,
                            provider=reviewer.provider,
                        )
                        active_doc = doc_service.record_review(
                            doc_session=active_doc,
                            update=review_update,
                            reviewer_name=reviewer.name,
                        )
                    console.print(
                        Panel(
                            f"[bold]Review[/bold]: {review_update.summary}",
                            title=f"[bold cyan]{reviewer.name} · Review[/bold cyan]",
                            border_style="cyan",
                        )
                    )
            except Exception as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                continue
            try:
                service.record_session_event(
                    session_id=current_session.session_id,
                    kind=SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
                    actor="labit",
                    summary=f"Document updated: {active_doc.title}",
                    payload={
                        "doc_id": active_doc.doc_id,
                        "title": active_doc.title,
                        "document_path": active_doc.document_path,
                        "log_path": active_doc.log_path,
                        "iteration": active_doc.iteration,
                        "agent_summary": update.summary,
                    },
                    evidence_refs=_session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
                )
            except Exception:
                pass
            continue

        result = _run_streaming_turn(
            service=service,
            session=current_session,
            query=raw,
            attachments=attachments,
            skip_participants=muted_next_turn if muted_next_turn else None,
        )
        if muted_next_turn:
            muted_next_turn.clear()
        if result is not None:
            current_session = result.session


@chat_app.callback()
def chat_callback(
    ctx: typer.Context,
    title: str | None = typer.Option(None, "--title", help="Optional title when opening the shell."),
    mode: ChatMode | None = typer.Option(None, "--mode", help="Optional mode when opening the shell."),
    provider: str = typer.Option("auto", "--provider", help="Primary provider when opening the shell."),
    second_provider: str = typer.Option("auto", "--second-provider", help="Secondary provider when opening the shell."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    service = _chat_service()
    try:
        session, created = _open_default_session(
            service=service,
            title=title,
            mode=mode,
            provider=provider,
            second_provider=second_provider,
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=False))
    if created:
        console.print("[dim]Created a new chat session.[/dim]")
    else:
        console.print("[dim]Resuming the most recent chat session.[/dim]")
    run_chat_shell(session=session, service=service)


@chat_app.command("open")
def open_chat(
    title: str | None = typer.Option(None, "--title", help="Optional session title."),
    mode: ChatMode | None = typer.Option(None, "--mode", help="single, round_robin, or parallel."),
    provider: str = typer.Option("auto", "--provider", help="Primary provider: auto, claude, or codex."),
    second_provider: str = typer.Option("auto", "--second-provider", help="Secondary provider for multi-agent modes."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _project_service().active_project_name()
    if not json_output:
        console.print("[bold]Open Chat[/bold]")
        console.print(f"[dim]Active project: {project or '(none)'}[/dim]")

    resolved_title = title or _prompt_optional("Title", default="Free Conversation")
    resolved_mode = mode or _prompt_mode()

    service = _chat_service()
    try:
        session = service.open_session(
            title=resolved_title,
            mode=resolved_mode,
            provider=provider,
            second_provider=second_provider,
            project=project,
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(session.model_dump(mode="json"), as_json=True)
        return
    render_session_summary(console,session)
    run_chat_shell(session=session, service=service)


@chat_app.command("list")
def list_chats(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    sessions = _chat_service().list_sessions()
    if json_output:
        _emit([session.model_dump(mode="json") for session in sessions], as_json=True)
        return
    if not sessions:
        console.print("[dim]No chat sessions yet.[/dim]")
        return
    table = Table(title="Chat Sessions", show_header=True, header_style=f"bold {COMMAND_COLOR}")
    table.add_column("Session")
    table.add_column("Title")
    table.add_column("Mode")
    table.add_column("Project")
    table.add_column("Participants")
    table.add_column("Status")
    for session in sessions:
        participants = ", ".join(item.name for item in session.participants)
        table.add_row(
            session.session_id,
            session.title,
            session.mode.value,
            session.project or "(none)",
            participants,
            session.status.value,
        )
    console.print(table)


@chat_app.command("show")
def show_chat(
    session_id: str = typer.Argument(..., help="Session id."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _chat_service()
    try:
        session = service.load_session(session_id)
        transcript = service.transcript(session_id)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "session": session.model_dump(mode="json"),
                "transcript": [message.model_dump(mode="json") for message in transcript],
            },
            as_json=True,
        )
        return

    render_session_summary(console,session)
    console.print("")
    render_transcript(console,transcript)


@chat_app.command("ask")
def ask_chat(
    session_id: str = typer.Argument(..., help="Session id."),
    message: str | None = typer.Argument(None, help="Optional user message."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _chat_service()
    resolved_message = message or _prompt_text("Message")
    try:
        result = service.ask(session_id=session_id, content=resolved_message)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "session": result.session.model_dump(mode="json"),
                "user_message": result.user_message.model_dump(mode="json"),
                "replies": [
                    {
                        "participant": reply.participant.model_dump(mode="json"),
                        "message": reply.message.model_dump(mode="json"),
                    }
                    for reply in result.replies
                ],
                "context": result.context_snapshot.model_dump(mode="json"),
            },
            as_json=True,
        )
        return

    render_session_summary(console,result.session)
    console.print("")
    console.print(f"[bold][turn {result.user_message.turn_index}] user[/bold]")
    console.print(result.user_message.content)
    console.print("")
    for reply in result.replies:
        console.print(f"[cyan][turn {reply.message.turn_index}] {reply.participant.name}[/cyan]")
        console.print(md(reply.message.content))
        console.print("")


@chat_app.command("resume")
def resume_chat(
    session_id: str = typer.Argument(..., help="Session id."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _chat_service()
    try:
        session = service.load_session(session_id)
        transcript = service.transcript(session_id)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "session": session.model_dump(mode="json"),
                "transcript": [message.model_dump(mode="json") for message in transcript],
            },
            as_json=True,
        )
        return

    console.print(f"[dim]Resuming chat session {session_id}.[/dim]")
    run_chat_shell(session=session, service=service)
