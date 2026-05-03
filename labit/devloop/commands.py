from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

from rich.panel import Panel
from rich.prompt import Prompt

from labit.commands.context import ChatContext
from labit.commands.rendering import render_dev_decision, render_dev_status
from labit.devloop.engine import run_dev_loop
from labit.devloop.git_ops import (
    create_dev_worktree,
    git_output,
    list_scope_dirty_files,
    remove_dev_worktree,
    resolve_dev_scope,
)
from labit.devloop.models import DevLoopSession
from labit.paths import RepoPaths

RunStreamingTurn = Callable[..., object]


def handle_dev_command(
    *,
    ctx: ChatContext,
    argument: str,
    active_dev: DevLoopSession | None,
    run_streaming_turn: RunStreamingTurn,
) -> DevLoopSession | None:
    console = ctx.console
    current_session = ctx.session

    sub = argument.strip()
    sub_parts = sub.split(maxsplit=1)
    dev_action = sub_parts[0] if sub_parts else ""
    dev_argument = sub_parts[1].strip() if len(sub_parts) > 1 else ""

    if dev_action == "status":
        if active_dev is None:
            console.print("[dim]No active dev loop.[/dim]")
        else:
            render_dev_status(console, active_dev)
        return active_dev

    if dev_action == "stop":
        if active_dev is None:
            console.print("[dim]No active dev loop to stop.[/dim]")
            return None
        active_dev.status = "stopped"
        console.print("[bold yellow]Dev loop stopped.[/bold yellow]")
        if active_dev.dev_branch:
            console.print(Panel(
                (
                    f"[bold]Branch[/bold]: {active_dev.dev_branch}\n"
                    f"[bold]Original[/bold]: {active_dev.original_branch}\n\n"
                    f"[bold]Branch repo[/bold]: {active_dev.branch_repo_root or active_dev.scope_git_root}\n"
                    f"[bold]Worktree[/bold]: {active_dev.worktree_path or '(none)'}\n\n"
                    "Use [bold]/dev finish[/bold] to merge, keep, or discard safely."
                ),
                title="[bold]Dev Branch[/bold]",
                border_style="blue",
            ))
        return None

    if dev_action == "finish":
        return finish_dev_loop(ctx, active_dev)

    if dev_action == "continue":
        if active_dev is None:
            console.print("[bold red]No active dev loop. Use /dev start <task> first.[/bold red]")
            return None
        if active_dev.status == "waiting_decision":
            console.print("[bold red]Decision pending. Reply with your choice first.[/bold red]")
            render_dev_decision(console, active_dev)
            return active_dev
        if active_dev.status in ("completed", "stopped"):
            console.print("[dim]Dev loop already finished. Use /dev start for a new one.[/dim]")
            return None
        return run_dev_loop(
            service=ctx.service,
            session=current_session,
            dev_session=active_dev,
            console=console,
            run_streaming_turn=run_streaming_turn,
        )

    if dev_action == "start":
        return start_dev_loop(
            ctx=ctx,
            dev_argument=dev_argument,
            active_dev=active_dev,
            run_streaming_turn=run_streaming_turn,
        )

    console.print(
        "[bold red]Usage:[/bold red] /dev start <task> | /dev status | /dev continue | /dev finish | /dev stop"
    )
    return active_dev


def finish_dev_loop(ctx: ChatContext, active_dev: DevLoopSession | None) -> DevLoopSession | None:
    console = ctx.console
    if active_dev is None:
        console.print("[dim]No active dev loop.[/dim]")
        return None
    if not active_dev.dev_branch:
        console.print("[dim]No dev branch to finish (--branch off was used).[/dim]")
        return None

    console.print(Panel(
        (
            f"[bold]Branch[/bold]: {active_dev.dev_branch}\n"
            f"[bold]Original[/bold]: {active_dev.original_branch}\n"
            f"[bold]Worktree[/bold]: {active_dev.worktree_path or '(none)'}\n"
            f"[bold]Rounds[/bold]: {active_dev.current_round}\n\n"
            "[bold]What would you like to do?[/bold]\n"
            f"  [bold cyan]1[/bold cyan] — Merge into {active_dev.original_branch}\n"
            "  [bold cyan]2[/bold cyan] — Keep branch and remove worktree\n"
            "  [bold cyan]3[/bold cyan] — Discard branch and remove worktree\n"
        ),
        title="[bold green]Finish Dev Loop[/bold green]",
        border_style="green",
    ))
    try:
        choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    except (KeyboardInterrupt, EOFError):
        return active_dev

    finish_cwd = active_dev.branch_repo_root or active_dev.scope_git_root or str(RepoPaths.discover().root)
    finish_repo = Path(finish_cwd)
    finish_worktree = Path(active_dev.worktree_path) if active_dev.worktree_path else None
    branch_check = git_output("git", "branch", "--list", active_dev.dev_branch, cwd=finish_repo)
    if not branch_check:
        console.print(
            f"[bold red]Branch '{active_dev.dev_branch}' not found in {finish_cwd}.[/bold red]\n"
            "This can happen if the dev session was created before a scope change.\n"
            "Refusing to guess which repo should be merged."
        )
        return None

    if choice == "1":
        subprocess.run(["git", "checkout", active_dev.original_branch], capture_output=True, cwd=finish_cwd)
        result = subprocess.run(
            ["git", "merge", active_dev.dev_branch, "--no-edit"],
            capture_output=True,
            text=True,
            cwd=finish_cwd,
        )
        if result.returncode == 0:
            console.print(f"[bold green]Merged {active_dev.dev_branch} into {active_dev.original_branch}.[/bold green]")
            if finish_worktree is not None:
                ok, msg = remove_dev_worktree(finish_repo, finish_worktree)
                if ok:
                    console.print(f"[dim]Removed worktree {finish_worktree}.[/dim]")
                else:
                    console.print(f"[yellow]Worktree cleanup failed:[/yellow] {msg}")
        else:
            console.print(f"[bold red]Merge failed:[/bold red] {result.stderr.strip()}")
            console.print(f"You are now on [bold]{active_dev.original_branch}[/bold]. Resolve manually.")
    elif choice == "2":
        subprocess.run(["git", "checkout", active_dev.original_branch], capture_output=True, cwd=finish_cwd)
        if finish_worktree is not None:
            ok, msg = remove_dev_worktree(finish_repo, finish_worktree)
            if not ok:
                console.print(f"[yellow]Worktree cleanup failed:[/yellow] {msg}")
        console.print(f"[bold]Branch {active_dev.dev_branch} is preserved.[/bold]")
    elif choice == "3":
        subprocess.run(["git", "checkout", active_dev.original_branch], capture_output=True, cwd=finish_cwd)
        if finish_worktree is not None:
            ok, msg = remove_dev_worktree(finish_repo, finish_worktree)
            if not ok:
                console.print(f"[yellow]Worktree cleanup failed:[/yellow] {msg}")
        subprocess.run(["git", "branch", "-D", active_dev.dev_branch], capture_output=True, cwd=finish_cwd)
        console.print(f"[bold red]Discarded branch {active_dev.dev_branch}.[/bold red]")
    return None


def start_dev_loop(
    *,
    ctx: ChatContext,
    dev_argument: str,
    active_dev: DevLoopSession | None,
    run_streaming_turn: RunStreamingTurn,
) -> DevLoopSession | None:
    console = ctx.console
    current_session = ctx.session
    if not dev_argument:
        console.print("[bold red]Usage:[/bold red] /dev start <task description>")
        return active_dev
    if active_dev is not None and active_dev.status == "active":
        console.print("[bold red]Dev loop already active. /dev stop first.[/bold red]")
        return active_dev
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] Session not attached to a project.")
        return active_dev

    task_text = dev_argument
    max_rounds = 6
    test_mode = "auto"
    mr_match = re.search(r"--max-rounds\s+(\d+)", task_text)
    if mr_match:
        max_rounds = min(int(mr_match.group(1)), 15)
        task_text = task_text[:mr_match.start()] + task_text[mr_match.end():]
    tm_match = re.search(r"--test\s+(off|auto|on)", task_text)
    if tm_match:
        test_mode = tm_match.group(1)
        task_text = task_text[:tm_match.start()] + task_text[tm_match.end():]
    branch_mode = "auto"
    bm_match = re.search(r"--branch\s+(off|auto)", task_text)
    if bm_match:
        branch_mode = bm_match.group(1)
        task_text = task_text[:bm_match.start()] + task_text[bm_match.end():]
    task_text = task_text.strip()
    if not task_text:
        console.print("[bold red]Usage:[/bold red] /dev start <task description>")
        return active_dev

    participants = current_session.participants
    if len(participants) >= 2:
        writer_name = participants[0].name
        reviewer_name = participants[1].name
    else:
        writer_name = participants[0].name
        reviewer_name = participants[0].name

    scope_label, scope_pathspecs, scope_git_root = resolve_dev_scope(current_session)
    initial_dirty_files = list_scope_dirty_files(scope_pathspecs, scope_git_root)

    allow_dirty = "--allow-dirty" in dev_argument
    if "--allow-dirty" in dev_argument:
        task_text = re.sub(r"--allow-dirty\s*", "", task_text).strip()
    if branch_mode == "off" and initial_dirty_files and not allow_dirty:
        console.print(
            "[bold red]Error:[/bold red] Working tree has uncommitted changes. "
            "Commit or stash them first, or use [bold]--allow-dirty[/bold] to proceed anyway."
        )
        preview = "\n".join(f"- {item}" for item in initial_dirty_files[:10])
        console.print(Panel(preview, title="Dirty Files", border_style="red"))
        return active_dev
    if branch_mode == "auto" and initial_dirty_files:
        preview = "\n".join(f"- {item}" for item in initial_dirty_files[:10])
        console.print(Panel(
            preview,
            title="[yellow]Original Checkout Has Dirty Files[/yellow]",
            border_style="yellow",
        ))
        console.print(
            "[yellow]Note:[/yellow] /dev will use a clean isolated worktree from HEAD; "
            "the dirty files above will not be included unless committed first."
        )

    dev_branch = ""
    original_branch = ""
    branch_repo_root = scope_git_root
    worktree_path = Path("")
    active_scope_git_root = scope_git_root
    active_scope_pathspecs = scope_pathspecs
    if branch_mode == "auto":
        try:
            dev_branch, original_branch, worktree_path = create_dev_worktree(
                task=task_text,
                git_root=scope_git_root,
                project=current_session.project,
            )
        except Exception as exc:
            console.print(f"[bold red]Failed to create dev worktree:[/bold red] {exc}")
            return active_dev
        active_scope_git_root = worktree_path
        active_scope_pathspecs = ["."]
        initial_dirty_files = []
    else:
        try:
            original_branch = git_output("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=scope_git_root)
        except Exception:
            pass

    dev_session = DevLoopSession(
        task=task_text,
        writer_name=writer_name,
        reviewer_name=reviewer_name,
        max_rounds=max_rounds,
        test_mode=test_mode,
        scope_label=scope_label,
        scope_pathspecs=active_scope_pathspecs,
        scope_git_root=str(active_scope_git_root),
        branch_repo_root=str(branch_repo_root),
        worktree_path=str(worktree_path) if str(worktree_path) != "." else "",
        initial_dirty_files=initial_dirty_files,
        dev_branch=dev_branch,
        original_branch=original_branch,
    )

    console.print(Panel(
        (
            f"[bold]Task[/bold]: {task_text}\n"
            f"[bold]Writer[/bold]: {writer_name}\n"
            f"[bold]Reviewer[/bold]: {reviewer_name}\n"
            f"[bold]Max rounds[/bold]: {max_rounds}\n"
            f"[bold]Test mode[/bold]: {test_mode}\n"
            f"[bold]Scope[/bold]: {scope_label}\n"
            f"[bold]Branch repo[/bold]: {branch_repo_root}\n"
            f"[bold]Editable workspace[/bold]: {active_scope_git_root}\n"
            f"[bold]Branch[/bold]: {dev_branch + ' (from ' + original_branch + ')' if dev_branch else original_branch + ' (no worktree isolation)'}"
        ),
        title="[bold green]Dev Loop Starting[/bold green]",
        border_style="green",
    ))

    dev_session = run_dev_loop(
        service=ctx.service,
        session=current_session,
        dev_session=dev_session,
        console=console,
        run_streaming_turn=run_streaming_turn,
    )
    if dev_session.dev_branch and dev_session.status in ("completed", "stopped"):
        console.print(Panel(
            (
                f"[bold]Branch[/bold]: {dev_session.dev_branch}\n"
                f"[bold]Original[/bold]: {dev_session.original_branch}\n"
                f"[bold]Branch repo[/bold]: {dev_session.branch_repo_root or dev_session.scope_git_root}\n"
                f"[bold]Worktree[/bold]: {dev_session.worktree_path or '(none)'}\n"
                f"[bold]Rounds[/bold]: {dev_session.current_round}\n\n"
                "Use [bold]/dev finish[/bold] to merge, keep, or discard the branch."
            ),
            title="[bold]Dev Loop Finished[/bold]",
            border_style="blue",
        ))
    return dev_session
