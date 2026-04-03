from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, TypeVar

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from labit.models import ComputeBackend, ProjectSpec, RuntimeKind
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

project_app = typer.Typer(help="Manage project state and project specs.")
console = Console()
T = TypeVar("T")


def _service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _emit(data: object, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    if isinstance(data, str):
        console.print(data)
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
        value = typer.prompt(
            f"{label}{help_suffix}",
            default=default,
            show_default=bool(default),
        ).strip()
        if value == "-" and not required:
            return ""
        if value or not required:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_choice(label: str, choices: list[str], *, default: str) -> str:
    normalized = {choice.lower(): choice for choice in choices}
    rendered = "/".join(choices)
    while True:
        value = typer.prompt(
            f"{label} [{rendered}]",
            default=default,
            show_default=True,
        ).strip().lower()
        if value in normalized:
            return normalized[value]
        console.print(f"[bold red]Choose one of:[/bold red] {', '.join(choices)}")


def _prompt_csv(label: str, *, default: str = "") -> list[str]:
    raw = typer.prompt(label, default=default, show_default=bool(default)).strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _prompt_edit_csv(label: str, *, default: list[str] | None = None) -> list[str]:
    default_items = default or []
    default_text = ", ".join(default_items)
    help_suffix = " [enter keeps current; '-' clears]" if default_items else ""
    raw = typer.prompt(
        f"{label}{help_suffix}",
        default=default_text,
        show_default=bool(default_text),
    ).strip()
    if raw == "-":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _run_step(
    label: str,
    *,
    step: int,
    total: int,
    as_json: bool,
    fn: Callable[[], T],
) -> T:
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
    arxiv_categories = _prompt_edit_csv("arXiv categories (comma-separated)", default=spec.arxiv_categories)
    relevance_criteria = _prompt_edit_text("Relevance criteria", default=spec.relevance_criteria)

    console.print("\n[bold]Compute[/bold]")
    backend = _prompt_choice(
        "Compute backend",
        [backend.value for backend in ComputeBackend],
        default=spec.compute.backend.value,
    )
    host = spec.compute.host or ""
    workdir = spec.compute.workdir or ""
    datadir = spec.compute.datadir or ""
    if backend == ComputeBackend.SSH.value:
        host = _prompt_edit_text("SSH host", default=spec.compute.host or "", required=True)
        workdir = _prompt_edit_text("SSH workdir", default=spec.compute.workdir or "", required=True)
        datadir = _prompt_edit_text("Data directory", default=spec.compute.datadir or "")
    elif backend == ComputeBackend.SKYPILOT.value:
        workdir = _prompt_edit_text("Workdir", default=spec.compute.workdir or "")
        datadir = _prompt_edit_text("Data directory", default=spec.compute.datadir or "")
        host = ""
    else:
        host = ""
        workdir = ""
        datadir = ""

    runtime = _prompt_choice(
        "Runtime",
        [runtime.value for runtime in RuntimeKind],
        default=spec.compute.runtime.value,
    )
    conda_env = spec.compute.conda_env or ""
    conda_init = spec.compute.conda_init or ""
    uv_project = spec.compute.uv_project or ""
    if runtime == RuntimeKind.CONDA.value:
        conda_env = _prompt_edit_text("Conda environment", default=spec.compute.conda_env or "", required=True)
        conda_init = _prompt_edit_text("Conda init command", default=spec.compute.conda_init or "", required=True)
        uv_project = ""
    elif runtime == RuntimeKind.UV.value:
        uv_project = _prompt_edit_text("UV project directory", default=spec.compute.uv_project or "", required=True)
        conda_env = ""
        conda_init = ""
    else:
        conda_env = ""
        conda_init = ""
        uv_project = ""

    console.print("\n[bold]Sync[/bold]")
    sync_dirs = _prompt_edit_csv("Sync directories (comma-separated)", default=spec.sync_dirs)

    return ProjectSpec.model_validate(
        {
            "name": spec.name,
            "description": description,
            "repo": repo or None,
            "keywords": keywords,
            "arxiv_categories": arxiv_categories,
            "relevance_criteria": relevance_criteria,
            "compute": {
                "backend": backend,
                "host": host or None,
                "workdir": workdir or None,
                "datadir": datadir or None,
                "runtime": runtime,
                "conda_env": conda_env or None,
                "conda_init": conda_init or None,
                "uv_project": uv_project or None,
            },
            "sync_dirs": sync_dirs,
        }
    )


def _render_spec_review(spec: ProjectSpec) -> None:
    table = Table(title="New Project Review")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Name", spec.name)
    table.add_row("Description", spec.description or "(blank)")
    table.add_row("Repo", spec.repo or "(blank)")
    table.add_row("Keywords", ", ".join(spec.keywords) or "(blank)")
    table.add_row("arXiv Categories", ", ".join(spec.arxiv_categories) or "(blank)")
    table.add_row("Relevance", spec.relevance_criteria or "(blank)")
    table.add_row("Compute Backend", spec.compute.backend.value)
    table.add_row("Host", spec.compute.host or "(blank)")
    table.add_row("Workdir", spec.compute.workdir or "(blank)")
    table.add_row("Datadir", spec.compute.datadir or "(blank)")
    table.add_row("Runtime", spec.compute.runtime.value)
    table.add_row("Conda Env", spec.compute.conda_env or "(blank)")
    table.add_row("Conda Init", spec.compute.conda_init or "(blank)")
    table.add_row("UV Project", spec.compute.uv_project or "(blank)")
    table.add_row("Sync Dirs", ", ".join(spec.sync_dirs))
    console.print(table)


@project_app.command("new")
def new_project(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()

    console.print("[bold]Basic[/bold]")
    name = _prompt_text("Project name", required=True)
    existing_name = service.resolve_project_name(name)
    if existing_name is not None:
        raise typer.Exit(
            code=_fail(
                f"Project '{existing_name}' already exists. "
                f"Use 'labit project show {existing_name}' to inspect it, "
                f"'labit project switch {existing_name}' to activate it, or "
                f"'labit project list' to browse all projects.",
                as_json=json_output,
            )
        )

    description = _prompt_text("Description")
    repo = _prompt_text("Repository URL or local path")
    keywords = _prompt_csv("Keywords (comma-separated)")
    arxiv_categories = _prompt_csv("arXiv categories (comma-separated)")
    relevance_criteria = _prompt_text("Relevance criteria")

    console.print("\n[bold]Compute[/bold]")
    backend = _prompt_choice(
        "Compute backend",
        [backend.value for backend in ComputeBackend],
        default=ComputeBackend.NONE.value,
    )
    host = ""
    workdir = ""
    datadir = ""
    if backend == ComputeBackend.SSH.value:
        host = _prompt_text("SSH host", required=True)
        workdir = _prompt_text("SSH workdir", required=True)
        datadir = _prompt_text("Data directory")
    elif backend == ComputeBackend.SKYPILOT.value:
        workdir = _prompt_text("Workdir")
        datadir = _prompt_text("Data directory")

    runtime = _prompt_choice(
        "Runtime",
        [runtime.value for runtime in RuntimeKind],
        default=RuntimeKind.PLAIN.value,
    )
    conda_env = ""
    conda_init = ""
    uv_project = ""
    if runtime == RuntimeKind.CONDA.value:
        conda_env = _prompt_text("Conda environment", required=True)
        conda_init = _prompt_text("Conda init command", required=True)
    elif runtime == RuntimeKind.UV.value:
        uv_project = _prompt_text("UV project directory", required=True)

    console.print("\n[bold]Sync[/bold]")
    sync_dirs = _prompt_csv("Sync directories (comma-separated)", default="outputs")

    try:
        project_spec = ProjectSpec.model_validate(
            {
                "name": name,
                "description": description,
                "repo": repo or None,
                "keywords": keywords,
                "arxiv_categories": arxiv_categories,
                "relevance_criteria": relevance_criteria,
                "compute": {
                    "backend": backend,
                    "host": host or None,
                    "workdir": workdir or None,
                    "datadir": datadir or None,
                    "runtime": runtime,
                    "conda_env": conda_env or None,
                    "conda_init": conda_init or None,
                    "uv_project": uv_project or None,
                },
                "sync_dirs": sync_dirs,
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
    clone = bool(project_spec.repo) and typer.confirm("Clone project code now?", default=True)
    total_steps = 2 if clone else 1

    try:
        created = _run_step(
            "Creating project files",
            step=1,
            total=total_steps,
            as_json=json_output,
            fn=lambda: service.save_project(project_spec, set_active=set_active),
        )
    except (FileExistsError, FileNotFoundError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    cloned = None
    if clone:
        try:
            cloned = _run_step(
                "Cloning repository (this may take a moment)",
                step=2,
                total=total_steps,
                as_json=json_output,
                fn=lambda: service.clone_project_code(project_spec.name),
            )
        except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
            payload = {
                "initialized": False,
                "created": created,
                "clone_requested": True,
                "error": str(exc),
            }
            if json_output:
                _emit(payload, as_json=True)
            else:
                _print_kv_summary(
                    "Project created, but clone failed",
                    [
                        ("Name", created["name"]),
                        ("Config", created["config_path"]),
                        ("Overlay", created["project_dir"]),
                        ("Clone error", str(exc)),
                        ("Retry", f"labit project clone-code {created['name']}"),
                    ],
                )
            raise typer.Exit(code=1)

    payload = {
        "initialized": True,
        "created": created,
        "clone_requested": clone,
        "cloned": cloned,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    rows = [
        ("Name", created["name"]),
        ("Config", created["config_path"]),
        ("Overlay", created["project_dir"]),
    ]
    if set_active:
        rows.append(("Active project", created["name"]))
    if cloned:
        rows.append(("Repo", cloned["repo"]))
        rows.append(("Code dir", cloned["target_dir"]))
    rows.append(("Next", f"labit project show {created['name']}"))
    _print_kv_summary("Project ready", rows)


@project_app.command("current")
def current(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    active = service.active_project_name()
    if active is None:
        raise typer.Exit(code=_fail("No active project. Create one or switch to an existing project.", as_json=json_output))

    try:
        summary = service.get_project_summary(active)
    except FileNotFoundError as exc:
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
    table.add_row("Papers", str(payload["paper_count"]))
    table.add_row("Hypotheses", str(payload["hypothesis_count"]))
    table.add_row("Config", payload["config_path"])
    console.print(table)


@project_app.command("list")
def list_projects(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    summaries = [
        {
            "name": summary.name,
            "active": summary.is_active,
        }
        for summary in service.list_project_summaries()
    ]
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


@project_app.command("show")
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
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "summary": summary.model_dump(),
        "spec": spec.model_dump(mode="json", exclude_none=True),
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    if name is None:
        console.print(f"[dim]Showing active project: {summary.name}[/dim]")
    console.print(f"[bold]{summary.name}[/bold]")
    console.print(summary.description)
    console.print(f"Keywords: {summary.keyword_count}")
    console.print(f"Papers: {summary.paper_count}")
    console.print(f"Hypotheses: {summary.hypothesis_count}")
    console.print(f"Config: {summary.config_path}")


@project_app.command("edit")
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
    except FileNotFoundError as exc:
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
        result = _run_step(
            "Updating project files",
            step=1,
            total=1,
            as_json=json_output,
            fn=lambda: service.save_project(updated_spec, force=True, set_active=False),
        )
    except (FileExistsError, FileNotFoundError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "updated": True,
        "project": result,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    _print_kv_summary(
        "Project updated",
        [
            ("Name", result["name"]),
            ("Config", result["config_path"]),
            ("Overlay", result["project_dir"]),
            ("Next", f"labit project show {result['name']}"),
        ],
    )


@project_app.command("switch")
def switch_project(
    name: str = typer.Argument(..., help="Project name to activate."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        service.set_active_project(name)
        summary = service.get_project_summary(name)
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "active_project": summary.name,
        "description": summary.description,
        "config_path": summary.config_path,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    console.print(f"Switched active project to [bold]{summary.name}[/bold].")
    console.print(summary.description)


@project_app.command("delete")
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
                ("Overlay", str(service.paths.vault_projects_dir / summary.name)),
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
        result = _run_step(
            "Deleting project files",
            step=1,
            total=1,
            as_json=json_output,
            fn=lambda: service.delete_project(summary.name),
        )
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "deleted": True,
        "project": result,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    rows = [
        ("Name", result["name"]),
        ("Deleted config", result["config_path"]),
        ("Deleted overlay", result["project_dir"]),
    ]
    if result["cleared_active"]:
        rows.append(("Active project", "cleared"))
    rows.append(("Next", "labit project list"))
    _print_kv_summary("Project deleted", rows)


@project_app.command("validate")
def validate_project(
    spec: Path = typer.Option(..., exists=True, dir_okay=False, readable=True, help="Path to a project spec YAML."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        project_spec = service.load_project_spec(spec)
    except ValidationError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "valid": True,
        "spec_path": str(spec.resolve()),
        "project": project_spec.model_dump(mode="json", exclude_none=True),
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    console.print(f"[bold green]Valid project spec[/bold green]: {project_spec.name}")
    console.print(f"Spec: {spec.resolve()}")


@project_app.command("draft")
def draft_project(
    seed: Path = typer.Option(..., exists=True, dir_okay=False, readable=True, help="Path to a ProjectSeed YAML."),
    brief: Path = typer.Option(..., exists=True, dir_okay=False, readable=True, help="Path to a SemanticBrief YAML."),
    output: Path | None = typer.Option(None, "--output", help="Optional output path for the generated draft spec YAML."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        project_seed = service.load_project_seed(seed)
        semantic_brief = service.load_semantic_brief(brief)
        project_draft = service.build_project_draft(semantic_brief)
        project_spec = service.compose_project_spec(project_seed, project_draft)
    except ValidationError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "seed": project_seed.model_dump(mode="json", exclude_none=True),
        "brief": semantic_brief.model_dump(mode="json", exclude_none=True),
        "draft": project_draft.model_dump(mode="json", exclude_none=True),
        "spec": project_spec.model_dump(mode="json", exclude_none=True),
    }

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json_to_yaml(payload["spec"]))
        payload["output"] = str(output.resolve())

    if json_output:
        _emit(payload, as_json=True)
        return

    console.print(f"[bold green]Drafted project spec[/bold green]: {project_spec.name}")
    console.print(f"Description: {project_draft.description}")
    console.print(f"Keywords: {', '.join(project_draft.keywords)}")
    console.print(f"Categories: {', '.join(project_draft.arxiv_categories)}")
    if output is not None:
        console.print(f"Wrote spec draft: {output.resolve()}")


@project_app.command("create")
def create_project(
    spec: Path = typer.Option(..., exists=True, dir_okay=False, readable=True, help="Path to a project spec YAML."),
    set_active: bool = typer.Option(False, "--set-active", help="Set the new project as active."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned actions without writing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing project config."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        project_spec = service.load_project_spec(spec)
    except ValidationError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    actions = service.planned_create_actions(project_spec, set_active=set_active)
    if dry_run:
        payload = {
            "dry_run": True,
            "project": project_spec.model_dump(mode="json", exclude_none=True),
            "actions": actions,
        }
        if json_output:
            _emit(payload, as_json=True)
            return
        console.print(f"[bold]Dry run for {project_spec.name}[/bold]")
        for action in actions:
            console.print(f"- {action}")
        return

    try:
        result = _run_step(
            "Creating project files",
            step=1,
            total=1,
            as_json=json_output,
            fn=lambda: service.save_project(project_spec, force=force, set_active=set_active),
        )
    except (FileExistsError, FileNotFoundError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "created": True,
        "project": result,
        "actions": actions,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    rows = [
        ("Name", result["name"]),
        ("Config", result["config_path"]),
        ("Overlay", result["project_dir"]),
    ]
    if set_active:
        rows.append(("Active project", result["name"]))
    rows.append(("Next", f"labit project show {result['name']}"))
    _print_kv_summary("Project ready", rows)


@project_app.command("clone-code")
def clone_code(
    name: str | None = typer.Argument(None, help="Project name. Defaults to the active project."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the clone action without executing it."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    if name and (name.startswith(("git@", "https://", "http://", "ssh://")) or "/" in name):
        raise typer.Exit(
            code=_fail(
                "clone-code expects a project name, not a repository URL or path. "
                "Use 'labit project new' or create the project first.",
                as_json=json_output,
            )
        )

    project_name = name or service.active_project_name()
    if project_name is None:
        raise typer.Exit(code=_fail("No active project. Pass a name or create a project first.", as_json=json_output))

    try:
        spec = service.load_project(project_name)
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    action = service.planned_clone_action(spec)
    if action is None:
        raise typer.Exit(code=_fail(f"Project '{spec.name}' does not declare a repository URL.", as_json=json_output))

    if dry_run:
        payload = {
            "dry_run": True,
            "project": spec.name,
            "repo": spec.repo,
            "target_dir": str(service.project_code_dir(spec.name)),
            "actions": [action],
        }
        if json_output:
            _emit(payload, as_json=True)
            return
        console.print(f"[bold]Dry run for {spec.name}[/bold]")
        console.print(f"- {action}")
        return

    try:
        result = _run_step(
            "Cloning repository (this may take a moment)",
            step=1,
            total=1,
            as_json=json_output,
            fn=lambda: service.clone_project_code(spec.name),
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    payload = {
        "cloned": True,
        "project": result,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    _print_kv_summary(
        "Project code ready",
        [
            ("Project", result["name"]),
            ("Repo", result["repo"]),
            ("Code dir", result["target_dir"]),
            ("Next", f"labit project show {result['name']}"),
        ],
    )


@project_app.command("init")
def init_project(
    spec: Path = typer.Option(..., exists=True, dir_okay=False, readable=True, help="Path to a project spec YAML."),
    set_active: bool = typer.Option(False, "--set-active", help="Set the new project as active."),
    clone: bool = typer.Option(True, "--clone/--no-clone", help="Clone the configured repo after project creation when a repo is declared."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned actions without writing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing project config."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        project_spec = service.load_project_spec(spec)
    except ValidationError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    actions = service.planned_create_actions(project_spec, set_active=set_active)
    clone_action = service.planned_clone_action(project_spec) if clone else None
    if clone_action:
        actions.append(clone_action)

    if dry_run:
        payload = {
            "dry_run": True,
            "project": project_spec.model_dump(mode="json", exclude_none=True),
            "actions": actions,
        }
        if json_output:
            _emit(payload, as_json=True)
            return
        console.print(f"[bold]Init dry run for {project_spec.name}[/bold]")
        for action in actions:
            console.print(f"- {action}")
        return

    total_steps = 2 if clone_action else 1
    try:
        created = _run_step(
            "Creating project files",
            step=1,
            total=total_steps,
            as_json=json_output,
            fn=lambda: service.save_project(project_spec, force=force, set_active=set_active),
        )
    except (FileExistsError, FileNotFoundError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    cloned = None
    if clone_action:
        try:
            cloned = _run_step(
                "Cloning repository (this may take a moment)",
                step=2,
                total=total_steps,
                as_json=json_output,
                fn=lambda: service.clone_project_code(project_spec.name),
            )
        except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
            payload = {
                "initialized": False,
                "created": created,
                "clone_requested": True,
                "error": str(exc),
            }
            if json_output:
                _emit(payload, as_json=True)
            else:
                _print_kv_summary(
                    "Project created, but clone failed",
                    [
                        ("Name", created["name"]),
                        ("Config", created["config_path"]),
                        ("Overlay", created["project_dir"]),
                        ("Clone error", str(exc)),
                        ("Retry", f"labit project clone-code {created['name']}"),
                    ],
                )
            raise typer.Exit(code=1)

    payload = {
        "initialized": True,
        "created": created,
        "clone_requested": bool(clone_action),
        "cloned": cloned,
        "actions": actions,
    }
    if json_output:
        _emit(payload, as_json=True)
        return

    rows = [
        ("Name", created["name"]),
        ("Config", created["config_path"]),
        ("Overlay", created["project_dir"]),
    ]
    if set_active:
        rows.append(("Active project", created["name"]))
    if cloned:
        rows.append(("Repo", cloned["repo"]))
        rows.append(("Code dir", cloned["target_dir"]))
    rows.append(("Next", f"labit project show {created['name']}"))
    _print_kv_summary("Project ready", rows)


def _fail(message: str, *, as_json: bool) -> int:
    if as_json:
        _emit({"error": message}, as_json=True)
    else:
        console.print(f"[bold red]Error:[/bold red] {message}")
    return 1


def json_to_yaml(data: object) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
