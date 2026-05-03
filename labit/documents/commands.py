from __future__ import annotations

from dataclasses import dataclass

from rich.panel import Panel
from rich.table import Table

from labit.chat.models import ChatMode
from labit.commands.context import ChatContext, session_evidence_refs
from labit.commands.rendering import print_doc_mode_hints, render_doc_status
from labit.context.events import SessionEventKind
from labit.documents.drafter import DocDrafter
from labit.documents.models import DocSession, DocStatus
from labit.documents.service import DocumentService


@dataclass(slots=True)
class DocumentCommandResult:
    active_doc: DocSession | None


def handle_document_command(
    *,
    ctx: ChatContext,
    argument: str,
    active_doc: DocSession | None,
) -> DocumentCommandResult:
    console = ctx.console
    current_session = ctx.session
    doc_parts = argument.split(maxsplit=1)
    doc_action = doc_parts[0].strip().lower() if doc_parts else "status"
    doc_argument = doc_parts[1].strip() if len(doc_parts) > 1 else ""

    if doc_action in {"status", ""}:
        if active_doc is None:
            console.print("[dim]No active document session. Use /doc start <title> or /doc open <id>.[/dim]")
        else:
            render_doc_status(console, active_doc)
        return DocumentCommandResult(active_doc)

    if doc_action == "done":
        if active_doc is None:
            console.print("[dim]No active document session.[/dim]")
            return DocumentCommandResult(active_doc)
        try:
            DocumentService(ctx.paths).end_session(active_doc)
        except Exception:
            pass
        console.print(
            f"[green]Document session closed.[/green] "
            f"[dim]{active_doc.doc_id} · {active_doc.document_path} ({active_doc.status.value})[/dim]"
        )
        return DocumentCommandResult(None)

    if doc_action == "publish":
        return DocumentCommandResult(_publish_document(ctx=ctx, doc_argument=doc_argument, active_doc=active_doc))

    if doc_action == "list":
        _list_documents(ctx)
        return DocumentCommandResult(active_doc)

    if doc_action == "open":
        return DocumentCommandResult(_open_document(ctx=ctx, doc_argument=doc_argument, active_doc=active_doc))

    if doc_action == "auto":
        return DocumentCommandResult(_auto_iterate_document(ctx=ctx, doc_argument=doc_argument, active_doc=active_doc))

    if doc_action != "start":
        console.print("[bold red]Usage:[/bold red] /doc start <title> | /doc open <id> | /doc auto [N] | /doc status | /doc done | /doc publish <id> | /doc list")
        return DocumentCommandResult(active_doc)

    return DocumentCommandResult(_start_document(ctx=ctx, title=doc_argument, active_doc=active_doc))


def _publish_document(
    *,
    ctx: ChatContext,
    doc_argument: str,
    active_doc: DocSession | None,
) -> DocSession | None:
    console = ctx.console
    current_session = ctx.session
    publish_target = doc_argument.strip()
    if not publish_target:
        if active_doc is None:
            console.print("[bold red]Usage:[/bold red] /doc publish <doc_id>")
            return active_doc
        publish_target = active_doc.doc_id
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return active_doc
    try:
        published_doc = DocumentService(ctx.paths).publish_document(
            project=current_session.project,
            doc_id=publish_target,
            source_session=current_session,
        )
        if active_doc is not None and active_doc.doc_id == published_doc.doc_id:
            active_doc = published_doc
        console.print(f"[green]Document published.[/green] [dim]{published_doc.doc_id} → active[/dim]")
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
    return active_doc


def _list_documents(ctx: ChatContext) -> None:
    console = ctx.console
    current_session = ctx.session
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return
    try:
        docs = DocumentService(ctx.paths).list_documents(current_session.project)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return
    if not docs:
        console.print("[dim]No documents found.[/dim]")
        return

    table = Table(title="Documents", border_style="dim")
    table.add_column("ID", style="bold")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Updated")
    for document in docs:
        table.add_row(
            document.get("doc_id", "?"),
            document.get("title", "?"),
            document.get("status", "?"),
            document.get("updated_at", "?"),
        )
    console.print(table)


def _open_document(
    *,
    ctx: ChatContext,
    doc_argument: str,
    active_doc: DocSession | None,
) -> DocSession | None:
    console = ctx.console
    current_session = ctx.session
    doc_id = doc_argument.strip()
    if not doc_id:
        console.print("[bold red]Usage:[/bold red] /doc open <doc_id>")
        return active_doc
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return active_doc
    if active_doc is not None:
        console.print("[bold red]Error:[/bold red] A document session is already active. Use /doc done first.")
        return active_doc
    try:
        active_doc = DocumentService(ctx.paths).open_document(
            project=current_session.project,
            doc_id=doc_id,
            session=current_session,
        )
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return active_doc

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
    print_doc_mode_hints(console, current_session)
    return active_doc


def _auto_iterate_document(
    *,
    ctx: ChatContext,
    doc_argument: str,
    active_doc: DocSession | None,
) -> DocSession | None:
    console = ctx.console
    current_session = ctx.session
    if active_doc is None:
        console.print("[bold red]Error:[/bold red] No active document. Use /doc start or /doc open first.")
        return active_doc

    max_rounds = 5
    if doc_argument.strip():
        try:
            max_rounds = int(doc_argument.strip())
        except ValueError:
            console.print("[bold red]Usage:[/bold red] /doc auto [N]  (N = number of rounds, default 5, max 10)")
            return active_doc
    max_rounds = min(max(max_rounds, 1), 10)

    doc_service = DocumentService(ctx.paths)
    drafter = DocDrafter(ctx.paths)
    author = current_session.participants[0]
    reviewer = (
        current_session.participants[1]
        if current_session.mode == ChatMode.ROUND_ROBIN and len(current_session.participants) >= 2
        else None
    )

    console.print(f"[bold yellow]Auto-iteration starting: up to {max_rounds} rounds. Ctrl+C to stop.[/bold yellow]")
    auto_instruction = "Review the document and improve it. Fix any issues, improve clarity, and strengthen the content."

    interrupted = False
    for round_num in range(1, max_rounds + 1):
        console.print(f"\n[bold]── Round {round_num}/{max_rounds} ──[/bold]")
        try:
            old_markdown = doc_service.read_document(active_doc)

            with console.status(f"[bold blue]{author.name} revising (round {round_num})...[/bold blue]"):
                update = drafter.revise_document(
                    session=current_session,
                    transcript=ctx.service.transcript(current_session.session_id),
                    context_snapshot=ctx.service.context_snapshot(current_session.session_id),
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
                auto_instruction = review_update.summary
            else:
                auto_instruction = f"Continue improving. Previous changes: {update.summary}"

            from labit.documents.drafter import count_open_reviews

            current_md = doc_service.read_document(active_doc)
            open_count = count_open_reviews(current_md)
            if open_count == 0:
                console.print(f"[bold green]Converged at round {round_num} — all review blocks resolved, no open issues remaining.[/bold green]")
                break
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
    print_doc_mode_hints(console, current_session)
    try:
        ctx.service.record_session_event(
            session_id=current_session.session_id,
            kind=SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
            actor="labit",
            summary=f"Document auto-iterated: {active_doc.title}",
            payload={
                "doc_id": active_doc.doc_id,
                "title": active_doc.title,
                "iteration": active_doc.iteration,
            },
            evidence_refs=session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
        )
    except Exception:
        pass
    return active_doc


def _start_document(
    *,
    ctx: ChatContext,
    title: str,
    active_doc: DocSession | None,
) -> DocSession | None:
    console = ctx.console
    current_session = ctx.session
    if not title:
        console.print("[bold red]Usage:[/bold red] /doc start <title>")
        return active_doc
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return active_doc
    if active_doc is not None:
        console.print("[bold red]Error:[/bold red] A document session is already active. Use /doc done first.")
        return active_doc

    doc_service = DocumentService(ctx.paths)
    try:
        with console.status(f"[bold blue]{current_session.participants[0].name} writing document draft...[/bold blue]"):
            update = DocDrafter(ctx.paths).draft_from_session(
                session=current_session,
                transcript=ctx.service.transcript(current_session.session_id),
                context_snapshot=ctx.service.context_snapshot(current_session.session_id),
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
        return active_doc

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
    print_doc_mode_hints(console, current_session)
    try:
        ctx.service.record_session_event(
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
            evidence_refs=session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
        )
    except Exception:
        pass
    return active_doc
