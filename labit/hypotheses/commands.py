from __future__ import annotations

from dataclasses import dataclass

from rich.panel import Panel

from labit.chat.models import ChatMode
from labit.commands.context import ChatContext, session_evidence_refs
from labit.commands.rendering import print_hypothesis_mode_hints, render_hypothesis_preview
from labit.context.events import SessionEventKind
from labit.hypotheses.drafter import HypothesisDrafter
from labit.hypotheses.models import HypothesisDraft
from labit.hypotheses.service import HypothesisService

ActiveHypothesis = tuple[str, str, HypothesisDraft]


@dataclass(slots=True)
class HypothesisCommandResult:
    active_hypothesis: ActiveHypothesis | None


def handle_hypothesis_command(
    *,
    ctx: ChatContext,
    argument: str,
    active_hypothesis: ActiveHypothesis | None,
) -> HypothesisCommandResult:
    console = ctx.console
    current_session = ctx.session
    sub = argument.strip()
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return HypothesisCommandResult(active_hypothesis)

    if sub == "status":
        if active_hypothesis is None:
            console.print("[dim]Not in hypothesis editing mode.[/dim]")
        else:
            h_id, _, h_draft = active_hypothesis
            console.print(
                Panel(
                    f"[bold]ID[/bold]: {h_id}\n"
                    f"[bold]Title[/bold]: {h_draft.title}\n"
                    f"[bold]Claim[/bold]: {h_draft.claim}",
                    title=f"[bold green]Editing hypothesis · {h_id}[/bold green]",
                    border_style="green",
                )
            )
        return HypothesisCommandResult(active_hypothesis)

    if sub == "done":
        if active_hypothesis is None:
            console.print("[dim]Not in hypothesis editing mode.[/dim]")
            return HypothesisCommandResult(active_hypothesis)
        h_id, h_proj, _ = active_hypothesis
        HypothesisService(ctx.paths).log_event(h_proj, h_id, "session_ended")
        console.print(f"[dim]Left hypothesis editing mode. {h_id} saved.[/dim]")
        return HypothesisCommandResult(None)

    if sub.startswith("open "):
        h_id = sub[5:].strip()
        if not h_id:
            console.print("[bold red]Usage:[/bold red] /hypothesis open <hypothesis_id>")
            return HypothesisCommandResult(active_hypothesis)
        if active_hypothesis is not None:
            console.print("[bold red]Error:[/bold red] Already editing a hypothesis. Use /hypothesis done first.")
            return HypothesisCommandResult(active_hypothesis)
        try:
            hyp_svc = HypothesisService(ctx.paths)
            detail = hyp_svc.load_hypothesis(current_session.project, h_id)
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
            return HypothesisCommandResult(active_hypothesis)
        console.print("")
        render_hypothesis_preview(console, h_draft, project=current_session.project)
        print_hypothesis_mode_hints(console, current_session, h_id)
        return HypothesisCommandResult(active_hypothesis)

    if active_hypothesis is not None:
        console.print("[bold red]Error:[/bold red] Already editing a hypothesis. Use /hypothesis done first.")
        return HypothesisCommandResult(active_hypothesis)

    user_intent = sub
    if user_intent == "new":
        user_intent = ""
    elif user_intent.startswith("new "):
        user_intent = user_intent[4:].strip()
    try:
        drafter = HypothesisDrafter(ctx.paths)
        transcript_msgs = ctx.service.transcript(current_session.session_id)
        ctx_snap = ctx.service.context_snapshot(current_session.session_id)
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
        return HypothesisCommandResult(active_hypothesis)

    try:
        hyp_svc = HypothesisService(ctx.paths)
        detail = hyp_svc.create_hypothesis(
            project=current_session.project,
            draft=draft,
            source_session_id=current_session.session_id,
        )
        hyp_svc.log_event(current_session.project, detail.record.hypothesis_id, "session_started")
        active_hypothesis = (detail.record.hypothesis_id, current_session.project, draft)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return HypothesisCommandResult(active_hypothesis)

    console.print("")
    render_hypothesis_preview(console, draft, project=current_session.project)
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
    print_hypothesis_mode_hints(console, current_session, detail.record.hypothesis_id)
    try:
        ctx.service.record_session_event(
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
            evidence_refs=session_evidence_refs(current_session)
            + [f"hypothesis:{detail.record.hypothesis_id}"]
            + [f"paper:{paper_id}" for paper_id in detail.record.source_paper_ids],
        )
    except Exception:
        pass
    try:
        ctx.service.record_discussion_synthesis(
            session_id=current_session.session_id,
            summary=f"Hypothesis discussion crystallized into {detail.record.hypothesis_id}: {detail.record.title}",
            consensus=[detail.record.claim],
            disagreements=[],
            followups=[f"Design or launch an experiment for {detail.record.hypothesis_id}."],
            evidence_refs=session_evidence_refs(current_session)
            + [f"hypothesis:{detail.record.hypothesis_id}"]
            + [f"paper:{paper_id}" for paper_id in detail.record.source_paper_ids],
        )
    except Exception:
        pass
    return HypothesisCommandResult(active_hypothesis)
