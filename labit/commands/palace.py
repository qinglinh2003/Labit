"""CLI commands for MemPalace integration.

Delegates to the upstream mempalace package for mining, search, and status.
"""

from __future__ import annotations

import typer
from rich.console import Console

from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

palace_app = typer.Typer(help="MemPalace memory management.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_service() -> ProjectService:
    return ProjectService(_paths())


def _ensure_mempalace_yaml(project_dir, project_name: str) -> None:
    """Write a mempalace.yaml in the project directory if one doesn't exist."""
    from pathlib import Path
    import yaml

    config_path = Path(project_dir) / "mempalace.yaml"
    if config_path.exists():
        return

    config = {
        "wing": project_name,
        "rooms": [
            {
                "name": "hypotheses",
                "description": "Research hypotheses and experiment plans",
                "keywords": ["hypothesis", "rationale", "experiment_plan", "prediction"],
            },
            {
                "name": "documents",
                "description": "Design documents, notes, and ideas",
                "keywords": ["design", "document", "note", "idea", "overview"],
            },
            {
                "name": "experiments",
                "description": "Experiment configs, tasks, and results",
                "keywords": ["experiment", "task", "config", "launch", "result", "metric"],
            },
            {
                "name": "papers",
                "description": "Paper summaries and references",
                "keywords": ["paper", "arxiv", "summary", "citation", "abstract"],
            },
            {
                "name": "memory",
                "description": "Long-term memory entries",
                "keywords": ["memory", "decision", "takeaway", "finding"],
            },
            {
                "name": "general",
                "description": "Everything else",
            },
        ],
    }
    config_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))
    console.print(f"[dim]Created {config_path}[/dim]")


@palace_app.command("mine")
def mine(
    project: str = typer.Option("", help="Project name (default: active project)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be mined without writing"),
):
    """Mine project artifacts into the MemPalace for semantic search."""
    paths = _paths()
    ps = _project_service()

    project_name = project or ps.active_project_name()
    if not project_name:
        console.print("[red]No active project. Set one with: labit project use <name>[/red]")
        raise typer.Exit(1)

    project_dir = paths.vault_projects_dir / project_name
    if not project_dir.is_dir():
        console.print(f"[red]Project directory not found: {project_dir}[/red]")
        raise typer.Exit(1)

    try:
        from labit.memory.palace.miner import mine as mp_mine
    except ImportError:
        console.print("[red]MemPalace dependencies not available. Install chromadb: pip install chromadb[/red]")
        raise typer.Exit(1)

    # Ensure mempalace.yaml exists in project dir
    _ensure_mempalace_yaml(project_dir, project_name)

    palace_path = str(paths.palace_dir)
    console.print(f"Mining [bold]{project_name}[/bold] into MemPalace...")

    mp_mine(
        project_dir=str(project_dir),
        palace_path=palace_path,
        wing_override=project_name,
        dry_run=dry_run,
    )


@palace_app.command("status")
def status():
    """Show MemPalace status and drawer counts."""
    paths = _paths()
    palace_path = str(paths.palace_dir)

    try:
        from labit.memory.palace.miner import status as mp_status
    except ImportError:
        console.print("[red]MemPalace dependencies not available.[/red]")
        raise typer.Exit(1)

    mp_status(palace_path)


@palace_app.command("backfill")
def backfill_cmd(
    project: str = typer.Option("", help="Filter by project (wing)"),
):
    """Backfill importance/memory_type for existing drawers that lack them."""
    paths = _paths()
    palace_path = str(paths.palace_dir)

    try:
        from labit.memory.palace.miner import backfill as mp_backfill
    except ImportError:
        console.print("[red]MemPalace dependencies not available.[/red]")
        raise typer.Exit(1)

    wing = project or None
    updated = mp_backfill(palace_path, wing=wing)
    if updated:
        console.print(f"[green]Backfilled {updated} drawers.[/green]")
    else:
        console.print("[dim]All drawers already have importance metadata.[/dim]")


@palace_app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    project: str = typer.Option("", help="Filter by project (wing)"),
    room: str = typer.Option("", help="Filter by room"),
    n: int = typer.Option(5, help="Number of results"),
):
    """Semantic search against the MemPalace."""
    from rich.panel import Panel
    from rich.text import Text

    paths = _paths()
    palace_path = str(paths.palace_dir)

    try:
        from labit.memory.palace.searcher import search_memories
    except ImportError:
        console.print("[red]MemPalace dependencies not available.[/red]")
        raise typer.Exit(1)

    wing = project or None
    room_filter = room or None

    try:
        result = search_memories(query, palace_path, wing=wing, room=room_filter, n_results=n)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)

    hits = result.get("results", [])
    if not hits:
        console.print("[dim]No results found.[/dim]")
        return

    console.print(f"\n[bold]Found {len(hits)} result(s) for:[/bold] {query}\n")
    for i, hit in enumerate(hits, 1):
        similarity = 1 - hit.get("distance", 0)
        room_name = hit.get("room", "?")
        wing_name = hit.get("wing", "?")
        source = hit.get("source_file", "unknown")
        text = hit.get("text", "").strip()
        # Truncate long text for display
        if len(text) > 400:
            text = text[:400] + "..."

        header = Text()
        header.append(f"#{i} ", style="bold")
        header.append(f"[{wing_name}/{room_name}] ", style="cyan")
        header.append(f"sim={similarity:.3f} ", style="green")
        header.append(source, style="dim")

        console.print(Panel(text, title=header, border_style="dim", padding=(0, 1)))


@palace_app.command("dedup")
def dedup(
    project: str = typer.Option("", help="Filter by project (wing)"),
    threshold: float = typer.Option(0.15, help="Cosine distance threshold for dedup"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show duplicates without removing"),
):
    """Remove near-duplicate drawers from the palace."""
    paths = _paths()
    palace_path = str(paths.palace_dir)

    try:
        from labit.memory.palace.dedup import dedup_palace
    except ImportError:
        console.print("[red]MemPalace dependencies not available.[/red]")
        raise typer.Exit(1)

    wing = project or None
    dedup_palace(palace_path, wing=wing, threshold=threshold, dry_run=dry_run)
