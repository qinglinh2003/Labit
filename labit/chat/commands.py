from __future__ import annotations

from labit.chat.synthesizer import DiscussionSynthesizer
from labit.commands.context import ChatContext, session_evidence_refs
from labit.commands.rendering import render_synthesis_preview


def handle_synthesize_command(*, ctx: ChatContext, argument: str) -> None:
    console = ctx.console
    current_session = ctx.session
    try:
        with console.status("[bold blue]Synthesizing current discussion...[/bold blue]"):
            draft = DiscussionSynthesizer(ctx.paths).synthesize_from_session(
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
    render_synthesis_preview(console, draft)
    if not _confirm(console, "Save this synthesis to working memory?", default=True):
        console.print("[dim]Cancelled synthesis.[/dim]")
        return

    try:
        ctx.service.record_discussion_synthesis(
            session_id=current_session.session_id,
            summary=draft.summary,
            consensus=draft.consensus,
            disagreements=draft.disagreements,
            followups=draft.followups,
            evidence_refs=session_evidence_refs(current_session),
        )
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return

    console.print("[green]Discussion synthesis saved.[/green]")


def _confirm(console, prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}
