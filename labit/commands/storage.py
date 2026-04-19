from __future__ import annotations

import json

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from labit.models import StorageProfile
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService
from labit.services.storage_service import StorageService

storage_app = typer.Typer(help="Manage reusable storage profiles.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _service() -> StorageService:
    return StorageService(_paths())


def _compute_service() -> ComputeService:
    return ComputeService(_paths())


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


def _prompt_text(label: str, *, default: str = "", required: bool = False) -> str:
    while True:
        value = typer.prompt(label, default=default, show_default=bool(default)).strip()
        if value or not required:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_compute_profile(*, default: str = "") -> str:
    service = _compute_service()
    names = service.list_compute_names()
    if not names:
        raise typer.Exit(code=_fail("No compute profiles found. Run 'labit compute add' first.", as_json=False))
    console.print("Available compute profiles: " + ", ".join(names))
    while True:
        value = typer.prompt("Compute profile for storage test", default=default or names[0], show_default=True).strip()
        resolved = service.resolve_compute_name(value)
        if resolved is not None:
            return resolved
        console.print(f"[bold red]Choose one of:[/bold red] {', '.join(names)}")


def _build_profile_from_prompt(existing: StorageProfile | None = None) -> StorageProfile:
    name = _prompt_text("Profile name", default=existing.name if existing else "", required=True)
    remote = _prompt_text("Rclone remote", default=existing.rclone.remote if existing else "", required=True)
    bucket = _prompt_text("Bucket", default=existing.rclone.bucket if existing else "", required=True)
    return StorageProfile.model_validate(
        {
            "name": name,
            "backend": "rclone",
            "rclone": {"remote": remote, "bucket": bucket},
        }
    )


def _render_profile(profile: StorageProfile) -> None:
    body = (
        f"[bold]Backend[/bold]: {profile.backend.value}\n"
        f"[bold]Remote[/bold]: {profile.rclone.remote}\n"
        f"[bold]Bucket[/bold]: {profile.rclone.bucket}\n"
        f"[bold]Path layout[/bold]: {profile.layout.path_template}\n"
        f"[bold]Policy[/bold]: {profile.policy.mode}"
    )
    console.print(Panel(body, title=f"[bold green]{profile.name}[/bold green]", border_style="green"))


def _render_test(result, *, compute_name: str | None) -> None:
    title = f"Storage Test · {result.name}"
    if compute_name:
        title = f"{title} · {compute_name}"
    table = Table(title=title, show_header=True, header_style="bold #0080ff")
    table.add_column("Check")
    table.add_column("Status")
    table.add_row("Config", "ok" if result.config_ok else "failed")
    if compute_name:
        table.add_row("Compute", "ok" if result.compute_ok else "failed")
        table.add_row("rclone", "ok" if result.rclone_ok else "failed")
        table.add_row("Remote", "ok" if result.remote_ok else "failed")
        table.add_row("Bucket", "ok" if result.bucket_ok else "failed")
    console.print(table)
    console.print(result.message)


def _run_storage_test(service: StorageService, name: str, *, compute_name: str | None):
    current_step = "Validating storage profile"

    def _on_step(step: str) -> None:
        nonlocal current_step
        current_step = step

    with console.status(f"{current_step}...", spinner="dots", spinner_style="bold #0080ff") as status:
        def _wrapped_on_step(step: str) -> None:
            _on_step(step)
            status.update(f"{current_step}...")

        result = service.test_storage(name, compute_name=compute_name, on_step=_wrapped_on_step)
        status.update("Finalizing...")
    return result


@storage_app.command("list", help="List available storage profiles.")
def list_storage(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    names = service.list_storage_names()
    if json_output:
        _emit({"profiles": names}, as_json=True)
        return
    console.print("[bold]Storage Profiles[/bold]")
    if not names:
        console.print("- (none)")
        return
    for name in names:
        console.print(f"- {name}")


@storage_app.command("show", help="Show one storage profile.")
def show_storage(
    name: str = typer.Argument(..., help="Storage profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        profile = service.load_storage(name)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(profile.model_dump(mode="json", exclude_none=True), as_json=True)
        return
    _render_profile(profile)


@storage_app.command("add", help="Create a storage profile and test it immediately.")
def add_storage(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    try:
        profile = _build_profile_from_prompt()
        saved = service.save_storage(profile)
        compute_name = _prompt_compute_profile()
        result = _run_storage_test(service, profile.name, compute_name=compute_name)
    except (ValidationError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit({"saved": saved, "compute": compute_name, "test": result.model_dump(mode="json")}, as_json=True)
        return
    console.print(f"[green]Saved storage profile[/green] {saved['name']}")
    _render_test(result, compute_name=compute_name)


@storage_app.command("edit", help="Edit a storage profile and retest it.")
def edit_storage(
    name: str = typer.Argument(..., help="Storage profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        existing = service.load_storage(name)
        updated = _build_profile_from_prompt(existing)
        saved = service.save_storage(updated, force=True)
        compute_name = _prompt_compute_profile()
        result = _run_storage_test(service, updated.name, compute_name=compute_name)
    except (ValidationError, FileNotFoundError, RuntimeError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit({"saved": saved, "compute": compute_name, "test": result.model_dump(mode="json")}, as_json=True)
        return
    console.print(f"[green]Updated storage profile[/green] {saved['name']}")
    _render_test(result, compute_name=compute_name)


@storage_app.command("delete", help="Delete a storage profile.")
def delete_storage(
    name: str = typer.Argument(..., help="Storage profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    if not typer.confirm(f"Delete storage profile '{name}'?", default=False):
        raise typer.Exit(code=1)
    try:
        result = service.delete_storage(name)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(result, as_json=True)
        return
    console.print(f"Deleted storage profile [bold]{result['name']}[/bold].")


@storage_app.command("test", help="Run storage checks. Optionally verify it on a compute host.")
def test_storage(
    name: str = typer.Argument(..., help="Storage profile name."),
    compute_name: str | None = typer.Option(None, "--compute", help="Compute profile to use for remote rclone checks."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        resolved_compute = None
        if compute_name:
            resolved_compute = _compute_service().resolve_compute_name(compute_name)
            if resolved_compute is None:
                raise FileNotFoundError(f"Compute profile '{compute_name}' not found.")
        result = _run_storage_test(service, name, compute_name=resolved_compute)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit({"compute": resolved_compute, "test": result.model_dump(mode="json")}, as_json=True)
        return
    _render_test(result, compute_name=resolved_compute)
