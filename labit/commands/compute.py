from __future__ import annotations

import json
import subprocess

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from labit.models import ComputeProfile
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService
from labit.services.project_service import ProjectService


compute_app = typer.Typer(help="Manage project SSH compute profiles.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _project_service() -> ProjectService:
    return ProjectService(_paths())


def _service() -> ComputeService:
    paths = _paths()
    return ComputeService(paths, project_service=ProjectService(paths))


def _resolve_project(name: str | None, *, as_json: bool = False) -> str:
    project_service = _project_service()
    project = name or project_service.active_project_name()
    if project is None:
        raise typer.Exit(code=_fail("No active project. Pass --project or run `labit project switch <name>`.", as_json=as_json))
    resolved = project_service.resolve_project_name(project)
    if resolved is None:
        raise typer.Exit(code=_fail(f"Project '{project}' not found.", as_json=as_json))
    return resolved


def _emit(data: object, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    console.print(data)


def _fail(message: str, *, as_json: bool = False) -> int:
    if as_json:
        _emit({"error": message}, as_json=True)
    else:
        console.print(f"[bold red]Error:[/bold red] {message}")
    return 1


def _profile_payload(profile: ComputeProfile) -> dict:
    payload = profile.model_dump(mode="json", exclude_none=True)
    payload["ssh"] = profile.ssh_display()
    return payload


def _render_profile_table(profiles: list[ComputeProfile], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Name")
    table.add_column("SSH")
    table.add_column("Workdir")
    table.add_column("Notes")
    for profile in profiles:
        table.add_row(
            profile.name,
            profile.ssh_display(),
            profile.workdir or "(blank)",
            profile.notes or "(blank)",
        )
    console.print(table)


@compute_app.command("list", help="List SSH compute profiles for a project.")
def list_profiles(
    project: str | None = typer.Option(None, "--project", "-p", help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project_name = _resolve_project(project, as_json=json_output)
    profiles = _service().list_profiles(project_name)
    if json_output:
        _emit({"project": project_name, "compute_profiles": [_profile_payload(profile) for profile in profiles]}, as_json=True)
        return
    if not profiles:
        console.print(f"[dim]No compute profiles configured for {project_name}.[/dim]")
        return
    _render_profile_table(profiles, title=f"{project_name} Compute Profiles")


@compute_app.command("show", help="Show one SSH compute profile.")
def show_profile(
    name: str = typer.Argument(..., help="Compute profile name."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project_name = _resolve_project(project, as_json=json_output)
    try:
        profile = _service().get_profile(project_name, name)
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit({"project": project_name, "compute_profile": _profile_payload(profile)}, as_json=True)
        return
    _render_profile_table([profile], title=f"{project_name}:{profile.name}")


@compute_app.command("add", help="Add or update an SSH compute profile.")
def add_profile(
    name: str = typer.Argument(..., help="Compute profile name."),
    host: str = typer.Option(..., "--host", help="SSH host."),
    user: str = typer.Option(..., "--user", help="SSH user."),
    port: int = typer.Option(22, "--port", help="SSH port."),
    identity_file: str | None = typer.Option(None, "--identity-file", "-i", help="SSH private key path."),
    workdir: str = typer.Option("", "--workdir", help="Default remote project workdir."),
    notes: str = typer.Option("", "--notes", help="Short note exposed to agents."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project_name = _resolve_project(project, as_json=json_output)
    service = _service()
    try:
        profile = service.build_profile(
            name=name,
            user=user,
            host=host,
            port=port,
            identity_file=identity_file,
            workdir=workdir,
            notes=notes,
        )
        service.save_profile(project_name, profile)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    payload = {"project": project_name, "compute_profile": _profile_payload(profile)}
    if json_output:
        _emit(payload, as_json=True)
        return
    console.print(f"[green]Saved[/green] compute profile [bold]{profile.name}[/bold] for [bold]{project_name}[/bold].")
    console.print(f"SSH: {profile.ssh_display()}")


@compute_app.command("delete", help="Delete an SSH compute profile.")
def delete_profile(
    name: str = typer.Argument(..., help="Compute profile name."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project name. Defaults to the active project."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project_name = _resolve_project(project, as_json=json_output)
    try:
        _service().delete_profile(project_name, name)
    except FileNotFoundError as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    payload = {"project": project_name, "deleted": name}
    if json_output:
        _emit(payload, as_json=True)
        return
    console.print(f"[green]Deleted[/green] compute profile [bold]{name}[/bold] from [bold]{project_name}[/bold].")


@compute_app.command("test", help="Test SSH connectivity for a compute profile.")
def test_profile(
    name: str = typer.Argument(..., help="Compute profile name."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project name. Defaults to the active project."),
    timeout: int = typer.Option(8, "--timeout", help="SSH connect timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project_name = _resolve_project(project, as_json=json_output)
    try:
        result = _service().test_profile(project_name, name, timeout_seconds=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    payload = {
        "project": project_name,
        "profile": name,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    if json_output:
        _emit(payload, as_json=True)
        return
    if result.returncode == 0:
        console.print(f"[green]SSH ok[/green] {project_name}:{name}")
        return
    console.print(f"[bold red]SSH failed[/bold red] {project_name}:{name}")
    if result.stderr.strip():
        console.print(result.stderr.strip())
    raise typer.Exit(code=1)
