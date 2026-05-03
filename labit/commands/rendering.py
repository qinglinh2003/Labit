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
    from labit.experiments.models import ExperimentTaskPlan, LaunchExpPhase, LaunchExpSession

COMMAND_COLOR = "#0080ff"

CHAT_SHELL_COMMANDS = (
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
                command_chip("/think"),
                command_chip("/think-ltm"),
                command_chip("/ltm"),
                command_chip("/image"),
                command_chip("/exit"),
            ]
        )
    )
    console.print(
        "Research: "
        + " · ".join(
            [
                command_chip("/memory"),
                command_chip("/idea"),
                command_chip("/todo"),
                command_chip("/doc"),
                command_chip("/investigate"),
                command_chip("/hypothesis"),
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
                command_chip("/dev"),
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


def render_hypothesis_preview(console: Console, draft, *, project: str) -> None:
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
        console.print(Panel(md(draft.rationale_markdown, sanitize=False), title="Rationale", border_style="blue"))
    if draft.experiment_plan_markdown:
        console.print(Panel(md(draft.experiment_plan_markdown, sanitize=False), title="Experiment Plan", border_style="magenta"))


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


def render_synthesis_preview(console: Console, draft) -> None:
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


def render_related_reports(console: Console, reports) -> None:
    console.print("[bold]Related reports[/bold]")
    for item in reports:
        summary = item.summary or "(no summary)"
        console.print(f"- [bold]{item.title}[/bold] [dim]({item.path})[/dim]")
        console.print(f"  {summary}")


def render_memory_records(console: Console, records) -> None:
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


def render_memory_detail(console: Console, record) -> None:
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


def render_investigation_result(console: Console, result) -> None:
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


def render_experiment_launch_preview(
    console: Console,
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


def print_hypothesis_mode_hints(console: Console, session, hypothesis_id: str) -> None:
    from labit.chat.models import ChatMode

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


def print_launch_exp_hints(console: Console, session: LaunchExpSession) -> None:
    from labit.experiments.models import LaunchExpPhase

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


# ---------------------------------------------------------------------------
# Experiment planning rendering
# ---------------------------------------------------------------------------


def render_task_breakdown(console: Console, session: LaunchExpSession) -> None:
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


def render_task_detail(console: Console, task: ExperimentTaskPlan) -> None:
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


def render_launch_exp_status(console: Console, session: LaunchExpSession) -> None:
    from labit.experiments.models import LaunchExpPhase

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


def launch_markdown(
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


# ---------------------------------------------------------------------------
# Dev loop rendering
# ---------------------------------------------------------------------------


def render_dev_decision(console: Console, dev_session) -> None:
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


def render_dev_status(console: Console, dev_session) -> None:
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
        border_style=COMMAND_COLOR,
    ))
