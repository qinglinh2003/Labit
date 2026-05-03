from __future__ import annotations

import json
from typing import Callable, TypeVar

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from labit.commands.compute import compute_app
from labit.models import ProjectSpec
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

project_app = typer.Typer(help="Manage local projects.")
project_app.add_typer(compute_app, name="compute")
console = Console()
T = TypeVar("T")


def _service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _emit(data: object, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    console.print(data)


def _prompt_text(label: str, *, default: str = "", required: bool = False) -> str:
    while True:
        value = typer.prompt(label, default=default, show_default=bool(default)).strip()
        if value or not required:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_edit_text(label: str, *, default: str = "", required: bool = False) -> str:
    help_suffix = " [enter keeps current; '-' clears]" if default and not required else ""
    while True:
        value = typer.prompt(f"{label}{help_suffix}", default=default, show_default=bool(default)).strip()
        if value == "-" and not required:
            return ""
        if value or not required:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_csv(label: str, *, default: str = "") -> list[str]:
    raw = typer.prompt(label, default=default, show_default=bool(default)).strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _prompt_edit_csv(label: str, *, default: list[str] | None = None) -> list[str]:
    default_items = default or []
    default_text = ", ".join(default_items)
    help_suffix = " [enter keeps current; '-' clears]" if default_items else ""
    raw = typer.prompt(f"{label}{help_suffix}", default=default_text, show_default=bool(default_text)).strip()
    if raw == "-":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _run_step(label: str, *, step: int, total: int, as_json: bool, fn: Callable[[], T]) -> T:
    if as_json:
        return fn()
    console.print(f"[bold cyan][{step}/{total}][/bold cyan] {label}")
    with console.status(f"{label}...", spinner="dots", spinner_style="cyan"):
        result = fn()
    console.print(f"[green]done[/green] {label}")
    return result


def _print_kv_summary(title: str, rows: list[tuple[str, str]]) -> None:
    console.print(f"[bold green]{title}[/bold green]")
    for label, value in rows:
        multiline = len(value) > 60 or value.startswith(("/", "~/", "git@", "http://", "https://", "ssh://"))
        if multiline:
            console.print(f"- {label}:")
            console.print(f"  {value}", soft_wrap=True)
            continue
        console.print(f"- {label}: {value}")


def _prompt_project_fields_for_edit(spec: ProjectSpec) -> ProjectSpec:
    console.print("[bold]Editing[/bold]")
    console.print(f"Project: [bold]{spec.name}[/bold]")

    description = _prompt_edit_text("Description", default=spec.description)
    repo = _prompt_edit_text("Repository URL or local path", default=spec.repo or "")
    keywords = _prompt_edit_csv("Keywords (comma-separated)", default=spec.keywords)
    relevance_criteria = _prompt_edit_text("Relevance criteria", default=spec.relevance_criteria)

    return ProjectSpec.model_validate(
        {
            "name": spec.name,
            "description": description,
            "repo": repo or None,
            "keywords": keywords,
            "relevance_criteria": relevance_criteria,
            "compute_profiles": [profile.model_dump(mode="json") for profile in spec.compute_profiles],
        }
    )


def _render_spec_review(spec: ProjectSpec) -> None:
    table = Table(title="Project Review")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Name", spec.name)
    table.add_row("Description", spec.description or "(blank)")
    table.add_row("Repo", spec.repo or "(blank)")
    table.add_row("Keywords", ", ".join(spec.keywords) or "(blank)")
    table.add_row("Relevance", spec.relevance_criteria or "(blank)")
    console.print(table)


@project_app.command("new", help="Create a project interactively.")
def new_project(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()

    console.print("[bold]Basic[/bold]")
    name = _prompt_text("Project name", required=True)
    existing_name = service.resolve_project_name(name)
    if existing_name is not None:
        raise typer.Exit(
            code=_fail(
                f"Project '{existing_name}' already exists. Use 'labit project show {existing_name}' to inspect it.",
                as_json=json_output,
            )
        )

    description = _prompt_text("Description")
    repo = _prompt_text("Repository URL or local path")
    keywords = _prompt_csv("Keywords (comma-separated)")
    relevance_criteria = _prompt_text("Relevance criteria")

    try:
        project_spec = ProjectSpec.model_validate(
            {
                "name": name,
                "description": description,
                "repo": repo or None,
                "keywords": keywords,
                "relevance_criteria": relevance_criteria,
            }
        )
    except ValidationError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    console.print()
    _render_spec_review(project_spec)

    if not typer.confirm("Create this project?", default=True):
        if json_output:
            _emit({"created": False, "aborted": True}, as_json=True)
        else:
            console.print("Cancelled.")
        raise typer.Exit(code=1)

    set_active = typer.confirm("Set as active project?", default=True)
    try:
        created = _run_step(
            "Creating project files",
            step=1,
            total=1,
            as_json=json_output,
            fn=lambda: service.save_project(project_spec, set_active=set_active),
        )
    except (FileExistsError, FileNotFoundError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {"initialized": True, "created": created}
    if json_output:
        _emit(payload, as_json=True)
        return

    rows = [
        ("Name", created["name"]),
        ("Config", created["config_path"]),
        ("Workspace", created["project_dir"]),
    ]
    if set_active:
        rows.append(("Active project", created["name"]))
    rows.append(("Next", f"labit project show {created['name']}"))
    _print_kv_summary("Project ready", rows)


@project_app.command("current", help="Show the active project name.")
def current(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    active = service.active_project_name()
    if active is None:
        raise typer.Exit(code=_fail("No active project. Create one or switch to an existing project.", as_json=json_output))

    try:
        summary = service.get_project_summary(active)
        spec = service.load_project(active)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = summary.model_dump()
    payload["active_project"] = payload.pop("name")
    if json_output:
        _emit(payload, as_json=True)
        return

    table = Table(title="Active Project")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Name", payload["active_project"])
    table.add_row("Description", payload["description"])
    table.add_row("Keywords", str(payload["keyword_count"]))
    table.add_row("Compute profiles", str(payload["compute_count"]))
    table.add_row("Config", payload["config_path"])
    console.print(table)


@project_app.command("list", help="List all projects.")
def list_projects(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    summaries = [{"name": summary.name, "active": summary.is_active} for summary in service.list_project_summaries()]
    if json_output:
        _emit({"projects": summaries}, as_json=True)
        return
    console.print("[bold]Projects[/bold]")
    if not summaries:
        console.print("- (none)")
        return
    for item in summaries:
        suffix = " (active)" if item["active"] else ""
        console.print(f"- {item['name']}{suffix}")


@project_app.command("show", help="Show project details.")
def show_project(
    name: str | None = typer.Argument(None, help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    project_name = name or service.active_project_name()
    if project_name is None:
        raise typer.Exit(code=_fail("No active project. Pass a name or create a project first.", as_json=json_output))

    try:
        spec = service.load_project(project_name)
        summary = service.get_project_summary(project_name)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {"summary": summary.model_dump(), "spec": spec.model_dump(mode="json", exclude_none=True)}
    if json_output:
        _emit(payload, as_json=True)
        return

    if name is None:
        console.print(f"[dim]Showing active project: {summary.name}[/dim]")
    table = Table(title=summary.name)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Description", summary.description or "(blank)")
    table.add_row("Repo", spec.repo or "(blank)")
    table.add_row("Keywords", ", ".join(spec.keywords) or "(blank)")
    table.add_row("Relevance", spec.relevance_criteria or "(blank)")
    if spec.compute_profiles:
        table.add_row("Compute profiles", ", ".join(profile.name for profile in spec.compute_profiles))
    else:
        table.add_row("Compute profiles", "(none)")
    table.add_row("Config", summary.config_path)
    console.print(table)


@project_app.command("edit", help="Edit project config.")
def edit_project(
    name: str | None = typer.Argument(None, help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    project_name = name or service.active_project_name()
    if project_name is None:
        raise typer.Exit(code=_fail("No active project. Pass a name or create a project first.", as_json=json_output))

    try:
        existing = service.load_project(project_name)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    try:
        updated_spec = _prompt_project_fields_for_edit(existing)
    except ValidationError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    console.print()
    _render_spec_review(updated_spec)

    if not typer.confirm("Save these changes?", default=True):
        if json_output:
            _emit({"updated": False, "aborted": True}, as_json=True)
        else:
            console.print("Cancelled.")
        raise typer.Exit(code=1)

    try:
        result = _run_step("Updating project files", step=1, total=1, as_json=json_output, fn=lambda: service.save_project(updated_spec, force=True, set_active=False))
    except (FileExistsError, FileNotFoundError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {"updated": True, "project": result}
    if json_output:
        _emit(payload, as_json=True)
        return

    _print_kv_summary(
        "Project updated",
        [
            ("Name", result["name"]),
            ("Config", result["config_path"]),
            ("Workspace", result["project_dir"]),
            ("Next", f"labit project show {result['name']}"),
        ],
    )


@project_app.command("switch", help="Switch active project.")
def switch_project(name: str = typer.Argument(..., help="Project name to activate."), json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    try:
        service.set_active_project(name)
        summary = service.get_project_summary(name)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {"active_project": summary.name, "description": summary.description, "config_path": summary.config_path}
    if json_output:
        _emit(payload, as_json=True)
        return
    console.print(f"Switched active project to [bold]{summary.name}[/bold].")
    console.print(summary.description)


@project_app.command("delete", help="Delete a project.")
def delete_project(
    name: str | None = typer.Argument(None, help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    project_name = name or service.active_project_name()
    if project_name is None:
        raise typer.Exit(code=_fail("No active project. Pass a name or create a project first.", as_json=json_output))

    try:
        summary = service.get_project_summary(project_name)
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if not json_output:
        _print_kv_summary(
            "Delete project",
            [
                ("Name", summary.name),
                ("Config", summary.config_path),
                ("Workspace", str(service.paths.vault_projects_dir / summary.name)),
                ("Active", "yes" if summary.is_active else "no"),
            ],
        )

    if not typer.confirm(f"Delete project '{summary.name}' and all local project files?", default=False):
        if json_output:
            _emit({"deleted": False, "aborted": True}, as_json=True)
        else:
            console.print("Cancelled.")
        raise typer.Exit(code=1)

    try:
        result = _run_step("Deleting project files", step=1, total=1, as_json=json_output, fn=lambda: service.delete_project(summary.name))
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {"deleted": True, "project": result}
    if json_output:
        _emit(payload, as_json=True)
        return

    rows = [("Name", result["name"]), ("Deleted config", result["config_path"]), ("Deleted workspace", result["project_dir"])]
    if result["cleared_active"]:
        rows.append(("Active project", "cleared"))
    rows.append(("Next", "labit project list"))
    _print_kv_summary("Project deleted", rows)


def _fail(message: str, *, as_json: bool) -> int:
    if as_json:
        _emit({"error": message}, as_json=True)
    else:
        console.print(f"[bold red]Error:[/bold red] {message}")
    return 1
