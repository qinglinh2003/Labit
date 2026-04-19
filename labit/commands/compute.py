from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from labit.models import ComputeProfile
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService

compute_app = typer.Typer(help="Manage reusable compute profiles.")
console = Console()


def _paths() -> RepoPaths:
    return RepoPaths.discover()


def _service() -> ComputeService:
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


def _prompt_int(label: str, *, default: int = 0, minimum: int = 0) -> int:
    while True:
        value = typer.prompt(label, default=str(default), show_default=True).strip()
        try:
            parsed = int(value)
        except ValueError:
            console.print("[bold red]Enter a whole number.[/bold red]")
            continue
        if parsed < minimum:
            console.print(f"[bold red]Value must be >= {minimum}.[/bold red]")
            continue
        return parsed


def _edit_script(default: str = "") -> str:
    seed = default.strip() or (
        "# Setup commands run before LABIT launches an experiment.\n"
        "# Example:\n"
        "# source ~/miniconda3/etc/profile.d/conda.sh\n"
        "# conda activate myenv\n"
    )
    edited = typer.edit(seed)
    if edited is None:
        return default.strip()
    return edited.strip()


def _default_ssh_key_path() -> str:
    candidates = (
        "~/.ssh/id_ed25519",
        "~/.ssh/id_rsa",
        "~/.ssh/id_ecdsa",
        "~/.ssh/id_ed25519_sk",
    )
    for candidate in candidates:
        if Path(candidate).expanduser().exists():
            return candidate
    return ""


def _build_profile_from_prompt(existing: ComputeProfile | None = None) -> ComputeProfile:
    name = _prompt_text("Profile name", default=existing.name if existing else "", required=True)
    user = _prompt_text("SSH user", default=existing.connection.user if existing else "root", required=True)
    host = _prompt_text("SSH host", default=existing.connection.host if existing else "", required=True)
    port = _prompt_int("SSH port", default=existing.connection.port if existing else 22, minimum=1)
    ssh_key = _prompt_text(
        "SSH key path",
        default=(existing.connection.ssh_key or "") if existing else _default_ssh_key_path(),
    )
    workdir = _prompt_text("Workdir", default=existing.workspace.workdir if existing else "", required=True)
    datadir = _prompt_text("Datadir", default=existing.workspace.datadir or "" if existing else "")
    gpu_count = _prompt_int("GPU count", default=existing.hardware.gpu_count if existing else 0, minimum=0)
    gpu_type = _prompt_text("GPU type", default=existing.hardware.gpu_type or "" if existing else "")
    console.print("\n[bold]Setup Script[/bold]")
    console.print("[dim]Your editor will open. Save and close it when the setup commands look right.[/dim]")
    script = _edit_script(existing.setup.script if existing else "")
    return ComputeProfile.model_validate(
        {
            "name": name,
            "backend": "ssh",
            "connection": {
                "user": user,
                "host": host,
                "port": port,
                "ssh_key": ssh_key or None,
            },
            "workspace": {
                "workdir": workdir,
                "datadir": datadir or None,
            },
            "setup": {"script": script},
            "hardware": {
                "gpu_count": gpu_count,
                "gpu_type": gpu_type or None,
            },
        }
    )


def _render_profile(profile: ComputeProfile) -> None:
    body = (
        f"[bold]Backend[/bold]: {profile.backend.value}\n"
        f"[bold]User[/bold]: {profile.connection.user}\n"
        f"[bold]Host[/bold]: {profile.connection.host}\n"
        f"[bold]Port[/bold]: {profile.connection.port}\n"
        f"[bold]SSH key[/bold]: {profile.connection.ssh_key or '(default)'}\n"
        f"[bold]Workdir[/bold]: {profile.workspace.workdir}\n"
        f"[bold]Datadir[/bold]: {profile.workspace.datadir or '(blank)'}\n"
        f"[bold]GPU count[/bold]: {profile.hardware.gpu_count}\n"
        f"[bold]GPU type[/bold]: {profile.hardware.gpu_type or '(blank)'}\n"
        f"[bold]Setup script[/bold]:\n{profile.setup.script or '(blank)'}"
    )
    console.print(Panel(body, title=f"[bold green]{profile.name}[/bold green]", border_style="green"))


def _render_test(result) -> None:
    table = Table(title=f"Compute Test · {result.name}", show_header=True, header_style="bold #0080ff")
    table.add_column("Check")
    table.add_column("Status")
    table.add_row("SSH", "ok" if result.ssh_ok else "failed")
    table.add_row("Workdir", "ok" if result.workdir_ok else "failed")
    table.add_row("Datadir", "ok" if result.datadir_ok else "failed")
    table.add_row("Setup", "ok" if result.setup_ok else "failed")
    table.add_row("Python", result.python_version or ("ok" if result.python_ok else "failed"))
    if result.detected_gpu_count is not None:
        gpu_label = f"{result.detected_gpu_count}"
        if result.detected_gpu_type:
            gpu_label = f"{gpu_label} · {result.detected_gpu_type}"
    else:
        gpu_label = "ok" if result.gpu_ok else "failed"
    table.add_row("GPU", gpu_label)
    console.print(table)
    console.print(result.message)


def _run_compute_test(service: ComputeService, name: str):
    current_step = "Starting checks"

    def _on_step(step: str) -> None:
        nonlocal current_step
        current_step = step

    with console.status(f"{current_step}...", spinner="dots", spinner_style="bold #0080ff") as status:
        def _wrapped_on_step(step: str) -> None:
            _on_step(step)
            status.update(f"{current_step}...")

        result = service.test_compute(name, on_step=_wrapped_on_step)
        status.update("Finalizing...")
    return result


@compute_app.command("list", help="List available compute profiles.")
def list_compute(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    names = service.list_compute_names()
    if json_output:
        _emit({"profiles": names}, as_json=True)
        return
    console.print("[bold]Compute Profiles[/bold]")
    if not names:
        console.print("- (none)")
        return
    for name in names:
        console.print(f"- {name}")


@compute_app.command("show", help="Show one compute profile.")
def show_compute(
    name: str = typer.Argument(..., help="Compute profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        profile = service.load_compute(name)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(profile.model_dump(mode="json", exclude_none=True), as_json=True)
        return
    _render_profile(profile)


@compute_app.command("add", help="Create a compute profile and test it immediately.")
def add_compute(json_output: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    service = _service()
    try:
        profile = _build_profile_from_prompt()
        saved = service.save_compute(profile)
        result = _run_compute_test(service, profile.name)
    except (ValidationError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit({"saved": saved, "test": result.model_dump(mode="json")}, as_json=True)
        return
    console.print(f"[green]Saved compute profile[/green] {saved['name']}")
    _render_test(result)


@compute_app.command("edit", help="Edit a compute profile and retest it.")
def edit_compute(
    name: str = typer.Argument(..., help="Compute profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        existing = service.load_compute(name)
        updated = _build_profile_from_prompt(existing)
        saved = service.save_compute(updated, force=True)
        result = _run_compute_test(service, updated.name)
    except (ValidationError, FileNotFoundError, RuntimeError) as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit({"saved": saved, "test": result.model_dump(mode="json")}, as_json=True)
        return
    console.print(f"[green]Updated compute profile[/green] {saved['name']}")
    _render_test(result)


@compute_app.command("delete", help="Delete a compute profile.")
def delete_compute(
    name: str = typer.Argument(..., help="Compute profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    if not typer.confirm(f"Delete compute profile '{name}'?", default=False):
        raise typer.Exit(code=1)
    try:
        result = service.delete_compute(name)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(result, as_json=True)
        return
    console.print(f"Deleted compute profile [bold]{result['name']}[/bold].")


@compute_app.command("test", help="Run connectivity and environment checks for a compute profile.")
def test_compute(
    name: str = typer.Argument(..., help="Compute profile name."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _service()
    try:
        result = _run_compute_test(service, name)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))
    if json_output:
        _emit(result.model_dump(mode="json"), as_json=True)
        return
    _render_test(result)
