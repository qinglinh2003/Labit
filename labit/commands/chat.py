from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from labit.capture.drafter import IdeaDrafter
from labit.capture.service import CaptureService
from labit.chat.models import ChatMode
from labit.chat.service import ChatService
from labit.chat.synthesizer import DiscussionSynthesizer
from labit.context.events import SessionEventKind
from labit.hypotheses.drafter import HypothesisDrafter
from labit.hypotheses.service import HypothesisService
from labit.investigations.service import InvestigationService
from labit.memory.models import MemoryKind
from labit.memory.store import MemoryStore
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

chat_app = typer.Typer(
    help="Shared free conversation sessions with one or more agent backends.",
    invoke_without_command=True,
)
console = Console()

_PROVIDER_STYLES = {
    "claude": ("blue", "CLAUDE"),
    "codex": ("green", "CODEX"),
}


def _chat_service() -> ChatService:
    return ChatService(RepoPaths.discover())


def _project_service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _hypothesis_service() -> HypothesisService:
    return HypothesisService(RepoPaths.discover())


def _hypothesis_drafter() -> HypothesisDrafter:
    return HypothesisDrafter(RepoPaths.discover())


def _capture_service() -> CaptureService:
    return CaptureService(RepoPaths.discover())


def _idea_drafter() -> IdeaDrafter:
    return IdeaDrafter(RepoPaths.discover())


def _investigation_service() -> InvestigationService:
    return InvestigationService(RepoPaths.discover())


def _discussion_synthesizer() -> DiscussionSynthesizer:
    return DiscussionSynthesizer(RepoPaths.discover())


def _memory_store() -> MemoryStore:
    return MemoryStore(RepoPaths.discover())


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


def _render_session_summary(session) -> None:
    participants = "\n".join(f"- {item.name} ({item.provider.value})" for item in session.participants)
    body = (
        f"[bold]Session ID[/bold]: {session.session_id}\n"
        f"[bold]Mode[/bold]: {session.mode.value}\n"
        f"[bold]Project[/bold]: {session.project or '(none)'}\n"
        f"[bold]Status[/bold]: {session.status.value}\n"
        f"[bold]Participants[/bold]:\n{participants}"
    )
    console.print(Panel(body, title=f"[bold green]{session.title}[/bold green]", border_style="green"))


def _render_transcript(messages) -> None:
    if not messages:
        console.print("[dim]No messages yet.[/dim]")
        return
    for message in messages:
        _render_message_block(message)


def _render_compact_transcript(messages) -> None:
    if not messages:
        console.print("[dim]No messages yet.[/dim]")
        return
    for message in messages:
        if message.message_type.value == "user":
            console.print(Panel.fit(message.content, title=f"user · turn {message.turn_index}", border_style="white"))
            continue
        provider_name = message.provider.value if message.provider else "agent"
        color, label = _PROVIDER_STYLES.get(provider_name, ("cyan", provider_name.upper()))
        title = f"{label} · {message.speaker} · turn {message.turn_index}"
        console.print(Panel.fit(message.content, title=title, border_style=color))


def _render_message_block(message) -> None:
    if message.message_type.value == "user":
        console.print(Panel(message.content, title=f"user · turn {message.turn_index}", border_style="white"))
        console.print("")
        return

    provider_name = message.provider.value if message.provider else "agent"
    color, label = _PROVIDER_STYLES.get(provider_name, ("cyan", provider_name.upper()))
    title = f"{label} · {message.speaker} · turn {message.turn_index}"
    console.print(Panel(message.content, title=title, border_style=color))
    console.print("")


def _render_recent_messages(messages, *, count: int = 8) -> None:
    console.print(Panel.fit(_transcript_preview_text(messages[-count:]), title="Recent Messages", border_style="blue"))


def _transcript_preview_text(messages) -> Text:
    if not messages:
        return Text("No messages yet.", style="dim")
    text = Text()
    for idx, message in enumerate(messages):
        if idx:
            text.append("\n")
        if message.message_type.value == "user":
            text.append("user", style="bold white on blue")
        else:
            text.append(message.speaker, style="bold cyan")
            if message.provider:
                text.append(f" ({message.provider.value})", style="dim")
        text.append(": ")
        text.append(message.content)
    return text


def _render_shell_header(session) -> None:
    mode_label = {
        ChatMode.SINGLE: "Single agent",
        ChatMode.ROUND_ROBIN: "Round robin",
        ChatMode.PARALLEL: "Parallel replies",
    }[session.mode]
    participants = ", ".join(f"{item.name}:{item.provider.value}" for item in session.participants)
    body = (
        f"[bold]Project[/bold]: {session.project or '(none)'}\n"
        f"[bold]Mode[/bold]: {mode_label}\n"
        f"[bold]Participants[/bold]: {participants}\n"
        f"[bold]Session ID[/bold]: {session.session_id}\n"
        "[dim]Type a message to continue. Use /help to see shell commands.[/dim]"
    )
    console.print(Panel(body, title=f"[bold green]LABIT Chat · {session.title}[/bold green]", border_style="green"))


def _box_width() -> int:
    width = console.size.width if console.size.width else 80
    return max(60, width - 4)


def _clip_box_text(text: str, width: int) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return f"{text[: width - 1]}…"


def _box_top(title: str, width: int) -> str:
    inner_width = width - 2
    title_text = f" {title} "
    if len(title_text) >= inner_width:
        return f"╭{title_text[:inner_width]}╮"
    filler = "─" * (inner_width - len(title_text))
    return f"╭{title_text}{filler}╮"


def _box_line(text: str, width: int) -> str:
    inner_width = width - 2
    content = _clip_box_text(text, inner_width)
    return f"│{content.ljust(inner_width)}│"


def _box_bottom(width: int) -> str:
    return f"╰{'─' * (width - 2)}╯"


def _prompt_in_box(session) -> str:
    width = _box_width()
    inner_width = width - 2
    project = session.project or "no-project"
    mode = session.mode.value
    participants = ", ".join(f"{item.name}:{item.provider.value}" for item in session.participants)
    prompt_prefix = " › "
    meta_line = f"{project} · {mode} · {participants}"
    command_line = "Commands: /help /list /new /switch /show /mode /memory /idea /note /todo /synthesize /investigate /hypothesis /exit"

    if not console.is_terminal:
        console.print(f"[dim]{meta_line}[/dim]")
        console.print(f"[dim]{command_line}[/dim]")
        console.print(f"[yellow]{_box_top('Input', width)}[/yellow]")
        console.print(f"[yellow]{_box_line('', width)}[/yellow]")
        console.print(f"[yellow]│[/yellow]{prompt_prefix}", end="")
        raw = console.input("")
        console.print(f"[yellow]{_box_line('', width)}[/yellow]")
        console.print(f"[yellow]{_box_bottom(width)}[/yellow]")
        return raw

    top = _box_top("Input", width)
    empty = _box_line("", width)
    prompt_fill = " " * max(0, inner_width - len(prompt_prefix))
    prompt_line = f"│{prompt_prefix}{prompt_fill}│"
    bottom = _box_bottom(width)

    stream = console.file
    yellow = "\x1b[33m"
    reset = "\x1b[0m"

    console.print(f"[dim]{meta_line}[/dim]")
    console.print(f"[dim]{command_line}[/dim]")
    stream.write(f"{yellow}{top}{reset}\n")
    stream.write(f"{yellow}{empty}{reset}\n")
    stream.write(f"{yellow}{prompt_line}{reset}\n")
    stream.write(f"{yellow}{empty}{reset}\n")
    stream.write(f"{yellow}{bottom}{reset}\n")
    stream.write("\n")
    stream.write(f"\x1b[4A\r\x1b[{len(prompt_prefix) + 1}C")
    stream.flush()

    raw = input()

    stream.write("\x1b[2B\r")
    stream.flush()
    return raw


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


def _shell_help() -> None:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Command", style="bold")
    table.add_column("What It Does")
    table.add_row("/help", "Show shell commands.")
    table.add_row("/list", "List existing chat sessions.")
    table.add_row("/new", "Create a new session and switch into it.")
    table.add_row("/switch <session_id>", "Switch to another session.")
    table.add_row("/show", "Show the full transcript for the current session.")
    table.add_row("/mode", "Show current mode, participants, and session info.")
    table.add_row("/memory [id|kind]", "Show recent project memory, one memory by id, or filter by memory kind.")
    table.add_row("/idea [text]", "Save a lightweight project idea. With no text, show saved ideas.")
    table.add_row("/note [text]", "Save a lightweight project note. With no text, show saved notes.")
    table.add_row("/todo [text]", "Save an actionable project todo. With no text, show saved todos.")
    table.add_row("/synthesize [hint]", "Distill the current discussion into consensus, disagreements, and follow-ups.")
    table.add_row("/investigate <topic>", "Investigate a topic from the current session and write a report.")
    table.add_row("/hypothesis [idea]", "Draft and create a structured hypothesis from the current session.")
    table.add_row("/exit", "Leave the chat shell.")
    console.print(Panel(table, title="LABIT Chat Commands", border_style="magenta"))


def _confirm_in_shell(prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _render_hypothesis_preview(draft, *, project: str) -> None:
    body = (
        f"[bold]Project[/bold]: {project}\n"
        f"[bold]Claim[/bold]: {draft.claim}\n"
        f"[bold]Independent variable[/bold]: {draft.independent_variable or '(blank)'}\n"
        f"[bold]Dependent variable[/bold]: {draft.dependent_variable or '(blank)'}\n"
        f"[bold]Success criteria[/bold]: {draft.success_criteria or '(blank)'}\n"
        f"[bold]Failure criteria[/bold]: {draft.failure_criteria or '(blank)'}\n"
        f"[bold]Source papers[/bold]: {', '.join(draft.source_paper_ids) or '(none)'}"
    )
    console.print(Panel(body, title=f"[bold green]Hypothesis Draft · {draft.title}[/bold green]", border_style="green"))
    if draft.motivation:
        console.print(Panel(draft.motivation, title="Motivation", border_style="cyan"))
    if draft.rationale_markdown:
        console.print(Panel(Markdown(draft.rationale_markdown), title="Rationale", border_style="blue"))
    if draft.experiment_plan_markdown:
        console.print(Panel(Markdown(draft.experiment_plan_markdown), title="Experiment Plan", border_style="magenta"))


def _render_idea_preview(draft) -> None:
    console.print(
        Panel(
            (
                f"[bold]Summary[/bold]:\n{draft.summary_markdown}\n\n"
                f"[bold]Key question[/bold]: {draft.key_question}"
            ),
            title=f"[bold green]Idea Draft · {draft.title}[/bold green]",
            border_style="green",
        )
    )


def _render_synthesis_preview(draft) -> None:
    parts = [f"[bold]Summary[/bold]:\n{draft.summary}"]
    if draft.consensus:
        parts.append("[bold]Consensus[/bold]:\n" + "\n".join(f"- {item}" for item in draft.consensus))
    if draft.disagreements:
        parts.append("[bold]Disagreements[/bold]:\n" + "\n".join(f"- {item}" for item in draft.disagreements))
    if draft.followups:
        parts.append("[bold]Follow-ups[/bold]:\n" + "\n".join(f"- {item}" for item in draft.followups))
    console.print(
        Panel(
            "\n\n".join(parts),
            title="[bold green]Discussion Synthesis[/bold green]",
            border_style="green",
        )
    )


def _render_capture_records(kind: str, records) -> None:
    label_map = {
        "idea": "Ideas",
        "note": "Notes",
        "todo": "Todos",
    }
    label = label_map.get(kind, f"{kind.title()}s")
    console.print(f"[bold]{label}[/bold]")
    if not records:
        console.print(f"[dim]No {kind}s yet.[/dim]")
        return
    for item in records:
        console.print(f"- [bold]{item.title}[/bold] [dim]({item.created_at or 'unknown date'})[/dim]")
        console.print(f"  [dim]{item.path}[/dim]")


def _render_related_reports(reports) -> None:
    console.print("[bold]Related reports[/bold]")
    for item in reports:
        summary = item.summary or "(no summary)"
        console.print(f"- [bold]{item.title}[/bold] [dim]({item.path})[/dim]")
        console.print(f"  {summary}")


def _render_memory_records(records) -> None:
    console.print("[bold]Project Memory[/bold]")
    if not records:
        console.print("[dim]No memory records yet.[/dim]")
        return
    for record in records:
        refs = f" · refs: {', '.join(record.evidence_refs[:2])}" if record.evidence_refs else ""
        console.print(
            f"- [bold]{record.memory_id}[/bold] [{record.kind.value}/{record.memory_type.value}] "
            f"{record.title} · {record.namespace.render()} · score:{record.promotion_score}{refs}"
        )


def _render_memory_detail(record) -> None:
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


def _render_investigation_result(result) -> None:
    console.print(
        Panel(
            (
                f"[bold]Title[/bold]: {result.title}\n"
                f"[bold]Path[/bold]: {result.report_path}\n"
                f"[bold]Run[/bold]: {result.run_id}\n"
                f"[bold]Summary[/bold]: {result.summary or '(blank)'}"
            ),
            title="[bold green]Investigation complete[/bold green]",
            border_style="green",
        )
    )


def _transcript_excerpt(messages, *, limit: int = 16, max_chars: int = 6000) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for message in messages[-limit:]:
        speaker = message.speaker
        if message.provider:
            speaker = f"{speaker} ({message.provider.value})"
        lines.append(f"{speaker}: {message.content.strip()}")
    text = "\n".join(lines).strip()
    return text[:max_chars].strip()


def _context_snapshot_excerpt(snapshot, *, max_blocks: int = 6, max_chars: int = 5000) -> str:
    pieces: list[str] = []
    for block in snapshot.blocks[:max_blocks]:
        pieces.append(f"[{block.title}]\n{block.content.strip()}")
    for memory in snapshot.memory[:max_blocks]:
        pieces.append(f"[{memory.title}]\n{memory.content.strip()}")
    text = "\n\n".join(piece for piece in pieces if piece).strip()
    return text[:max_chars].strip()


def _session_evidence_refs(session) -> list[str]:
    refs: list[str] = []
    if session.project:
        refs.append(f"project:{session.project}")
    for binding in session.context_bindings:
        if binding.provider != "paper_focus":
            continue
        paper_id = str(binding.config.get("paper_id", "")).strip()
        if paper_id:
            refs.append(f"paper:{paper_id}")
    return refs


def run_chat_shell(
    *,
    session,
    service: ChatService,
) -> None:
    _render_shell_header(session)
    transcript = service.transcript(session.session_id)
    console.print("")
    _render_recent_messages(transcript, count=8)
    console.print("")

    current_session = session
    while True:
        raw = _prompt_in_box(current_session).strip()
        if not raw:
            continue
        if raw.startswith("/"):
            parts = raw.split(maxsplit=1)
            command = parts[0]
            argument = parts[1].strip() if len(parts) > 1 else ""

            if command == "/exit":
                console.print("[dim]Leaving chat shell.[/dim]")
                return
            if command == "/help":
                _shell_help()
                continue
            if command == "/list":
                list_chats(json_output=False)
                continue
            if command == "/show":
                _render_session_summary(current_session)
                console.print("")
                _render_transcript(service.transcript(current_session.session_id))
                continue
            if command == "/mode":
                _render_session_summary(current_session)
                continue
            if command == "/memory":
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                store = _memory_store()
                try:
                    if not argument:
                        records = store.list_records(current_session.project)[:10]
                        _render_memory_records(records)
                        continue
                    token = argument.strip()
                    try:
                        kind = MemoryKind(token)
                    except ValueError:
                        kind = None
                    if kind is not None:
                        records = [record for record in store.list_records(current_session.project) if record.kind == kind][:10]
                        _render_memory_records(records)
                        continue
                    record = store.load_record(current_session.project, token)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                _render_memory_detail(record)
                continue
            if command == "/synthesize":
                try:
                    with console.status("[bold blue]Synthesizing current discussion...[/bold blue]"):
                        draft = _discussion_synthesizer().synthesize_from_session(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            user_intent=argument,
                            provider=current_session.participants[0].provider,
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                _render_synthesis_preview(draft)
                if not _confirm_in_shell("Save this synthesis to working memory?", default=True):
                    console.print("[dim]Cancelled synthesis.[/dim]")
                    continue

                try:
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=draft.summary,
                        consensus=draft.consensus,
                        disagreements=draft.disagreements,
                        followups=draft.followups,
                        evidence_refs=_session_evidence_refs(current_session),
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("[green]Discussion synthesis saved.[/green]")
                continue
            if command == "/investigate":
                topic = argument.strip()
                if not topic:
                    console.print("[bold red]Usage:[/bold red] /investigate <topic>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                transcript = service.transcript(current_session.session_id)
                snapshot = service.context_snapshot(current_session.session_id)
                investigation_service = _investigation_service()
                try:
                    related = investigation_service.find_related_reports(current_session.project, topic)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                if related:
                    console.print("")
                    _render_related_reports(related)
                    if not _confirm_in_shell("Investigate further?", default=True):
                        console.print("[dim]Cancelled investigation.[/dim]")
                        continue

                primary_provider = current_session.participants[0].provider.value
                second_provider = (
                    current_session.participants[1].provider.value
                    if len(current_session.participants) > 1
                    else primary_provider
                )

                try:
                    with console.status("[bold blue]Investigating current topic...[/bold blue]"):
                        result = investigation_service.investigate(
                            project=current_session.project,
                            topic=topic,
                            mode=current_session.mode,
                            provider=primary_provider,
                            second_provider=second_provider,
                            source_session_id=current_session.session_id,
                            session_title=current_session.title,
                            transcript_excerpt=_transcript_excerpt(transcript),
                            session_context=_context_snapshot_excerpt(snapshot),
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                _render_investigation_result(result)
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_REPORT_CREATED,
                        actor="labit",
                        summary=f"Investigation report created: {result.title}",
                        payload={
                            "title": result.title,
                            "topic": result.topic,
                            "mode": result.mode.value,
                            "run_id": result.run_id,
                            "report_path": result.report_path,
                            "summary": result.summary,
                        },
                        evidence_refs=_session_evidence_refs(current_session) + [f"report:{result.report_path}"],
                    )
                except Exception:
                    pass
                try:
                    consensus = [result.summary] if result.summary else [f"Investigation completed on topic: {result.topic}"]
                    followups = ["Review the report and decide whether follow-up experiments are needed."]
                    disagreements: list[str] = []
                    if result.mode == ChatMode.ROUND_ROBIN:
                        disagreements.append("Round-robin investigation revised an earlier draft; inspect the run artifacts for any unresolved differences.")
                    elif result.mode == ChatMode.PARALLEL:
                        disagreements.append("Parallel investigation merged two independent drafts; inspect the run artifacts for competing framings.")
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=f"Investigation discussion synthesized around topic: {result.topic}",
                        consensus=consensus,
                        disagreements=disagreements,
                        followups=followups,
                        evidence_refs=_session_evidence_refs(current_session) + [f"report:{result.report_path}"],
                    )
                except Exception:
                    pass
                try:
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=f"Hypothesis discussion crystallized into {detail.record.hypothesis_id}: {detail.record.title}",
                        consensus=[detail.record.claim],
                        disagreements=[],
                        followups=[f"Design or launch an experiment for {detail.record.hypothesis_id}."],
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{detail.record.hypothesis_id}"]
                        + [f"paper:{paper_id}" for paper_id in detail.record.source_paper_ids],
                    )
                except Exception:
                    pass
                continue
            if command in {"/idea", "/note", "/todo"}:
                kind_map = {
                    "/idea": "idea",
                    "/note": "note",
                    "/todo": "todo",
                }
                kind = kind_map[command]
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                capture_service = _capture_service()
                if not argument:
                    try:
                        if kind == "idea":
                            records = capture_service.list_ideas(current_session.project)
                        elif kind == "note":
                            records = capture_service.list_notes(current_session.project)
                        else:
                            records = capture_service.list_todos(current_session.project)
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    _render_capture_records(kind, records)
                    continue

                if kind == "idea":
                    try:
                        with console.status("[bold blue]Drafting idea from current session...[/bold blue]"):
                            draft = _idea_drafter().draft_from_session(
                                session=current_session,
                                transcript=service.transcript(current_session.session_id),
                                context_snapshot=service.context_snapshot(current_session.session_id),
                                user_intent=argument,
                                provider=current_session.participants[0].provider,
                            )
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue

                    console.print("")
                    _render_idea_preview(draft)
                    if not _confirm_in_shell("Save this idea?", default=True):
                        console.print("[dim]Cancelled idea capture.[/dim]")
                        continue

                    try:
                        record = capture_service.save_idea(
                            project=current_session.project,
                            draft=draft,
                            session=current_session,
                            intent=argument,
                        )
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                else:
                    try:
                        if kind == "note":
                            record = capture_service.save_note(
                                project=current_session.project,
                                content=argument,
                                session=current_session,
                            )
                        else:
                            record = capture_service.save_todo(
                                project=current_session.project,
                                content=argument,
                                session=current_session,
                            )
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue

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
                    "note": SessionEventKind.ARTIFACT_NOTE_CREATED,
                    "todo": SessionEventKind.ARTIFACT_TODO_CREATED,
                }
                try:
                    service.record_session_event(
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
                        evidence_refs=_session_evidence_refs(current_session) + [f"{kind}:{record.path}"],
                    )
                except Exception:
                    pass
                continue
            if command == "/hypothesis":
                user_intent = argument
                if user_intent == "new":
                    user_intent = ""
                elif user_intent.startswith("new "):
                    user_intent = user_intent[4:].strip()
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                try:
                    with console.status("[bold blue]Drafting hypothesis from current session...[/bold blue]"):
                        draft = _hypothesis_drafter().draft_from_session(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            user_intent=user_intent,
                            provider=current_session.participants[0].provider,
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                _render_hypothesis_preview(draft, project=current_session.project)
                if not _confirm_in_shell("Create this hypothesis?", default=True):
                    console.print("[dim]Cancelled hypothesis creation.[/dim]")
                    continue

                try:
                    detail = _hypothesis_service().create_hypothesis(
                        project=current_session.project,
                        draft=draft,
                        source_session_id=current_session.session_id,
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                console.print(
                    Panel(
                        (
                            f"[bold]Created[/bold]: {detail.record.hypothesis_id}\n"
                            f"[bold]Path[/bold]: {detail.path}\n"
                            f"[bold]Next[/bold]: labit hypothesis show {detail.record.hypothesis_id}"
                        ),
                        title=f"[bold green]{detail.record.title}[/bold green]",
                        border_style="green",
                    )
                )
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_HYPOTHESIS_CREATED,
                        actor="labit",
                        summary=f"Hypothesis created: {detail.record.hypothesis_id} · {detail.record.title}",
                        payload={
                            "hypothesis_id": detail.record.hypothesis_id,
                            "title": detail.record.title,
                            "path": detail.path,
                            "claim": detail.record.claim,
                            "source_paper_ids": detail.record.source_paper_ids,
                        },
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{detail.record.hypothesis_id}"]
                        + [f"paper:{paper_id}" for paper_id in detail.record.source_paper_ids],
                    )
                except Exception:
                    pass
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
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                _render_shell_header(current_session)
                continue
            if command == "/switch":
                if not argument:
                    console.print("[bold red]Usage:[/bold red] /switch <session_id>")
                    continue
                try:
                    current_session = service.load_session(argument)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                _render_shell_header(current_session)
                console.print("")
                _render_recent_messages(service.transcript(current_session.session_id), count=8)
                continue

            console.print(f"[bold red]Unknown command:[/bold red] {command}")
            continue

        try:
            result = service.ask(session_id=current_session.session_id, content=raw)
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            continue
        current_session = result.session
        console.print("")
        _render_message_block(result.user_message)
        for reply in result.replies:
            _render_message_block(reply.message)


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
    _render_session_summary(session)


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
    table = Table(title="Chat Sessions", show_header=True, header_style="bold magenta")
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

    _render_session_summary(session)
    console.print("")
    _render_transcript(transcript)


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

    _render_session_summary(result.session)
    console.print("")
    console.print(f"[bold][turn {result.user_message.turn_index}] user[/bold]")
    console.print(result.user_message.content)
    console.print("")
    for reply in result.replies:
        console.print(f"[cyan][turn {reply.message.turn_index}] {reply.participant.name}[/cyan]")
        console.print(reply.message.content)
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

    _render_session_summary(session)
    console.print("")
    _render_transcript(transcript[-8:])
    next_message = _prompt_optional("Next message", default="")
    if not next_message:
        console.print("[dim]No new message sent.[/dim]")
        return

    try:
        result = service.ask(session_id=session_id, content=next_message)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=False))

    console.print(f"[bold][turn {result.user_message.turn_index}] user[/bold]")
    console.print(result.user_message.content)
    console.print("")
    for reply in result.replies:
        console.print(f"[cyan][turn {reply.message.turn_index}] {reply.participant.name}[/cyan]")
        console.print(reply.message.content)
        console.print("")
