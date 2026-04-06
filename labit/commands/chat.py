from __future__ import annotations

import json
import re
import threading
import time

import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from labit.rendering import LaTeXMarkdown as Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from labit.capture.drafter import IdeaDrafter
from labit.capture.service import CaptureService
from labit.chat.clipboard import ClipboardImageError, capture_clipboard_image
from labit.chat.composer import ComposerResult, prompt_toolkit_available, prompt_with_clipboard_image
from labit.chat.models import ChatMode
from labit.chat.service import ChatService
from labit.chat.synthesizer import DiscussionSynthesizer
from labit.context.events import SessionEventKind
from labit.documents.drafter import DocDrafter
from labit.documents.models import DocSession, DocStatus
from labit.documents.service import DocumentService
from labit.experiments.executors.ssh import SSHExecutor
from labit.experiments.models import (
    ExperimentDraft,
    ResearchRole,
    TaskDraft,
    TaskKind,
    TaskResources,
    TaskSpec,
    TaskStatus,
)
from labit.experiments.service import ExperimentService
from labit.hypotheses.drafter import HypothesisDrafter
from labit.hypotheses.models import HypothesisResolution, HypothesisState, utc_now_iso
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
_LABIT_THEME = Theme(
    {
        # ── Headers ──────────────────────────────────────────────
        "markdown.h1": "bold bright_cyan",
        "markdown.h1.border": "bright_cyan",
        "markdown.h2": "bold bright_white underline",
        "markdown.h3": "bold dodger_blue2",
        "markdown.h4": "bold grey70",
        "markdown.h5": "grey70 underline",
        "markdown.h6": "dim italic",
        # ── Inline ───────────────────────────────────────────────
        "markdown.strong": "bold #0080ff",
        "markdown.em": "italic dim",
        "markdown.emph": "italic dim",
        "markdown.s": "dim strike",
        # ── Code ─────────────────────────────────────────────────
        "markdown.code": "bold cyan",
        "markdown.code_block": "",
        # ── Blocks ───────────────────────────────────────────────
        "markdown.block_quote": "dim",
        "markdown.hr": "dim cyan",
        # ── Lists ────────────────────────────────────────────────
        "markdown.item.bullet": "bright_cyan",
        "markdown.item.number": "bright_cyan",
        # ── Links ────────────────────────────────────────────────
        "markdown.link": "bright_blue underline",
        "markdown.link_url": "dim blue",
    }
)

_CODE_THEME = "default"

console = Console(theme=_LABIT_THEME)
_COMMAND_COLOR = "#0080ff"
_ACCENT_COLOR = "#a0a000"

_PROVIDER_STYLES = {
    "claude": ("blue", "CLAUDE"),
    "codex": ("green", "CODEX"),
}

_CHAT_SHELL_COMMANDS = (
    "/help",
    "/list",
    "/show",
    "/mode",
    "/memory",
    "/paste-image",
    "/image",
    "/think",
    "/think-long-term",
    "/think-ltm",
    "/long-term-memory",
    "/ltm",
    "/synthesize",
    "/investigate",
    "/idea",
    "/note",
    "/todo",
    "/doc",
    "/hypothesis",
    "/launch-exp",
    "/debrief",
    "/review-results",
    "/new",
    "/switch",
    "/exit",
)


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


def _experiment_service() -> ExperimentService:
    return ExperimentService(RepoPaths.discover())


def _idea_drafter() -> IdeaDrafter:
    return IdeaDrafter(RepoPaths.discover())


def _doc_drafter() -> DocDrafter:
    return DocDrafter(RepoPaths.discover())


def _document_service() -> DocumentService:
    return DocumentService(RepoPaths.discover())


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
            console.print(Panel.fit(_message_body(message), title=f"user · turn {message.turn_index}", border_style="white"))
            continue
        provider_name = message.provider.value if message.provider else "agent"
        color, label = _PROVIDER_STYLES.get(provider_name, ("cyan", provider_name.upper()))
        title = f"{label} · {message.speaker} · turn {message.turn_index}"
        console.print(Panel.fit(message.content, title=title, border_style=color))


def _render_message_block(message) -> None:
    if message.message_type.value == "user":
        console.print(Panel(_message_body(message), title=f"user · turn {message.turn_index}", border_style="white"))
        console.print("")
        return

    console.print(
        _agent_panel(
            message.speaker,
            message.provider.value if message.provider else "agent",
            message.content,
            turn_index=message.turn_index,
        )
    )
    console.print("")


class _ThinkingIndicator:
    """Animated spinner with elapsed time for the generating placeholder."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._spinner = Spinner("dots", style="dim")

    def __rich_console__(self, console: Console, options: object):  # noqa: ANN001
        elapsed = time.monotonic() - self._start
        # Build a single-line Text: spinner frame + label
        text = self._spinner.render(time.monotonic())
        text.append(f" Thinking… {elapsed:.1f}s", style="dim")
        yield text


_FENCE_INLINE_RE = re.compile(r"(`{3,})(.+)$", re.MULTILINE)


def _sanitize_markdown(text: str) -> str:
    """Fix common AI markdown issues that break the Rich parser.

    1. Closing ``` stuck on the end of a code line → move to its own line.
    2. Ensure fenced code blocks are always properly closed.
    """
    # Fix closing ``` appended to the end of a code line, e.g.:
    #   python train.py --config foo.yaml```
    # becomes:
    #   python train.py --config foo.yaml
    #   ```
    in_fence = False
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_fence:
            # Opening fence: ```python or just ```
            if stripped.startswith("```"):
                in_fence = True
                result.append(line)
                # If line is both opening and closing on same line like ```code```
                # count backticks
                if stripped.count("```") >= 2 and len(stripped) > 3:
                    in_fence = False
            else:
                result.append(line)
        else:
            # Inside a fence — check if line ends with ``` (closing stuck to content)
            if stripped == "```":
                in_fence = False
                result.append(line)
            elif stripped.endswith("```") and not stripped.startswith("```"):
                # e.g. "python train.py```" → split into two lines
                result.append(line[: line.rfind("```")])
                result.append("```")
                in_fence = False
            else:
                result.append(line)
    # If still in an unclosed fence, close it
    if in_fence:
        result.append("```")
    return "\n".join(result)


def _md(content: str, *, sanitize: bool = True) -> Markdown:
    """Create a themed Markdown renderable."""
    text = _sanitize_markdown(content) if sanitize else content
    return Markdown(text, code_theme=_CODE_THEME)


def _agent_panel(
    speaker: str,
    provider_name: str,
    content: str,
    *,
    turn_index: int | None = None,
    thinking: _ThinkingIndicator | None = None,
    status_text: str | None = None,
) -> Panel:
    color, label = _PROVIDER_STYLES.get(provider_name, ("cyan", provider_name.upper()))
    title = f"{label} · {speaker}"
    if turn_index is not None:
        title = f"{title} · turn {turn_index}"
    if status_text:
        title = f"{title} · {status_text}"
    body: RenderableType = _md(content) if content.strip() else (thinking or _ThinkingIndicator())
    return Panel(body, title=title, border_style=color)


def _render_user_shell_message(content: str, *, attachments: list | None = None) -> None:
    if attachments:
        body = _message_body(type("ShellMessage", (), {"content": content, "attachments": attachments})())
    else:
        body = content
    console.print(Panel(body, title="user", border_style="white"))
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
        if getattr(message, "attachments", None):
            text.append(f" [{len(message.attachments)} attachment", style="dim")
            if len(message.attachments) != 1:
                text.append("s", style="dim")
            text.append("]", style="dim")
    return text


def _message_body(message) -> str:
    body = message.content
    attachments = getattr(message, "attachments", None) or []
    if not attachments:
        return body
    lines = [body, "", "Attachments:"]
    for attachment in attachments:
        label = attachment.label or attachment.path.rsplit("/", 1)[-1]
        lines.append(f"- {attachment.kind.value}: {label}")
    return "\n".join(lines).strip()


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


def _command_chip(label: str) -> str:
    return f"[bold {_COMMAND_COLOR}]{label}[/bold {_COMMAND_COLOR}]"


def _render_console_header(*, project: str, mode: str, participants: str) -> None:
    console.print(f"[dim]{project} · {mode} · {participants}[/dim]")
    console.print(
        "Shortcuts: "
        + " · ".join(
            [
                _command_chip("/help"),
                _command_chip("/think"),
                _command_chip("/think-ltm"),
                _command_chip("/ltm"),
                _command_chip("/image"),
                _command_chip("/exit"),
            ]
        )
    )
    console.print(
        "Research: "
        + " · ".join(
            [
                _command_chip("/memory"),
                _command_chip("/idea"),
                _command_chip("/todo"),
                _command_chip("/investigate"),
                _command_chip("/hypothesis"),
            ]
        )
    )


def _prompt_in_box(session) -> ComposerResult:
    width = _box_width()
    inner_width = width - 2
    project = session.project or "no-project"
    mode = session.mode.value
    participants = ", ".join(f"{item.name}:{item.provider.value}" for item in session.participants)
    prompt_prefix = " › "
    if not console.is_terminal:
        _render_console_header(project=project, mode=mode, participants=participants)
        console.print(f"[yellow]{_box_top('Input', width)}[/yellow]")
        console.print(f"[yellow]{_box_line('', width)}[/yellow]")
        console.print(f"[yellow]│[/yellow]{prompt_prefix}", end="")
        raw = console.input("")
        console.print(f"[yellow]{_box_line('', width)}[/yellow]")
        console.print(f"[yellow]{_box_bottom(width)}[/yellow]")
        return ComposerResult(text=raw)

    if prompt_toolkit_available():
        _render_console_header(project=project, mode=mode, participants=participants)
        return prompt_with_clipboard_image(
            console=console,
            paths=RepoPaths.discover(),
            session_id=session.session_id,
            prompt_prefix=prompt_prefix,
            slash_commands=_CHAT_SHELL_COMMANDS,
        )

    top = _box_top("Input", width)
    empty = _box_line("", width)
    prompt_fill = " " * max(0, inner_width - len(prompt_prefix))
    prompt_line = f"│{prompt_prefix}{prompt_fill}│"
    bottom = _box_bottom(width)

    stream = console.file
    yellow = "\x1b[33m"
    reset = "\x1b[0m"

    _render_console_header(project=project, mode=mode, participants=participants)
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


def _shell_help() -> None:
    table = Table(show_header=True, header_style=f"bold {_COMMAND_COLOR}")
    table.add_column("Command", style=f"bold {_COMMAND_COLOR}")
    table.add_column("What It Does")
    table.add_row("/help", "Show shell commands.")
    table.add_row("/list", "List existing chat sessions.")
    table.add_row("/new", "Create a new session and switch into it.")
    table.add_row("/switch <session_id>", "Switch to another session.")
    table.add_row("/show", "Show the full transcript for the current session.")
    table.add_row("/mode [mode]", "Show or switch mode (single, round_robin, parallel).")
    table.add_row("/memory [id|kind]", "Show recent project memory, one memory by id, or filter by memory kind.")
    table.add_row("/think <question>", "Ask the next turn with the highest reasoning effort, while keeping the normal chat context shape.")
    table.add_row("/long-term-memory <question>", "Run a deep long-term memory search for this turn, then answer from the richer retrieved context.")
    table.add_row("/think-long-term <question>", "Run the next turn with both deep long-term memory search and the highest reasoning effort.")
    table.add_row("/paste-image [question]", "Read one image from the system clipboard, save it under .labit/, and send it as this turn's image input.")
    table.add_row("/idea [text]", "Save a lightweight project idea. With no text, show saved ideas.")
    table.add_row("/note [text]", "Save a lightweight project note. With no text, show saved notes.")
    table.add_row("/todo [text]", "Save an actionable project todo. With no text, show saved todos.")
    table.add_row("/doc start <title>", "Enter document mode and write a design doc to docs/designs/.")
    table.add_row("/doc open <doc_id>", "Re-open an existing document for editing.")
    table.add_row("/doc status|done", "Show or leave the active document editing session.")
    table.add_row("/doc publish <doc_id>", "Promote a document from draft to active.")
    table.add_row("/doc list", "List all documents in the current project.")
    table.add_row("/synthesize [hint]", "Distill the current discussion into consensus, disagreements, and follow-ups.")
    table.add_row("/investigate <topic>", "Investigate a topic from the current session and write a report.")
    table.add_row("/hypothesis [idea]", "Draft and create a structured hypothesis from the current session.")
    table.add_row("/launch-exp <hypothesis_id>", "Draft a simple experiment from a hypothesis, freeze a launch artifact, and submit it over SSH.")
    table.add_row("/debrief", "Inspect active experiment launches and show their latest runtime state.")
    table.add_row("/review-results <hypothesis_id>", "Summarize experiments linked to a hypothesis, suggest a resolution, and optionally write the decision back.")
    table.add_row("/exit", "Leave the chat shell.")
    console.print(Panel(table, title="LABIT Chat Commands", border_style=_COMMAND_COLOR))


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
        console.print(Panel(_md(draft.rationale_markdown, sanitize=False), title="Rationale", border_style="blue"))
    if draft.experiment_plan_markdown:
        console.print(Panel(_md(draft.experiment_plan_markdown, sanitize=False), title="Experiment Plan", border_style="magenta"))


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


def _render_doc_status(doc_session: DocSession) -> None:
    console.print(
        Panel(
            (
                f"[bold]ID[/bold]: {doc_session.doc_id}\n"
                f"[bold]Title[/bold]: {doc_session.title}\n"
                f"[bold]Status[/bold]: {doc_session.status.value}\n"
                f"[bold]Project[/bold]: {doc_session.project}\n"
                f"[bold]Document[/bold]: {doc_session.document_path}\n"
                f"[bold]Interaction log[/bold]: {doc_session.log_path}\n"
                f"[bold]Iterations[/bold]: {doc_session.iteration}\n"
                f"[bold]Updated[/bold]: {doc_session.updated_at}"
            ),
            title="[bold green]Active Document Session[/bold green]",
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


def _render_experiment_launch_preview(
    *,
    hypothesis_id: str,
    defaults: dict[str, str],
    execution,
) -> None:
    body = (
        f"[bold]Hypothesis[/bold]: {hypothesis_id}\n"
        f"[bold]Title[/bold]: {defaults.get('title') or '(blank)'}\n"
        f"[bold]Objective[/bold]: {defaults.get('objective') or '(blank)'}\n"
        f"[bold]Task kind[/bold]: {defaults.get('task_kind') or '(blank)'}\n"
        f"[bold]Research role[/bold]: {defaults.get('research_role') or '(blank)'}\n"
        f"[bold]Branch[/bold]: {defaults.get('branch') or '(blank)'}\n"
        f"[bold]Config[/bold]: {defaults.get('config_ref') or '(blank)'}\n"
        f"[bold]GPU[/bold]: {defaults.get('gpu') or '(blank)'}\n"
        f"[bold]Output dir[/bold]: {defaults.get('output_dir') or '(blank)'}\n"
        f"[bold]Command[/bold]: {defaults.get('command') or '(blank)'}\n\n"
        f"[bold]Compute[/bold]: {execution.profile}\n"
        f"[bold]Backend[/bold]: {execution.backend.value}\n"
        f"[bold]User[/bold]: {execution.user or '(blank)'}\n"
        f"[bold]Host[/bold]: {execution.host or '(blank)'}\n"
        f"[bold]Workdir[/bold]: {execution.workdir or '(blank)'}\n"
        f"[bold]Setup[/bold]: {'configured' if execution.setup_script else '(blank)'}"
    )
    console.print(Panel(body, title="[bold green]Launch Experiment Preview[/bold green]", border_style="green"))


def _render_review_suggestion(suggestion) -> None:
    body = (
        f"[bold]Hypothesis[/bold]: {suggestion.hypothesis_id}\n"
        f"[bold]Current[/bold]: {suggestion.current_state}/{suggestion.current_resolution}\n"
        f"[bold]Suggested[/bold]: {suggestion.suggested_state}/{suggestion.suggested_resolution}\n"
        f"[bold]Supporting[/bold]: {', '.join(suggestion.supporting_experiment_ids) or '(none)'}\n"
        f"[bold]Contradicting[/bold]: {', '.join(suggestion.contradicting_experiment_ids) or '(none)'}\n"
        f"[bold]Pending[/bold]: {', '.join(suggestion.pending_experiment_ids) or '(none)'}\n"
        f"[bold]Reviewed[/bold]: {', '.join(suggestion.reviewed_experiment_ids) or '(none)'}\n\n"
        f"[bold]Result summary[/bold]: {suggestion.result_summary or '(blank)'}\n\n"
        f"[bold]Decision rationale[/bold]: {suggestion.decision_rationale or '(blank)'}"
    )
    console.print(
        Panel(
            body,
            title=f"[bold green]Review Results · {suggestion.title}[/bold green]",
            border_style="green",
        )
    )
    if suggestion.next_steps:
        console.print("[bold]Next steps[/bold]")
        for item in suggestion.next_steps:
            console.print(f"- {item}")


def _launch_markdown(
    *,
    hypothesis_id: str,
    experiment_id: str,
    task_id: str,
    launch_id: str,
    defaults: dict[str, str],
    execution,
    receipt,
) -> str:
    lines = [
        f"# Launch {experiment_id}",
        "",
        f"- Hypothesis: {hypothesis_id}",
        f"- Task: {task_id}",
        f"- Launch: {launch_id}",
        f"- Accepted: {'yes' if receipt.accepted else 'no'}",
        f"- Compute: {execution.profile}",
        f"- Backend: {execution.backend.value}",
        f"- User: {execution.user or '(blank)'}",
        f"- Host: {receipt.remote_host or execution.host or '(blank)'}",
        f"- Setup: {'configured' if execution.setup_script else '(blank)'}",
        f"- Branch: {defaults.get('branch') or '(blank)'}",
        f"- Config: {defaults.get('config_ref') or '(blank)'}",
        f"- GPU: {defaults.get('gpu') or '(blank)'}",
        f"- Output dir: {defaults.get('output_dir') or '(blank)'}",
        f"- PID: {receipt.pid or '(none)'}",
        f"- Log: {receipt.log_path or '(none)'}",
        "",
        "## Command",
        "",
        "```bash",
        defaults.get("command", "").strip(),
        "```",
    ]
    if receipt.stderr_tail:
        lines.extend(["", "## Submission stderr", "", "```text", receipt.stderr_tail.strip(), "```"])
    return "\n".join(lines).rstrip()


def _debrief_markdown(*, experiment_id: str, rows: list[str]) -> str:
    lines = [f"# Debrief {experiment_id}", ""]
    if not rows:
        lines.append("No active launches found.")
        return "\n".join(lines)
    lines.extend(rows)
    return "\n".join(lines)


def _review_markdown(*, hypothesis_id: str, suggestion, saved) -> str:
    lines = [
        f"# Review {hypothesis_id}",
        "",
        f"- Current -> Suggested: {suggestion.current_state}/{suggestion.current_resolution} -> {suggestion.suggested_state}/{suggestion.suggested_resolution}",
        f"- Final state: {saved.record.state.value}",
        f"- Final resolution: {saved.record.resolution.value}",
        f"- Supporting experiments: {', '.join(saved.record.supporting_experiment_ids) or '(none)'}",
        f"- Contradicting experiments: {', '.join(saved.record.contradicting_experiment_ids) or '(none)'}",
        f"- Reviewed experiments: {', '.join(suggestion.reviewed_experiment_ids) or '(none)'}",
        "",
        "## Result Summary",
        "",
        saved.record.result_summary or "(blank)",
        "",
        "## Decision Rationale",
        "",
        saved.record.decision_rationale or "(blank)",
    ]
    return "\n".join(lines)


def _flatten_numeric_metrics(value, *, prefix: str = "", depth: int = 0, max_depth: int = 2) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if depth > max_depth:
        return metrics
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            nested_prefix = f"{prefix}.{key_text}" if prefix else key_text
            metrics.update(_flatten_numeric_metrics(item, prefix=nested_prefix, depth=depth + 1, max_depth=max_depth))
    elif isinstance(value, list):
        return metrics
    elif isinstance(value, bool):
        return metrics
    elif isinstance(value, (int, float)):
        metrics[prefix or "value"] = float(value)
    return metrics


def _collect_task_metrics(collected: dict) -> dict[str, float]:
    metrics: dict[str, float] = {}
    files = collected.get("files", {}) or {}
    for path, content in files.items():
        try:
            parsed = json.loads(content)
        except Exception:
            continue
        base = str(path).rsplit("/", 1)[-1].rsplit(".", 1)[0]
        for key, value in _flatten_numeric_metrics(parsed).items():
            metric_key = f"{base}.{key}" if key and key != "value" else base
            metrics[metric_key] = value
    manifest_count = collected.get("manifest_line_count")
    if isinstance(manifest_count, int):
        metrics["manifest.line_count"] = float(manifest_count)
    return metrics


def _task_summary_from_collect(task, collected: dict, metrics: dict[str, float]) -> str:
    status = str(collected.get("status", "unknown")).strip() or "unknown"
    if status == "running":
        log_tail = str(collected.get("log_tail", "") or "").strip()
        if log_tail:
            last_line = log_tail.splitlines()[-1].strip()
            return f"Task is still running. Latest log line: {last_line}"
        return "Task is still running."
    if metrics:
        preview = ", ".join(f"{key}={value:.4g}" for key, value in list(metrics.items())[:4])
        return f"Collected result metrics: {preview}"
    if collected.get("output_dir_exists"):
        refs = collected.get("artifact_refs", []) or []
        return f"Task stopped and produced artifacts ({len(refs)} files discovered)."
    log_tail = str(collected.get("log_tail", "") or "").strip()
    if log_tail:
        last_line = log_tail.splitlines()[-1].strip()
        return f"Task stopped without recognized result files. Latest log line: {last_line}"
    return f"Task stopped without recognized result files for {task.task_kind.value}."


def _task_error_from_collect(collected: dict) -> str:
    if str(collected.get("status", "")).strip() == "running":
        return ""
    stderr = str(collected.get("stderr", "") or "").strip()
    if stderr:
        return stderr
    log_tail = str(collected.get("log_tail", "") or "").strip()
    if not log_tail:
        return ""
    error_markers = ("traceback", "error", "exception", "failed", "fatal")
    if any(marker in log_tail.lower() for marker in error_markers):
        return log_tail
    return ""


def _transcript_excerpt(messages, *, limit: int = 16, max_chars: int = 6000) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for message in messages[-limit:]:
        speaker = message.speaker
        if message.provider:
            speaker = f"{speaker} ({message.provider.value})"
        line = f"{speaker}: {message.content.strip()}"
        if getattr(message, "attachments", None):
            attachment_labels = ", ".join(
                attachment.label or attachment.path.rsplit("/", 1)[-1] for attachment in message.attachments
            )
            line = f"{line} [attachments: {attachment_labels}]"
        lines.append(line)
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


def _run_streaming_turn(
    *,
    service: ChatService,
    session,
    query: str,
    attachments: list | None = None,
    force_deep_context: bool = False,
    reasoning_effort: str | None = None,
) -> object | None:
    _render_user_shell_message(query, attachments=attachments)
    participant_state = {
        participant.name: {
            "provider": participant.provider.value,
            "content": "",
            "status": "queued",
            "started_at": None,
            "thinking": None,
        }
        for participant in session.participants
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
        for participant in session.participants:
            state = snapshot[participant.name]
            status = state["status"]
            if status == "queued":
                continue
            started_at = state["started_at"]
            status_text = status
            if started_at is not None and status in {"thinking", "streaming"}:
                status_text = f"{status} · {time.monotonic() - started_at:.1f}s"
            panels.append(
                _agent_panel(
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
            participant_state[participant.name]["thinking"] = _ThinkingIndicator()
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
                _agent_panel(
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
    _render_shell_header(session)
    transcript = service.transcript(session.session_id)
    console.print("")
    _render_recent_messages(transcript, count=8)
    console.print("")

    current_session = session
    active_doc: DocSession | None = None
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
                if not argument:
                    _render_session_summary(current_session)
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
            if command == "/doc":
                doc_parts = argument.split(maxsplit=1)
                doc_action = doc_parts[0].strip().lower() if doc_parts else "status"
                doc_argument = doc_parts[1].strip() if len(doc_parts) > 1 else ""
                if doc_action in {"status", ""}:
                    if active_doc is None:
                        console.print("[dim]No active document session. Use /doc start <title> or /doc open <id>.[/dim]")
                    else:
                        _render_doc_status(active_doc)
                    continue
                if doc_action == "done":
                    if active_doc is None:
                        console.print("[dim]No active document session.[/dim]")
                    else:
                        try:
                            _document_service().end_session(active_doc)
                        except Exception:
                            pass
                        console.print(
                            f"[green]Document session closed.[/green] "
                            f"[dim]{active_doc.doc_id} · {active_doc.document_path} ({active_doc.status.value})[/dim]"
                        )
                        active_doc = None
                    continue
                if doc_action == "publish":
                    publish_target = doc_argument.strip()
                    if not publish_target:
                        if active_doc is None:
                            console.print("[bold red]Usage:[/bold red] /doc publish <doc_id>")
                            continue
                        publish_target = active_doc.doc_id
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                        continue
                    try:
                        published_doc = _document_service().publish_document(
                            project=current_session.project,
                            doc_id=publish_target,
                            source_session=current_session,
                        )
                        if active_doc is not None and active_doc.doc_id == published_doc.doc_id:
                            active_doc = published_doc
                        console.print(f"[green]Document published.[/green] [dim]{published_doc.doc_id} → active[/dim]")
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                if doc_action == "list":
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                        continue
                    try:
                        docs = _document_service().list_documents(current_session.project)
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    if not docs:
                        console.print("[dim]No documents found.[/dim]")
                    else:
                        from rich.table import Table as RichTable

                        t = RichTable(title="Documents", border_style="dim")
                        t.add_column("ID", style="bold")
                        t.add_column("Title")
                        t.add_column("Status")
                        t.add_column("Updated")
                        for d in docs:
                            t.add_row(
                                d.get("doc_id", "?"),
                                d.get("title", "?"),
                                d.get("status", "?"),
                                d.get("updated_at", "?"),
                            )
                        console.print(t)
                    continue
                if doc_action == "open":
                    doc_id = doc_argument.strip()
                    if not doc_id:
                        console.print("[bold red]Usage:[/bold red] /doc open <doc_id>")
                        continue
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                        continue
                    if active_doc is not None:
                        console.print("[bold red]Error:[/bold red] A document session is already active. Use /doc done first.")
                        continue
                    try:
                        active_doc = _document_service().open_document(
                            project=current_session.project,
                            doc_id=doc_id,
                            session=current_session,
                        )
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    status_note = ""
                    if active_doc.status == DocStatus.DRAFT:
                        status_note = " (demoted to draft for editing)"
                    console.print(
                        Panel(
                            (
                                f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                                f"[bold]Title[/bold]: {active_doc.title}\n"
                                f"[bold]Status[/bold]: {active_doc.status.value}{status_note}\n"
                                f"[bold]Document[/bold]: {active_doc.document_path}\n\n"
                                "Type feedback to revise. Use /doc done to leave document mode."
                            ),
                            title="[bold green]Document opened[/bold green]",
                            border_style="green",
                        )
                    )
                    continue
                if doc_action != "start":
                    console.print("[bold red]Usage:[/bold red] /doc start <title> | /doc open <id> | /doc status | /doc done | /doc publish <id> | /doc list")
                    continue
                title = doc_argument
                if not title:
                    console.print("[bold red]Usage:[/bold red] /doc start <title>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                if active_doc is not None:
                    console.print("[bold red]Error:[/bold red] A document session is already active. Use /doc done first.")
                    continue

                doc_service = _document_service()
                try:
                    with console.status(f"[bold blue]{current_session.participants[0].name} writing document draft...[/bold blue]"):
                        update = _doc_drafter().draft_from_session(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            title=title,
                            provider=current_session.participants[0].provider,
                        )
                        active_doc = doc_service.start_document(
                            project=current_session.project,
                            title=title,
                            update=update,
                            session=current_session,
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print(
                    Panel(
                        (
                            f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                            f"[bold]Document[/bold]: {active_doc.document_path}\n"
                            f"[bold]Interaction log[/bold]: {active_doc.log_path}\n"
                            f"[bold]Summary[/bold]: {update.summary}\n\n"
                            "Continue typing feedback to revise the document. Use /doc done to leave document mode."
                        ),
                        title="[bold green]Document draft saved[/bold green]",
                        border_style="green",
                    )
                )
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_DOCUMENT_CREATED,
                        actor="labit",
                        summary=f"Document draft created: {update.title}",
                        payload={
                            "doc_id": active_doc.doc_id,
                            "title": update.title,
                            "document_path": active_doc.document_path,
                            "log_path": active_doc.log_path,
                        },
                        evidence_refs=_session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
                    )
                except Exception:
                    pass
                continue
            if command in {"/paste-image", "/image"}:
                query = argument.strip() or "Please inspect the attached image and describe anything important."
                try:
                    attachment = capture_clipboard_image(
                        paths=RepoPaths.discover(),
                        session_id=current_session.session_id,
                    )
                except ClipboardImageError as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print(
                    Panel(
                        f"[bold]Saved[/bold]: {attachment.label or attachment.path}\n[bold]Path[/bold]: {attachment.path}",
                        title="[bold green]Clipboard image attached[/bold green]",
                        border_style="green",
                    )
                )
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=[attachment],
                )
                if result is not None:
                    current_session = result.session
                continue
            if command == "/think":
                query = argument.strip()
                if not query:
                    console.print("[bold red]Usage:[/bold red] /think <question>")
                    continue
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=attachments,
                    reasoning_effort=ChatService.THINK_REASONING_EFFORT,
                )
                if result is not None:
                    current_session = result.session
                continue
            if command in {"/think-long-term", "/think-ltm"}:
                query = argument.strip()
                if not query:
                    console.print("[bold red]Usage:[/bold red] /think-long-term <question>")
                    continue
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=attachments,
                    force_deep_context=True,
                    reasoning_effort=ChatService.THINK_REASONING_EFFORT,
                )
                if result is not None:
                    current_session = result.session
                continue
            if command in {"/long-term-memory", "/ltm"}:
                query = argument.strip()
                if not query:
                    console.print("[bold red]Usage:[/bold red] /long-term-memory <question>")
                    continue
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=attachments,
                    force_deep_context=True,
                )
                if result is not None:
                    current_session = result.session
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
                    drafter = _hypothesis_drafter()
                    transcript = service.transcript(current_session.session_id)
                    ctx_snap = service.context_snapshot(current_session.session_id)
                    with console.status(f"[bold blue]{current_session.participants[0].name} drafting hypothesis...[/bold blue]"):
                        draft = drafter.draft_from_session(
                            session=current_session,
                            transcript=transcript,
                            context_snapshot=ctx_snap,
                            user_intent=user_intent,
                            provider=current_session.participants[0].provider,
                        )
                    use_round_robin = (
                        current_session.mode == ChatMode.ROUND_ROBIN
                        and len(current_session.participants) >= 2
                    )
                    if use_round_robin:
                        reviewer = current_session.participants[1]
                        with console.status(f"[bold blue]{reviewer.name} reviewing and refining...[/bold blue]"):
                            draft = drafter.refine_draft(
                                draft=draft,
                                session=current_session,
                                transcript=transcript,
                                context_snapshot=ctx_snap,
                                user_intent=user_intent,
                                provider=reviewer.provider,
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
            if command == "/launch-exp":
                hypothesis_id = argument.strip()
                if not hypothesis_id:
                    console.print("[bold red]Usage:[/bold red] /launch-exp <hypothesis_id>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                experiment_service = _experiment_service()
                try:
                    execution = experiment_service.build_default_execution_profile(current_session.project)
                    defaults = experiment_service.suggest_task_defaults(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                _render_experiment_launch_preview(
                    hypothesis_id=hypothesis_id,
                    defaults=defaults,
                    execution=execution,
                )

                if not defaults.get("command"):
                    console.print("[yellow]No runnable command was inferred from the hypothesis. Fill it in before launch.[/yellow]")
                    defaults["command"] = _prompt_text("Command")
                elif _confirm_in_shell("Edit launch fields before submitting?", default=False):
                    defaults["command"] = _prompt_optional("Command", default=defaults.get("command", ""))
                    defaults["branch"] = _prompt_optional("Branch", default=defaults.get("branch", ""))
                    defaults["config_ref"] = _prompt_optional("Config", default=defaults.get("config_ref", ""))
                    defaults["gpu"] = _prompt_optional("GPU", default=defaults.get("gpu", ""))
                    defaults["output_dir"] = _prompt_optional("Output dir", default=defaults.get("output_dir", ""))

                if not defaults.get("command", "").strip():
                    console.print("[bold red]Error:[/bold red] Launch command cannot be empty.")
                    continue

                try:
                    draft = ExperimentDraft(
                        title=defaults["title"],
                        objective=defaults["objective"],
                        execution=execution,
                        source_session_id=current_session.session_id,
                        source_paper_ids=[item for item in defaults.get("source_paper_ids", "").split(",") if item],
                        tasks=[
                            TaskDraft(
                                title=defaults["title"],
                                task_kind=TaskKind(defaults.get("task_kind", TaskKind.CUSTOM.value)),
                                research_role=ResearchRole.EVIDENCE,
                                spec=TaskSpec(
                                    branch=defaults.get("branch", ""),
                                    config_ref=defaults.get("config_ref", ""),
                                    command=defaults.get("command", ""),
                                    output_dir=defaults.get("output_dir", ""),
                                ),
                                resources=TaskResources(profile="default", gpu=defaults.get("gpu", "")),
                            )
                        ],
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                if not _confirm_in_shell("Create experiment and submit the first task?", default=True):
                    console.print("[dim]Cancelled launch.[/dim]")
                    continue

                try:
                    detail = experiment_service.create_experiment(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                        draft=draft,
                    )
                    first_task_id = detail.tasks[0].task_id
                    artifact = experiment_service.materialize_launch_artifact(
                        project=current_session.project,
                        experiment_id=detail.record.experiment_id,
                        task_id=first_task_id,
                    )
                    receipt = SSHExecutor(RepoPaths.discover()).submit(artifact)
                    artifact = experiment_service.record_submission_receipt(
                        project=current_session.project,
                        experiment_id=detail.record.experiment_id,
                        launch_id=artifact.launch_id,
                        receipt=receipt,
                    )
                    experiment_service.write_launch_markdown(
                        project=current_session.project,
                        experiment_id=detail.record.experiment_id,
                        content=_launch_markdown(
                            hypothesis_id=hypothesis_id,
                            experiment_id=detail.record.experiment_id,
                            task_id=first_task_id,
                            launch_id=artifact.launch_id,
                            defaults=defaults,
                            execution=execution,
                            receipt=receipt,
                        ),
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print(
                    Panel(
                        (
                            f"[bold]Experiment[/bold]: {detail.record.experiment_id}\n"
                            f"[bold]Task[/bold]: {first_task_id}\n"
                            f"[bold]Launch[/bold]: {artifact.launch_id}\n"
                            f"[bold]Accepted[/bold]: {receipt.accepted}\n"
                            f"[bold]Host[/bold]: {receipt.remote_host or '(blank)'}\n"
                            f"[bold]PID[/bold]: {receipt.pid or '(none)'}\n"
                            f"[bold]Log[/bold]: {receipt.log_path or '(none)'}\n"
                            f"[bold]stderr[/bold]: {receipt.stderr_tail or '(blank)'}\n"
                            f"[bold]Path[/bold]: vault/projects/{current_session.project}/experiments/{detail.record.experiment_id}"
                        ),
                        title="[bold green]Experiment launch[/bold green]" if receipt.accepted else "[bold red]Experiment launch failed[/bold red]",
                        border_style="green" if receipt.accepted else "red",
                    )
                )
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_EXPERIMENT_CREATED,
                        actor="labit",
                        summary=f"Experiment created: {detail.record.experiment_id} for {hypothesis_id}",
                        payload={
                            "experiment_id": detail.record.experiment_id,
                            "hypothesis_id": hypothesis_id,
                            "task_id": first_task_id,
                            "launch_id": artifact.launch_id,
                            "accepted": receipt.accepted,
                            "remote_host": receipt.remote_host,
                            "pid": receipt.pid,
                            "log_path": receipt.log_path,
                        },
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{hypothesis_id}", f"experiment:{detail.record.experiment_id}"],
                    )
                except Exception:
                    pass
                if receipt.accepted:
                    try:
                        service.record_session_event(
                            session_id=current_session.session_id,
                            kind=SessionEventKind.ARTIFACT_TASK_LAUNCHED,
                            actor="labit",
                            summary=f"Task launched: {detail.record.experiment_id}/{first_task_id}/{artifact.launch_id}",
                            payload={
                                "experiment_id": detail.record.experiment_id,
                                "task_id": first_task_id,
                                "launch_id": artifact.launch_id,
                                "remote_host": receipt.remote_host,
                                "pid": receipt.pid,
                                "log_path": receipt.log_path,
                            },
                            evidence_refs=_session_evidence_refs(current_session)
                            + [f"experiment:{detail.record.experiment_id}", f"launch:{artifact.launch_id}"],
                        )
                    except Exception:
                        pass
                continue
            if command == "/debrief":
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                experiment_service = _experiment_service()
                try:
                    experiments = experiment_service.list_experiments(current_session.project)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                rows: list[str] = []
                rows_by_experiment: dict[str, list[str]] = {}
                executor = SSHExecutor(RepoPaths.discover())
                for summary in experiments:
                    detail = experiment_service.load_experiment(current_session.project, summary.experiment_id)
                    for task in detail.tasks:
                        if not task.latest_launch_id:
                            continue
                        task_record = experiment_service.load_task(
                            current_session.project,
                            detail.record.experiment_id,
                            task.task_id,
                        )
                        artifact = experiment_service.load_launch_artifact(
                            current_session.project,
                            detail.record.experiment_id,
                            task.latest_launch_id,
                        )
                        if not artifact.submission or not artifact.submission.accepted:
                            continue
                        collected = executor.collect(artifact)
                        collected_status = str(collected.get("status", "unknown")).strip() or "unknown"
                        metrics = _collect_task_metrics(collected)
                        summary_text = _task_summary_from_collect(task_record, collected, metrics)
                        error_text = _task_error_from_collect(collected)

                        next_status = task_record.status
                        runtime_updates = {
                            "started_at": task_record.runtime.started_at or (
                                utc_now_iso() if collected_status == "running" else task_record.runtime.started_at
                            ),
                        }
                        if collected_status == "running":
                            next_status = TaskStatus.RUNNING
                        elif collected_status == "stopped":
                            next_status = (
                                TaskStatus.COMPLETED
                                if metrics or collected.get("output_dir_exists") or collected.get("artifact_refs")
                                else TaskStatus.FAILED
                            )
                            runtime_updates["finished_at"] = task_record.runtime.finished_at or utc_now_iso()
                        updated_task = task_record.model_copy(
                            update={
                                "status": next_status,
                                "runtime": task_record.runtime.model_copy(update=runtime_updates),
                                "results": task_record.results.model_copy(
                                    update={
                                        "metrics": metrics or task_record.results.metrics,
                                        "artifact_refs": list(dict.fromkeys(
                                            [*task_record.results.artifact_refs, *(collected.get("artifact_refs", []) or [])]
                                        )),
                                        "summary": summary_text,
                                        "error": error_text,
                                    }
                                ),
                            }
                        )
                        try:
                            experiment_service.save_task_record(
                                project=current_session.project,
                                task=updated_task,
                            )
                        except Exception:
                            pass

                        row = (
                            f"- {detail.record.experiment_id}/{task.task_id}/{artifact.launch_id} · "
                            f"{next_status.value} · {summary_text}"
                        )
                        rows.append(f"[bold]{row.split(' · ', 1)[0]}[/bold] · {row.split(' · ', 1)[1]}")
                        rows_by_experiment.setdefault(detail.record.experiment_id, []).append(row)

                console.print("[bold]Debrief[/bold]")
                if not rows:
                    console.print("[dim]No active launches found.[/dim]")
                else:
                    for row in rows:
                        console.print(row)
                    for experiment_id, experiment_rows in rows_by_experiment.items():
                        try:
                            experiment_service.write_debrief_markdown(
                                project=current_session.project,
                                experiment_id=experiment_id,
                                content=_debrief_markdown(
                                    experiment_id=experiment_id,
                                    rows=experiment_rows,
                                ),
                            )
                        except Exception:
                            pass
                continue
            if command == "/review-results":
                hypothesis_id = argument.strip()
                if not hypothesis_id:
                    console.print("[bold red]Usage:[/bold red] /review-results <hypothesis_id>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                experiment_service = _experiment_service()
                hypothesis_service = _hypothesis_service()
                try:
                    suggestion = experiment_service.suggest_hypothesis_review(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                    )
                    hypothesis_detail = hypothesis_service.load_hypothesis(current_session.project, hypothesis_id)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                _render_review_suggestion(suggestion)
                if not _confirm_in_shell("Write this review decision back to the hypothesis?", default=False):
                    console.print("[dim]Kept as suggestion only.[/dim]")
                    continue

                try:
                    now = utc_now_iso()
                    next_state = HypothesisState(suggestion.suggested_state)
                    next_resolution = HypothesisResolution(suggestion.suggested_resolution)
                    updated_record = hypothesis_detail.record.model_copy(
                        update={
                            "state": next_state,
                            "resolution": next_resolution,
                            "result_summary": suggestion.result_summary,
                            "decision_rationale": suggestion.decision_rationale,
                            "supporting_experiment_ids": suggestion.supporting_experiment_ids,
                            "contradicting_experiment_ids": suggestion.contradicting_experiment_ids,
                            "closed_at": (
                                (hypothesis_detail.record.closed_at or now)
                                if next_state == HypothesisState.CLOSED
                                else None
                            ),
                            "updated_at": now,
                        }
                    )
                    saved = hypothesis_service.update_hypothesis_record(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                        record=updated_record,
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                review_markdown = _review_markdown(
                    hypothesis_id=hypothesis_id,
                    suggestion=suggestion,
                    saved=saved,
                )
                for experiment_id in suggestion.reviewed_experiment_ids:
                    try:
                        experiment_service.write_review_markdown(
                            project=current_session.project,
                            experiment_id=experiment_id,
                            content=review_markdown,
                        )
                    except Exception:
                        pass

                console.print(
                    Panel(
                        (
                            f"[bold]Hypothesis[/bold]: {saved.record.hypothesis_id}\n"
                            f"[bold]State[/bold]: {saved.record.state.value}\n"
                            f"[bold]Resolution[/bold]: {saved.record.resolution.value}\n"
                            f"[bold]Result[/bold]: {saved.record.result_summary or '(blank)'}"
                        ),
                        title="[bold green]Review saved[/bold green]",
                        border_style="green",
                    )
                )
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_HYPOTHESIS_REVIEWED,
                        actor="labit",
                        summary=f"Hypothesis reviewed: {saved.record.hypothesis_id} -> {saved.record.state.value}/{saved.record.resolution.value}",
                        payload={
                            "hypothesis_id": saved.record.hypothesis_id,
                            "state": saved.record.state.value,
                            "resolution": saved.record.resolution.value,
                            "supporting_experiment_ids": saved.record.supporting_experiment_ids,
                            "contradicting_experiment_ids": saved.record.contradicting_experiment_ids,
                        },
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{saved.record.hypothesis_id}"]
                        + [f"experiment:{item}" for item in suggestion.reviewed_experiment_ids],
                    )
                except Exception:
                    pass
                try:
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=(
                            f"Hypothesis {saved.record.hypothesis_id} reviewed as "
                            f"{saved.record.state.value}/{saved.record.resolution.value}."
                        ),
                        consensus=[saved.record.result_summary] if saved.record.result_summary else [],
                        disagreements=[],
                        followups=suggestion.next_steps,
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{saved.record.hypothesis_id}"]
                        + [f"experiment:{item}" for item in suggestion.reviewed_experiment_ids],
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
                    active_doc = None
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
                    active_doc = None
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

        if active_doc is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Document mode does not support image attachments yet.")
                continue
            doc_service = _document_service()
            try:
                with console.status(f"[bold blue]{current_session.participants[0].name} updating document...[/bold blue]"):
                    update = _doc_drafter().revise_document(
                        session=current_session,
                        transcript=service.transcript(current_session.session_id),
                        context_snapshot=service.context_snapshot(current_session.session_id),
                        doc_title=active_doc.title,
                        current_markdown=doc_service.read_document(active_doc),
                        user_instruction=raw,
                        interaction_log=doc_service.interaction_excerpt(active_doc),
                        provider=current_session.participants[0].provider,
                    )
                    active_doc = doc_service.revise_document(
                        doc_session=active_doc,
                        update=update,
                        user_instruction=raw,
                    )
            except Exception as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                continue

            console.print(
                Panel(
                    (
                        f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                        f"[bold]Document[/bold]: {active_doc.document_path}\n"
                        f"[bold]Iteration[/bold]: {active_doc.iteration}\n"
                        f"[bold]Summary[/bold]: {update.summary}"
                    ),
                    title="[bold green]Document updated[/bold green]",
                    border_style="green",
                )
            )
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
        )
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
    _render_session_summary(session)
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
    table = Table(title="Chat Sessions", show_header=True, header_style=f"bold {_COMMAND_COLOR}")
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
        console.print(_md(reply.message.content))
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
