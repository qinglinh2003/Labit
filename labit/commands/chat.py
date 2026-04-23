from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.pretty import Pretty
from labit.rendering import LaTeXMarkdown as Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from labit.automation import AutoActor, AutoIterationEngine
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
    ExperimentTaskPlan,
    LaunchExpPhase,
    LaunchExpSession,
    ResearchRole,
    TaskDraft,
    TaskKind,
    TaskResources,
    TaskSpec,
    TaskStatus,
)
from labit.experiments.planner import ExperimentPlanner
from labit.experiments.service import ExperimentService
from labit.hypotheses.drafter import HypothesisDrafter
from labit.hypotheses.models import HypothesisDraft
from labit.hypotheses.models import HypothesisResolution, HypothesisState, utc_now_iso
from labit.hypotheses.service import HypothesisService
from labit.investigations.service import InvestigationService
from labit.memory.models import MemoryKind
from labit.memory.store import MemoryStore
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

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
    "/doc auto",
    "/doc start",
    "/doc open",
    "/doc done",
    "/doc status",
    "/doc publish",
    "/doc list",
    "/hypothesis",
    "/launch-exp",
    "/swap",
    "/mute",
    "/launch-exp resume",
    "/launch-exp approve-tasks",
    "/launch-exp approve-task",
    "/launch-exp reopen-task",
    "/launch-exp generate-script",
    "/launch-exp run-task",
    "/launch-exp status",
    "/launch-exp done",
    "/auto",
    "/auto start",
    "/auto run",
    "/auto log",
    "/auto status",
    "/auto stop",
    "/debrief",
    "/review-results",
    "/dev",
    "/dev start",
    "/dev status",
    "/dev continue",
    "/dev stop",
    "/dev finish",
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


def _auto_engine() -> AutoIterationEngine:
    return AutoIterationEngine(RepoPaths.discover())


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
                _command_chip("/doc"),
                _command_chip("/investigate"),
                _command_chip("/hypothesis"),
            ]
        )
    )
    console.print(
        "Multi-agent: "
        + " · ".join(
            [
                _command_chip("/mode"),
                _command_chip("/swap"),
                _command_chip("/mute"),
                _command_chip("/dev"),
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
    table.add_row("/swap", "Swap the response order of participants (e.g. claude,codex → codex,claude).")
    table.add_row("/mute <name>", "Mute an agent for the next turn only. Toggle: run again to unmute.")
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
    table.add_row("/hypothesis [idea]", "Draft a hypothesis and enter editing mode for iterative refinement.")
    table.add_row("/hypothesis open <id>", "Re-open an existing hypothesis for editing.")
    table.add_row("/hypothesis status", "Show current hypothesis being edited.")
    table.add_row("/hypothesis done", "Leave hypothesis editing mode.")
    table.add_row("/launch-exp <hypothesis_id>", "Start interactive experiment planning from a hypothesis.")
    table.add_row("/launch-exp resume <experiment_id>", "Resume a failed/existing experiment for revision or resubmission.")
    table.add_row("/launch-exp approve-tasks", "Approve the current task breakdown and move to detailed planning.")
    table.add_row("/launch-exp approve-task <id>", "Approve a specific task's detailed plan.")
    table.add_row("/launch-exp reopen-task <id>", "Reopen a previously approved task for re-planning.")
    table.add_row("/launch-exp generate-script", "Generate run.sh from approved task plans.")
    table.add_row("/launch-exp run-task <id>", "Submit only one task from the run.sh, reusing earlier outputs.")
    table.add_row("/launch-exp status", "Show current experiment planning status.")
    table.add_row("/launch-exp done", "Finalize the experiment and exit planning mode.")
    table.add_row("/auto start <doc_path>", "Start auto-iteration from a design doc (or <constraint> || <success>).")
    table.add_row("/auto run [N]", "Run N auto-iteration rounds.")
    table.add_row("/auto log [N]", "Show detailed view of last N iterations.")
    table.add_row("/auto status", "Show auto-iteration session overview and timeline.")
    table.add_row("/auto stop", "Stop the current auto-iteration session.")
    table.add_row("/debrief", "Inspect active experiment launches and show their latest runtime state.")
    table.add_row("/review-results <hypothesis_id>", "Summarize experiments linked to a hypothesis, suggest a resolution, and optionally write the decision back.")
    table.add_row("/dev start <task>", "Start autonomous dev loop in an isolated worktree (writer+reviewer auto-iterate).")
    table.add_row("/dev status", "Show current dev loop status.")
    table.add_row("/dev continue", "Resume dev loop after a decision point.")
    table.add_row("/dev finish", "Merge, keep, or discard the dev worktree branch.")
    table.add_row("/dev stop", "Stop the current dev loop.")
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


def _print_hypothesis_mode_hints(console: Console, session, hypothesis_id: str) -> None:
    """Print helpful hints when entering hypothesis editing mode."""
    lines = [
        f"[dim]──── Hypothesis Mode · {hypothesis_id} ────[/dim]",
        "[dim]  Type feedback to revise the hypothesis (agent updates files, not chat).[/dim]",
        "[dim]  /hypothesis status  — show current hypothesis info[/dim]",
        "[dim]  /hypothesis done    — leave hypothesis editing mode[/dim]",
        "[dim]  Ctrl+C              — interrupt current revision[/dim]",
    ]
    if session.mode == ChatMode.ROUND_ROBIN and len(session.participants) >= 2:
        author = session.participants[0].name
        reviewer = session.participants[1].name
        lines.append(f"[dim]  Round-robin: {author} revises → {reviewer} reviews[/dim]")
    console.print("\n".join(lines))
    console.print("")


def _print_doc_mode_hints(console: Console, session) -> None:
    """Print helpful hints when entering document editing mode."""
    lines = [
        "[dim]──── Document Mode ────[/dim]",
        "[dim]  Type feedback to revise the document (agent writes to file, not chat).[/dim]",
        "[dim]  /doc status   — show current document info[/dim]",
        "[dim]  /doc auto [N] — auto-iterate N rounds (default 5, max 10); Ctrl+C to stop[/dim]",
        "[dim]  /doc done     — leave document mode (status unchanged)[/dim]",
        "[dim]  /doc publish   — mark document as active (usable after /doc done)[/dim]",
        "[dim]  Ctrl+C        — interrupt current revision[/dim]",
    ]
    if session.mode == ChatMode.ROUND_ROBIN and len(session.participants) >= 2:
        author = session.participants[0].name
        reviewer = session.participants[1].name
        lines.append(f"[dim]  Round-robin: {author} revises → {reviewer} reviews (review blocks in doc)[/dim]")
    console.print("\n".join(lines))
    console.print("")


def _submit_and_monitor(
    *,
    console: Console,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession,
    current_session,
    service: ChatService,
    planner: ExperimentPlanner,
    _hypothesis_service,
    first_participant,
    max_retries: int = 3,
    stabilize_seconds: int = 90,
    poll_interval: int = 15,
    env_overrides: dict[str, str] | None = None,
) -> bool:
    """Submit experiment, monitor for early failures, auto-fix and resubmit.

    After submission, polls the remote log for *stabilize_seconds*. If the
    process dies early, shows the error log and lets the agent revise run.sh,
    then resubmits. Gives up after *max_retries* total attempts.
    """
    from labit.paths import RepoPaths

    executor = SSHExecutor(RepoPaths.discover())
    attempt = 0
    launch_env = {str(k): str(v) for k, v in (env_overrides or {}).items() if str(v).strip()}

    while attempt < max_retries:
        attempt += 1
        console.print(f"[dim]Submitting experiment (attempt {attempt}/{max_retries})...[/dim]")
        try:
            receipt = experiment_service.submit_experiment(active_launch_exp, env_overrides=launch_env)
        except Exception as submit_exc:
            console.print(f"[bold red]Submission error:[/bold red] {submit_exc}")
            console.print("[dim]Experiment finalized but not submitted.[/dim]")
            return False

        if not receipt.accepted:
            console.print(
                Panel(
                    f"[bold]Error[/bold]: {receipt.stderr_tail}",
                    title="[bold red]Submission Failed[/bold red]",
                    border_style="red",
                )
            )
            return False

        console.print(
            Panel(
                f"[bold]PID[/bold]: {receipt.pid}\n"
                f"[bold]Log[/bold]: {receipt.log_path}\n"
                f"[bold]Host[/bold]: {receipt.remote_host}"
                + ("" if not launch_env else "\n[bold]Env[/bold]: " + ", ".join(f"{k}={v}" for k, v in launch_env.items())),
                title=f"[bold green]Submitted (attempt {attempt})[/bold green]",
                border_style="green",
            )
        )

        # ── Monitor phase: poll for early crash ──
        console.print(f"[dim]Monitoring for early failures ({stabilize_seconds}s)... Ctrl+C to skip.[/dim]")
        # Build a minimal artifact for polling
        from labit.experiments.models import (
            ExecutionBackend,
            FrozenLaunchSpec,
            LaunchArtifact,
        )

        exec_profile = experiment_service.build_default_execution_profile(active_launch_exp.project)
        poll_artifact = LaunchArtifact(
            launch_id=receipt.remote_job_id or "",
            task_id="experiment",
            experiment_id=active_launch_exp.experiment_id,
            project=active_launch_exp.project,
            executor=ExecutionBackend.SSH,
            remote_user=exec_profile.user,
            remote_host=exec_profile.host,
            remote_port=exec_profile.port,
            ssh_key=exec_profile.ssh_key,
            frozen_spec=FrozenLaunchSpec(
                command=active_launch_exp.run_sh_content or "#!/bin/bash\ntrue",
                workdir=exec_profile.workdir,
                output_dir=f"outputs/experiments/{active_launch_exp.experiment_id}",
                env=launch_env,
            ),
            submission=receipt,
        )

        elapsed = 0
        crashed = False
        crash_log = ""
        try:
            while elapsed < stabilize_seconds:
                time.sleep(poll_interval)
                elapsed += poll_interval
                try:
                    poll_result = executor.poll(poll_artifact)
                except Exception:
                    continue
                status = poll_result.get("status", "unknown")
                if status == "running":
                    remaining = stabilize_seconds - elapsed
                    console.print(f"[dim]  [{elapsed}s] Running... ({remaining}s to stable)[/dim]")
                elif status == "stopped":
                    # Process died — check if it's a crash or normal completion
                    try:
                        collected = executor.collect(poll_artifact)
                    except Exception:
                        collected = {}
                    log_tail = str(collected.get("log_tail", poll_result.get("stdout", "")))
                    # If stopped very early, likely a crash
                    if elapsed <= stabilize_seconds:
                        crashed = True
                        crash_log = log_tail
                        console.print(f"[bold red]  [{elapsed}s] Process stopped early![/bold red]")
                    break
                else:
                    console.print(f"[dim]  [{elapsed}s] Status: {status}[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Monitoring skipped.[/yellow]")
            return False

        if not crashed:
            console.print(
                Panel(
                    f"Experiment running for {elapsed}s without errors.",
                    title="[bold green]Experiment Stable[/bold green]",
                    border_style="green",
                )
            )
            return True

        # ── Crash detected: show error and auto-fix ──
        console.print(
            Panel(
                crash_log[-2000:] if crash_log else "(no log captured)",
                title="[bold red]Early Crash Detected[/bold red]",
                border_style="red",
            )
        )

        if attempt >= max_retries:
            console.print(f"[bold red]Exhausted {max_retries} retries. Giving up.[/bold red]")
            console.print("[dim]Use /launch-exp resume to manually fix and resubmit.[/dim]")
            return False

        # Auto-fix: have the agent revise run.sh based on the error
        console.print(f"[dim]Auto-fixing run.sh based on error log (attempt {attempt + 1}/{max_retries})...[/dim]")
        try:
            hyp_detail = _hypothesis_service().load_hypothesis(
                current_session.project, active_launch_exp.hypothesis_id
            )
            code_context = experiment_service.get_code_context(current_session.project)
            tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
            try:
                workdir = exec_profile.workdir or ""
                setup_summary = exec_profile.setup_script or ""
            except Exception:
                workdir = ""
                setup_summary = ""

            provider = first_participant.provider if first_participant else None
            fix_instruction = (
                f"The experiment crashed immediately after submission. "
                f"Here is the error log from the remote:\n\n"
                f"```\n{crash_log[-3000:]}\n```\n\n"
                f"Fix the run.sh to address this error. Common issues: "
                f"wrong paths, missing dependencies, incorrect CLI arguments, "
                f"environment assumptions."
            )
            if launch_env:
                fix_instruction += (
                    "\n\nThis launch is a task-level retry with environment overrides: "
                    + ", ".join(f"{k}={v}" for k, v in launch_env.items())
                    + ". Preserve the LABIT_ONLY_TASK/LABIT_START_AT/LABIT_FORCE_TASK/"
                    "LABIT_FORCE_CLEAN resume contract, should_run_task(), and "
                    "require_checkpoint() while fixing the crash."
                )

            with console.status("[bold cyan]Agent fixing run.sh...[/bold cyan]"):
                result = planner.revise_run_sh(
                    current_run_sh=active_launch_exp.run_sh_content,
                    current_config_yaml=active_launch_exp.config_yaml_content,
                    tasks_json=tasks_json,
                    user_instruction=fix_instruction,
                    code_tree=code_context,
                    workdir=workdir,
                    setup_script_summary=setup_summary,
                    provider=provider,
                )
            active_launch_exp = experiment_service.save_script(
                active_launch_exp,
                result["run_sh"],
                result["config_yaml"],
            )
            if launch_env:
                resume_issues = _run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
                if resume_issues:
                    console.print(
                        "[bold red]Auto-fix removed or failed to preserve task-level resume controls.[/bold red]\n"
                        f"[dim]Issues: {', '.join(resume_issues)}[/dim]"
                    )
                    return False
            console.print(
                Panel(
                    f"[bold]Fix summary[/bold]: {result['summary']}\n"
                    f"[bold]run.sh[/bold] ({len(result['run_sh'].splitlines())} lines)",
                    title="[bold yellow]Script Revised[/bold yellow]",
                    border_style="yellow",
                )
            )
        except Exception as fix_exc:
            console.print(f"[bold red]Auto-fix failed:[/bold red] {fix_exc}")
            console.print("[dim]Use /launch-exp resume to manually fix and resubmit.[/dim]")
            return False

    console.print(f"[bold red]Exhausted {max_retries} retries.[/bold red]")
    return False


def _run_sh_has_task_resume_contract(run_sh: str) -> bool:
    return not _run_sh_resume_contract_issues(run_sh)


def _run_sh_resume_contract_issues(run_sh: str) -> list[str]:
    """Return missing pieces of the task-level resume contract.

    This is intentionally heuristic: run.sh is arbitrary bash, but checking for
    helpers in addition to env var names catches scripts that merely mention the
    contract in comments without implementing it.
    """
    issues: list[str] = []
    required_markers = (
        "LABIT_ONLY_TASK",
        "LABIT_START_AT",
        "LABIT_FORCE_TASK",
        "LABIT_FORCE_CLEAN",
    )
    for marker in required_markers:
        if marker not in run_sh:
            issues.append(f"missing {marker}")
    if not re.search(r"(\bshould_run_task\s*\(\s*\)|\bfunction\s+should_run_task\b)", run_sh):
        issues.append("missing should_run_task() helper")
    if not re.search(r"(\brequire_checkpoint\s*\(\s*\)|\bfunction\s+require_checkpoint\b)", run_sh):
        issues.append("missing require_checkpoint() helper")
    destructive_lines: list[str] = []
    lines = run_sh.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not re.search(r"\brm\s+(-[rfRF]+\s+)+", stripped) or stripped.startswith("#"):
            continue
        guard_context = "\n".join(
            context_line
            for context_line in lines[max(0, idx - 4): idx + 1]
            if not context_line.strip().startswith("#")
        )
        if "LABIT_FORCE_CLEAN" not in guard_context:
            destructive_lines.append(stripped)
    if destructive_lines:
        issues.append(
            "contains cleanup not visibly guarded by LABIT_FORCE_CLEAN: "
            + destructive_lines[0][:120]
        )
    return issues


def _print_launch_exp_hints(console: Console, session: LaunchExpSession) -> None:
    phase_label = {
        LaunchExpPhase.TASK_BREAKDOWN: "Task Breakdown",
        LaunchExpPhase.TASK_PLANNING: "Task Planning",
        LaunchExpPhase.SCRIPT_GENERATION: "Script Generation",
    }.get(session.phase, session.phase.value)
    lines = [
        f"[dim]──── Experiment Planning Mode ({phase_label}) ────[/dim]",
        "[dim]  Type feedback to iterate on the current phase.[/dim]",
    ]
    if session.phase == LaunchExpPhase.TASK_BREAKDOWN:
        lines.append("[dim]  /launch-exp approve-tasks   — approve task list, move to detailed planning[/dim]")
    elif session.phase == LaunchExpPhase.TASK_PLANNING:
        ct = session.current_task
        if ct:
            lines.append(f"[dim]  Current task: {ct.id} — {ct.name}[/dim]")
        lines.append("[dim]  /launch-exp approve-task     — approve current task's detail[/dim]")
        lines.append("[dim]  /launch-exp reopen-task <id> — reopen a previously approved task[/dim]")
    elif session.phase == LaunchExpPhase.SCRIPT_GENERATION:
        lines.append("[dim]  /launch-exp generate-script  — generate run.sh[/dim]")
        if session.run_sh_content:
            lines.append("[dim]  /launch-exp run-task <id>    — submit only one task, reusing prior outputs[/dim]")
            lines.append("[dim]  /launch-exp done              — finalize and exit planning mode[/dim]")
        else:
            lines.append("[dim]  Generate script first, then /launch-exp done to finalize.[/dim]")
    lines.append("[dim]  /launch-exp status            — show planning progress[/dim]")
    lines.append("[dim]  Ctrl+C                        — interrupt current operation[/dim]")
    console.print("\n".join(lines))
    console.print("")


def _auto_iteration_actors(session, supervisor_agent: str = "codex") -> list[AutoActor]:
    participants = list(session.participants)
    if not participants:
        raise ValueError("No chat participants available for auto-iteration.")
    # Reorder so the designated supervisor goes first
    supervisor_idx = next(
        (i for i, p in enumerate(participants) if p.name == supervisor_agent),
        None,
    )
    if supervisor_idx is not None and supervisor_idx != 0:
        participants[0], participants[supervisor_idx] = participants[supervisor_idx], participants[0]
    if len(participants) == 1:
        participants = [participants[0], participants[0], participants[0]]
    elif len(participants) == 2:
        participants = [participants[0], participants[0], participants[1]]
    return [
        AutoActor(name="supervisor", provider=participants[0].provider),
        AutoActor(name="worker_a", provider=participants[1].provider),
        AutoActor(name="worker_b", provider=participants[2].provider),
    ]


def _parse_design_doc(text: str) -> tuple[str, str]:
    """Extract constraint and success_criteria from a loose design doc."""
    import re
    constraint = ""
    success = ""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        heading_match = re.match(r"^#{1,3}\s+(.+)", line)
        if heading_match:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = heading_match.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    # Match common section names for constraints
    for key in ["constraint", "constraints", "rules", "bounds", "limitations",
                "hard project requirements", "hard requirements", "requirements",
                "convergence policy", "convergence rules"]:
        if key in sections and sections[key]:
            constraint = sections[key]
            break

    # Match common section names for success
    for key in ["success criteria", "success", "goal", "goals", "objective",
                "objectives", "done when", "current design objective",
                "design objective", "bottom line"]:
        if key in sections and sections[key]:
            success = sections[key]
            break

    # Fallback: if either is missing, synthesize from the full doc
    if not constraint:
        constraint = "(see design doc)"
    if not success:
        success = "(see design doc)"

    return constraint, success


def _render_auto_status(console: Console, session_record, iterations) -> None:
    if session_record is None:
        console.print("[dim]No active auto-iteration session.[/dim]")
        return

    # Session overview
    status_style = {
        "running": "green", "waiting": "yellow", "needs_human": "red",
        "done": "cyan", "stopped": "dim",
    }.get(session_record.status.value, "white")
    console.print(
        Panel(
            (
                f"[bold]Project[/bold]: {session_record.project}\n"
                f"[bold]Status[/bold]: [{status_style}]{session_record.status.value}[/{status_style}]\n"
                f"[bold]Supervisor[/bold]: {session_record.supervisor_agent}\n"
                f"[bold]Iteration[/bold]: {session_record.current_iteration}/{session_record.max_iterations}\n"
                f"[bold]Updated[/bold]: {session_record.updated_at}\n"
                f"\n[bold]Constraint[/bold]: {session_record.constraint}\n"
                f"[bold]Success[/bold]: {session_record.success_criteria}"
            ),
            title="[bold green]Auto Session[/bold green]",
            border_style="green",
        )
    )

    # Latest observation & decision
    if session_record.last_observation_summary:
        console.print(
            Panel(session_record.last_observation_summary, title="Latest Observation", border_style="blue")
        )
    if session_record.last_decision_summary:
        console.print(
            Panel(session_record.last_decision_summary, title="Latest Decision", border_style="cyan")
        )

    # Iteration timeline
    if not iterations:
        console.print("[dim]No iterations recorded yet.[/dim]")
        return
    table = Table(title="Recent Iterations", border_style="dim", show_lines=True)
    table.add_column("Iter", style="bold", width=4)
    table.add_column("Trigger", width=18)
    table.add_column("Action", width=12)
    table.add_column("Decision", min_width=30)
    table.add_column("Workers", min_width=20)
    for entry in iterations:
        worker_info = ""
        if entry.worker_results:
            worker_info = "\n".join(
                f"{wr.worker}[{wr.status}]: {wr.summary[:80]}" for wr in entry.worker_results
            )
        elif entry.worker_tasks:
            worker_info = "\n".join(f"{wt.worker}: {wt.title}" for wt in entry.worker_tasks)
        if entry.discussion:
            disc = "\n".join(f"{n.actor}: {n.summary[:60]}" for n in entry.discussion)
            worker_info = f"{worker_info}\n---\n{disc}" if worker_info else disc
        table.add_row(
            str(entry.iteration),
            entry.trigger,
            entry.action.value,
            entry.decision_summary[:120],
            worker_info[:300] if worker_info else "-",
        )
    console.print(table)


def _render_auto_log(console: Console, iterations: list, n: int = 3) -> None:
    """Render detailed view of the last N iterations."""
    if not iterations:
        console.print("[dim]No iterations recorded yet.[/dim]")
        return
    for entry in iterations[-n:]:
        # Header
        header = f"Iteration {entry.iteration} | {entry.trigger} | {entry.action.value}"
        if entry.human_needed:
            header += " | [bold red]NEEDS HUMAN[/bold red]"
        if entry.success_reached:
            header += " | [bold green]SUCCESS[/bold green]"

        parts = [f"[bold]Decision[/bold]: {entry.decision_summary}"]

        # Observation
        if entry.observation_summary:
            parts.append(f"\n[bold]Observation[/bold]:\n{entry.observation_summary}")

        # Worker tasks & results
        if entry.worker_tasks:
            parts.append("\n[bold]Worker Tasks[/bold]:")
            for wt in entry.worker_tasks:
                parts.append(f"  {wt.worker}: {wt.title}\n    {wt.instructions[:200]}")

        if entry.worker_results:
            parts.append("\n[bold]Worker Results[/bold]:")
            for wr in entry.worker_results:
                parts.append(f"  {wr.worker} [{wr.status}]: {wr.summary}")
                if wr.actions_taken:
                    parts.append("    Actions: " + ", ".join(wr.actions_taken[:5]))
                if wr.outputs:
                    parts.append("    Outputs: " + ", ".join(wr.outputs[:5]))
                if wr.follow_up:
                    parts.append(f"    Follow-up: {wr.follow_up}")

        # Discussion
        if entry.discussion:
            parts.append("\n[bold]Discussion[/bold]:")
            for note in entry.discussion:
                parts.append(f"  {note.actor}: {note.summary}")
                if note.evidence:
                    for ev in note.evidence[:3]:
                        parts.append(f"    - {ev}")
                if note.next_step:
                    parts.append(f"    Next: {note.next_step}")

        console.print(Panel("\n".join(parts), title=header, border_style="cyan"))
        console.print()


def _render_task_breakdown(session: LaunchExpSession) -> None:
    table = Table(title="Task Breakdown", show_header=True, border_style="blue")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Goal")
    table.add_column("Depends On")
    table.add_column("Status")
    for t in session.task_plans:
        status = "[green]approved[/green]" if t.approved else "[dim]pending[/dim]"
        deps = ", ".join(t.depends_on) if t.depends_on else "-"
        table.add_row(t.id, t.name, t.goal[:80] if t.goal else "-", deps, status)
    console.print(table)
    console.print("")


def _render_task_detail(task: ExperimentTaskPlan) -> None:
    parts = [
        f"[bold]ID[/bold]: {task.id}",
        f"[bold]Name[/bold]: {task.name}",
        f"[bold]Goal[/bold]: {task.goal}",
    ]
    if task.depends_on:
        parts.append(f"[bold]Depends on[/bold]: {', '.join(task.depends_on)}")
    if task.entry_hint:
        parts.append(f"[bold]Entry hint[/bold]: {task.entry_hint}")
    if task.inputs:
        parts.append(f"[bold]Inputs[/bold]: {task.inputs}")
    if task.outputs:
        parts.append(f"[bold]Outputs[/bold]: {task.outputs}")
    if task.checkpoint:
        parts.append(f"[bold]Checkpoint[/bold]: {task.checkpoint}")
    if task.failure_modes:
        parts.append(f"[bold]Failure modes[/bold]: {task.failure_modes}")
    console.print(
        Panel(
            "\n".join(parts),
            title=f"[bold cyan]Task Detail: {task.id}[/bold cyan]",
            border_style="cyan",
        )
    )


def _render_launch_exp_status(session: LaunchExpSession) -> None:
    phase_label = {
        LaunchExpPhase.TASK_BREAKDOWN: "Task Breakdown",
        LaunchExpPhase.TASK_PLANNING: "Task Planning",
        LaunchExpPhase.SCRIPT_GENERATION: "Script Generation",
    }.get(session.phase, session.phase.value)
    approved = sum(1 for t in session.task_plans if t.approved)
    total = len(session.task_plans)
    parts = [
        f"[bold]Hypothesis[/bold]: {session.hypothesis_id}",
        f"[bold]Experiment[/bold]: {session.experiment_id}",
        f"[bold]Phase[/bold]: {phase_label}",
        f"[bold]Tasks[/bold]: {approved}/{total} approved",
    ]
    if session.phase == LaunchExpPhase.TASK_PLANNING:
        ct = session.current_task
        if ct:
            parts.append(f"[bold]Current task[/bold]: {ct.id}: {ct.name}")
    if session.run_sh_content:
        parts.append(f"[bold]run.sh[/bold]: {len(session.run_sh_content.splitlines())} lines")
    console.print(
        Panel(
            "\n".join(parts),
            title="[bold blue]Experiment Planning Status[/bold blue]",
            border_style="blue",
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


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _extract_log_hint(log_tail: str, *, max_len: int = 140) -> str:
    if not log_tail:
        return ""
    lines: list[str] = []
    for raw in log_tail.splitlines():
        cleaned = _ANSI_ESCAPE_RE.sub("", raw).strip()
        if cleaned:
            lines.append(cleaned)
    if not lines:
        return ""
    for candidate in reversed(lines):
        if re.search(r"[A-Za-z0-9]", candidate):
            return candidate[:max_len]
    return lines[-1][:max_len]


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
    skip_participants: set[str] | None = None,
    cwd_override: str | None = None,
) -> object | None:
    _render_user_shell_message(query, attachments=attachments)
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


# ── /dev auto-development loop ────────────────────────────────────────


@dataclass
class DevDecision:
    question: str
    options: list[str]
    recommended: int | None = None
    rationale: str | None = None
    asked_by: str = "writer"  # writer|reviewer


@dataclass
class DevRound:
    round_index: int
    writer_summary: str = ""
    reviewer_summary: str = ""
    findings: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    status: str = "pending"  # pending|writer_done|approved|decision_needed


@dataclass
class DevLoopSession:
    task: str
    writer_name: str
    reviewer_name: str
    max_rounds: int = 6
    test_mode: str = "auto"  # off|auto|on
    current_round: int = 0
    history: list[DevRound] = field(default_factory=list)
    pending_decision: DevDecision | None = None
    user_decision: str | None = None  # answer to pending decision
    status: str = "active"  # active|waiting_decision|completed|stopped
    scope_label: str = ""
    scope_pathspecs: list[str] = field(default_factory=list)
    scope_git_root: str = ""  # git root for the dev scope (may differ from RepoPaths.root for nested repos)
    branch_repo_root: str = ""  # repo that owns dev_branch and worktree metadata
    worktree_path: str = ""  # isolated worktree used by this dev loop, if any
    initial_dirty_files: list[str] = field(default_factory=list)
    dev_branch: str = ""  # branch created for this dev loop
    original_branch: str = ""  # branch to return to after dev loop


def _parse_dev_decision(text: str) -> DevDecision | None:
    """Parse DECISION_NEEDED block from agent output."""
    if "DECISION_NEEDED" not in text:
        return None
    lines = text[text.index("DECISION_NEEDED"):].splitlines()
    question = ""
    options: list[str] = []
    recommended: int | None = None
    rationale: str | None = None
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("question:"):
            question = line.split(":", 1)[1].strip()
        elif re.match(r"option_[a-z]:", low):
            options.append(line.split(":", 1)[1].strip())
        elif low.startswith("recommended:"):
            letter = line.split(":", 1)[1].strip().lower()
            idx = ord(letter[0]) - ord("a") if letter else None
            recommended = idx
        elif low.startswith("reason:"):
            rationale = line.split(":", 1)[1].strip()
    if not question and not options:
        return None
    return DevDecision(
        question=question or "(agent requests a decision)",
        options=options if options else ["(see agent output above)"],
        recommended=recommended,
        rationale=rationale,
    )


def _parse_review_findings(text: str) -> tuple[bool, list[str], str]:
    """Parse reviewer output. Returns (is_approved, findings, summary)."""
    lines = text.strip().splitlines()
    findings: list[str] = []
    summary_parts: list[str] = []
    is_lgtm = False

    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("lgtm"):
            is_lgtm = True
        elif low.startswith("finding:"):
            findings.append(stripped.split(":", 1)[1].strip())
        elif low.startswith("summary:"):
            summary_parts.append(stripped.split(":", 1)[1].strip())

    # Also detect LGTM in the middle of text
    if not is_lgtm and re.search(r"\bLGTM\b", text):
        is_lgtm = True

    summary = " ".join(summary_parts) if summary_parts else text[:200]
    approved = not findings and (is_lgtm or bool(text.strip()))
    return approved, findings, summary


def _normalize_git_remote(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    if value.startswith("git@"):
        value = value[4:]
        value = value.replace(":", "/", 1)
    value = re.sub(r"^https?://", "", value)
    value = value.removesuffix(".git")
    return value.rstrip("/")


def _git_output(*args: str, cwd: Path, timeout: int = 10) -> str:
    result = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=timeout,
    )
    return result.stdout.strip()


def _resolve_dev_scope(session) -> tuple[str, list[str], Path]:
    """Choose the git scope that /dev should review.

    Returns (label, pathspecs, git_root).
    git_root may point to a nested repo (e.g. vault/projects/X/code/.git)
    rather than the outer Research-OS repo.
    """
    paths = RepoPaths.discover()
    project = session.project or ""
    if not project:
        return ("repository", ["."], paths.root)

    project_dir = paths.vault_projects_dir / project
    if project_dir.exists():
        # Check for nested git repo in project code dir
        code_dir = project_dir / "code"
        if code_dir.exists() and (code_dir / ".git").exists():
            return (f"project code ({project})", ["."], code_dir)

        try:
            spec = ProjectService(paths).load_project(project)
        except Exception:
            spec = None
        repo_root_remote = _normalize_git_remote(_git_output("git", "config", "--get", "remote.origin.url", cwd=paths.root))
        spec_remote = _normalize_git_remote(spec.repo) if spec and spec.repo else ""
        if spec_remote and repo_root_remote and spec_remote == repo_root_remote:
            return (f"repository ({paths.root.name})", ["."], paths.root)
        try:
            project_pathspec = str(project_dir.relative_to(paths.root))
        except ValueError:
            project_pathspec = str(project_dir)
        return (f"project ({project})", [project_pathspec], paths.root)
    return ("repository", ["."], paths.root)


def _list_scope_dirty_files(pathspecs: list[str], git_root: Path | None = None) -> list[str]:
    cwd = str(git_root) if git_root else str(RepoPaths.discover().root)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *pathspecs],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
    except Exception:
        return []
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        candidate = line[3:].strip()
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1].strip()
        if candidate and candidate not in dirty:
            dirty.append(candidate)
    return dirty


def _create_dev_branch(task: str, git_root: Path | None = None) -> tuple[str, str]:
    """Create a dev branch and return (branch_name, original_branch)."""
    cwd = git_root or RepoPaths.discover().root
    original = _git_output("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=cwd) or "main"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task.lower())[:40].strip("-")
    timestamp = datetime.now().strftime("%m%d-%H%M")
    branch_name = f"labit-dev/{slug}-{timestamp}"
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        capture_output=True, text=True, cwd=str(cwd),
    )
    return branch_name, original


def _create_dev_worktree(
    *,
    task: str,
    git_root: Path,
    project: str | None,
) -> tuple[str, str, Path]:
    """Create an isolated git worktree for a /dev run.

    Returns (branch_name, original_branch, worktree_path). The original checkout is
    not switched; writer/reviewer turns run inside the returned worktree path.
    """
    paths = RepoPaths.discover()
    original = _git_output("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=git_root) or "main"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task.lower())[:40].strip("-") or "task"
    timestamp = datetime.now().strftime("%m%d-%H%M%S")
    branch_name = f"labit-dev/{slug}-{timestamp}"
    project_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", project or git_root.name).strip("-") or "repo"
    worktree_path = paths.root / ".labit" / "dev-worktrees" / project_slug / f"{slug}-{timestamp}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(git_root),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git worktree add failed")
    return branch_name, original, worktree_path


def _remove_dev_worktree(repo_root: Path, worktree_path: Path) -> tuple[bool, str]:
    """Remove a /dev worktree. Returns (ok, message)."""
    if not str(worktree_path):
        return True, ""
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    subprocess.run(
        ["git", "worktree", "prune"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, ""


def _dev_auto_commit(round_num: int, pathspecs: list[str], task: str, git_root: Path | None = None) -> str | None:
    """Stage and commit changes from a dev round. Returns commit hash or None."""
    cwd = git_root or RepoPaths.discover().root
    # Stage changes in scope
    subprocess.run(
        ["git", "add", "--", *pathspecs],
        capture_output=True, text=True, cwd=str(cwd),
    )
    # Check if there's anything to commit
    status = _git_output("git", "diff", "--cached", "--stat", cwd=cwd)
    if not status:
        return None
    msg = f"dev(round {round_num}): {task[:60]}"
    result = subprocess.run(
        ["git", "commit", "-m", msg, "--no-verify"],
        capture_output=True, text=True, cwd=str(cwd),
    )
    if result.returncode != 0:
        return None
    return _git_output("git", "rev-parse", "--short", "HEAD", cwd=cwd)


def _get_last_commit_diff(git_root: Path | None = None) -> str:
    """Get the diff of the most recent commit (for reviewer to review per-round changes)."""
    cwd = git_root or RepoPaths.discover().root
    try:
        diff = _git_output("git", "diff", "HEAD~1..HEAD", cwd=cwd)
        stat = _git_output("git", "diff", "--stat", "HEAD~1..HEAD", cwd=cwd)
    except Exception:
        return "(unable to get commit diff)"
    parts = []
    if stat:
        parts.append(f"Diff stat:\n{stat}")
    if diff:
        if len(diff) > 8000:
            diff = diff[:8000] + "\n... (truncated)"
        parts.append(f"Full diff:\n{diff}")
    return "\n\n".join(parts) if parts else "(no changes in last commit)"


def _get_scope_diff(pathspecs: list[str], git_root: Path | None = None) -> str:
    """Get git diff for the selected /dev scope."""
    cwd = git_root or RepoPaths.discover().root
    try:
        stat = _git_output("git", "diff", "--stat", "--", *pathspecs, cwd=cwd)
        diff = _git_output("git", "diff", "--", *pathspecs, cwd=cwd)
        untracked = _git_output("git", "status", "--porcelain", "--", *pathspecs, cwd=cwd)
    except Exception:
        return "(unable to get diff)"
    parts = []
    if stat:
        parts.append(f"Diff stat:\n{stat}")
    if diff:
        if len(diff) > 8000:
            diff = diff[:8000] + "\n... (truncated)"
        parts.append(f"Full diff:\n{diff}")
    if untracked:
        parts.append(f"Untracked/modified:\n{untracked}")
    return "\n\n".join(parts) if parts else "(no changes detected)"


def _render_dev_decision(dev_session: DevLoopSession) -> None:
    """Render a decision prompt for the user."""
    decision = dev_session.pending_decision
    if not decision:
        return
    lines = [f"[bold]{decision.question}[/bold]\n"]
    for i, opt in enumerate(decision.options):
        letter = chr(ord("A") + i)
        rec = " [bold green](recommended)[/bold green]" if decision.recommended == i else ""
        lines.append(f"  [{letter}] {opt}{rec}")
    if decision.rationale:
        lines.append(f"\n[dim]Reason: {decision.rationale}[/dim]")
    lines.append(f"\n[dim]Asked by: {decision.asked_by}[/dim]")
    console.print(Panel(
        "\n".join(lines),
        title="[bold yellow]Decision needed[/bold yellow]",
        border_style="yellow",
    ))


def _run_dev_loop(
    *,
    service: ChatService,
    session,
    dev_session: DevLoopSession,
) -> DevLoopSession:
    """Run the writer/reviewer auto-iteration loop."""
    participants = session.participants
    writer = next((p for p in participants if p.name == dev_session.writer_name), participants[0])
    reviewer = next((p for p in participants if p.name == dev_session.reviewer_name), participants[-1])

    # All participants except writer
    skip_for_writer = {p.name for p in participants if p.name != writer.name}
    # All participants except reviewer
    skip_for_reviewer = {p.name for p in participants if p.name != reviewer.name}

    project = session.project
    scope_label = dev_session.scope_label or "repository"
    scope_pathspecs = dev_session.scope_pathspecs or ["."]
    scope_git_root = Path(dev_session.scope_git_root) if dev_session.scope_git_root else RepoPaths.discover().root
    turn_cwd = str(scope_git_root)

    for round_num in range(dev_session.current_round + 1, dev_session.max_rounds + 1):
        dev_session.current_round = round_num
        dev_round = DevRound(round_index=round_num)

        console.print(f"\n[bold]── Dev Round {round_num}/{dev_session.max_rounds} ──[/bold]")

        # ── Writer turn ──
        writer_query_parts = [f"[Dev Loop — Round {round_num}/{dev_session.max_rounds}]"]
        writer_query_parts.append(f"Task: {dev_session.task}")
        writer_query_parts.append(f"Scope: {scope_label}")
        writer_query_parts.append(f"Editable workspace: {turn_cwd}")
        if dev_session.worktree_path:
            writer_query_parts.append(
                "This /dev loop is running in an isolated git worktree. "
                "Use the editable workspace path above, not the original checkout, for all file edits and commands."
            )

        # Include decision answer if pending
        if dev_session.user_decision:
            writer_query_parts.append(f"User decided: {dev_session.user_decision}")
            dev_session.user_decision = None
            dev_session.pending_decision = None

        # Include previous reviewer findings
        if dev_session.history:
            last = dev_session.history[-1]
            if last.findings:
                writer_query_parts.append("Reviewer findings from last round:")
                for f in last.findings:
                    writer_query_parts.append(f"- {f}")
            elif last.reviewer_summary:
                writer_query_parts.append(f"Reviewer feedback: {last.reviewer_summary}")

        writer_query_parts.append(
            "\nYou are the WRITER. Read the codebase, implement the change, and summarize what you did."
            "\nFollow the session's project boundary rules from the system prompt."
            f"\nWork only within this /dev scope: {scope_label}."
            "\nIf you hit a genuine architecture/design fork requiring user input, output:\n"
            "DECISION_NEEDED\nquestion: ...\noption_a: ...\noption_b: ...\nrecommended: a\nreason: ..."
        )

        writer_query = "\n".join(writer_query_parts)

        try:
            writer_result = _run_streaming_turn(
                service=service,
                session=session,
                query=writer_query,
                skip_participants=skip_for_writer,
                cwd_override=turn_cwd,
            )
        except KeyboardInterrupt:
            console.print(f"\n[bold yellow]Dev loop interrupted at round {round_num} (writer phase).[/bold yellow]")
            dev_session.status = "stopped"
            return dev_session

        if writer_result is None or not writer_result.replies:
            console.print("[bold red]Writer produced no output. Stopping dev loop.[/bold red]")
            dev_session.status = "stopped"
            return dev_session

        writer_text = writer_result.replies[0].message.content
        dev_round.writer_summary = writer_text[:500]
        changed_files = _list_scope_dirty_files(scope_pathspecs, scope_git_root)
        if dev_session.initial_dirty_files:
            changed_files = [item for item in changed_files if item not in dev_session.initial_dirty_files] or changed_files
        dev_round.changed_files = changed_files[:20]

        # Auto-commit writer's changes to the dev branch
        commit_hash = _dev_auto_commit(round_num, scope_pathspecs, dev_session.task, scope_git_root)
        if commit_hash:
            console.print(f"[dim]Auto-committed: {commit_hash}[/dim]")

        # Check for decision needed
        decision = _parse_dev_decision(writer_text)
        if decision:
            decision.asked_by = "writer"
            dev_session.pending_decision = decision
            dev_session.status = "waiting_decision"
            dev_session.history.append(dev_round)
            _render_dev_decision(dev_session)
            return dev_session

        # ── Reviewer turn ──
        console.print(f"\n[dim]── Reviewer ({reviewer.name}) ──[/dim]")

        # Use per-commit diff if we just committed, otherwise fall back to worktree diff
        if commit_hash:
            diff_text = _get_last_commit_diff(scope_git_root)
        else:
            diff_text = _get_scope_diff(scope_pathspecs, scope_git_root)

        reviewer_query_parts = [f"[Dev Loop — Review Round {round_num}/{dev_session.max_rounds}]"]
        reviewer_query_parts.append(f"Task: {dev_session.task}")
        reviewer_query_parts.append(f"Scope: {scope_label}")
        reviewer_query_parts.append(f"Editable workspace: {turn_cwd}")
        if dev_round.changed_files:
            reviewer_query_parts.append(
                "Files changed this round:\n" + "\n".join(f"- {item}" for item in dev_round.changed_files[:20])
            )
        reviewer_query_parts.append(f"\nWriter's changes (this round only):\n{diff_text}")

        if dev_session.test_mode == "on":
            reviewer_query_parts.append(
                "\nRun targeted tests relevant to the changes if test infrastructure exists."
            )
        elif dev_session.test_mode == "auto":
            reviewer_query_parts.append(
                "\nIf you see obvious test infrastructure (pytest, unittest), run a quick targeted check. "
                "Otherwise skip testing and focus on code review."
            )

        reviewer_query_parts.append(
            "\nYou are the REVIEWER. Review the diff above."
            "\n- If the changes look correct and complete, output: LGTM\\nsummary: ..."
            "\n- If there are issues, output: FINDING: <issue>\\n for each issue, then summary: ..."
            "\n- If a real design fork needs user input, output DECISION_NEEDED (same format as writer)."
            "\n- Be concrete and actionable. Don't nitpick style — focus on bugs, regressions, missing logic."
        )

        reviewer_query = "\n".join(reviewer_query_parts)

        try:
            reviewer_result = _run_streaming_turn(
                service=service,
                session=session,
                query=reviewer_query,
                skip_participants=skip_for_reviewer,
                cwd_override=turn_cwd,
            )
        except KeyboardInterrupt:
            console.print(f"\n[bold yellow]Dev loop interrupted at round {round_num} (reviewer phase).[/bold yellow]")
            dev_session.status = "stopped"
            dev_session.history.append(dev_round)
            return dev_session

        if reviewer_result is None or not reviewer_result.replies:
            console.print("[bold red]Reviewer produced no output. Stopping dev loop.[/bold red]")
            dev_session.status = "stopped"
            dev_session.history.append(dev_round)
            return dev_session

        reviewer_text = reviewer_result.replies[0].message.content
        dev_round.reviewer_summary = reviewer_text[:500]

        # Check for decision needed from reviewer
        decision = _parse_dev_decision(reviewer_text)
        if decision:
            decision.asked_by = "reviewer"
            dev_session.pending_decision = decision
            dev_session.status = "waiting_decision"
            dev_session.history.append(dev_round)
            _render_dev_decision(dev_session)
            return dev_session

        # Parse findings
        approved, findings, summary = _parse_review_findings(reviewer_text)
        dev_round.findings = findings
        dev_round.reviewer_summary = summary
        dev_round.status = "approved" if approved else "review_failed"
        dev_session.history.append(dev_round)

        if approved:
            console.print(f"[bold green]Approved at round {round_num}. Dev loop complete.[/bold green]")
            dev_session.status = "completed"
            return dev_session

        console.print(f"[dim]{len(findings)} finding(s) — continuing to next round[/dim]")

    # Max rounds reached
    console.print(f"[bold yellow]Reached max rounds ({dev_session.max_rounds}). Dev loop finished.[/bold yellow]")
    dev_session.status = "completed"
    return dev_session


def _render_dev_status(dev_session: DevLoopSession) -> None:
    """Render current dev loop status."""
    lines = [
        f"[bold]Task[/bold]: {dev_session.task}",
        f"[bold]Writer[/bold]: {dev_session.writer_name}",
        f"[bold]Reviewer[/bold]: {dev_session.reviewer_name}",
        f"[bold]Round[/bold]: {dev_session.current_round}/{dev_session.max_rounds}",
        f"[bold]Status[/bold]: {dev_session.status}",
        f"[bold]Test mode[/bold]: {dev_session.test_mode}",
        f"[bold]Scope[/bold]: {dev_session.scope_label or 'repository'}",
        f"[bold]Git root[/bold]: {dev_session.scope_git_root or '(default)'}",
        f"[bold]Branch repo[/bold]: {dev_session.branch_repo_root or dev_session.scope_git_root or '(default)'}",
        f"[bold]Worktree[/bold]: {dev_session.worktree_path or '(none)'}",
        f"[bold]Branch[/bold]: {dev_session.dev_branch or '(none)'}",
    ]
    if dev_session.history:
        last = dev_session.history[-1]
        if last.changed_files:
            lines.append(f"\n[bold]Last changed files[/bold]:")
            for path in last.changed_files[:8]:
                lines.append(f"  - {path}")
        if last.findings:
            lines.append(f"\n[bold]Last findings[/bold]:")
            for f in last.findings[:5]:
                lines.append(f"  - {f}")
    console.print(Panel(
        "\n".join(lines),
        title="[bold]Dev Loop Status[/bold]",
        border_style=_COMMAND_COLOR,
    ))


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
    # Hypothesis editing mode state: (hypothesis_id, project, current_draft)
    active_hypothesis: tuple[str, str, HypothesisDraft] | None = None
    active_launch_exp: LaunchExpSession | None = None
    active_dev: DevLoopSession | None = None
    muted_next_turn: set[str] = set()  # agent names to skip on next turn only
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
                                    ),
                            title="[bold green]Document opened[/bold green]",
                            border_style="green",
                        )
                    )
                    _print_doc_mode_hints(console, current_session)
                    continue
                if doc_action == "auto":
                    if active_doc is None:
                        console.print("[bold red]Error:[/bold red] No active document. Use /doc start or /doc open first.")
                        continue
                    # Parse round count
                    max_rounds = 5
                    if doc_argument.strip():
                        try:
                            max_rounds = int(doc_argument.strip())
                        except ValueError:
                            console.print("[bold red]Usage:[/bold red] /doc auto [N]  (N = number of rounds, default 5, max 10)")
                            continue
                    max_rounds = min(max(max_rounds, 1), 10)

                    doc_service = _document_service()
                    drafter = _doc_drafter()
                    author = current_session.participants[0]
                    reviewer = (
                        current_session.participants[1]
                        if current_session.mode == ChatMode.ROUND_ROBIN and len(current_session.participants) >= 2
                        else None
                    )

                    console.print(f"[bold yellow]Auto-iteration starting: up to {max_rounds} rounds. Ctrl+C to stop.[/bold yellow]")
                    # Initial instruction for first round: use reviewer's last review or generic
                    auto_instruction = "Review the document and improve it. Fix any issues, improve clarity, and strengthen the content."

                    interrupted = False
                    for round_num in range(1, max_rounds + 1):
                        console.print(f"\n[bold]── Round {round_num}/{max_rounds} ──[/bold]")
                        try:
                            old_markdown = doc_service.read_document(active_doc)

                            # Author revises
                            with console.status(f"[bold blue]{author.name} revising (round {round_num})...[/bold blue]"):
                                update = drafter.revise_document(
                                    session=current_session,
                                    transcript=service.transcript(current_session.session_id),
                                    context_snapshot=service.context_snapshot(current_session.session_id),
                                    doc_title=active_doc.title,
                                    current_markdown=old_markdown,
                                    user_instruction=auto_instruction,
                                    interaction_log=doc_service.interaction_excerpt(active_doc),
                                    author_name=author.name,
                                    provider=author.provider,
                                )
                                active_doc = doc_service.revise_document(
                                    doc_session=active_doc,
                                    update=update,
                                    user_instruction=auto_instruction,
                                )
                            console.print(
                                Panel(
                                    f"[bold]Iteration[/bold]: {active_doc.iteration}\n[bold]Summary[/bold]: {update.summary}",
                                    title=f"[bold green]{author.name} · Round {round_num}[/bold green]",
                                    border_style="green",
                                )
                            )

                            # Reviewer reviews (round-robin) or self-review (single)
                            if reviewer is not None:
                                from labit.documents.drafter import compute_changed_sections

                                new_markdown = doc_service.read_document(active_doc)
                                changed_sections = compute_changed_sections(old_markdown, new_markdown)

                                with console.status(f"[bold cyan]{reviewer.name} reviewing (round {round_num})...[/bold cyan]"):
                                    review_update = drafter.review_document(
                                        current_markdown=new_markdown,
                                        revision_summary=update.summary,
                                        user_instruction=auto_instruction,
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
                                # Use reviewer feedback as next round's instruction
                                auto_instruction = review_update.summary
                            else:
                                # Single agent: use own revision summary as next instruction
                                auto_instruction = f"Continue improving. Previous changes: {update.summary}"

                            # Convergence check: all review blocks closed + no new open reviews
                            from labit.documents.drafter import count_open_reviews

                            current_md = doc_service.read_document(active_doc)
                            open_count = count_open_reviews(current_md)
                            if open_count == 0:
                                console.print(f"[bold green]Converged at round {round_num} — all review blocks resolved, no open issues remaining.[/bold green]")
                                break
                            else:
                                console.print(f"[dim]  {open_count} open review(s) remaining[/dim]")

                        except KeyboardInterrupt:
                            console.print(f"\n[bold yellow]Auto-iteration interrupted at round {round_num}.[/bold yellow]")
                            interrupted = True
                            break
                        except Exception as exc:
                            console.print(f"[bold red]Error in round {round_num}:[/bold red] {exc}")
                            break

                    if not interrupted:
                        console.print(f"[bold green]Auto-iteration complete. {active_doc.iteration} total iterations.[/bold green]")
                    _print_doc_mode_hints(console, current_session)
                    try:
                        service.record_session_event(
                            session_id=current_session.session_id,
                            kind=SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
                            actor="labit",
                            summary=f"Document auto-iterated: {active_doc.title}",
                            payload={
                                "doc_id": active_doc.doc_id,
                                "title": active_doc.title,
                                "iteration": active_doc.iteration,
                            },
                            evidence_refs=_session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
                        )
                    except Exception:
                        pass
                    continue
                if doc_action != "start":
                    console.print("[bold red]Usage:[/bold red] /doc start <title> | /doc open <id> | /doc auto [N] | /doc status | /doc done | /doc publish <id> | /doc list")
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
                            ),
                        title="[bold green]Document draft saved[/bold green]",
                        border_style="green",
                    )
                )
                _print_doc_mode_hints(console, current_session)
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
            if command == "/auto":
                auto_parts = argument.split(maxsplit=1)
                auto_action = auto_parts[0].strip().lower() if auto_parts and auto_parts[0].strip() else "status"
                auto_argument = auto_parts[1].strip() if len(auto_parts) > 1 else ""
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                engine = _auto_engine()
                if auto_action in {"status", ""}:
                    session_record, iterations = engine.status(current_session.project)
                    _render_auto_status(console, session_record, iterations)
                    continue
                if auto_action == "stop":
                    try:
                        stopped = engine.stop_session(current_session.project)
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    console.print(f"[green]Auto session stopped.[/green] [dim]{stopped.project}[/dim]")
                    continue
                if auto_action == "start":
                    design_doc = ""
                    constraint = ""
                    success = ""
                    doc_path_str = auto_argument.strip()
                    # Try to load as design doc file first
                    if doc_path_str and "||" not in doc_path_str:
                        doc_path = Path(doc_path_str).expanduser()
                        if not doc_path.is_absolute():
                            doc_path = (paths.vault_projects_dir / current_session.project / doc_path_str)
                        if doc_path.exists() and doc_path.is_file():
                            design_doc = doc_path.read_text(encoding="utf-8").strip()
                            # Parse loose sections from design doc
                            constraint, success = _parse_design_doc(design_doc)
                        else:
                            console.print(f"[bold red]Error:[/bold red] Design doc not found: {doc_path}")
                            console.print("[dim]Usage: /auto start <design_doc_path> or /auto start <constraint> || <success>[/dim]")
                            continue
                    elif "||" in auto_argument:
                        constraint, _, success = auto_argument.partition("||")
                        constraint = constraint.strip()
                        success = success.strip()
                    if not design_doc and (not constraint or not success):
                        console.print("[bold red]Usage:[/bold red] /auto start <design_doc_path> or /auto start <constraint> || <success criteria>")
                        continue
                    try:
                        session_record = engine.start_session(
                            project=current_session.project,
                            constraint=constraint,
                            success_criteria=success,
                            design_doc=design_doc,
                        )
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    body = (
                        f"[bold]Constraint[/bold]: {session_record.constraint}\n"
                        f"[bold]Success[/bold]: {session_record.success_criteria}\n"
                        f"[bold]Supervisor[/bold]: {session_record.supervisor_agent}\n"
                        f"[bold]Rounds[/bold]: {session_record.max_iterations}\n"
                        f"[bold]Poll Seconds[/bold]: {session_record.poll_seconds}"
                    )
                    if design_doc:
                        body += f"\n[bold]Design Doc[/bold]: {doc_path_str}"
                    console.print(
                        Panel(body, title="[bold green]Auto Iteration Started[/bold green]", border_style="green")
                    )
                    console.print("[dim]Run /auto run [N] to execute rounds, /auto log [N] for detail, /auto stop to halt.[/dim]")
                    continue
                if auto_action == "run":
                    rounds = 1
                    if auto_argument:
                        try:
                            rounds = max(1, int(auto_argument))
                        except ValueError:
                            console.print("[bold red]Usage:[/bold red] /auto run [N]")
                            continue
                    try:
                        session_record, _ = engine.status(current_session.project)
                        if session_record is None:
                            console.print("[bold red]Error:[/bold red] No active auto session. Use /auto start first.")
                            continue
                        actors = _auto_iteration_actors(current_session, supervisor_agent=session_record.supervisor_agent)
                        for _ in range(rounds):
                            if session_record.current_iteration >= session_record.max_iterations:
                                console.print("[yellow]Auto session already hit max_iterations.[/yellow]")
                                break
                            with console.status("[bold blue]Running auto-iteration round...[/bold blue]"):
                                entry = engine.run_iteration(project=current_session.project, actors=actors)
                            _render_auto_log(console, [entry], n=1)
                            session_record, _ = engine.status(current_session.project)
                            if session_record is None or session_record.status.value in {"done", "needs_human", "stopped"}:
                                break
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                if auto_action == "log":
                    n = 3
                    if auto_argument:
                        try:
                            n = max(1, int(auto_argument))
                        except ValueError:
                            pass
                    _, iterations = engine.status(current_session.project)
                    _render_auto_log(console, iterations, n=n)
                    continue
                console.print("[bold red]Usage:[/bold red] /auto start <doc_path> | /auto run [N] | /auto log [N] | /auto status | /auto stop")
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
                sub = argument.strip()
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                # ── /hypothesis status ──
                if sub == "status":
                    if active_hypothesis is None:
                        console.print("[dim]Not in hypothesis editing mode.[/dim]")
                    else:
                        h_id, h_proj, h_draft = active_hypothesis
                        console.print(Panel(
                            f"[bold]ID[/bold]: {h_id}\n"
                            f"[bold]Title[/bold]: {h_draft.title}\n"
                            f"[bold]Claim[/bold]: {h_draft.claim}",
                            title=f"[bold green]Editing hypothesis · {h_id}[/bold green]",
                            border_style="green",
                        ))
                    continue

                # ── /hypothesis done ──
                if sub == "done":
                    if active_hypothesis is None:
                        console.print("[dim]Not in hypothesis editing mode.[/dim]")
                        continue
                    h_id, h_proj, h_draft = active_hypothesis
                    hyp_svc = _hypothesis_service()
                    hyp_svc.log_event(h_proj, h_id, "session_ended")
                    console.print(f"[dim]Left hypothesis editing mode. {h_id} saved.[/dim]")
                    active_hypothesis = None
                    continue

                # ── /hypothesis open <id> ──
                if sub.startswith("open "):
                    h_id = sub[5:].strip()
                    if not h_id:
                        console.print("[bold red]Usage:[/bold red] /hypothesis open <hypothesis_id>")
                        continue
                    if active_hypothesis is not None:
                        console.print("[bold red]Error:[/bold red] Already editing a hypothesis. Use /hypothesis done first.")
                        continue
                    try:
                        hyp_svc = _hypothesis_service()
                        detail = hyp_svc.load_hypothesis(current_session.project, h_id)
                        # Reconstruct draft from detail
                        h_draft = HypothesisDraft(
                            title=detail.record.title,
                            claim=detail.record.claim,
                            motivation=detail.record.motivation,
                            independent_variable=detail.record.independent_variable,
                            dependent_variable=detail.record.dependent_variable,
                            success_criteria=detail.record.success_criteria,
                            failure_criteria=detail.record.failure_criteria,
                            rationale_markdown=detail.rationale_markdown,
                            experiment_plan_markdown=detail.experiment_plan_markdown,
                            source_paper_ids=detail.record.source_paper_ids,
                        )
                        active_hypothesis = (h_id, current_session.project, h_draft)
                        hyp_svc.log_event(current_session.project, h_id, "session_started")
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    console.print("")
                    _render_hypothesis_preview(h_draft, project=current_session.project)
                    _print_hypothesis_mode_hints(console, current_session, h_id)
                    continue

                # ── /hypothesis [idea] — draft new hypothesis ──
                if active_hypothesis is not None:
                    console.print("[bold red]Error:[/bold red] Already editing a hypothesis. Use /hypothesis done first.")
                    continue
                user_intent = sub
                if user_intent == "new":
                    user_intent = ""
                elif user_intent.startswith("new "):
                    user_intent = user_intent[4:].strip()
                try:
                    drafter = _hypothesis_drafter()
                    transcript_msgs = service.transcript(current_session.session_id)
                    ctx_snap = service.context_snapshot(current_session.session_id)
                    with console.status(f"[bold blue]{current_session.participants[0].name} drafting hypothesis...[/bold blue]"):
                        draft = drafter.draft_from_session(
                            session=current_session,
                            transcript=transcript_msgs,
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
                                transcript=transcript_msgs,
                                context_snapshot=ctx_snap,
                                user_intent=user_intent,
                                provider=reviewer.provider,
                            )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                # Create hypothesis immediately and enter editing mode
                try:
                    hyp_svc = _hypothesis_service()
                    detail = hyp_svc.create_hypothesis(
                        project=current_session.project,
                        draft=draft,
                        source_session_id=current_session.session_id,
                    )
                    hyp_svc.log_event(current_session.project, detail.record.hypothesis_id, "session_started")
                    active_hypothesis = (detail.record.hypothesis_id, current_session.project, draft)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                _render_hypothesis_preview(draft, project=current_session.project)
                console.print(
                    Panel(
                        (
                            f"[bold]Created[/bold]: {detail.record.hypothesis_id}\n"
                            f"[bold]Path[/bold]: {detail.path}"
                        ),
                        title=f"[bold green]{detail.record.title}[/bold green]",
                        border_style="green",
                    )
                )
                _print_hypothesis_mode_hints(console, current_session, detail.record.hypothesis_id)
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
                sub_arg = argument.strip()
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                experiment_service = _experiment_service()

                # ── Sub-commands when in planning mode ──
                if sub_arg == "status":
                    if active_launch_exp is None:
                        console.print("[dim]Not in experiment planning mode.[/dim]")
                    else:
                        _render_launch_exp_status(active_launch_exp)
                    continue

                if sub_arg == "approve-tasks":
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.TASK_BREAKDOWN:
                        console.print("[bold red]Error:[/bold red] Not in task breakdown phase.")
                        continue
                    if not active_launch_exp.task_plans:
                        console.print("[bold red]Error:[/bold red] No tasks to approve.")
                        continue
                    dep_err = experiment_service.validate_dependency_graph(active_launch_exp.task_plans)
                    if dep_err:
                        console.print(f"[bold red]Dependency error:[/bold red] {dep_err}")
                        continue
                    active_launch_exp = experiment_service.approve_task_list(active_launch_exp)
                    ct = active_launch_exp.current_task
                    console.print(
                        Panel(
                            f"Task list approved ({len(active_launch_exp.task_plans)} tasks).\n"
                            f"Now planning task details. Starting with: [bold]{ct.id}: {ct.name}[/bold]" if ct else "All tasks already approved.",
                            title="[bold green]Phase: Task Planning[/bold green]",
                            border_style="green",
                        )
                    )
                    _print_launch_exp_hints(console, active_launch_exp)
                    continue

                if sub_arg.startswith("approve-task"):
                    task_id = sub_arg.replace("approve-task", "").strip()
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.TASK_PLANNING:
                        console.print("[bold red]Error:[/bold red] Not in task planning phase.")
                        continue
                    if not task_id:
                        ct = active_launch_exp.current_task
                        task_id = ct.id if ct else ""
                    if not task_id:
                        console.print("[bold red]Error:[/bold red] No task to approve.")
                        continue
                    active_launch_exp = experiment_service.approve_task(active_launch_exp, task_id)
                    if active_launch_exp.phase == LaunchExpPhase.SCRIPT_GENERATION:
                        console.print(
                            Panel(
                                "All tasks approved! Ready to generate run.sh.\n"
                                "Type feedback or use [bold]/launch-exp generate-script[/bold] to generate.",
                                title="[bold green]Phase: Script Generation[/bold green]",
                                border_style="green",
                            )
                        )
                        _print_launch_exp_hints(console, active_launch_exp)
                    else:
                        ct = active_launch_exp.current_task
                        console.print(f"[green]Task {task_id} approved.[/green] Next: [bold]{ct.id}: {ct.name}[/bold]" if ct else f"[green]Task {task_id} approved.[/green]")
                        _print_launch_exp_hints(console, active_launch_exp)
                    continue

                if sub_arg.startswith("reopen-task"):
                    task_id = sub_arg.replace("reopen-task", "").strip()
                    if active_launch_exp is None:
                        console.print("[bold red]Error:[/bold red] Not in experiment planning mode.")
                        continue
                    if not task_id:
                        console.print("[bold red]Usage:[/bold red] /launch-exp reopen-task <task_id>")
                        continue
                    try:
                        active_launch_exp = experiment_service.reopen_task(active_launch_exp, task_id)
                        console.print(f"[yellow]Task {task_id} reopened.[/yellow] Now re-planning it.")
                    except ValueError as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                if sub_arg == "generate-script":
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
                        console.print("[bold red]Error:[/bold red] Not in script generation phase. Approve all tasks first.")
                        continue
                    try:
                        hyp_detail = _hypothesis_service().load_hypothesis(
                            current_session.project,
                            active_launch_exp.hypothesis_id,
                        )
                        code_context = experiment_service.get_code_context(current_session.project)
                        planner = ExperimentPlanner(RepoPaths.discover())
                        first_participant = current_session.participants[0] if current_session.participants else None
                        provider = first_participant.provider if first_participant else None
                        # Get runtime context for the script prompt
                        try:
                            exec_profile = experiment_service.build_default_execution_profile(current_session.project)
                            workdir = exec_profile.workdir or ""
                            setup_summary = exec_profile.setup_script or ""
                        except Exception:
                            workdir = ""
                            setup_summary = ""
                        with console.status("[bold cyan]Generating run.sh...[/bold cyan]"):
                            result = planner.generate_run_sh(
                                session=active_launch_exp,
                                hypothesis_title=hyp_detail.record.title,
                                hypothesis_claim=hyp_detail.record.claim,
                                code_tree=code_context,
                                workdir=workdir,
                                setup_script_summary=setup_summary,
                                provider=provider,
                            )
                        active_launch_exp = experiment_service.save_script(
                            active_launch_exp,
                            result["run_sh"],
                            result["config_yaml"],
                        )
                        console.print(
                            Panel(
                                f"[bold]Summary[/bold]: {result['summary']}\n\n"
                                f"[bold]run.sh[/bold] ({len(result['run_sh'].splitlines())} lines)\n"
                                + ("" if not result["config_yaml"] else f"[bold]config.yaml[/bold] generated\n")
                                + "\nType feedback to revise, or [bold]/launch-exp done[/bold] to finalize.",
                                title="[bold green]Script Generated[/bold green]",
                                border_style="green",
                            )
                        )
                        _print_launch_exp_hints(console, active_launch_exp)
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                if sub_arg.startswith("run-task"):
                    task_id = sub_arg.replace("run-task", "").strip()
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
                        console.print("[bold red]Error:[/bold red] Not in script generation phase. Use /launch-exp resume <experiment_id> first for an existing experiment.")
                        continue
                    if not task_id:
                        console.print("[bold red]Usage:[/bold red] /launch-exp run-task <task_id>")
                        continue
                    known_task_ids = {t.id for t in active_launch_exp.task_plans}
                    if known_task_ids and task_id not in known_task_ids:
                        console.print(f"[bold red]Error:[/bold red] Unknown task '{task_id}'. Known tasks: {', '.join(sorted(known_task_ids))}")
                        continue
                    if not active_launch_exp.run_sh_content:
                        console.print("[bold red]Error:[/bold red] No run.sh available. Use /launch-exp generate-script first.")
                        continue

                    try:
                        hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
                        code_context = experiment_service.get_code_context(current_session.project)
                        planner = ExperimentPlanner(RepoPaths.discover())
                        first_participant = current_session.participants[0] if current_session.participants else None
                        provider = first_participant.provider if first_participant else None
                        tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
                        try:
                            exec_profile = experiment_service.build_default_execution_profile(current_session.project)
                            workdir = exec_profile.workdir or ""
                            setup_summary = exec_profile.setup_script or ""
                        except Exception:
                            workdir = ""
                            setup_summary = ""

                        resume_issues = _run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
                        if resume_issues:
                            console.print("[dim]run.sh does not expose task-level resume controls; asking agent to add them before submitting...[/dim]")
                            instruction = (
                                f"Revise this run.sh so it supports task-level resume/retry controls, then keep the existing experiment logic intact.\n"
                                f"Required contract:\n"
                                f"- LABIT_ONLY_TASK={task_id} runs only task {task_id} after verifying dependency checkpoints.\n"
                                f"- LABIT_START_AT={task_id} skips tasks before {task_id}.\n"
                                f"- LABIT_FORCE_TASK={task_id} reruns {task_id} even if its checkpoint exists.\n"
                                f"- Default reruns must be non-destructive: do not delete prior outputs unless LABIT_FORCE_CLEAN=1.\n"
                                f"- Add should_run_task() and require_checkpoint() bash helpers.\n"
                                f"Current missing/unsafe pieces: {', '.join(resume_issues)}.\n"
                                f"Preserve the standard experiment_results.json output contract."
                            )
                            with console.status("[bold cyan]Adding task-level resume support to run.sh...[/bold cyan]"):
                                result = planner.revise_run_sh(
                                    current_run_sh=active_launch_exp.run_sh_content,
                                    current_config_yaml=active_launch_exp.config_yaml_content,
                                    tasks_json=tasks_json,
                                    user_instruction=instruction,
                                    code_tree=code_context,
                                    workdir=workdir,
                                    setup_script_summary=setup_summary,
                                    provider=provider,
                                )
                            active_launch_exp = experiment_service.save_script(
                                active_launch_exp,
                                result["run_sh"],
                                result["config_yaml"],
                            )
                            resume_issues = _run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
                            if resume_issues:
                                console.print(
                                    "[bold red]Error:[/bold red] Agent revision did not add the required task-level resume controls. "
                                    "Revise run.sh manually or try again.\n"
                                    f"[dim]Still missing: {', '.join(resume_issues)}[/dim]"
                                )
                                continue
                            console.print(
                                Panel(
                                    f"[bold]Summary[/bold]: {result['summary']}\n"
                                    f"[bold]run.sh[/bold]: {len(result['run_sh'].splitlines())} lines",
                                    title="[bold green]Resume Controls Added[/bold green]",
                                    border_style="green",
                                )
                            )

                        detail = experiment_service.finalize_experiment(active_launch_exp)
                        env_overrides = {
                            "LABIT_ONLY_TASK": task_id,
                            "LABIT_START_AT": task_id,
                            "LABIT_FORCE_TASK": task_id,
                        }
                        console.print(
                            Panel(
                                f"[bold]Experiment[/bold]: {detail.record.experiment_id}\n"
                                f"[bold]Task[/bold]: {task_id}\n"
                                f"[bold]Mode[/bold]: run only this task; reuse prior outputs",
                                title="[bold cyan]Task Retry Submit[/bold cyan]",
                                border_style="cyan",
                            )
                        )
                        submit_ok = _submit_and_monitor(
                            console=console,
                            experiment_service=experiment_service,
                            active_launch_exp=active_launch_exp,
                            current_session=current_session,
                            service=service,
                            planner=planner,
                            _hypothesis_service=_hypothesis_service,
                            first_participant=first_participant,
                            env_overrides=env_overrides,
                        )
                        if submit_ok:
                            active_launch_exp = None
                        else:
                            console.print("[yellow]Task retry was not confirmed stable; keeping launch-exp session active for revision.[/yellow]")
                    except Exception as exc:
                        console.print(f"[bold red]Error submitting task retry:[/bold red] {exc}")
                    continue

                if sub_arg == "done":
                    if active_launch_exp is None:
                        console.print("[dim]Not in experiment planning mode.[/dim]")
                        continue
                    if active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
                        console.print("[yellow]Warning:[/yellow] Not all phases completed. Exiting anyway.")
                    submit_ok = False
                    if active_launch_exp.run_sh_content:
                        try:
                            detail = experiment_service.finalize_experiment(active_launch_exp)
                            console.print(
                                Panel(
                                    f"[bold]Experiment[/bold]: {detail.record.experiment_id}\n"
                                    f"[bold]Hypothesis[/bold]: {active_launch_exp.hypothesis_id}\n"
                                    f"[bold]Tasks[/bold]: {len(detail.tasks)}\n"
                                    f"[bold]Path[/bold]: {detail.path}",
                                    title="[bold green]Experiment Finalized[/bold green]",
                                    border_style="green",
                                )
                            )
                            # Submit + monitor loop
                            submit_ok = _submit_and_monitor(
                                console=console,
                                experiment_service=experiment_service,
                                active_launch_exp=active_launch_exp,
                                current_session=current_session,
                                service=service,
                                planner=ExperimentPlanner(RepoPaths.discover()),
                                _hypothesis_service=_hypothesis_service,
                                first_participant=current_session.participants[0] if current_session.participants else None,
                            )
                            try:
                                summary = (
                                    f"Experiment planned and submitted: {detail.record.experiment_id} "
                                    f"for {active_launch_exp.hypothesis_id}"
                                    if submit_ok
                                    else f"Experiment planned (submission pending): {detail.record.experiment_id} "
                                         f"for {active_launch_exp.hypothesis_id}"
                                )
                                service.record_session_event(
                                    session_id=current_session.session_id,
                                    kind=SessionEventKind.ARTIFACT_EXPERIMENT_CREATED,
                                    actor="labit",
                                    summary=summary,
                                    payload={
                                        "experiment_id": detail.record.experiment_id,
                                        "hypothesis_id": active_launch_exp.hypothesis_id,
                                        "task_count": len(detail.tasks),
                                        "submitted": submit_ok,
                                    },
                                    evidence_refs=_session_evidence_refs(current_session)
                                    + [f"hypothesis:{active_launch_exp.hypothesis_id}", f"experiment:{detail.record.experiment_id}"],
                                )
                            except Exception:
                                pass
                        except Exception as exc:
                            console.print(f"[bold red]Error finalizing:[/bold red] {exc}")
                    else:
                        console.print("[dim]No script generated. Experiment not finalized.[/dim]")
                    if submit_ok:
                        active_launch_exp = None
                    continue

                # ── Resume existing experiment ──
                if sub_arg.startswith("resume"):
                    exp_id = sub_arg.replace("resume", "").strip()
                    if not exp_id:
                        console.print("[bold red]Usage:[/bold red] /launch-exp resume <experiment_id>")
                        continue
                    if active_launch_exp is not None:
                        console.print(f"[bold red]Error:[/bold red] Already planning experiment for {active_launch_exp.hypothesis_id}. Use /launch-exp done first.")
                        continue
                    try:
                        active_launch_exp = experiment_service.resume_launch_exp_session(
                            project=current_session.project,
                            experiment_id=exp_id,
                        )
                        phase_label = active_launch_exp.phase.value.replace("_", " ").title()
                        console.print(
                            Panel(
                                f"[bold]Experiment[/bold]: {exp_id}\n"
                                f"[bold]Hypothesis[/bold]: {active_launch_exp.hypothesis_id}\n"
                                f"[bold]Tasks[/bold]: {len(active_launch_exp.task_plans)}\n"
                                f"[bold]Phase[/bold]: {phase_label}\n"
                                f"[bold]Has run.sh[/bold]: {'yes' if active_launch_exp.run_sh_content else 'no'}",
                                title="[bold cyan]Experiment Resumed[/bold cyan]",
                                border_style="cyan",
                            )
                        )
                        _print_launch_exp_hints(console, active_launch_exp)
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                # ── Start new planning session ──
                hypothesis_id = sub_arg
                if not hypothesis_id:
                    console.print("[bold red]Usage:[/bold red] /launch-exp <hypothesis_id>")
                    continue
                if active_launch_exp is not None:
                    console.print(f"[bold red]Error:[/bold red] Already planning experiment for {active_launch_exp.hypothesis_id}. Use /launch-exp done first.")
                    continue

                try:
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, hypothesis_id)
                    active_launch_exp = experiment_service.start_launch_exp_session(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                    )
                    code_tree = experiment_service.get_code_tree(current_session.project)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                # Draft initial task breakdown
                try:
                    planner = ExperimentPlanner(RepoPaths.discover())
                    first_participant = current_session.participants[0] if current_session.participants else None
                    provider = first_participant.provider if first_participant else None
                    with console.status("[bold cyan]Drafting task breakdown...[/bold cyan]"):
                        tasks = planner.draft_task_breakdown(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            hypothesis_title=hyp_detail.record.title,
                            hypothesis_claim=hyp_detail.record.claim,
                            experiment_plan_md=hyp_detail.experiment_plan_markdown,
                            code_tree=code_tree,
                            provider=provider,
                        )
                    active_launch_exp = experiment_service.save_task_plans(active_launch_exp, tasks)
                except Exception as exc:
                    console.print(f"[bold red]Error drafting tasks:[/bold red] {exc}")
                    active_launch_exp = None
                    continue

                # Display task breakdown
                _render_task_breakdown(active_launch_exp)
                _print_launch_exp_hints(console, active_launch_exp)
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
                seen_launch_ids: set[str] = set()
                max_rows = 10
                for summary in experiments:
                    detail = experiment_service.load_experiment(current_session.project, summary.experiment_id)

                    if len(rows) >= max_rows:
                        break

                    # ── Scan experiment-level launches (only latest per experiment) ──
                    has_experiment_launch = False
                    launches_dir = experiment_service.tasks_dir(
                        current_session.project, detail.record.experiment_id
                    ) / "launches"
                    if launches_dir.is_dir():
                        # Only show the most recent launch (sorted reverse = newest first)
                        for launch_subdir in sorted(launches_dir.iterdir(), reverse=True):
                            launch_yaml = launch_subdir / "launch.yaml"
                            if not launch_yaml.exists():
                                continue
                            try:
                                artifact = experiment_service.load_launch_artifact(
                                    current_session.project,
                                    detail.record.experiment_id,
                                    launch_subdir.name,
                                )
                            except Exception:
                                continue
                            if not artifact.submission or not artifact.submission.accepted:
                                continue
                            seen_launch_ids.add(artifact.launch_id)
                            try:
                                collected = executor.collect(artifact)
                            except Exception:
                                collected = {}
                            collected_status = str(collected.get("status", "unknown")).strip() or "unknown"
                            log_tail = str(collected.get("log_tail", ""))[:2000]
                            status_label = collected_status
                            if collected_status == "stopped":
                                has_output = collected.get("output_dir_exists") or collected.get("artifact_refs")
                                status_label = "completed" if has_output else "failed"

                            progress_hint = _extract_log_hint(log_tail)
                            error_hint = _task_error_from_collect(collected) or progress_hint

                            # Build display based on status
                            prefix = f"- {detail.record.experiment_id}/{artifact.launch_id}"
                            if status_label == "running":
                                # Show last meaningful log line as progress
                                if progress_hint:
                                    row = f"{prefix} · [green]running[/green] · {progress_hint}"
                                else:
                                    pid_hint = artifact.submission.pid if artifact.submission else None
                                    log_hint = artifact.submission.log_path if artifact.submission else None
                                    if pid_hint and log_hint:
                                        row = f"{prefix} · [green]running[/green] · pid {pid_hint} · log {log_hint}"
                                    elif pid_hint:
                                        row = f"{prefix} · [green]running[/green] · pid {pid_hint}"
                                    else:
                                        row = f"{prefix} · [green]running[/green] · (starting up...)"
                            elif status_label == "failed":
                                # Show error summary
                                error_line = error_hint or "(no log)"
                                row = f"{prefix} · [red]failed[/red] · {error_line}"
                            elif status_label == "completed":
                                # Show metrics if available, otherwise last log line
                                metrics_hint = ""
                                for file_content in collected.get("files", {}).values():
                                    try:
                                        file_data = json.loads(file_content) if isinstance(file_content, str) else file_content
                                    except (json.JSONDecodeError, TypeError):
                                        continue
                                    if isinstance(file_data, dict):
                                        # Check for nested "metrics" dict (experiment_results.json format)
                                        metrics_dict = file_data.get("metrics", {}) if "metrics" in file_data else file_data
                                        if isinstance(metrics_dict, dict):
                                            for k in ("auroc", "accuracy", "loss", "f1", "eval_loss"):
                                                if k in metrics_dict:
                                                    v = metrics_dict[k]
                                                    metrics_hint += f"{k}={v:.4f} " if isinstance(v, float) else f"{k}={v} "
                                        # Prefer conclusion over raw metrics
                                        conclusion = file_data.get("conclusion", "")
                                        if conclusion:
                                            metrics_hint = (conclusion[:80] + (" | " + metrics_hint.strip() if metrics_hint else "")).strip()
                                            break
                                if metrics_hint:
                                    row = f"{prefix} · [blue]completed[/blue] · {metrics_hint.strip()}"
                                elif progress_hint:
                                    row = f"{prefix} · [blue]completed[/blue] · {progress_hint}"
                                else:
                                    row = f"{prefix} · [blue]completed[/blue]"
                            else:
                                row = f"{prefix} · {status_label}"
                                if progress_hint:
                                    row += f" · {progress_hint}"

                            rows.append(row)
                            rows_by_experiment.setdefault(detail.record.experiment_id, []).append(row)
                            has_experiment_launch = True
                            break  # only show latest launch per experiment

                    # ── Scan task-level launches ──
                    if has_experiment_launch or len(rows) >= max_rows:
                        continue
                    for task in detail.tasks:
                        if not task.latest_launch_id:
                            continue
                        if task.latest_launch_id in seen_launch_ids:
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
                        if len(rows) >= max_rows:
                            break

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

                # --- Step 1: Collect remote results ---
                try:
                    hypothesis_detail = hypothesis_service.load_hypothesis(current_session.project, hypothesis_id)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                with console.status("[bold blue]Collecting experiment results from remote...[/bold blue]"):
                    try:
                        collected_results = experiment_service.collect_experiment_results(
                            project=current_session.project,
                            hypothesis_id=hypothesis_id,
                        )
                    except Exception as exc:
                        console.print(f"[bold red]Error collecting results:[/bold red] {exc}")
                        collected_results = []

                if not collected_results:
                    console.print("[yellow]No experiments found for this hypothesis.[/yellow]")
                    continue

                # --- Step 2: Show results and let agent assess ---
                from labit.experiments.models import ExperimentAssessment
                results_summary_parts: list[str] = []
                assessable_ids: list[str] = []  # only completed experiments get assessed
                for cr in collected_results:
                    exp_id = cr["experiment_id"]
                    exp_title = cr["title"]
                    collected = cr.get("collected") or {}
                    remote_status = collected.get("status", "unknown")
                    log_tail = collected.get("log_tail", "")
                    files = collected.get("files", {})

                    # Determine if this experiment is assessable (has results)
                    is_running = remote_status == "running"
                    has_files = bool(files)
                    has_log = bool(log_tail and log_tail.strip())

                    # Display each experiment's results
                    status_color = {"running": "blue", "stopped": "yellow"}.get(remote_status, "dim")
                    console.print(f"  [bold]{exp_id}[/bold] ({exp_title}) — [{status_color}]{remote_status}[/{status_color}]")

                    if is_running:
                        console.print(f"    [dim](still running — skipping assessment)[/dim]")
                        continue
                    if not has_files and not has_log:
                        console.print(f"    [dim](no results available — skipping assessment)[/dim]")
                        continue

                    # Check for standardized experiment_results.json first
                    std_results = None
                    if files:
                        for fpath, fcontent in files.items():
                            if fpath.endswith("experiment_results.json"):
                                try:
                                    std_results = json.loads(fcontent) if isinstance(fcontent, str) else fcontent
                                except (json.JSONDecodeError, TypeError):
                                    pass
                                break

                    # Only assess experiments that completed with results, not failed ones
                    exp_failed = False
                    if std_results:
                        exp_status = std_results.get("status", "unknown")
                        exp_metrics = std_results.get("metrics", {})
                        if exp_status == "failed" and not exp_metrics:
                            exp_failed = True

                    # Build text summary for agent
                    part = f"## Experiment {exp_id}: {exp_title}\n"
                    part += f"Remote status: {remote_status}\n"

                    if std_results:
                        # Use the standardized results file
                        exp_status = std_results.get("status", "unknown")
                        metrics = std_results.get("metrics", {})
                        conclusion = std_results.get("conclusion", "")
                        error = std_results.get("error", "")
                        artifacts = std_results.get("artifacts", [])

                        part += f"Experiment status: {exp_status}\n"
                        if metrics:
                            metrics_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
                            part += f"Metrics: {metrics_str}\n"
                            console.print(f"    [green]metrics:[/green] {metrics_str}")
                        if conclusion:
                            part += f"Conclusion: {conclusion}\n"
                            console.print(f"    [cyan]conclusion:[/cyan] {conclusion}")
                        if error:
                            part += f"Error: {error}\n"
                            console.print(f"    [red]error:[/red] {error}")
                        if artifacts:
                            part += f"Artifacts: {', '.join(artifacts)}\n"
                    elif files:
                        # Limit to 2 most relevant files to avoid prompt bloat
                        file_items = list(files.items())
                        # Prioritize results/summary/metrics files
                        def _file_priority(item: tuple) -> int:
                            name = item[0].lower()
                            for i, kw in enumerate(["result", "summary", "metric", "eval"]):
                                if kw in name:
                                    return i
                            return 99
                        file_items.sort(key=_file_priority)
                        for fpath, fcontent in file_items[:2]:
                            fname = fpath.rsplit("/", 1)[-1] if "/" in fpath else fpath
                            content_preview = fcontent[:3000] if len(fcontent) > 3000 else fcontent
                            part += f"\n### {fname}\n```json\n{content_preview}\n```\n"
                            console.print(f"    [dim]{fname}[/dim]: {fcontent[:200]}{'...' if len(fcontent) > 200 else ''}")
                    elif log_tail:
                        last_lines = "\n".join(log_tail.strip().splitlines()[-5:])
                        part += f"\n### Log tail\n```\n{last_lines}\n```\n"
                        console.print(f"    [dim]log:[/dim] {log_tail.strip().splitlines()[-1][:120] if log_tail.strip() else '(empty)'}")
                    else:
                        part += "No results or logs available yet.\n"
                        console.print("    [dim](no results yet)[/dim]")
                    results_summary_parts.append(part)
                    if exp_failed:
                        console.print(f"    [dim](failed without metrics — marking as invalid, skipping assessment)[/dim]")
                    else:
                        assessable_ids.append(exp_id)

                console.print("")

                if not assessable_ids:
                    console.print("[yellow]No completed experiments with results to assess. Wait for running experiments to finish.[/yellow]")
                    continue

                # Cap to most recent 5 experiments to avoid prompt bloat
                if len(results_summary_parts) > 5:
                    results_summary_parts = results_summary_parts[:5]
                    assessable_ids = assessable_ids[:5]
                    console.print(f"[dim](showing 5 most recent experiments out of {len(collected_results)})[/dim]")

                # --- Step 3: Ask agent to assess each experiment ---
                h_rec = hypothesis_detail.record
                assessment_prompt = (
                    f"You are reviewing experiment results for hypothesis {h_rec.hypothesis_id}: \"{h_rec.title}\"\n\n"
                    f"**Claim**: {h_rec.claim}\n"
                    f"**Success criteria**: {h_rec.success_criteria or '(not specified)'}\n"
                    f"**Failure criteria**: {h_rec.failure_criteria or '(not specified)'}\n\n"
                    f"## Collected Results\n\n"
                    + "\n".join(results_summary_parts)
                    + "\n\n---\n\n"
                    f"Assess ONLY the following experiments: {', '.join(assessable_ids)}\n\n"
                    "For each experiment, provide an assessment: `supports`, `contradicts`, `inconclusive`, or `invalid`.\n"
                    "Also provide a brief rationale for each.\n\n"
                    "Format your response as:\n"
                    "ASSESSMENT <experiment_id> <supports|contradicts|inconclusive|invalid>\n"
                    "RATIONALE <experiment_id> <brief explanation>\n\n"
                    "Then provide an overall summary of the evidence."
                )

                console.print("[bold blue]Asking agent to assess results...[/bold blue]")
                _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=assessment_prompt,
                )

                # --- Step 4: Parse agent's assessments from transcript ---
                transcript = service.transcript(current_session.session_id)
                last_agent_msg = ""
                for msg in reversed(transcript):
                    if msg.role != "user":
                        last_agent_msg = msg.text
                        break

                assessment_map = {
                    "supports": ExperimentAssessment.SUPPORTS,
                    "contradicts": ExperimentAssessment.CONTRADICTS,
                    "inconclusive": ExperimentAssessment.INCONCLUSIVE,
                    "invalid": ExperimentAssessment.INVALID,
                }
                assessed_count = 0
                # Try strict format first: ASSESSMENT <id> <label>
                for match in re.finditer(r"ASSESSMENT\s+(\S+)\s+(supports|contradicts|inconclusive|invalid)", last_agent_msg, re.IGNORECASE):
                    exp_id = match.group(1)
                    assessment_val = match.group(2).lower()
                    if assessment_val in assessment_map:
                        try:
                            experiment_service.update_assessment(
                                project=current_session.project,
                                experiment_id=exp_id,
                                assessment=assessment_map[assessment_val],
                            )
                            assessed_count += 1
                            console.print(f"  [green]Updated {exp_id} → {assessment_val}[/green]")
                        except Exception as exc:
                            console.print(f"  [yellow]Could not update {exp_id}: {exc}[/yellow]")
                # Fallback: try looser patterns like "e001: supports" or "**e001** — supports"
                if assessed_count == 0:
                    for match in re.finditer(r"[*`]*(e[-\w]+)[*`]*\s*[:—\-]\s*(supports|contradicts|inconclusive|invalid)", last_agent_msg, re.IGNORECASE):
                        exp_id = match.group(1)
                        assessment_val = match.group(2).lower()
                        if assessment_val in assessment_map:
                            try:
                                experiment_service.update_assessment(
                                    project=current_session.project,
                                    experiment_id=exp_id,
                                    assessment=assessment_map[assessment_val],
                                )
                                assessed_count += 1
                                console.print(f"  [green]Updated {exp_id} → {assessment_val}[/green]")
                            except Exception as exc:
                                console.print(f"  [yellow]Could not update {exp_id}: {exc}[/yellow]")

                if assessed_count == 0:
                    console.print("[yellow]Could not parse assessments from agent response. You can set them manually.[/yellow]")
                    # Let user set assessments interactively
                    for cr in collected_results:
                        if cr["assessment"] == "pending":
                            exp_id = cr["experiment_id"]
                            choice = Prompt.ask(
                                f"  Assessment for {exp_id}",
                                choices=["supports", "contradicts", "inconclusive", "invalid", "skip"],
                                default="skip",
                            )
                            if choice != "skip" and choice in assessment_map:
                                try:
                                    experiment_service.update_assessment(
                                        project=current_session.project,
                                        experiment_id=exp_id,
                                        assessment=assessment_map[choice],
                                    )
                                    console.print(f"  [green]Updated {exp_id} → {choice}[/green]")
                                except Exception as exc:
                                    console.print(f"  [yellow]Could not update {exp_id}: {exc}[/yellow]")

                # --- Step 5: Generate review suggestion (now with assessments) ---
                try:
                    suggestion = experiment_service.suggest_hypothesis_review(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                    )
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
                    active_hypothesis = None
                    active_launch_exp = None
                    active_dev = None
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
                    active_hypothesis = None
                    active_launch_exp = None
                    active_dev = None
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                _render_shell_header(current_session)
                console.print("")
                _render_recent_messages(service.transcript(current_session.session_id), count=8)
                continue

            if command == "/dev":
                sub = argument.strip()
                sub_parts = sub.split(maxsplit=1)
                dev_action = sub_parts[0] if sub_parts else ""
                dev_argument = sub_parts[1].strip() if len(sub_parts) > 1 else ""

                if dev_action == "status":
                    if active_dev is None:
                        console.print("[dim]No active dev loop.[/dim]")
                    else:
                        _render_dev_status(active_dev)
                    continue

                if dev_action == "stop":
                    if active_dev is None:
                        console.print("[dim]No active dev loop to stop.[/dim]")
                    else:
                        active_dev.status = "stopped"
                        console.print("[bold yellow]Dev loop stopped.[/bold yellow]")
                        if active_dev.dev_branch:
                            console.print(Panel(
                                (
                                    f"[bold]Branch[/bold]: {active_dev.dev_branch}\n"
                                    f"[bold]Original[/bold]: {active_dev.original_branch}\n\n"
                                    f"[bold]Branch repo[/bold]: {active_dev.branch_repo_root or active_dev.scope_git_root}\n"
                                    f"[bold]Worktree[/bold]: {active_dev.worktree_path or '(none)'}\n\n"
                                    "Use [bold]/dev finish[/bold] to merge, keep, or discard safely."
                                ),
                                title="[bold]Dev Branch[/bold]",
                                border_style="blue",
                            ))
                        active_dev = None
                    continue

                if dev_action == "finish":
                    if active_dev is None:
                        console.print("[dim]No active dev loop.[/dim]")
                        continue
                    if not active_dev.dev_branch:
                        console.print("[dim]No dev branch to finish (--branch off was used).[/dim]")
                        active_dev = None
                        continue
                    # Show options: merge, PR, discard, or keep
                    console.print(Panel(
                        (
                            f"[bold]Branch[/bold]: {active_dev.dev_branch}\n"
                            f"[bold]Original[/bold]: {active_dev.original_branch}\n"
                            f"[bold]Worktree[/bold]: {active_dev.worktree_path or '(none)'}\n"
                            f"[bold]Rounds[/bold]: {active_dev.current_round}\n\n"
                            "[bold]What would you like to do?[/bold]\n"
                            f"  [bold cyan]1[/bold cyan] — Merge into {active_dev.original_branch}\n"
                            "  [bold cyan]2[/bold cyan] — Keep branch and remove worktree\n"
                            "  [bold cyan]3[/bold cyan] — Discard branch and remove worktree\n"
                        ),
                        title="[bold green]Finish Dev Loop[/bold green]",
                        border_style="green",
                    ))
                    try:
                        choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
                    except (KeyboardInterrupt, EOFError):
                        continue
                    finish_cwd = active_dev.branch_repo_root or active_dev.scope_git_root or str(RepoPaths.discover().root)
                    finish_repo = Path(finish_cwd)
                    finish_worktree = Path(active_dev.worktree_path) if active_dev.worktree_path else None
                    # Validate the dev branch exists in this repo
                    branch_check = _git_output(
                        "git", "branch", "--list", active_dev.dev_branch, cwd=finish_repo
                    )
                    if not branch_check:
                        console.print(
                            f"[bold red]Branch '{active_dev.dev_branch}' not found in {finish_cwd}.[/bold red]\n"
                            "This can happen if the dev session was created before a scope change.\n"
                            "Refusing to guess which repo should be merged."
                        )
                        active_dev = None
                        continue
                    if choice == "1":
                        # Merge into original branch
                        subprocess.run(["git", "checkout", active_dev.original_branch], capture_output=True, cwd=finish_cwd)
                        result = subprocess.run(
                            ["git", "merge", active_dev.dev_branch, "--no-edit"],
                            capture_output=True, text=True, cwd=finish_cwd,
                        )
                        if result.returncode == 0:
                            console.print(f"[bold green]Merged {active_dev.dev_branch} into {active_dev.original_branch}.[/bold green]")
                            if finish_worktree is not None:
                                ok, msg = _remove_dev_worktree(finish_repo, finish_worktree)
                                if ok:
                                    console.print(f"[dim]Removed worktree {finish_worktree}.[/dim]")
                                else:
                                    console.print(f"[yellow]Worktree cleanup failed:[/yellow] {msg}")
                        else:
                            console.print(f"[bold red]Merge failed:[/bold red] {result.stderr.strip()}")
                            console.print(f"You are now on [bold]{active_dev.original_branch}[/bold]. Resolve manually.")
                    elif choice == "2":
                        # Keep branch, remove worktree if present, switch back
                        subprocess.run(["git", "checkout", active_dev.original_branch], capture_output=True, cwd=finish_cwd)
                        if finish_worktree is not None:
                            ok, msg = _remove_dev_worktree(finish_repo, finish_worktree)
                            if not ok:
                                console.print(f"[yellow]Worktree cleanup failed:[/yellow] {msg}")
                        console.print(f"[bold]Branch {active_dev.dev_branch} is preserved.[/bold]")
                    elif choice == "3":
                        # Discard branch and worktree
                        subprocess.run(["git", "checkout", active_dev.original_branch], capture_output=True, cwd=finish_cwd)
                        if finish_worktree is not None:
                            ok, msg = _remove_dev_worktree(finish_repo, finish_worktree)
                            if not ok:
                                console.print(f"[yellow]Worktree cleanup failed:[/yellow] {msg}")
                        subprocess.run(["git", "branch", "-D", active_dev.dev_branch], capture_output=True, cwd=finish_cwd)
                        console.print(f"[bold red]Discarded branch {active_dev.dev_branch}.[/bold red]")
                    active_dev = None
                    continue

                if dev_action == "continue":
                    if active_dev is None:
                        console.print("[bold red]No active dev loop. Use /dev start <task> first.[/bold red]")
                        continue
                    if active_dev.status == "waiting_decision":
                        console.print("[bold red]Decision pending. Reply with your choice first.[/bold red]")
                        _render_dev_decision(active_dev)
                        continue
                    if active_dev.status in ("completed", "stopped"):
                        console.print("[dim]Dev loop already finished. Use /dev start for a new one.[/dim]")
                        active_dev = None
                        continue
                    active_dev = _run_dev_loop(
                        service=service,
                        session=current_session,
                        dev_session=active_dev,
                    )
                    continue

                if dev_action == "start":
                    if not dev_argument:
                        console.print("[bold red]Usage:[/bold red] /dev start <task description>")
                        continue
                    if active_dev is not None and active_dev.status == "active":
                        console.print("[bold red]Dev loop already active. /dev stop first.[/bold red]")
                        continue
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] Session not attached to a project.")
                        continue

                    # Parse optional flags from task
                    task_text = dev_argument
                    max_rounds = 6
                    test_mode = "auto"
                    # Extract --max-rounds N
                    mr_match = re.search(r"--max-rounds\s+(\d+)", task_text)
                    if mr_match:
                        max_rounds = min(int(mr_match.group(1)), 15)
                        task_text = task_text[:mr_match.start()] + task_text[mr_match.end():]
                    # Extract --test off|auto|on
                    tm_match = re.search(r"--test\s+(off|auto|on)", task_text)
                    if tm_match:
                        test_mode = tm_match.group(1)
                        task_text = task_text[:tm_match.start()] + task_text[tm_match.end():]
                    # Extract --branch off|auto (default: auto)
                    branch_mode = "auto"
                    bm_match = re.search(r"--branch\s+(off|auto)", task_text)
                    if bm_match:
                        branch_mode = bm_match.group(1)
                        task_text = task_text[:bm_match.start()] + task_text[bm_match.end():]
                    task_text = task_text.strip()
                    if not task_text:
                        console.print("[bold red]Usage:[/bold red] /dev start <task description>")
                        continue

                    # Assign writer/reviewer from participants
                    participants = current_session.participants
                    if len(participants) >= 2:
                        writer_name = participants[0].name
                        reviewer_name = participants[1].name
                    else:
                        writer_name = participants[0].name
                        reviewer_name = participants[0].name

                    scope_label, scope_pathspecs, scope_git_root = _resolve_dev_scope(current_session)
                    initial_dirty_files = _list_scope_dirty_files(scope_pathspecs, scope_git_root)

                    # In isolated worktree mode, dirty files in the original checkout
                    # are not copied into the worktree. In branch-off mode, refuse
                    # dirty starts unless the user explicitly opts in.
                    allow_dirty = "--allow-dirty" in dev_argument
                    if "--allow-dirty" in dev_argument:
                        task_text = re.sub(r"--allow-dirty\s*", "", task_text).strip()
                    if branch_mode == "off" and initial_dirty_files and not allow_dirty:
                        console.print(
                            "[bold red]Error:[/bold red] Working tree has uncommitted changes. "
                            "Commit or stash them first, or use [bold]--allow-dirty[/bold] to proceed anyway."
                        )
                        preview = "\n".join(f"- {item}" for item in initial_dirty_files[:10])
                        console.print(Panel(preview, title="Dirty Files", border_style="red"))
                        continue
                    if branch_mode == "auto" and initial_dirty_files:
                        preview = "\n".join(f"- {item}" for item in initial_dirty_files[:10])
                        console.print(Panel(
                            preview,
                            title="[yellow]Original Checkout Has Dirty Files[/yellow]",
                            border_style="yellow",
                        ))
                        console.print(
                            "[yellow]Note:[/yellow] /dev will use a clean isolated worktree from HEAD; "
                            "the dirty files above will not be included unless committed first."
                        )

                    # Create isolated dev worktree (unless --branch off)
                    dev_branch = ""
                    original_branch = ""
                    branch_repo_root = scope_git_root
                    worktree_path = Path("")
                    active_scope_git_root = scope_git_root
                    active_scope_pathspecs = scope_pathspecs
                    if branch_mode == "auto":
                        try:
                            dev_branch, original_branch, worktree_path = _create_dev_worktree(
                                task=task_text,
                                git_root=scope_git_root,
                                project=current_session.project,
                            )
                        except Exception as exc:
                            console.print(f"[bold red]Failed to create dev worktree:[/bold red] {exc}")
                            continue
                        active_scope_git_root = worktree_path
                        active_scope_pathspecs = ["."]
                        initial_dirty_files = []
                    else:
                        # Stay on current branch
                        try:
                            original_branch = _git_output(
                                "git", "rev-parse", "--abbrev-ref", "HEAD",
                                cwd=scope_git_root,
                            )
                        except Exception:
                            pass

                    active_dev = DevLoopSession(
                        task=task_text,
                        writer_name=writer_name,
                        reviewer_name=reviewer_name,
                        max_rounds=max_rounds,
                        test_mode=test_mode,
                        scope_label=scope_label,
                        scope_pathspecs=active_scope_pathspecs,
                        scope_git_root=str(active_scope_git_root),
                        branch_repo_root=str(branch_repo_root),
                        worktree_path=str(worktree_path) if str(worktree_path) != "." else "",
                        initial_dirty_files=initial_dirty_files,
                        dev_branch=dev_branch,
                        original_branch=original_branch,
                    )

                    console.print(Panel(
                        (
                            f"[bold]Task[/bold]: {task_text}\n"
                            f"[bold]Writer[/bold]: {writer_name}\n"
                            f"[bold]Reviewer[/bold]: {reviewer_name}\n"
                            f"[bold]Max rounds[/bold]: {max_rounds}\n"
                            f"[bold]Test mode[/bold]: {test_mode}\n"
                            f"[bold]Scope[/bold]: {scope_label}\n"
                            f"[bold]Branch repo[/bold]: {branch_repo_root}\n"
                            f"[bold]Editable workspace[/bold]: {active_scope_git_root}\n"
                            f"[bold]Branch[/bold]: {dev_branch + ' (from ' + original_branch + ')' if dev_branch else original_branch + ' (no worktree isolation)'}"
                        ),
                        title="[bold green]Dev Loop Starting[/bold green]",
                        border_style="green",
                    ))

                    active_dev = _run_dev_loop(
                        service=service,
                        session=current_session,
                        dev_session=active_dev,
                    )
                    # Show branch summary after loop ends
                    if active_dev.dev_branch and active_dev.status in ("completed", "stopped"):
                        console.print(Panel(
                            (
                                f"[bold]Branch[/bold]: {active_dev.dev_branch}\n"
                                f"[bold]Original[/bold]: {active_dev.original_branch}\n"
                                f"[bold]Branch repo[/bold]: {active_dev.branch_repo_root or active_dev.scope_git_root}\n"
                                f"[bold]Worktree[/bold]: {active_dev.worktree_path or '(none)'}\n"
                                f"[bold]Rounds[/bold]: {active_dev.current_round}\n\n"
                                "Use [bold]/dev finish[/bold] to merge, keep, or discard the branch."
                            ),
                            title="[bold]Dev Loop Finished[/bold]",
                            border_style="blue",
                        ))
                    continue

                console.print(
                    "[bold red]Usage:[/bold red] /dev start <task> | /dev status | /dev continue | /dev finish | /dev stop"
                )
                continue

            console.print(f"[bold red]Unknown command:[/bold red] {command}")
            continue

        # ── Dev loop decision handling ──
        if active_dev is not None and active_dev.status == "waiting_decision":
            # User is answering a decision question
            decision = active_dev.pending_decision
            if decision:
                # Check if it's a letter choice
                choice = raw.strip().upper()
                if len(choice) == 1 and "A" <= choice <= chr(ord("A") + len(decision.options) - 1):
                    idx = ord(choice) - ord("A")
                    active_dev.user_decision = f"Option {choice}: {decision.options[idx]}"
                else:
                    active_dev.user_decision = raw.strip()

                active_dev.pending_decision = None
                active_dev.status = "active"
                console.print(f"[bold green]Decision recorded:[/bold green] {active_dev.user_decision}")
                active_dev = _run_dev_loop(
                    service=service,
                    session=current_session,
                    dev_session=active_dev,
                )
            continue

        # ── Launch-exp planning mode ──
        if active_launch_exp is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Experiment planning mode does not support image attachments yet.")
                continue
            exp_service = _experiment_service()
            planner = ExperimentPlanner(RepoPaths.discover())
            first_participant = current_session.participants[0] if current_session.participants else None
            provider = first_participant.provider if first_participant else None
            phase = active_launch_exp.phase

            try:
                exp_service.log_user_instruction(active_launch_exp, raw)

                if phase == LaunchExpPhase.TASK_BREAKDOWN:
                    # User is iterating on task list
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
                    with console.status("[bold blue]Revising task breakdown...[/bold blue]"):
                        revised_tasks = planner.revise_task_breakdown(
                            current_tasks=active_launch_exp.task_plans,
                            user_instruction=raw,
                            hypothesis_title=hyp_detail.record.title,
                            hypothesis_claim=hyp_detail.record.claim,
                            interaction_log=exp_service.planning_interaction_excerpt(active_launch_exp),
                            provider=provider,
                        )
                    dep_err = exp_service.validate_dependency_graph(revised_tasks)
                    if dep_err:
                        console.print(f"[yellow]Warning:[/yellow] {dep_err}")
                    active_launch_exp = exp_service.save_task_plans(active_launch_exp, revised_tasks)
                    exp_service.log_agent_revision(active_launch_exp, f"Revised task breakdown: {len(revised_tasks)} tasks", first_participant.name if first_participant else "")
                    _render_task_breakdown(active_launch_exp)

                elif phase == LaunchExpPhase.TASK_PLANNING:
                    # User is iterating on current task's detail
                    ct = active_launch_exp.current_task
                    if ct is None:
                        console.print("[dim]No task to plan. Use /launch-exp approve-tasks or /launch-exp done.[/dim]")
                        continue
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
                    code_tree = exp_service.get_code_tree(current_session.project)
                    with console.status(f"[bold blue]Planning {ct.id}: {ct.name}...[/bold blue]"):
                        detailed_task = planner.plan_task_detail(
                            task=ct,
                            all_tasks=active_launch_exp.task_plans,
                            hypothesis_title=hyp_detail.record.title,
                            hypothesis_claim=hyp_detail.record.claim,
                            code_tree=code_tree,
                            user_instruction=raw,
                            interaction_log=exp_service.planning_interaction_excerpt(active_launch_exp),
                            provider=provider,
                        )
                    active_launch_exp = exp_service.update_task_detail(active_launch_exp, detailed_task)
                    exp_service.log_agent_revision(active_launch_exp, f"Planned {ct.id}: {ct.name}", first_participant.name if first_participant else "")
                    _render_task_detail(detailed_task)

                elif phase == LaunchExpPhase.SCRIPT_GENERATION:
                    # User is iterating on run.sh
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
                    code_tree = exp_service.get_code_context(current_session.project)
                    tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
                    # Get runtime context for revision prompt
                    try:
                        exec_profile = exp_service.build_default_execution_profile(current_session.project)
                        workdir = exec_profile.workdir or ""
                        setup_summary = exec_profile.setup_script or ""
                    except Exception:
                        workdir = ""
                        setup_summary = ""
                    with console.status("[bold blue]Revising run.sh...[/bold blue]"):
                        result = planner.revise_run_sh(
                            current_run_sh=active_launch_exp.run_sh_content,
                            current_config_yaml=active_launch_exp.config_yaml_content,
                            tasks_json=tasks_json,
                            user_instruction=raw,
                            code_tree=code_tree,
                            workdir=workdir,
                            setup_script_summary=setup_summary,
                            provider=provider,
                        )
                    active_launch_exp = exp_service.save_script(
                        active_launch_exp,
                        result["run_sh"],
                        result["config_yaml"],
                    )
                    exp_service.log_agent_revision(active_launch_exp, result["summary"], first_participant.name if first_participant else "")
                    console.print(
                        Panel(
                            f"[bold]Summary[/bold]: {result['summary']}\n"
                            f"[bold]run.sh[/bold]: {len(result['run_sh'].splitlines())} lines",
                            title="[bold green]Script Revised[/bold green]",
                            border_style="green",
                        )
                    )

            except KeyboardInterrupt:
                console.print("[dim italic]Interrupted.[/dim italic]")
            except Exception as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
            continue

        # ── Hypothesis editing mode ──
        if active_hypothesis is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Hypothesis mode does not support image attachments yet.")
                continue
            h_id, h_proj, h_draft = active_hypothesis
            hyp_drafter = _hypothesis_drafter()
            hyp_svc = _hypothesis_service()
            author = current_session.participants[0]
            try:
                # Log user instruction
                hyp_svc.log_event(h_proj, h_id, "user_instruction", content=raw)

                # Step 1: Author revises hypothesis
                with console.status(f"[bold blue]{author.name} revising hypothesis...[/bold blue]"):
                    revised_draft = hyp_drafter.revise_hypothesis(
                        current_draft=h_draft,
                        session=current_session,
                        transcript=service.transcript(current_session.session_id),
                        context_snapshot=service.context_snapshot(current_session.session_id),
                        user_instruction=raw,
                        interaction_log=hyp_svc.interaction_excerpt(h_proj, h_id),
                        provider=author.provider,
                    )
                    detail = hyp_svc.revise_hypothesis_files(
                        project=h_proj,
                        hypothesis_id=h_id,
                        draft=revised_draft,
                        user_instruction=raw,
                        agent_name=author.name,
                    )

                # Update active state
                active_hypothesis = (h_id, h_proj, revised_draft)

                # Show revision summary
                console.print(
                    Panel(
                        (
                            f"[bold]ID[/bold]: {h_id}\n"
                            f"[bold]Title[/bold]: {revised_draft.title}\n"
                            f"[bold]Claim[/bold]: {revised_draft.claim}\n"
                            f"[bold]Success criteria[/bold]: {revised_draft.success_criteria or '(blank)'}\n"
                            f"[bold]Failure criteria[/bold]: {revised_draft.failure_criteria or '(blank)'}"
                        ),
                        title=f"[bold green]{author.name} · Hypothesis revised[/bold green]",
                        border_style="green",
                    )
                )

                # Step 2: Reviewer refines (round-robin only)
                if (
                    current_session.mode == ChatMode.ROUND_ROBIN
                    and len(current_session.participants) >= 2
                ):
                    reviewer = current_session.participants[1]
                    with console.status(f"[bold cyan]{reviewer.name} reviewing hypothesis...[/bold cyan]"):
                        refined_draft = hyp_drafter.refine_draft(
                            draft=revised_draft,
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            user_intent=raw,
                            provider=reviewer.provider,
                        )
                        hyp_svc.revise_hypothesis_files(
                            project=h_proj,
                            hypothesis_id=h_id,
                            draft=refined_draft,
                            user_instruction=f"Review refinement based on: {raw}",
                            agent_name=reviewer.name,
                        )
                    active_hypothesis = (h_id, h_proj, refined_draft)

                    # Show what changed
                    changes: list[str] = []
                    if refined_draft.claim != revised_draft.claim:
                        changes.append(f"Claim: {refined_draft.claim}")
                    if refined_draft.success_criteria != revised_draft.success_criteria:
                        changes.append(f"Success criteria: {refined_draft.success_criteria}")
                    if refined_draft.failure_criteria != revised_draft.failure_criteria:
                        changes.append(f"Failure criteria: {refined_draft.failure_criteria}")
                    change_text = "\n".join(changes) if changes else "Minor refinements only."
                    console.print(
                        Panel(
                            change_text,
                            title=f"[bold cyan]{reviewer.name} · Review refinement[/bold cyan]",
                            border_style="cyan",
                        )
                    )
            except Exception as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                continue
            try:
                service.record_session_event(
                    session_id=current_session.session_id,
                    kind=SessionEventKind.ARTIFACT_HYPOTHESIS_UPDATED,
                    actor="labit",
                    summary=f"Hypothesis revised: {h_id}",
                    payload={"hypothesis_id": h_id},
                    evidence_refs=_session_evidence_refs(current_session) + [f"hypothesis:{h_id}"],
                )
            except Exception:
                pass
            continue

        if active_doc is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Document mode does not support image attachments yet.")
                continue
            doc_service = _document_service()
            drafter = _doc_drafter()
            author = current_session.participants[0]
            # Round-robin: second participant is reviewer
            reviewer = (
                current_session.participants[1]
                if current_session.mode == ChatMode.ROUND_ROBIN and len(current_session.participants) >= 2
                else None
            )
            try:
                # Capture pre-revision markdown for diff
                old_markdown = doc_service.read_document(active_doc)

                # Step 1: Author revises
                with console.status(f"[bold blue]{author.name} updating document...[/bold blue]"):
                    update = drafter.revise_document(
                        session=current_session,
                        transcript=service.transcript(current_session.session_id),
                        context_snapshot=service.context_snapshot(current_session.session_id),
                        doc_title=active_doc.title,
                        current_markdown=old_markdown,
                        user_instruction=raw,
                        interaction_log=doc_service.interaction_excerpt(active_doc),
                        author_name=author.name,
                        provider=author.provider,
                    )
                    active_doc = doc_service.revise_document(
                        doc_session=active_doc,
                        update=update,
                        user_instruction=raw,
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

                # Step 2: Reviewer adds inline review blocks (round-robin only)
                if reviewer is not None:
                    from labit.documents.drafter import compute_changed_sections

                    new_markdown = doc_service.read_document(active_doc)
                    changed_sections = compute_changed_sections(old_markdown, new_markdown)

                    with console.status(f"[bold cyan]{reviewer.name} reviewing document...[/bold cyan]"):
                        review_update = drafter.review_document(
                            current_markdown=new_markdown,
                            revision_summary=update.summary,
                            user_instruction=raw,
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
