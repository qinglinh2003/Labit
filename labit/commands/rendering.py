from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

if TYPE_CHECKING:
    from labit.documents.models import DocSession

COMMAND_COLOR = "#0080ff"

CHAT_SHELL_COMMANDS = (
    "/help",
    "/list",
    "/show",
    "/mode",
    "/idea",
    "/todo",
    "/doc",
    "/doc auto",
    "/doc start",
    "/doc open",
    "/doc done",
    "/doc status",
    "/doc publish",
    "/doc list",
    "/swap",
    "/mute",
    "/debrief",
    "/review-results",
    "/new",
    "/switch",
    "/exit",
)

LABIT_THEME = Theme(
    {
        "markdown.h1": "bold bright_cyan",
        "markdown.h1.border": "bright_cyan",
        "markdown.h2": "bold bright_white underline",
        "markdown.h3": "bold dodger_blue2",
        "markdown.h4": "bold grey70",
        "markdown.h5": "grey70 underline",
        "markdown.h6": "dim italic",
        "markdown.strong": "bold #0080ff",
        "markdown.em": "italic dim",
        "markdown.emph": "italic dim",
        "markdown.s": "dim strike",
        "markdown.code": "bold cyan",
        "markdown.code_block": "",
        "markdown.block_quote": "dim",
        "markdown.hr": "dim cyan",
        "markdown.item.bullet": "bright_cyan",
        "markdown.item.number": "bright_cyan",
        "markdown.link": "bright_blue underline",
        "markdown.link_url": "dim blue",
    }
)

CODE_THEME = "default"

PROVIDER_STYLES = {
    "claude": ("blue", "CLAUDE"),
    "codex": ("green", "CODEX"),
}

_FENCE_INLINE_RE = re.compile(r"(`{3,})(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def sanitize_markdown(text: str) -> str:
    """Fix common AI markdown issues that break the Rich parser."""
    in_fence = False
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```"):
                in_fence = True
                result.append(line)
                if stripped.count("```") >= 2 and len(stripped) > 3:
                    in_fence = False
            else:
                result.append(line)
        else:
            if stripped == "```":
                in_fence = False
                result.append(line)
            elif stripped.endswith("```") and not stripped.startswith("```"):
                result.append(line[: line.rfind("```")])
                result.append("```")
                in_fence = False
            else:
                result.append(line)
    if in_fence:
        result.append("```")
    return "\n".join(result)


def md(content: str, *, sanitize: bool = True):
    """Create a themed Markdown renderable."""
    from labit.rendering import LaTeXMarkdown as Markdown

    text = sanitize_markdown(content) if sanitize else content
    return Markdown(text, code_theme=CODE_THEME)


class ThinkingIndicator:
    """Animated spinner with elapsed time for the generating placeholder."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._spinner = Spinner("dots", style="dim")

    def __rich_console__(self, console: Console, options: object):  # noqa: ANN001
        elapsed = time.monotonic() - self._start
        text = self._spinner.render(time.monotonic())
        text.append(f" Thinking… {elapsed:.1f}s", style="dim")
        yield text


# ---------------------------------------------------------------------------
# Agent panel
# ---------------------------------------------------------------------------


def agent_panel(
    speaker: str,
    provider_name: str,
    content: str,
    *,
    turn_index: int | None = None,
    thinking: ThinkingIndicator | None = None,
    status_text: str | None = None,
) -> Panel:
    color, label = PROVIDER_STYLES.get(provider_name, ("cyan", provider_name.upper()))
    title = f"{label} · {speaker}"
    if turn_index is not None:
        title = f"{title} · turn {turn_index}"
    if status_text:
        title = f"{title} · {status_text}"
    body: RenderableType = md(content) if content.strip() else (thinking or ThinkingIndicator())
    return Panel(body, title=title, border_style=color)


# ---------------------------------------------------------------------------
# Console header / help
# ---------------------------------------------------------------------------


def command_chip(label: str) -> str:
    return f"[bold {COMMAND_COLOR}]{label}[/bold {COMMAND_COLOR}]"


def render_console_header(
    console: Console,
    *,
    project: str,
    mode: str,
    participants: str,
) -> None:
    console.print(f"[dim]{project} · {mode} · {participants}[/dim]")
    console.print(
        "Shortcuts: "
        + " · ".join(
            [
                command_chip("/help"),
                command_chip("/exit"),
            ]
        )
    )
    console.print(
        "Research: "
        + " · ".join(
            [
                command_chip("/idea"),
                command_chip("/todo"),
                command_chip("/doc"),
            ]
        )
    )
    console.print(
        "Multi-agent: "
        + " · ".join(
            [
                command_chip("/mode"),
                command_chip("/swap"),
                command_chip("/mute"),
            ]
        )
    )


def render_shell_help(console: Console) -> None:
    table = Table(show_header=True, header_style=f"bold {COMMAND_COLOR}")
    table.add_column("Command", style=f"bold {COMMAND_COLOR}")
    table.add_column("What It Does")
    table.add_row("/help", "Show shell commands.")
    table.add_row("/list", "List existing chat sessions.")
    table.add_row("/new", "Create a new session and switch into it.")
    table.add_row("/switch <session_id>", "Switch to another session.")
    table.add_row("/show", "Show the full transcript for the current session.")
    table.add_row("/mode [mode]", "Show or switch mode (single, round_robin, parallel).")
    table.add_row("/swap", "Swap the response order of participants (e.g. claude,codex → codex,claude).")
    table.add_row("/mute <name>", "Mute an agent for the next turn only. Toggle: run again to unmute.")
    table.add_row("/idea [text]", "Save a lightweight project idea. With no text, show saved ideas.")
    table.add_row("/todo [text]", "Save an actionable project todo. With no text, show saved todos.")
    table.add_row("/doc start <title>", "Enter document mode and write a design doc to docs/designs/.")
    table.add_row("/doc open <doc_id>", "Re-open an existing document for editing.")
    table.add_row("/doc status|done", "Show or leave the active document editing session.")
    table.add_row("/doc publish <doc_id>", "Promote a document from draft to active.")
    table.add_row("/doc list", "List all documents in the current project.")
    table.add_row("/debrief", "Inspect active experiment launches and show their latest runtime state.")
    table.add_row("/review-results <hypothesis_id>", "Summarize experiments linked to a hypothesis, suggest a resolution, and optionally write the decision back.")
    table.add_row("/exit", "Leave the chat shell.")
    console.print(Panel(table, title="LABIT Chat Commands", border_style=COMMAND_COLOR))


# ---------------------------------------------------------------------------
# Message / transcript rendering
# ---------------------------------------------------------------------------


def message_body(message) -> str:
    body = message.content
    attachments = getattr(message, "attachments", None) or []
    if not attachments:
        return body
    lines = [body, "", "Attachments:"]
    for attachment in attachments:
        label = attachment.label or attachment.path.rsplit("/", 1)[-1]
        lines.append(f"- {attachment.kind.value}: {label}")
    return "\n".join(lines).strip()


def transcript_preview_text(messages) -> Text:
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


def render_session_summary(console: Console, session) -> None:
    participants = "\n".join(f"- {item.name} ({item.provider.value})" for item in session.participants)
    body = (
        f"[bold]Session ID[/bold]: {session.session_id}\n"
        f"[bold]Mode[/bold]: {session.mode.value}\n"
        f"[bold]Project[/bold]: {session.project or '(none)'}\n"
        f"[bold]Status[/bold]: {session.status.value}\n"
        f"[bold]Participants[/bold]:\n{participants}"
    )
    console.print(Panel(body, title=f"[bold green]{session.title}[/bold green]", border_style="green"))


def render_transcript(console: Console, messages) -> None:
    if not messages:
        console.print("[dim]No messages yet.[/dim]")
        return
    for message in messages:
        render_message_block(console, message)


def render_compact_transcript(console: Console, messages) -> None:
    if not messages:
        console.print("[dim]No messages yet.[/dim]")
        return
    for message in messages:
        if message.message_type.value == "user":
            console.print(Panel.fit(message_body(message), title=f"user · turn {message.turn_index}", border_style="white"))
            continue
        provider_name = message.provider.value if message.provider else "agent"
        color, label = PROVIDER_STYLES.get(provider_name, ("cyan", provider_name.upper()))
        title = f"{label} · {message.speaker} · turn {message.turn_index}"
        console.print(Panel.fit(message.content, title=title, border_style=color))


def render_message_block(console: Console, message) -> None:
    if message.message_type.value == "user":
        console.print(Panel(message_body(message), title=f"user · turn {message.turn_index}", border_style="white"))
        console.print("")
        return
    console.print(
        agent_panel(
            message.speaker,
            message.provider.value if message.provider else "agent",
            message.content,
            turn_index=message.turn_index,
        )
    )
    console.print("")


def render_user_shell_message(console: Console, content: str, *, attachments: list | None = None) -> None:
    if attachments:
        body = message_body(type("ShellMessage", (), {"content": content, "attachments": attachments})())
    else:
        body = content
    console.print(Panel(body, title="user", border_style="white"))
    console.print("")


def render_recent_messages(console: Console, messages, *, count: int = 8) -> None:
    console.print(Panel.fit(transcript_preview_text(messages[-count:]), title="Recent Messages", border_style="blue"))


def render_shell_header(console: Console, session) -> None:
    from labit.chat.models import ChatMode

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


# ---------------------------------------------------------------------------
# Box drawing helpers (for prompt input)
# ---------------------------------------------------------------------------


def box_width(console: Console) -> int:
    width = console.size.width if console.size.width else 80
    return max(60, width - 4)


def clip_box_text(text: str, width: int) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return f"{text[: width - 1]}…"


def box_top(title: str, width: int) -> str:
    inner_width = width - 2
    title_text = f" {title} "
    if len(title_text) >= inner_width:
        return f"╭{title_text[:inner_width]}╮"
    filler = "─" * (inner_width - len(title_text))
    return f"╭{title_text}{filler}╮"


def box_line(text: str, width: int) -> str:
    inner_width = width - 2
    content = clip_box_text(text, inner_width)
    return f"│{content.ljust(inner_width)}│"


def box_bottom(width: int) -> str:
    return f"╰{'─' * (width - 2)}╯"


# ---------------------------------------------------------------------------
# Domain previews
# ---------------------------------------------------------------------------


def render_idea_preview(console: Console, draft) -> None:
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


def render_capture_records(console: Console, kind: str, records) -> None:
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


def render_review_suggestion(console: Console, suggestion) -> None:
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


# ---------------------------------------------------------------------------
# Mode hints
# ---------------------------------------------------------------------------


def print_doc_mode_hints(console: Console, session) -> None:
    from labit.chat.models import ChatMode

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

def render_doc_status(console: Console, doc_session: DocSession) -> None:
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


# ---------------------------------------------------------------------------
# Markdown generators (pure string → string, no console)
# ---------------------------------------------------------------------------


def debrief_markdown(*, experiment_id: str, rows: list[str]) -> str:
    lines = [f"# Debrief {experiment_id}", ""]
    if not rows:
        lines.append("No active launches found.")
        return "\n".join(lines)
    lines.extend(rows)
    return "\n".join(lines)


def review_markdown(*, hypothesis_id: str, suggestion, saved) -> str:
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
