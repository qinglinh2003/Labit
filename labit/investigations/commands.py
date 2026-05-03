from __future__ import annotations

from labit.chat.models import ChatMode
from labit.chat.models import ChatMessage, ContextSnapshot
from labit.commands.context import ChatContext, session_evidence_refs
from labit.commands.rendering import render_investigation_result, render_related_reports
from labit.context.events import SessionEventKind
from labit.investigations.service import InvestigationService


def handle_investigate_command(*, ctx: ChatContext, argument: str) -> None:
    console = ctx.console
    current_session = ctx.session
    topic = argument.strip()
    if not topic:
        console.print("[bold red]Usage:[/bold red] /investigate <topic>")
        return
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return

    transcript = ctx.service.transcript(current_session.session_id)
    snapshot = ctx.service.context_snapshot(current_session.session_id)
    investigation_service = InvestigationService(ctx.paths)
    try:
        related = investigation_service.find_related_reports(current_session.project, topic)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return

    if related:
        console.print("")
        render_related_reports(console, related)
        if not _confirm(console, "Investigate further?", default=True):
            console.print("[dim]Cancelled investigation.[/dim]")
            return

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
        return

    console.print("")
    render_investigation_result(console, result)
    try:
        ctx.service.record_session_event(
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
            evidence_refs=session_evidence_refs(current_session) + [f"report:{result.report_path}"],
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
        ctx.service.record_discussion_synthesis(
            session_id=current_session.session_id,
            summary=f"Investigation discussion synthesized around topic: {result.topic}",
            consensus=consensus,
            disagreements=disagreements,
            followups=followups,
            evidence_refs=session_evidence_refs(current_session) + [f"report:{result.report_path}"],
        )
    except Exception:
        pass


def _transcript_excerpt(messages: list[ChatMessage], *, limit: int = 16, max_chars: int = 6000) -> str:
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


def _context_snapshot_excerpt(snapshot: ContextSnapshot, *, max_blocks: int = 6, max_chars: int = 5000) -> str:
    pieces: list[str] = []
    for block in snapshot.blocks[:max_blocks]:
        pieces.append(f"[{block.title}]\n{block.content.strip()}")
    for memory in snapshot.memory[:max_blocks]:
        pieces.append(f"[{memory.title}]\n{memory.content.strip()}")
    text = "\n\n".join(piece for piece in pieces if piece).strip()
    return text[:max_chars].strip()


def _confirm(console, prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}
