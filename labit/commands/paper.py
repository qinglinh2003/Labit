from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from labit.agents.providers import resolve_provider_kind
from labit.commands.focus import focus_app
from labit.paths import RepoPaths
from labit.papers.models import PaperSearchIntent, SearchMode, SearchScope
from labit.papers.search import PaperSearchService
from labit.papers.service import PaperService
from labit.papers.workflows import PaperWorkflowService
from labit.services.project_service import ProjectService

paper_app = typer.Typer(help="Inspect the canonical paper library and project key papers.")
paper_app.add_typer(focus_app, name="focus")
console = Console()


def _paper_service() -> PaperService:
    return PaperService(RepoPaths.discover())


def _project_service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _workflow_service() -> PaperWorkflowService:
    return PaperWorkflowService(RepoPaths.discover())


def _search_service() -> PaperSearchService:
    return PaperSearchService(RepoPaths.discover())


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


def _require_active_project(*, as_json: bool) -> str:
    project_service = _project_service()
    active_project = project_service.active_project_name()
    if active_project is None:
        raise typer.Exit(
            code=_fail(
                "No active project. Switch to a project before using the paper module.",
                as_json=as_json,
            )
        )
    return active_project


def _prompt_reference(label: str) -> str:
    while True:
        value = typer.prompt(label).strip()
        if value:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_optional_text(label: str) -> str:
    return typer.prompt(label, default="", show_default=False).strip()


def _prompt_search_priority() -> str:
    console.print(
        "[dim]Optional: tell the search what to prioritize, for example "
        "`probing methods`, `recent papers`, `strong experiments`, or "
        "`implementation details`. Leave blank to keep it broad.[/dim]"
    )
    return _prompt_optional_text("What should we prioritize?")


def _prompt_choice(label: str, choices: list[str], *, default: str) -> str:
    rendered = "/".join(choices)
    normalized = {choice.lower(): choice for choice in choices}
    while True:
        value = typer.prompt(f"{label} [{rendered}]", default=default, show_default=True).strip().lower()
        if value in normalized:
            return normalized[value]
        console.print(f"[bold red]Choose one of:[/bold red] {', '.join(choices)}")


def _prompt_int(label: str, *, default: int, minimum: int = 1, maximum: int = 20) -> int:
    while True:
        value = typer.prompt(label, default=str(default), show_default=True).strip()
        try:
            parsed = int(value)
        except ValueError:
            console.print("[bold red]Enter a number.[/bold red]")
            continue
        if minimum <= parsed <= maximum:
            return parsed
        console.print(f"[bold red]Enter a number between {minimum} and {maximum}.[/bold red]")


def _prompt_indices(max_index: int) -> list[int]:
    raw = typer.prompt(
        "Select result numbers to act on (comma-separated, blank to skip)",
        default="",
        show_default=False,
    ).strip()
    if not raw:
        return []

    values: list[int] = []
    seen: set[int] = set()
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        try:
            parsed = int(token)
        except ValueError:
            console.print(f"[bold red]Ignoring invalid selection:[/bold red] {token}")
            continue
        if parsed < 1 or parsed > max_index or parsed in seen:
            continue
        values.append(parsed)
        seen.add(parsed)
    return values


def _run_step(label: str, *, step: int, total: int, as_json: bool, fn):
    if as_json:
        return fn()
    console.print(f"[bold cyan][{step}/{total}][/bold cyan] {label}")
    with console.status(f"{label}...", spinner="dots", spinner_style="cyan"):
        result = fn()
    console.print(f"[green]done[/green] {label}")
    return result


def _run_search_step(label: str, *, as_json: bool, fn):
    if as_json:
        return fn(None)

    console.print(f"[bold cyan][1/1][/bold cyan] {label}")
    stage = "Building project-aware search context"

    def on_progress(message: str) -> None:
        nonlocal stage
        stage = message

    with console.status(f"{stage}...", spinner="dots", spinner_style="cyan") as status:
        def wrapped_progress(message: str) -> None:
            on_progress(message)
            status.update(f"{message}...")

        result = fn(wrapped_progress)
    console.print(f"[green]done[/green] {label}")
    return result


def _print_result(title: str, rows: list[tuple[str, str]]) -> None:
    console.print(f"[bold green]{title}[/bold green]")
    for label, value in rows:
        if len(value) > 70 or value.startswith(("/", "http://", "https://")):
            console.print(f"- {label}:")
            console.print(f"  {value}", soft_wrap=True)
        else:
            console.print(f"- {label}: {value}")


def _render_search_results(project: str, results: list[dict]) -> None:
    console.print(f"[bold]Search Results[/bold] ({project})")
    for idx, item in enumerate(results, start=1):
        title_line = f"[bold]{idx}. {item['title']}[/bold]"
        year = item.get("year")
        status = item.get("duplicate_status", "new")
        suffix_parts = []
        if year:
            suffix_parts.append(str(year))
        suffix_parts.append(status)
        console.print(f"{title_line} [dim]({', '.join(suffix_parts)})[/dim]")

        description = (item.get("one_line_description") or "").strip()
        if description:
            console.print(f"   Summary: {description}")

        reason = (item.get("why_relevant") or "").strip()
        if reason:
            console.print(f"   Why: {reason}")

        sources = item.get("retrieval_sources") or []
        if sources:
            console.print(f"   Found via: {', '.join(sources)}")

        duplicate_reason = (item.get("duplicate_reason") or "").strip()
        if duplicate_reason:
            console.print(f"   Existing: {duplicate_reason}")

        console.print("")


@paper_app.command("search", help="Search for project-relevant papers, review results, and optionally pull or ingest them.")
def search_papers(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    active_project = _require_active_project(as_json=json_output)
    console.print(f"[bold]Search Papers[/bold] ({active_project})")

    intent = PaperSearchIntent.model_validate(
        {
            "query": _prompt_reference("What are you trying to find?"),
            "focus": _prompt_search_priority(),
            "scope": _prompt_choice(
                "Search scope",
                [scope.value for scope in SearchScope],
                default=SearchScope.NARROW.value,
            ),
            "mode": _prompt_choice(
                "Search mode",
                [mode.value for mode in SearchMode],
                default=SearchMode.SINGLE.value,
            ),
            "limit": _prompt_int("How many candidates do you want to review?", default=6),
        }
    )

    search_service = _search_service()
    try:
        payload = _run_search_step(
            "Searching and ranking papers",
            as_json=json_output,
            fn=lambda progress: search_service.search(project=active_project, intent=intent, progress=progress),
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(payload, as_json=True)
        return

    results = payload["results"]
    if not results:
        console.print("[dim]No candidates found.[/dim]")
        return

    _render_search_results(active_project, results)

    selected = _prompt_indices(len(results))
    if not selected:
        console.print("[dim]No papers selected.[/dim]")
        return

    action = _prompt_choice("Action", ["pull", "ingest", "skip"], default="pull")
    if action == "skip":
        console.print("[dim]Selection kept without mutation.[/dim]")
        return

    provider = "auto"
    if action == "ingest":
        provider = _prompt_choice("Summary provider", ["auto", "claude", "codex"], default="auto")

    workflow = _workflow_service()
    mutations: list[dict] = []
    total = len(selected)
    for offset, index in enumerate(selected, start=1):
        candidate = results[index - 1]
        label = f"{action.capitalize()} {candidate['paper_id']}"
        try:
            result = _run_step(
                label,
                step=offset,
                total=total,
                as_json=False,
                fn=(
                    lambda c=candidate: workflow.pull(project=active_project, reference=c["arxiv_id"])
                    if action == "pull"
                    else workflow.ingest(project=active_project, reference=c["arxiv_id"], provider=provider)
                ),
            )
        except Exception as exc:
            console.print(f"[bold red]Failed:[/bold red] {candidate['paper_id']} ({exc})")
            continue
        mutations.append(result)

    if not mutations:
        console.print("[dim]No papers were updated.[/dim]")
        return

    rows = [(item["paper_id"], item["status"]) for item in mutations]
    _print_result("Search action complete", rows)


@paper_app.command("pull", help="Resolve a paper by arXiv id or URL, download canonical assets, and link it into the active project.")
def pull_paper(
    reference: str | None = typer.Argument(None, help="Optional arXiv id or arXiv URL."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    active_project = _require_active_project(as_json=json_output)
    if reference is None:
        console.print(f"[bold]Pull Paper[/bold] ({active_project})")
        reference = _prompt_reference("arXiv id or URL")

    workflow = _workflow_service()
    try:
        result = _run_step(
            "Resolving paper and downloading canonical assets",
            step=1,
            total=2,
            as_json=json_output,
            fn=lambda: workflow.pull(project=active_project, reference=reference),
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    service = _paper_service()
    record = _run_step(
        "Refreshing canonical indexes",
        step=2,
        total=2,
        as_json=json_output,
        fn=lambda: service.load_global_record(result["paper_id"]),
    )
    project_record = service.load_project_record(active_project, result["paper_id"])

    payload = {
        "project": active_project,
        "paper": record.model_dump(mode="json"),
        "project_paper": project_record.model_dump(mode="json"),
        "result": result,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    _print_result(
        "Paper pulled",
        [
            ("Project", active_project),
            ("Paper ID", result["paper_id"]),
            ("Title", result["title"]),
            ("HTML saved", "yes" if result["downloaded_html"] else "no"),
            ("PDF saved", "yes" if result["downloaded_pdf"] else "no"),
            ("Global dir", result["global_dir"]),
            ("Project paper", result["project_dir"]),
            ("Next", f"labit paper show {result['paper_id']}"),
        ],
    )


@paper_app.command("ingest", help="Pull a paper into the active project and generate a project-specific summary.")
def ingest_paper(
    reference: str | None = typer.Argument(None, help="Optional arXiv id or arXiv URL."),
    provider: str = typer.Option("auto", "--provider", help="Summary provider: auto, claude, or codex."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    active_project = _require_active_project(as_json=json_output)
    if reference is None:
        console.print(f"[bold]Ingest Paper[/bold] ({active_project})")
        reference = _prompt_reference("arXiv id or URL")

    try:
        resolved_provider = resolve_provider_kind(provider)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    workflow = _workflow_service()
    try:
        result = _run_step(
            "Resolving paper, downloading assets, and generating summary",
            step=1,
            total=2,
            as_json=json_output,
            fn=lambda: workflow.ingest(
                project=active_project,
                reference=reference,
                provider=resolved_provider.value,
            ),
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    service = _paper_service()
    record = _run_step(
        "Refreshing canonical indexes",
        step=2,
        total=2,
        as_json=json_output,
        fn=lambda: service.load_global_record(result["paper_id"]),
    )
    project_record = service.load_project_record(active_project, result["paper_id"])

    payload = {
        "project": active_project,
        "provider": resolved_provider.value,
        "paper": record.model_dump(mode="json"),
        "project_paper": project_record.model_dump(mode="json"),
        "result": result,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    _print_result(
        "Paper ingested",
        [
            ("Project", active_project),
            ("Provider", resolved_provider.value),
            ("Paper ID", result["paper_id"]),
            ("Title", result["title"]),
            ("HTML saved", "yes" if result["downloaded_html"] else "no"),
            ("PDF saved", "yes" if result["downloaded_pdf"] else "no"),
            ("Summary", result["summary_path"] or "(none)"),
            ("Global dir", result["global_dir"]),
            ("Project paper", result["project_dir"]),
            ("Run", result["summary_run_id"]),
            ("Next", f"labit paper show {result['paper_id']}"),
        ],
    )


@paper_app.command("show", help="Show the global paper library overview or inspect one canonical paper in the active project.")
def show_paper(
    paper_id: str | None = typer.Argument(None, help="Optional canonical paper id."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    active_project = _require_active_project(as_json=json_output)

    service = _paper_service()

    if paper_id:
        try:
            record = service.load_global_record(paper_id)
        except FileNotFoundError as exc:
            raise typer.Exit(code=_fail(str(exc), as_json=json_output))

        try:
            project_record = service.load_project_record(active_project, paper_id)
        except FileNotFoundError:
            project_record = None
        in_active_project = project_record is not None
        payload = {
            "paper": record.model_dump(mode="json"),
            "active_project": active_project,
            "in_active_project": in_active_project,
            "project_paper": project_record.model_dump(mode="json") if project_record else None,
        }
        if json_output:
            _emit(payload, as_json=True)
            return

        console.print(f"[bold]Paper[/bold] {record.meta.paper_id}")
        table = Table(show_header=False, box=None)
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("Title", record.meta.title)
        table.add_row("Authors", ", ".join(record.meta.authors) or "(unknown)")
        table.add_row("Year", str(record.meta.year or "(unknown)"))
        table.add_row("Venue", record.meta.venue or "(blank)")
        table.add_row("Source", record.meta.source or "(blank)")
        table.add_row("Global dir", record.global_dir)
        table.add_row("HTML", record.html_path or "(none)")
        table.add_row("PDF", record.pdf_path or "(none)")
        table.add_row("Linked projects", ", ".join(record.linked_projects) or "(none)")
        table.add_row("Active project linked", "yes" if in_active_project else "no")
        if project_record is not None:
            table.add_row("Project status", project_record.status.value)
            table.add_row("Project summary", project_record.summary_path or "(none)")
            table.add_row("Project paper", service.relative_path(service.project_key_paper_dir(active_project, paper_id)))
        console.print(table)
        return

    overview = service.build_overview(active_project)
    payload = overview.model_dump(mode="json")
    if json_output:
        _emit(payload, as_json=True)
        return

    console.print(f"[bold]Paper Library[/bold] ({active_project})")
    summary = Table(show_header=False, box=None)
    summary.add_column("Field")
    summary.add_column("Value")
    summary.add_row("Active project", active_project)
    summary.add_row("Global canonical papers", str(overview.global_paper_count))
    summary.add_row("Project key papers", str(overview.project_paper_count))
    console.print(summary)

    if overview.project_papers:
        table = Table(title="Project Key Papers")
        table.add_column("Paper ID")
        table.add_column("Title")
        table.add_column("Status")
        for entry in overview.project_papers:
            table.add_row(entry.paper_id, entry.title, entry.status.value)
        console.print(table)
    else:
        console.print("[dim]No project key papers yet.[/dim]")
