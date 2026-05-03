from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

from labit.commands.context import ChatContext, session_evidence_refs
from labit.commands.rendering import (
    print_launch_exp_hints,
    render_launch_exp_status,
    render_task_breakdown,
    render_task_detail,
)
from labit.context.events import SessionEventKind
from labit.experiments.executors.ssh import SSHExecutor
from labit.experiments.models import (
    ExecutionBackend,
    FrozenLaunchSpec,
    LaunchArtifact,
    LaunchExpPhase,
    LaunchExpSession,
)
from labit.experiments.planner import ExperimentPlanner
from labit.experiments.service import ExperimentService
from labit.hypotheses.service import HypothesisService


@dataclass(slots=True)
class LaunchExpCommandResult:
    active_launch_exp: LaunchExpSession | None


def handle_launch_exp_command(
    *,
    ctx: ChatContext,
    argument: str,
    active_launch_exp: LaunchExpSession | None,
) -> LaunchExpCommandResult:
    console = ctx.console
    current_session = ctx.session
    sub_arg = argument.strip()
    if not current_session.project:
        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
        return LaunchExpCommandResult(active_launch_exp)

    experiment_service = ExperimentService(ctx.paths)

    if sub_arg == "status":
        if active_launch_exp is None:
            console.print("[dim]Not in experiment planning mode.[/dim]")
        else:
            render_launch_exp_status(console, active_launch_exp)
        return LaunchExpCommandResult(active_launch_exp)

    if sub_arg == "approve-tasks":
        return LaunchExpCommandResult(_approve_tasks(ctx, experiment_service, active_launch_exp))

    if sub_arg.startswith("approve-task"):
        task_id = sub_arg.replace("approve-task", "").strip()
        return LaunchExpCommandResult(_approve_task(ctx, experiment_service, active_launch_exp, task_id))

    if sub_arg.startswith("reopen-task"):
        task_id = sub_arg.replace("reopen-task", "").strip()
        return LaunchExpCommandResult(_reopen_task(ctx, experiment_service, active_launch_exp, task_id))

    if sub_arg == "generate-script":
        return LaunchExpCommandResult(_generate_script(ctx, experiment_service, active_launch_exp))

    if sub_arg.startswith("run-task"):
        task_id = sub_arg.replace("run-task", "").strip()
        return LaunchExpCommandResult(_run_task(ctx, experiment_service, active_launch_exp, task_id))

    if sub_arg == "done":
        return LaunchExpCommandResult(_finish_launch_exp(ctx, experiment_service, active_launch_exp))

    if sub_arg.startswith("resume"):
        exp_id = sub_arg.replace("resume", "").strip()
        return LaunchExpCommandResult(_resume_launch_exp(ctx, experiment_service, active_launch_exp, exp_id))

    return LaunchExpCommandResult(_start_launch_exp(ctx, experiment_service, active_launch_exp, sub_arg))


def handle_launch_exp_instruction(
    *,
    ctx: ChatContext,
    raw: str,
    active_launch_exp: LaunchExpSession,
) -> LaunchExpSession:
    console = ctx.console
    current_session = ctx.session
    exp_service = ExperimentService(ctx.paths)
    planner = ExperimentPlanner(ctx.paths)
    first_participant = current_session.participants[0] if current_session.participants else None
    provider = first_participant.provider if first_participant else None
    phase = active_launch_exp.phase

    try:
        exp_service.log_user_instruction(active_launch_exp, raw)

        if phase == LaunchExpPhase.TASK_BREAKDOWN:
            hyp_detail = HypothesisService(ctx.paths).load_hypothesis(
                current_session.project, active_launch_exp.hypothesis_id
            )
            with console.status("[bold blue]Revising task breakdown...[/bold blue]"):
                revised_tasks = planner.revise_task_breakdown(
                    current_tasks=active_launch_exp.task_plans,
                    user_instruction=raw,
                    hypothesis_title=hyp_detail.record.title,
                    hypothesis_claim=hyp_detail.record.claim,
                    interaction_log=exp_service.planning_interaction_excerpt(active_launch_exp),
                    provider=provider,
                )
            dep_err = exp_service.validate_dependency_graph(revised_tasks)
            if dep_err:
                console.print(f"[yellow]Warning:[/yellow] {dep_err}")
            active_launch_exp = exp_service.save_task_plans(active_launch_exp, revised_tasks)
            exp_service.log_agent_revision(
                active_launch_exp,
                f"Revised task breakdown: {len(revised_tasks)} tasks",
                first_participant.name if first_participant else "",
            )
            render_task_breakdown(console, active_launch_exp)

        elif phase == LaunchExpPhase.TASK_PLANNING:
            ct = active_launch_exp.current_task
            if ct is None:
                console.print("[dim]No task to plan. Use /launch-exp approve-tasks or /launch-exp done.[/dim]")
                return active_launch_exp
            hyp_detail = HypothesisService(ctx.paths).load_hypothesis(
                current_session.project, active_launch_exp.hypothesis_id
            )
            code_tree = exp_service.get_code_tree(current_session.project)
            with console.status(f"[bold blue]Planning {ct.id}: {ct.name}...[/bold blue]"):
                detailed_task = planner.plan_task_detail(
                    task=ct,
                    all_tasks=active_launch_exp.task_plans,
                    hypothesis_title=hyp_detail.record.title,
                    hypothesis_claim=hyp_detail.record.claim,
                    code_tree=code_tree,
                    user_instruction=raw,
                    interaction_log=exp_service.planning_interaction_excerpt(active_launch_exp),
                    provider=provider,
                )
            active_launch_exp = exp_service.update_task_detail(active_launch_exp, detailed_task)
            exp_service.log_agent_revision(
                active_launch_exp,
                f"Planned {ct.id}: {ct.name}",
                first_participant.name if first_participant else "",
            )
            render_task_detail(console, detailed_task)

        elif phase == LaunchExpPhase.SCRIPT_GENERATION:
            code_tree = exp_service.get_code_context(current_session.project)
            tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
            workdir, setup_summary = _execution_prompt_context(exp_service, current_session.project)
            with console.status("[bold blue]Revising run.sh...[/bold blue]"):
                result = planner.revise_run_sh(
                    current_run_sh=active_launch_exp.run_sh_content,
                    current_config_yaml=active_launch_exp.config_yaml_content,
                    tasks_json=tasks_json,
                    user_instruction=raw,
                    code_tree=code_tree,
                    workdir=workdir,
                    setup_script_summary=setup_summary,
                    provider=provider,
                )
            active_launch_exp = exp_service.save_script(
                active_launch_exp,
                result["run_sh"],
                result["config_yaml"],
            )
            exp_service.log_agent_revision(
                active_launch_exp,
                result["summary"],
                first_participant.name if first_participant else "",
            )
            console.print(
                Panel(
                    f"[bold]Summary[/bold]: {result['summary']}\n"
                    f"[bold]run.sh[/bold]: {len(result['run_sh'].splitlines())} lines",
                    title="[bold green]Script Revised[/bold green]",
                    border_style="green",
                )
            )

    except KeyboardInterrupt:
        console.print("[dim italic]Interrupted.[/dim italic]")
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
    return active_launch_exp


def _approve_tasks(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
) -> LaunchExpSession | None:
    console = ctx.console
    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.TASK_BREAKDOWN:
        console.print("[bold red]Error:[/bold red] Not in task breakdown phase.")
        return active_launch_exp
    if not active_launch_exp.task_plans:
        console.print("[bold red]Error:[/bold red] No tasks to approve.")
        return active_launch_exp
    dep_err = experiment_service.validate_dependency_graph(active_launch_exp.task_plans)
    if dep_err:
        console.print(f"[bold red]Dependency error:[/bold red] {dep_err}")
        return active_launch_exp
    active_launch_exp = experiment_service.approve_task_list(active_launch_exp)
    ct = active_launch_exp.current_task
    console.print(
        Panel(
            f"Task list approved ({len(active_launch_exp.task_plans)} tasks).\n"
            f"Now planning task details. Starting with: [bold]{ct.id}: {ct.name}[/bold]"
            if ct
            else "All tasks already approved.",
            title="[bold green]Phase: Task Planning[/bold green]",
            border_style="green",
        )
    )
    print_launch_exp_hints(console, active_launch_exp)
    return active_launch_exp


def _approve_task(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
    task_id: str,
) -> LaunchExpSession | None:
    console = ctx.console
    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.TASK_PLANNING:
        console.print("[bold red]Error:[/bold red] Not in task planning phase.")
        return active_launch_exp
    if not task_id:
        ct = active_launch_exp.current_task
        task_id = ct.id if ct else ""
    if not task_id:
        console.print("[bold red]Error:[/bold red] No task to approve.")
        return active_launch_exp
    active_launch_exp = experiment_service.approve_task(active_launch_exp, task_id)
    if active_launch_exp.phase == LaunchExpPhase.SCRIPT_GENERATION:
        console.print(
            Panel(
                "All tasks approved! Ready to generate run.sh.\n"
                "Type feedback or use [bold]/launch-exp generate-script[/bold] to generate.",
                title="[bold green]Phase: Script Generation[/bold green]",
                border_style="green",
            )
        )
    else:
        ct = active_launch_exp.current_task
        console.print(
            f"[green]Task {task_id} approved.[/green] Next: [bold]{ct.id}: {ct.name}[/bold]"
            if ct
            else f"[green]Task {task_id} approved.[/green]"
        )
    print_launch_exp_hints(console, active_launch_exp)
    return active_launch_exp


def _reopen_task(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
    task_id: str,
) -> LaunchExpSession | None:
    console = ctx.console
    if active_launch_exp is None:
        console.print("[bold red]Error:[/bold red] Not in experiment planning mode.")
        return active_launch_exp
    if not task_id:
        console.print("[bold red]Usage:[/bold red] /launch-exp reopen-task <task_id>")
        return active_launch_exp
    try:
        active_launch_exp = experiment_service.reopen_task(active_launch_exp, task_id)
        console.print(f"[yellow]Task {task_id} reopened.[/yellow] Now re-planning it.")
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
    return active_launch_exp


def _generate_script(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
) -> LaunchExpSession | None:
    console = ctx.console
    current_session = ctx.session
    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
        console.print("[bold red]Error:[/bold red] Not in script generation phase. Approve all tasks first.")
        return active_launch_exp
    try:
        hyp_detail = HypothesisService(ctx.paths).load_hypothesis(
            current_session.project,
            active_launch_exp.hypothesis_id,
        )
        code_context = experiment_service.get_code_context(current_session.project)
        planner = ExperimentPlanner(ctx.paths)
        first_participant = current_session.participants[0] if current_session.participants else None
        provider = first_participant.provider if first_participant else None
        workdir, setup_summary = _execution_prompt_context(experiment_service, current_session.project)
        with console.status("[bold cyan]Generating run.sh...[/bold cyan]"):
            result = planner.generate_run_sh(
                session=active_launch_exp,
                hypothesis_title=hyp_detail.record.title,
                hypothesis_claim=hyp_detail.record.claim,
                code_tree=code_context,
                workdir=workdir,
                setup_script_summary=setup_summary,
                provider=provider,
            )
        active_launch_exp = experiment_service.save_script(
            active_launch_exp,
            result["run_sh"],
            result["config_yaml"],
        )
        console.print(
            Panel(
                f"[bold]Summary[/bold]: {result['summary']}\n\n"
                f"[bold]run.sh[/bold] ({len(result['run_sh'].splitlines())} lines)\n"
                + ("" if not result["config_yaml"] else f"[bold]config.yaml[/bold] generated\n")
                + "\nType feedback to revise, or [bold]/launch-exp done[/bold] to finalize.",
                title="[bold green]Script Generated[/bold green]",
                border_style="green",
            )
        )
        print_launch_exp_hints(console, active_launch_exp)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
    return active_launch_exp


def _run_task(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
    task_id: str,
) -> LaunchExpSession | None:
    console = ctx.console
    current_session = ctx.session
    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
        console.print("[bold red]Error:[/bold red] Not in script generation phase. Use /launch-exp resume <experiment_id> first for an existing experiment.")
        return active_launch_exp
    if not task_id:
        console.print("[bold red]Usage:[/bold red] /launch-exp run-task <task_id>")
        return active_launch_exp
    known_task_ids = {t.id for t in active_launch_exp.task_plans}
    if known_task_ids and task_id not in known_task_ids:
        console.print(f"[bold red]Error:[/bold red] Unknown task '{task_id}'. Known tasks: {', '.join(sorted(known_task_ids))}")
        return active_launch_exp
    if not active_launch_exp.run_sh_content:
        console.print("[bold red]Error:[/bold red] No run.sh available. Use /launch-exp generate-script first.")
        return active_launch_exp

    try:
        code_context = experiment_service.get_code_context(current_session.project)
        planner = ExperimentPlanner(ctx.paths)
        first_participant = current_session.participants[0] if current_session.participants else None
        provider = first_participant.provider if first_participant else None
        tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
        workdir, setup_summary = _execution_prompt_context(experiment_service, current_session.project)

        resume_issues = run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
        if resume_issues:
            console.print("[dim]run.sh does not expose task-level resume controls; asking agent to add them before submitting...[/dim]")
            instruction = (
                f"Revise this run.sh so it supports task-level resume/retry controls, then keep the existing experiment logic intact.\n"
                f"Required contract:\n"
                f"- LABIT_ONLY_TASK={task_id} runs only task {task_id} after verifying dependency checkpoints.\n"
                f"- LABIT_START_AT={task_id} skips tasks before {task_id}.\n"
                f"- LABIT_FORCE_TASK={task_id} reruns {task_id} even if its checkpoint exists.\n"
                f"- Default reruns must be non-destructive: do not delete prior outputs unless LABIT_FORCE_CLEAN=1.\n"
                f"- Add should_run_task() and require_checkpoint() bash helpers.\n"
                f"Current missing/unsafe pieces: {', '.join(resume_issues)}.\n"
                f"Preserve the standard experiment_results.json output contract."
            )
            with console.status("[bold cyan]Adding task-level resume support to run.sh...[/bold cyan]"):
                result = planner.revise_run_sh(
                    current_run_sh=active_launch_exp.run_sh_content,
                    current_config_yaml=active_launch_exp.config_yaml_content,
                    tasks_json=tasks_json,
                    user_instruction=instruction,
                    code_tree=code_context,
                    workdir=workdir,
                    setup_script_summary=setup_summary,
                    provider=provider,
                )
            active_launch_exp = experiment_service.save_script(
                active_launch_exp,
                result["run_sh"],
                result["config_yaml"],
            )
            resume_issues = run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
            if resume_issues:
                console.print(
                    "[bold red]Error:[/bold red] Agent revision did not add the required task-level resume controls. "
                    "Revise run.sh manually or try again.\n"
                    f"[dim]Still missing: {', '.join(resume_issues)}[/dim]"
                )
                return active_launch_exp
            console.print(
                Panel(
                    f"[bold]Summary[/bold]: {result['summary']}\n"
                    f"[bold]run.sh[/bold]: {len(result['run_sh'].splitlines())} lines",
                    title="[bold green]Resume Controls Added[/bold green]",
                    border_style="green",
                )
            )

        detail = experiment_service.finalize_experiment(active_launch_exp)
        env_overrides = {
            "LABIT_ONLY_TASK": task_id,
            "LABIT_START_AT": task_id,
            "LABIT_FORCE_TASK": task_id,
        }
        console.print(
            Panel(
                f"[bold]Experiment[/bold]: {detail.record.experiment_id}\n"
                f"[bold]Task[/bold]: {task_id}\n"
                f"[bold]Mode[/bold]: run only this task; reuse prior outputs",
                title="[bold cyan]Task Retry Submit[/bold cyan]",
                border_style="cyan",
            )
        )
        submit_ok = submit_and_monitor(
            console=console,
            experiment_service=experiment_service,
            active_launch_exp=active_launch_exp,
            ctx=ctx,
            planner=planner,
            first_participant=first_participant,
            env_overrides=env_overrides,
        )
        if submit_ok:
            return None
        console.print("[yellow]Task retry was not confirmed stable; keeping launch-exp session active for revision.[/yellow]")
    except Exception as exc:
        console.print(f"[bold red]Error submitting task retry:[/bold red] {exc}")
    return active_launch_exp


def _finish_launch_exp(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
) -> LaunchExpSession | None:
    console = ctx.console
    current_session = ctx.session
    if active_launch_exp is None:
        console.print("[dim]Not in experiment planning mode.[/dim]")
        return active_launch_exp
    if active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
        console.print("[yellow]Warning:[/yellow] Not all phases completed. Exiting anyway.")
    submit_ok = False
    if active_launch_exp.run_sh_content:
        try:
            detail = experiment_service.finalize_experiment(active_launch_exp)
            console.print(
                Panel(
                    f"[bold]Experiment[/bold]: {detail.record.experiment_id}\n"
                    f"[bold]Hypothesis[/bold]: {active_launch_exp.hypothesis_id}\n"
                    f"[bold]Tasks[/bold]: {len(detail.tasks)}\n"
                    f"[bold]Path[/bold]: {detail.path}",
                    title="[bold green]Experiment Finalized[/bold green]",
                    border_style="green",
                )
            )
            submit_ok = submit_and_monitor(
                console=console,
                experiment_service=experiment_service,
                active_launch_exp=active_launch_exp,
                ctx=ctx,
                planner=ExperimentPlanner(ctx.paths),
                first_participant=current_session.participants[0] if current_session.participants else None,
            )
            try:
                summary = (
                    f"Experiment planned and submitted: {detail.record.experiment_id} "
                    f"for {active_launch_exp.hypothesis_id}"
                    if submit_ok
                    else f"Experiment planned (submission pending): {detail.record.experiment_id} "
                    f"for {active_launch_exp.hypothesis_id}"
                )
                ctx.service.record_session_event(
                    session_id=current_session.session_id,
                    kind=SessionEventKind.ARTIFACT_EXPERIMENT_CREATED,
                    actor="labit",
                    summary=summary,
                    payload={
                        "experiment_id": detail.record.experiment_id,
                        "hypothesis_id": active_launch_exp.hypothesis_id,
                        "task_count": len(detail.tasks),
                        "submitted": submit_ok,
                    },
                    evidence_refs=session_evidence_refs(current_session)
                    + [f"hypothesis:{active_launch_exp.hypothesis_id}", f"experiment:{detail.record.experiment_id}"],
                )
            except Exception:
                pass
        except Exception as exc:
            console.print(f"[bold red]Error finalizing:[/bold red] {exc}")
    else:
        console.print("[dim]No script generated. Experiment not finalized.[/dim]")
    if submit_ok:
        return None
    return active_launch_exp


def _resume_launch_exp(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
    exp_id: str,
) -> LaunchExpSession | None:
    console = ctx.console
    current_session = ctx.session
    if not exp_id:
        console.print("[bold red]Usage:[/bold red] /launch-exp resume <experiment_id>")
        return active_launch_exp
    if active_launch_exp is not None:
        console.print(f"[bold red]Error:[/bold red] Already planning experiment for {active_launch_exp.hypothesis_id}. Use /launch-exp done first.")
        return active_launch_exp
    try:
        active_launch_exp = experiment_service.resume_launch_exp_session(
            project=current_session.project,
            experiment_id=exp_id,
        )
        phase_label = active_launch_exp.phase.value.replace("_", " ").title()
        console.print(
            Panel(
                f"[bold]Experiment[/bold]: {exp_id}\n"
                f"[bold]Hypothesis[/bold]: {active_launch_exp.hypothesis_id}\n"
                f"[bold]Tasks[/bold]: {len(active_launch_exp.task_plans)}\n"
                f"[bold]Phase[/bold]: {phase_label}\n"
                f"[bold]Has run.sh[/bold]: {'yes' if active_launch_exp.run_sh_content else 'no'}",
                title="[bold cyan]Experiment Resumed[/bold cyan]",
                border_style="cyan",
            )
        )
        print_launch_exp_hints(console, active_launch_exp)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
    return active_launch_exp


def _start_launch_exp(
    ctx: ChatContext,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession | None,
    hypothesis_id: str,
) -> LaunchExpSession | None:
    console = ctx.console
    current_session = ctx.session
    if not hypothesis_id:
        console.print("[bold red]Usage:[/bold red] /launch-exp <hypothesis_id>")
        return active_launch_exp
    if active_launch_exp is not None:
        console.print(f"[bold red]Error:[/bold red] Already planning experiment for {active_launch_exp.hypothesis_id}. Use /launch-exp done first.")
        return active_launch_exp

    try:
        hyp_detail = HypothesisService(ctx.paths).load_hypothesis(current_session.project, hypothesis_id)
        active_launch_exp = experiment_service.start_launch_exp_session(
            project=current_session.project,
            hypothesis_id=hypothesis_id,
        )
        code_tree = experiment_service.get_code_tree(current_session.project)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return active_launch_exp

    try:
        planner = ExperimentPlanner(ctx.paths)
        first_participant = current_session.participants[0] if current_session.participants else None
        provider = first_participant.provider if first_participant else None
        with console.status("[bold cyan]Drafting task breakdown...[/bold cyan]"):
            tasks = planner.draft_task_breakdown(
                session=current_session,
                transcript=ctx.service.transcript(current_session.session_id),
                context_snapshot=ctx.service.context_snapshot(current_session.session_id),
                hypothesis_title=hyp_detail.record.title,
                hypothesis_claim=hyp_detail.record.claim,
                experiment_plan_md=hyp_detail.experiment_plan_markdown,
                code_tree=code_tree,
                provider=provider,
            )
        active_launch_exp = experiment_service.save_task_plans(active_launch_exp, tasks)
    except Exception as exc:
        console.print(f"[bold red]Error drafting tasks:[/bold red] {exc}")
        return None

    render_task_breakdown(console, active_launch_exp)
    print_launch_exp_hints(console, active_launch_exp)
    return active_launch_exp


def submit_and_monitor(
    *,
    console: Console,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession,
    ctx: ChatContext,
    planner: ExperimentPlanner,
    first_participant,
    max_retries: int = 3,
    stabilize_seconds: int = 90,
    poll_interval: int = 15,
    env_overrides: dict[str, str] | None = None,
) -> bool:
    executor = SSHExecutor(ctx.paths)
    attempt = 0
    launch_env = {str(k): str(v) for k, v in (env_overrides or {}).items() if str(v).strip()}

    while attempt < max_retries:
        attempt += 1
        console.print(f"[dim]Submitting experiment (attempt {attempt}/{max_retries})...[/dim]")
        try:
            receipt = experiment_service.submit_experiment(active_launch_exp, env_overrides=launch_env)
        except Exception as submit_exc:
            console.print(f"[bold red]Submission error:[/bold red] {submit_exc}")
            console.print("[dim]Experiment finalized but not submitted.[/dim]")
            return False

        if not receipt.accepted:
            console.print(
                Panel(
                    f"[bold]Error[/bold]: {receipt.stderr_tail}",
                    title="[bold red]Submission Failed[/bold red]",
                    border_style="red",
                )
            )
            return False

        console.print(
            Panel(
                f"[bold]PID[/bold]: {receipt.pid}\n"
                f"[bold]Log[/bold]: {receipt.log_path}\n"
                f"[bold]Host[/bold]: {receipt.remote_host}"
                + ("" if not launch_env else "\n[bold]Env[/bold]: " + ", ".join(f"{k}={v}" for k, v in launch_env.items())),
                title=f"[bold green]Submitted (attempt {attempt})[/bold green]",
                border_style="green",
            )
        )

        console.print(f"[dim]Monitoring for early failures ({stabilize_seconds}s)... Ctrl+C to skip.[/dim]")
        exec_profile = experiment_service.build_default_execution_profile(active_launch_exp.project)
        poll_artifact = LaunchArtifact(
            launch_id=receipt.remote_job_id or "",
            task_id="experiment",
            experiment_id=active_launch_exp.experiment_id,
            project=active_launch_exp.project,
            executor=ExecutionBackend.SSH,
            remote_user=exec_profile.user,
            remote_host=exec_profile.host,
            remote_port=exec_profile.port,
            ssh_key=exec_profile.ssh_key,
            frozen_spec=FrozenLaunchSpec(
                command=active_launch_exp.run_sh_content or "#!/bin/bash\ntrue",
                workdir=exec_profile.workdir,
                output_dir=f"outputs/experiments/{active_launch_exp.experiment_id}",
                env=launch_env,
            ),
            submission=receipt,
        )

        elapsed = 0
        crashed = False
        crash_log = ""
        try:
            while elapsed < stabilize_seconds:
                time.sleep(poll_interval)
                elapsed += poll_interval
                try:
                    poll_result = executor.poll(poll_artifact)
                except Exception:
                    continue
                status = poll_result.get("status", "unknown")
                if status == "running":
                    remaining = stabilize_seconds - elapsed
                    console.print(f"[dim]  [{elapsed}s] Running... ({remaining}s to stable)[/dim]")
                elif status == "stopped":
                    try:
                        collected = executor.collect(poll_artifact)
                    except Exception:
                        collected = {}
                    log_tail = str(collected.get("log_tail", poll_result.get("stdout", "")))
                    if elapsed <= stabilize_seconds:
                        crashed = True
                        crash_log = log_tail
                        console.print(f"[bold red]  [{elapsed}s] Process stopped early![/bold red]")
                    break
                else:
                    console.print(f"[dim]  [{elapsed}s] Status: {status}[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Monitoring skipped.[/yellow]")
            return False

        if not crashed:
            console.print(
                Panel(
                    f"Experiment running for {elapsed}s without errors.",
                    title="[bold green]Experiment Stable[/bold green]",
                    border_style="green",
                )
            )
            return True

        console.print(
            Panel(
                crash_log[-2000:] if crash_log else "(no log captured)",
                title="[bold red]Early Crash Detected[/bold red]",
                border_style="red",
            )
        )

        if attempt >= max_retries:
            console.print(f"[bold red]Exhausted {max_retries} retries. Giving up.[/bold red]")
            console.print("[dim]Use /launch-exp resume to manually fix and resubmit.[/dim]")
            return False

        console.print(f"[dim]Auto-fixing run.sh based on error log (attempt {attempt + 1}/{max_retries})...[/dim]")
        try:
            code_context = experiment_service.get_code_context(ctx.session.project)
            tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
            workdir, setup_summary = _execution_prompt_context(experiment_service, ctx.session.project)

            provider = first_participant.provider if first_participant else None
            fix_instruction = (
                f"The experiment crashed immediately after submission. "
                f"Here is the error log from the remote:\n\n"
                f"```\n{crash_log[-3000:]}\n```\n\n"
                f"Fix the run.sh to address this error. Common issues: "
                f"wrong paths, missing dependencies, incorrect CLI arguments, "
                f"environment assumptions."
            )
            if launch_env:
                fix_instruction += (
                    "\n\nThis launch is a task-level retry with environment overrides: "
                    + ", ".join(f"{k}={v}" for k, v in launch_env.items())
                    + ". Preserve the LABIT_ONLY_TASK/LABIT_START_AT/LABIT_FORCE_TASK/"
                    "LABIT_FORCE_CLEAN resume contract, should_run_task(), and "
                    "require_checkpoint() while fixing the crash."
                )

            with console.status("[bold cyan]Agent fixing run.sh...[/bold cyan]"):
                result = planner.revise_run_sh(
                    current_run_sh=active_launch_exp.run_sh_content,
                    current_config_yaml=active_launch_exp.config_yaml_content,
                    tasks_json=tasks_json,
                    user_instruction=fix_instruction,
                    code_tree=code_context,
                    workdir=workdir,
                    setup_script_summary=setup_summary,
                    provider=provider,
                )
            active_launch_exp = experiment_service.save_script(
                active_launch_exp,
                result["run_sh"],
                result["config_yaml"],
            )
            if launch_env:
                resume_issues = run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
                if resume_issues:
                    console.print(
                        "[bold red]Auto-fix removed or failed to preserve task-level resume controls.[/bold red]\n"
                        f"[dim]Issues: {', '.join(resume_issues)}[/dim]"
                    )
                    return False
            console.print(
                Panel(
                    f"[bold]Fix summary[/bold]: {result['summary']}\n"
                    f"[bold]run.sh[/bold] ({len(result['run_sh'].splitlines())} lines)",
                    title="[bold yellow]Script Revised[/bold yellow]",
                    border_style="yellow",
                )
            )
        except Exception as fix_exc:
            console.print(f"[bold red]Auto-fix failed:[/bold red] {fix_exc}")
            console.print("[dim]Use /launch-exp resume to manually fix and resubmit.[/dim]")
            return False

    console.print(f"[bold red]Exhausted {max_retries} retries.[/bold red]")
    return False


def run_sh_has_task_resume_contract(run_sh: str) -> bool:
    return not run_sh_resume_contract_issues(run_sh)


def run_sh_resume_contract_issues(run_sh: str) -> list[str]:
    issues: list[str] = []
    required_markers = (
        "LABIT_ONLY_TASK",
        "LABIT_START_AT",
        "LABIT_FORCE_TASK",
        "LABIT_FORCE_CLEAN",
    )
    for marker in required_markers:
        if marker not in run_sh:
            issues.append(f"missing {marker}")
    if not re.search(r"(\bshould_run_task\s*\(\s*\)|\bfunction\s+should_run_task\b)", run_sh):
        issues.append("missing should_run_task() helper")
    if not re.search(r"(\brequire_checkpoint\s*\(\s*\)|\bfunction\s+require_checkpoint\b)", run_sh):
        issues.append("missing require_checkpoint() helper")
    destructive_lines: list[str] = []
    lines = run_sh.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not re.search(r"\brm\s+(-[rfRF]+\s+)+", stripped) or stripped.startswith("#"):
            continue
        guard_context = "\n".join(
            context_line
            for context_line in lines[max(0, idx - 4): idx + 1]
            if not context_line.strip().startswith("#")
        )
        if "LABIT_FORCE_CLEAN" not in guard_context:
            destructive_lines.append(stripped)
    if destructive_lines:
        issues.append(
            "contains cleanup not visibly guarded by LABIT_FORCE_CLEAN: "
            + destructive_lines[0][:120]
        )
    return issues


def _execution_prompt_context(experiment_service: ExperimentService, project: str) -> tuple[str, str]:
    try:
        exec_profile = experiment_service.build_default_execution_profile(project)
        return exec_profile.workdir or "", exec_profile.setup_script or ""
    except Exception:
        return "", ""
