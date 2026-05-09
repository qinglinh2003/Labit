from __future__ import annotations

from rich.panel import Panel
from rich.table import Table

from labit.commands.context import ChatContext
from labit.papers.models import PaperRecord
from labit.papers.service import PaperService


def handle_paper_command(*, ctx: ChatContext, argument: str) -> None:
    console = ctx.console
    current_session = ctx.session
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return

    parts = argument.split(maxsplit=1)
    action = parts[0].strip().lower() if parts else "list"
    value = parts[1].strip() if len(parts) > 1 else ""
    service = PaperService(ctx.paths)

    if action in {"", "list"}:
        try:
            records = service.list_papers(current_session.project)
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
        _render_paper_list(console, records)
        return

    if action == "add":
        if not value:
            console.print("[bold red]Usage:[/bold red] /paper add <arxiv-id-or-url>")
            return
        try:
            with console.status("[bold blue]Fetching arXiv metadata and HTML...[/bold blue]"):
                record = service.add_paper(project=current_session.project, reference=value)
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
        _render_paper_added(console, record)
        return

    if action in {"remove", "delete", "rm"}:
        if not value:
            console.print("[bold red]Usage:[/bold red] /paper remove <arxiv-id-or-url>")
            return
        try:
            record = service.remove_paper(project=current_session.project, arxiv_id_or_url=value)
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
        console.print(f"[green]Removed paper.[/green] [dim]{record.arxiv_id} · {record.title}[/dim]")
        return

    console.print("[bold red]Usage:[/bold red] /paper add <arxiv-id-or-url> | /paper list | /paper remove <arxiv-id>")


def _render_paper_added(console, record: PaperRecord) -> None:
    authors = ", ".join(record.authors[:6])
    if len(record.authors) > 6:
        authors += ", ..."
    body = "\n".join(
        [
            f"[bold]ID[/bold]: {record.arxiv_id}",
            f"[bold]Title[/bold]: {record.title}",
            f"[bold]Authors[/bold]: {authors or '(unknown)'}",
            f"[bold]Metadata[/bold]: {record.local_metadata_path}",
            f"[bold]HTML[/bold]: {record.local_html_path}",
        ]
    )
    console.print(Panel(body, title="[bold green]Paper saved[/bold green]", border_style="green"))


def _render_paper_list(console, records: list[PaperRecord]) -> None:
    if not records:
        console.print("[dim]No papers saved for this project. Use /paper add <arxiv-id-or-url>.[/dim]")
        return
    table = Table(show_header=True, header_style="bold #0080ff")
    table.add_column("ID", style="bold #0080ff", no_wrap=True)
    table.add_column("Title")
    table.add_column("Abstract")
    table.add_column("HTML")
    for record in records:
        table.add_row(
            record.arxiv_id,
            record.title,
            _clip(record.abstract, 220),
            record.local_html_path,
        )
    console.print(Panel(table, title="Project Papers", border_style="#0080ff"))


def _clip(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."
