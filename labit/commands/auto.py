from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from labit.automation import AutoActor, AutoIterationEngine
from labit.commands.context import ChatContext


def handle_auto_command(
    *,
    ctx: ChatContext,
    argument: str,
) -> None:
    console = ctx.console
    paths = ctx.paths
    current_session = ctx.session
    auto_parts = argument.split(maxsplit=1)
    auto_action = auto_parts[0].strip().lower() if auto_parts and auto_parts[0].strip() else "status"
    auto_argument = auto_parts[1].strip() if len(auto_parts) > 1 else ""
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return

    engine = AutoIterationEngine(paths)
    if auto_action in {"status", ""}:
        session_record, iterations = engine.status(current_session.project)
        render_auto_status(console, session_record, iterations)
        return
    if auto_action == "stop":
        try:
            stopped = engine.stop_session(current_session.project)
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
        console.print(f"[green]Auto session stopped.[/green] [dim]{stopped.project}[/dim]")
        return
    if auto_action == "start":
        design_doc = ""
        constraint = ""
        success = ""
        doc_path_str = auto_argument.strip()
        if doc_path_str and "||" not in doc_path_str:
            doc_path = Path(doc_path_str).expanduser()
            if not doc_path.is_absolute():
                doc_path = paths.vault_projects_dir / current_session.project / doc_path_str
            if doc_path.exists() and doc_path.is_file():
                design_doc = doc_path.read_text(encoding="utf-8").strip()
                constraint, success = parse_design_doc(design_doc)
            else:
                console.print(f"[bold red]Error:[/bold red] Design doc not found: {doc_path}")
                console.print("[dim]Usage: /auto start <design_doc_path> or /auto start <constraint> || <success>[/dim]")
                return
        elif "||" in auto_argument:
            constraint, _, success = auto_argument.partition("||")
            constraint = constraint.strip()
            success = success.strip()
        if not design_doc and (not constraint or not success):
            console.print("[bold red]Usage:[/bold red] /auto start <design_doc_path> or /auto start <constraint> || <success criteria>")
            return
        try:
            session_record = engine.start_session(
                project=current_session.project,
                constraint=constraint,
                success_criteria=success,
                design_doc=design_doc,
            )
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return
        body = (
            f"[bold]Constraint[/bold]: {session_record.constraint}\n"
            f"[bold]Success[/bold]: {session_record.success_criteria}\n"
            f"[bold]Supervisor[/bold]: {session_record.supervisor_agent}\n"
            f"[bold]Rounds[/bold]: {session_record.max_iterations}\n"
            f"[bold]Poll Seconds[/bold]: {session_record.poll_seconds}"
        )
        if design_doc:
            body += f"\n[bold]Design Doc[/bold]: {doc_path_str}"
        console.print(
            Panel(body, title="[bold green]Auto Iteration Started[/bold green]", border_style="green")
        )
        console.print("[dim]Run /auto run [N] to execute rounds, /auto log [N] for detail, /auto stop to halt.[/dim]")
        return
    if auto_action == "run":
        rounds = 1
        if auto_argument:
            try:
                rounds = max(1, int(auto_argument))
            except ValueError:
                console.print("[bold red]Usage:[/bold red] /auto run [N]")
                return
        try:
            session_record, _ = engine.status(current_session.project)
            if session_record is None:
                console.print("[bold red]Error:[/bold red] No active auto session. Use /auto start first.")
                return
            actors = auto_iteration_actors(current_session, supervisor_agent=session_record.supervisor_agent)
            for _ in range(rounds):
                if session_record.current_iteration >= session_record.max_iterations:
                    console.print("[yellow]Auto session already hit max_iterations.[/yellow]")
                    break
                with console.status("[bold blue]Running auto-iteration round...[/bold blue]"):
                    entry = engine.run_iteration(project=current_session.project, actors=actors)
                render_auto_log(console, [entry], n=1)
                session_record, _ = engine.status(current_session.project)
                if session_record is None or session_record.status.value in {"done", "needs_human", "stopped"}:
                    break
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
        return
    if auto_action == "log":
        n = 3
        if auto_argument:
            try:
                n = max(1, int(auto_argument))
            except ValueError:
                pass
        _, iterations = engine.status(current_session.project)
        render_auto_log(console, iterations, n=n)
        return
    console.print("[bold red]Usage:[/bold red] /auto start <doc_path> | /auto run [N] | /auto log [N] | /auto status | /auto stop")


def auto_iteration_actors(session, supervisor_agent: str = "codex") -> list[AutoActor]:
    participants = list(session.participants)
    if not participants:
        raise ValueError("No chat participants available for auto-iteration.")
    supervisor_idx = next(
        (i for i, p in enumerate(participants) if p.name == supervisor_agent),
        None,
    )
    if supervisor_idx is not None and supervisor_idx != 0:
        participants[0], participants[supervisor_idx] = participants[supervisor_idx], participants[0]
    if len(participants) == 1:
        participants = [participants[0], participants[0], participants[0]]
    elif len(participants) == 2:
        participants = [participants[0], participants[0], participants[1]]
    return [
        AutoActor(name="supervisor", provider=participants[0].provider),
        AutoActor(name="worker_a", provider=participants[1].provider),
        AutoActor(name="worker_b", provider=participants[2].provider),
    ]


def parse_design_doc(text: str) -> tuple[str, str]:
    constraint = ""
    success = ""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        heading_match = re.match(r"^#{1,3}\s+(.+)", line)
        if heading_match:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = heading_match.group(1).strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()

    for key in [
        "constraint",
        "constraints",
        "rules",
        "bounds",
        "limitations",
        "hard project requirements",
        "hard requirements",
        "requirements",
        "convergence policy",
        "convergence rules",
    ]:
        if key in sections and sections[key]:
            constraint = sections[key]
            break

    for key in [
        "success criteria",
        "success",
        "goal",
        "goals",
        "objective",
        "objectives",
        "done when",
        "current design objective",
        "design objective",
        "bottom line",
    ]:
        if key in sections and sections[key]:
            success = sections[key]
            break

    if not constraint:
        constraint = "(see design doc)"
    if not success:
        success = "(see design doc)"

    return constraint, success


def render_auto_status(console: Console, session_record, iterations) -> None:
    if session_record is None:
        console.print("[dim]No active auto-iteration session.[/dim]")
        return

    status_style = {
        "running": "green",
        "waiting": "yellow",
        "needs_human": "red",
        "done": "cyan",
        "stopped": "dim",
    }.get(session_record.status.value, "white")
    console.print(
        Panel(
            (
                f"[bold]Project[/bold]: {session_record.project}\n"
                f"[bold]Status[/bold]: [{status_style}]{session_record.status.value}[/{status_style}]\n"
                f"[bold]Supervisor[/bold]: {session_record.supervisor_agent}\n"
                f"[bold]Iteration[/bold]: {session_record.current_iteration}/{session_record.max_iterations}\n"
                f"[bold]Updated[/bold]: {session_record.updated_at}\n"
                f"\n[bold]Constraint[/bold]: {session_record.constraint}\n"
                f"[bold]Success[/bold]: {session_record.success_criteria}"
            ),
            title="[bold green]Auto Session[/bold green]",
            border_style="green",
        )
    )

    if session_record.last_observation_summary:
        console.print(
            Panel(session_record.last_observation_summary, title="Latest Observation", border_style="blue")
        )
    if session_record.last_decision_summary:
        console.print(
            Panel(session_record.last_decision_summary, title="Latest Decision", border_style="cyan")
        )

    if not iterations:
        console.print("[dim]No iterations recorded yet.[/dim]")
        return
    table = Table(title="Recent Iterations", border_style="dim", show_lines=True)
    table.add_column("Iter", style="bold", width=4)
    table.add_column("Trigger", width=18)
    table.add_column("Action", width=12)
    table.add_column("Decision", min_width=30)
    table.add_column("Workers", min_width=20)
    for entry in iterations:
        worker_info = ""
        if entry.worker_results:
            worker_info = "\n".join(
                f"{wr.worker}[{wr.status}]: {wr.summary[:80]}" for wr in entry.worker_results
            )
        elif entry.worker_tasks:
            worker_info = "\n".join(f"{wt.worker}: {wt.title}" for wt in entry.worker_tasks)
        if entry.discussion:
            disc = "\n".join(f"{n.actor}: {n.summary[:60]}" for n in entry.discussion)
            worker_info = f"{worker_info}\n---\n{disc}" if worker_info else disc
        table.add_row(
            str(entry.iteration),
            entry.trigger,
            entry.action.value,
            entry.decision_summary[:120],
            worker_info[:300] if worker_info else "-",
        )
    console.print(table)


def render_auto_log(console: Console, iterations: list, n: int = 3) -> None:
    if not iterations:
        console.print("[dim]No iterations recorded yet.[/dim]")
        return
    for entry in iterations[-n:]:
        header = f"Iteration {entry.iteration} | {entry.trigger} | {entry.action.value}"
        if entry.human_needed:
            header += " | [bold red]NEEDS HUMAN[/bold red]"
        if entry.success_reached:
            header += " | [bold green]SUCCESS[/bold green]"

        parts = [f"[bold]Decision[/bold]: {entry.decision_summary}"]

        if entry.observation_summary:
            parts.append(f"\n[bold]Observation[/bold]:\n{entry.observation_summary}")

        if entry.worker_tasks:
            parts.append("\n[bold]Worker Tasks[/bold]:")
            for wt in entry.worker_tasks:
                parts.append(f"  {wt.worker}: {wt.title}\n    {wt.instructions[:200]}")

        if entry.worker_results:
            parts.append("\n[bold]Worker Results[/bold]:")
            for wr in entry.worker_results:
                parts.append(f"  {wr.worker} [{wr.status}]: {wr.summary}")
                if wr.actions_taken:
                    parts.append("    Actions: " + ", ".join(wr.actions_taken[:5]))
                if wr.outputs:
                    parts.append("    Outputs: " + ", ".join(wr.outputs[:5]))
                if wr.follow_up:
                    parts.append(f"    Follow-up: {wr.follow_up}")

        if entry.discussion:
            parts.append("\n[bold]Discussion[/bold]:")
            for note in entry.discussion:
                parts.append(f"  {note.actor}: {note.summary}")
                if note.evidence:
                    for ev in note.evidence[:3]:
                        parts.append(f"    - {ev}")
                if note.next_step:
                    parts.append(f"    Next: {note.next_step}")

        console.print(Panel("\n".join(parts), title=header, border_style="cyan"))
        console.print()
