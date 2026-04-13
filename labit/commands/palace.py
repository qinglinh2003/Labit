"""CLI commands for MemPalace integration."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

palace_app = typer.Typer(help="MemPalace memory management.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_service() -> ProjectService:
    return ProjectService(_paths())


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

    from labit.memory.palace_miner import mine_project

    console.print(f"Mining [bold]{project_name}[/bold] into MemPalace...")
    if dry_run:
        console.print("[dim](dry run — nothing will be written)[/dim]")

    try:
        stats = mine_project(
            project_name=project_name,
            project_dir=project_dir,
            palace_path=paths.palace_dir,
            dry_run=dry_run,
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Mining results — {project_name}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total files scanned", str(stats["total_files"]))
    table.add_row("Files processed", str(stats["files_processed"]))
    table.add_row("Files skipped (unchanged)", str(stats["files_skipped"]))
    table.add_row("Drawers filed", str(stats["drawers_filed"]))
    console.print(table)

    if stats["rooms"]:
        room_table = Table(title="By room")
        room_table.add_column("Room")
        room_table.add_column("Files", justify="right")
        for room, count in sorted(stats["rooms"].items(), key=lambda x: x[1], reverse=True):
            room_table.add_row(room, str(count))
        console.print(room_table)


@palace_app.command("status")
def status():
    """Show MemPalace status and drawer counts."""
    paths = _paths()
    palace_path = paths.palace_dir

    if not palace_path.is_dir():
        console.print("[yellow]No palace found. Run: labit palace mine[/yellow]")
        raise typer.Exit(0)

    try:
        import chromadb
    except ImportError:
        console.print("[red]chromadb not installed. Install with: pip install 'labit[mempalace]'[/red]")
        raise typer.Exit(1)

    client = chromadb.PersistentClient(path=str(palace_path))
    try:
        col = client.get_collection("mempalace_drawers")
    except Exception:
        console.print("[yellow]Palace exists but no drawers collection found.[/yellow]")
        raise typer.Exit(0)

    count = col.count()
    console.print(f"Palace: [bold]{palace_path}[/bold]")
    console.print(f"Total drawers: [bold]{count}[/bold]")

    if count > 0:
        r = col.get(limit=min(count, 10000), include=["metadatas"])
        metas = r["metadatas"]
        wing_rooms: dict[str, dict[str, int]] = {}
        for m in metas:
            w = m.get("wing", "?")
            rm = m.get("room", "?")
            wing_rooms.setdefault(w, {})
            wing_rooms[w][rm] = wing_rooms[w].get(rm, 0) + 1

        for wing, rooms in sorted(wing_rooms.items()):
            table = Table(title=f"Wing: {wing}")
            table.add_column("Room")
            table.add_column("Drawers", justify="right")
            for room, cnt in sorted(rooms.items(), key=lambda x: x[1], reverse=True):
                table.add_row(room, str(cnt))
            console.print(table)


@palace_app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    project: str = typer.Option("", help="Filter by project (wing)"),
    n: int = typer.Option(5, help="Number of results"),
):
    """Semantic search against the MemPalace."""
    paths = _paths()
    palace_path = paths.palace_dir

    if not palace_path.is_dir():
        console.print("[yellow]No palace found. Run: labit palace mine[/yellow]")
        raise typer.Exit(0)

    try:
        import chromadb
    except ImportError:
        console.print("[red]chromadb not installed.[/red]")
        raise typer.Exit(1)

    client = chromadb.PersistentClient(path=str(palace_path))
    try:
        col = client.get_collection("mempalace_drawers")
    except Exception:
        console.print("[yellow]No drawers collection found.[/yellow]")
        raise typer.Exit(0)

    wing = project or None
    kwargs = {
        "query_texts": [query],
        "n_results": n,
        "include": ["documents", "metadatas", "distances"],
    }
    if wing:
        kwargs["where"] = {"wing": wing}

    results = col.query(**kwargs)
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    if not docs:
        console.print(f"No results for: [bold]{query}[/bold]")
        return

    console.print(f"\nResults for: [bold]{query}[/bold]\n")
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        sim = round(max(0.0, 1 - dist), 3)
        wing_name = meta.get("wing", "?")
        room = meta.get("room", "?")
        source = meta.get("source_file", "?")
        console.print(f"[bold][{i}][/bold] {wing_name}/{room}  sim={sim}  src={source}")
        snippet = doc.strip()
        if len(snippet) > 300:
            snippet = snippet[:297] + "..."
        console.print(f"    {snippet}\n")
