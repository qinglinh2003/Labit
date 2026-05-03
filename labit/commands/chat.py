from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.pretty import Pretty
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from labit.capture.commands import handle_capture_command
from labit.commands.auto import handle_auto_command
from labit.commands.context import ChatContext
from labit.commands.dispatch import SlashCommandDispatcher
from labit.commands.rendering import (
    CHAT_SHELL_COMMANDS,
    COMMAND_COLOR,
    CODE_THEME,
    LABIT_THEME,
    PROVIDER_STYLES,
    ThinkingIndicator,
    agent_panel,
    box_bottom,
    box_line,
    box_top,
    box_width,
    clip_box_text,
    debrief_markdown,
    launch_markdown,
    md,
    message_body,
    print_doc_mode_hints,
    print_launch_exp_hints,
    render_compact_transcript,
    render_console_header,
    render_doc_status,
    render_experiment_launch_preview,
    render_investigation_result,
    render_launch_exp_status,
    render_message_block,
    render_recent_messages,
    render_related_reports,
    render_review_suggestion,
    render_session_summary,
    render_shell_header,
    render_shell_help,
    render_synthesis_preview,
    render_task_breakdown,
    render_task_detail,
    render_transcript,
    render_user_shell_message,
    review_markdown,
    sanitize_markdown,
    transcript_preview_text,
)
from labit.chat.clipboard import ClipboardImageError, capture_clipboard_image
from labit.chat.composer import ComposerResult, prompt_toolkit_available, prompt_with_clipboard_image
from labit.chat.models import ChatMode
from labit.chat.service import ChatService
from labit.chat.synthesizer import DiscussionSynthesizer
from labit.context.events import SessionEventKind
from labit.documents.drafter import DocDrafter
from labit.documents.models import DocSession, DocStatus
from labit.documents.service import DocumentService
from labit.devloop.commands import handle_dev_command
from labit.devloop.engine import run_dev_loop
from labit.devloop.models import DevLoopSession
from labit.experiments.executors.ssh import SSHExecutor
from labit.experiments.models import (
    ExperimentDraft,
    ExperimentTaskPlan,
    LaunchExpPhase,
    LaunchExpSession,
    ResearchRole,
    TaskDraft,
    TaskKind,
    TaskResources,
    TaskSpec,
    TaskStatus,
)
from labit.experiments.planner import ExperimentPlanner
from labit.experiments.service import ExperimentService
from labit.hypotheses.drafter import HypothesisDrafter
from labit.hypotheses.commands import handle_hypothesis_command
from labit.hypotheses.models import HypothesisDraft
from labit.hypotheses.models import HypothesisResolution, HypothesisState, utc_now_iso
from labit.hypotheses.service import HypothesisService
from labit.investigations.service import InvestigationService
from labit.memory.commands import handle_memory_command
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

chat_app = typer.Typer(
    help="Shared free conversation sessions with one or more agent backends.",
    invoke_without_command=True,
)

console = Console(theme=LABIT_THEME)


def _chat_service() -> ChatService:
    return ChatService(RepoPaths.discover())


def _project_service() -> ProjectService:
    return ProjectService(RepoPaths.discover())


def _hypothesis_service() -> HypothesisService:
    return HypothesisService(RepoPaths.discover())


def _hypothesis_drafter() -> HypothesisDrafter:
    return HypothesisDrafter(RepoPaths.discover())


def _experiment_service() -> ExperimentService:
    return ExperimentService(RepoPaths.discover())


def _doc_drafter() -> DocDrafter:
    return DocDrafter(RepoPaths.discover())


def _document_service() -> DocumentService:
    return DocumentService(RepoPaths.discover())


def _investigation_service() -> InvestigationService:
    return InvestigationService(RepoPaths.discover())


def _discussion_synthesizer() -> DiscussionSynthesizer:
    return DiscussionSynthesizer(RepoPaths.discover())


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


def _prompt_text(label: str) -> str:
    while True:
        value = typer.prompt(label).strip()
        if value:
            return value
        console.print("[bold red]This field is required.[/bold red]")


def _prompt_optional(label: str, default: str = "") -> str:
    return typer.prompt(label, default=default, show_default=bool(default)).strip()


def _prompt_mode() -> ChatMode:
    choices = [mode.value for mode in ChatMode]
    normalized = {choice.lower(): choice for choice in choices}
    while True:
        value = typer.prompt(
            f"Mode [{' / '.join(choices)}]",
            default=ChatMode.SINGLE.value,
            show_default=True,
        ).strip().lower()
        if value in normalized:
            return ChatMode(normalized[value])
        console.print(f"[bold red]Choose one of:[/bold red] {', '.join(choices)}")




def _prompt_in_box(session) -> ComposerResult:
    width = box_width(console)
    inner_width = width - 2
    project = session.project or "no-project"
    mode = session.mode.value
    participants = ", ".join(f"{item.name}:{item.provider.value}" for item in session.participants)
    prompt_prefix = " › "
    if not console.is_terminal:
        render_console_header(console, project=project, mode=mode, participants=participants)
        console.print(f"[yellow]{box_top('Input', width)}[/yellow]")
        console.print(f"[yellow]{box_line('', width)}[/yellow]")
        console.print(f"[yellow]│[/yellow]{prompt_prefix}", end="")
        raw = console.input("")
        console.print(f"[yellow]{box_line('', width)}[/yellow]")
        console.print(f"[yellow]{box_bottom(width)}[/yellow]")
        return ComposerResult(text=raw)

    if prompt_toolkit_available():
        render_console_header(console, project=project, mode=mode, participants=participants)
        return prompt_with_clipboard_image(
            console=console,
            paths=RepoPaths.discover(),
            session_id=session.session_id,
            prompt_prefix=prompt_prefix,
            slash_commands=CHAT_SHELL_COMMANDS,
        )

    top = box_top("Input", width)
    empty = box_line("", width)
    prompt_fill = " " * max(0, inner_width - len(prompt_prefix))
    prompt_line = f"│{prompt_prefix}{prompt_fill}│"
    bottom = box_bottom(width)

    stream = console.file
    yellow = "\x1b[33m"
    reset = "\x1b[0m"

    render_console_header(console, project=project, mode=mode, participants=participants)
    stream.write(f"{yellow}{top}{reset}\n")
    stream.write(f"{yellow}{empty}{reset}\n")
    stream.write(f"{yellow}{prompt_line}{reset}\n")
    stream.write(f"{yellow}{empty}{reset}\n")
    stream.write(f"{yellow}{bottom}{reset}\n")
    stream.write("\n")
    stream.write(f"\x1b[4A\r\x1b[{len(prompt_prefix) + 1}C")
    stream.flush()

    raw_result = prompt_with_clipboard_image(
        console=console,
        paths=RepoPaths.discover(),
        session_id=session.session_id,
        prompt_prefix="",
    )

    stream.write("\x1b[2B\r")
    stream.flush()
    return raw_result


def _open_default_session(
    *,
    service: ChatService,
    title: str | None,
    mode: ChatMode | None,
    provider: str,
    second_provider: str,
):
    sessions = service.list_sessions()
    if sessions:
        return sessions[0], False
    session = service.open_session(
        title=title or "Free Conversation",
        mode=mode or ChatMode.SINGLE,
        provider=provider,
        second_provider=second_provider,
        project=_project_service().active_project_name(),
    )
    return session, True


def _confirm_in_shell(prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}




def _submit_and_monitor(
    *,
    console: Console,
    experiment_service: ExperimentService,
    active_launch_exp: LaunchExpSession,
    current_session,
    service: ChatService,
    planner: ExperimentPlanner,
    _hypothesis_service,
    first_participant,
    max_retries: int = 3,
    stabilize_seconds: int = 90,
    poll_interval: int = 15,
    env_overrides: dict[str, str] | None = None,
) -> bool:
    """Submit experiment, monitor for early failures, auto-fix and resubmit.

    After submission, polls the remote log for *stabilize_seconds*. If the
    process dies early, shows the error log and lets the agent revise run.sh,
    then resubmits. Gives up after *max_retries* total attempts.
    """
    from labit.paths import RepoPaths

    executor = SSHExecutor(RepoPaths.discover())
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

        # ── Monitor phase: poll for early crash ──
        console.print(f"[dim]Monitoring for early failures ({stabilize_seconds}s)... Ctrl+C to skip.[/dim]")
        # Build a minimal artifact for polling
        from labit.experiments.models import (
            ExecutionBackend,
            FrozenLaunchSpec,
            LaunchArtifact,
        )

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
                    # Process died — check if it's a crash or normal completion
                    try:
                        collected = executor.collect(poll_artifact)
                    except Exception:
                        collected = {}
                    log_tail = str(collected.get("log_tail", poll_result.get("stdout", "")))
                    # If stopped very early, likely a crash
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

        # ── Crash detected: show error and auto-fix ──
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

        # Auto-fix: have the agent revise run.sh based on the error
        console.print(f"[dim]Auto-fixing run.sh based on error log (attempt {attempt + 1}/{max_retries})...[/dim]")
        try:
            hyp_detail = _hypothesis_service().load_hypothesis(
                current_session.project, active_launch_exp.hypothesis_id
            )
            code_context = experiment_service.get_code_context(current_session.project)
            tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
            try:
                workdir = exec_profile.workdir or ""
                setup_summary = exec_profile.setup_script or ""
            except Exception:
                workdir = ""
                setup_summary = ""

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
                resume_issues = _run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
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


def _run_sh_has_task_resume_contract(run_sh: str) -> bool:
    return not _run_sh_resume_contract_issues(run_sh)


def _run_sh_resume_contract_issues(run_sh: str) -> list[str]:
    """Return missing pieces of the task-level resume contract.

    This is intentionally heuristic: run.sh is arbitrary bash, but checking for
    helpers in addition to env var names catches scripts that merely mention the
    contract in comments without implementing it.
    """
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




def _flatten_numeric_metrics(value, *, prefix: str = "", depth: int = 0, max_depth: int = 2) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if depth > max_depth:
        return metrics
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            nested_prefix = f"{prefix}.{key_text}" if prefix else key_text
            metrics.update(_flatten_numeric_metrics(item, prefix=nested_prefix, depth=depth + 1, max_depth=max_depth))
    elif isinstance(value, list):
        return metrics
    elif isinstance(value, bool):
        return metrics
    elif isinstance(value, (int, float)):
        metrics[prefix or "value"] = float(value)
    return metrics


def _collect_task_metrics(collected: dict) -> dict[str, float]:
    metrics: dict[str, float] = {}
    files = collected.get("files", {}) or {}
    for path, content in files.items():
        try:
            parsed = json.loads(content)
        except Exception:
            continue
        base = str(path).rsplit("/", 1)[-1].rsplit(".", 1)[0]
        for key, value in _flatten_numeric_metrics(parsed).items():
            metric_key = f"{base}.{key}" if key and key != "value" else base
            metrics[metric_key] = value
    manifest_count = collected.get("manifest_line_count")
    if isinstance(manifest_count, int):
        metrics["manifest.line_count"] = float(manifest_count)
    return metrics


def _task_summary_from_collect(task, collected: dict, metrics: dict[str, float]) -> str:
    status = str(collected.get("status", "unknown")).strip() or "unknown"
    if status == "running":
        log_tail = str(collected.get("log_tail", "") or "").strip()
        if log_tail:
            last_line = log_tail.splitlines()[-1].strip()
            return f"Task is still running. Latest log line: {last_line}"
        return "Task is still running."
    if metrics:
        preview = ", ".join(f"{key}={value:.4g}" for key, value in list(metrics.items())[:4])
        return f"Collected result metrics: {preview}"
    if collected.get("output_dir_exists"):
        refs = collected.get("artifact_refs", []) or []
        return f"Task stopped and produced artifacts ({len(refs)} files discovered)."
    log_tail = str(collected.get("log_tail", "") or "").strip()
    if log_tail:
        last_line = log_tail.splitlines()[-1].strip()
        return f"Task stopped without recognized result files. Latest log line: {last_line}"
    return f"Task stopped without recognized result files for {task.task_kind.value}."


def _task_error_from_collect(collected: dict) -> str:
    if str(collected.get("status", "")).strip() == "running":
        return ""
    stderr = str(collected.get("stderr", "") or "").strip()
    if stderr:
        return stderr
    log_tail = str(collected.get("log_tail", "") or "").strip()
    if not log_tail:
        return ""
    error_markers = ("traceback", "error", "exception", "failed", "fatal")
    if any(marker in log_tail.lower() for marker in error_markers):
        return log_tail
    return ""


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _extract_log_hint(log_tail: str, *, max_len: int = 140) -> str:
    if not log_tail:
        return ""
    lines: list[str] = []
    for raw in log_tail.splitlines():
        cleaned = _ANSI_ESCAPE_RE.sub("", raw).strip()
        if cleaned:
            lines.append(cleaned)
    if not lines:
        return ""
    for candidate in reversed(lines):
        if re.search(r"[A-Za-z0-9]", candidate):
            return candidate[:max_len]
    return lines[-1][:max_len]


def _transcript_excerpt(messages, *, limit: int = 16, max_chars: int = 6000) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for message in messages[-limit:]:
        speaker = message.speaker
        if message.provider:
            speaker = f"{speaker} ({message.provider.value})"
        line = f"{speaker}: {message.content.strip()}"
        if getattr(message, "attachments", None):
            attachment_labels = ", ".join(
                attachment.label or attachment.path.rsplit("/", 1)[-1] for attachment in message.attachments
            )
            line = f"{line} [attachments: {attachment_labels}]"
        lines.append(line)
    text = "\n".join(lines).strip()
    return text[:max_chars].strip()


def _context_snapshot_excerpt(snapshot, *, max_blocks: int = 6, max_chars: int = 5000) -> str:
    pieces: list[str] = []
    for block in snapshot.blocks[:max_blocks]:
        pieces.append(f"[{block.title}]\n{block.content.strip()}")
    for memory in snapshot.memory[:max_blocks]:
        pieces.append(f"[{memory.title}]\n{memory.content.strip()}")
    text = "\n\n".join(piece for piece in pieces if piece).strip()
    return text[:max_chars].strip()


def _session_evidence_refs(session) -> list[str]:
    refs: list[str] = []
    if session.project:
        refs.append(f"project:{session.project}")
    for binding in session.context_bindings:
        if binding.provider != "paper_focus":
            continue
        paper_id = str(binding.config.get("paper_id", "")).strip()
        if paper_id:
            refs.append(f"paper:{paper_id}")
    return refs


def _run_streaming_turn(
    *,
    service: ChatService,
    session,
    query: str,
    attachments: list | None = None,
    force_deep_context: bool = False,
    reasoning_effort: str | None = None,
    skip_participants: set[str] | None = None,
    cwd_override: str | None = None,
) -> object | None:
    render_user_shell_message(console, query, attachments=attachments)
    # Temporarily filter out muted participants for this turn
    effective_session = session
    if skip_participants:
        filtered = [p for p in session.participants if p.name not in skip_participants]
        if filtered:
            effective_session = session.model_copy(update={"participants": filtered})
    participant_state = {
        participant.name: {
            "provider": participant.provider.value,
            "content": "",
            "status": "queued",
            "started_at": None,
            "thinking": None,
        }
        for participant in effective_session.participants
    }
    state_lock = threading.Lock()
    live_lock = threading.Lock()
    cancel_event = threading.Event()

    def _render_live() -> Group:
        panels: list[Panel] = []
        with state_lock:
            snapshot = {
                name: dict(values)
                for name, values in participant_state.items()
            }
        for participant in effective_session.participants:
            state = snapshot[participant.name]
            status = state["status"]
            if status == "queued":
                continue
            started_at = state["started_at"]
            status_text = status
            if started_at is not None and status in {"thinking", "streaming"}:
                status_text = f"{status} · {time.monotonic() - started_at:.1f}s"
            panels.append(
                agent_panel(
                    participant.name,
                    state["provider"],
                    state["content"],
                    thinking=state["thinking"],
                    status_text=status_text,
                )
            )
        return Group(*panels)

    def _refresh_live() -> None:
        with live_lock:
            live.update(_render_live(), refresh=True)

    def _on_reply_start(participant) -> None:
        with state_lock:
            participant_state[participant.name]["content"] = ""
            participant_state[participant.name]["status"] = "thinking"
            participant_state[participant.name]["started_at"] = time.monotonic()
            participant_state[participant.name]["thinking"] = ThinkingIndicator()
        _refresh_live()

    def _on_reply_delta(participant, content: str) -> None:
        with state_lock:
            participant_state[participant.name]["content"] = content
            participant_state[participant.name]["status"] = "streaming" if content.strip() else "thinking"
        _refresh_live()

    def _on_reply_complete(participant, content: str) -> None:
        with state_lock:
            participant_state[participant.name]["content"] = content
            participant_state[participant.name]["status"] = "done"
            participant_state[participant.name]["thinking"] = None
        _refresh_live()

    cancelled = False
    result = None
    with Live(_render_live(), console=console, refresh_per_second=8, transient=True) as live:
        try:
            result = service.ask_stream(
                session_id=session.session_id,
                content=query,
                attachments=attachments,
                force_deep_context=force_deep_context,
                reasoning_effort=reasoning_effort,
                on_reply_start=_on_reply_start,
                on_reply_delta=_on_reply_delta,
                on_reply_complete=_on_reply_complete,
                cancel_event=cancel_event,
                skip_participants=skip_participants,
                cwd_override=cwd_override,
            )
        except KeyboardInterrupt:
            cancel_event.set()
            cancelled = True
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return None

    if result is not None and result.replies:
        for reply in result.replies:
            console.print(
                agent_panel(
                    reply.participant.name,
                    reply.participant.provider.value,
                    reply.message.content,
                    turn_index=reply.message.turn_index,
                )
            )
            console.print("")

    if cancelled:
        with state_lock:
            for state in participant_state.values():
                if state["status"] in {"thinking", "streaming"}:
                    state["status"] = "interrupted"
                    state["thinking"] = None
        console.print("[dim italic]Interrupted.[/dim italic]")
    return result


# /dev auto-development loop lives in labit.devloop.

def run_chat_shell(
    *,
    session,
    service: ChatService,
) -> None:
    render_shell_header(console,session)
    transcript = service.transcript(session.session_id)
    console.print("")
    render_recent_messages(console,transcript, count=8)
    console.print("")

    current_session = session
    active_doc: DocSession | None = None
    # Hypothesis editing mode state: (hypothesis_id, project, current_draft)
    active_hypothesis: tuple[str, str, HypothesisDraft] | None = None
    active_launch_exp: LaunchExpSession | None = None
    active_dev: DevLoopSession | None = None
    muted_next_turn: set[str] = set()  # agent names to skip on next turn only
    dispatcher = SlashCommandDispatcher()
    dispatcher.register("/auto", lambda ctx, arg: handle_auto_command(ctx=ctx, argument=arg))

    for capture_command in ("/idea", "/note", "/todo"):
        dispatcher.register(
            capture_command,
            lambda ctx, arg, command=capture_command: handle_capture_command(
                ctx=ctx,
                command=command,
                argument=arg,
            ),
        )

    def _handle_hypothesis(ctx: ChatContext, arg: str) -> None:
        nonlocal active_hypothesis
        result = handle_hypothesis_command(
            ctx=ctx,
            argument=arg,
            active_hypothesis=active_hypothesis,
        )
        active_hypothesis = result.active_hypothesis

    dispatcher.register("/hypothesis", _handle_hypothesis)
    dispatcher.register("/memory", lambda ctx, arg: handle_memory_command(ctx=ctx, argument=arg))

    def _handle_dev(ctx: ChatContext, arg: str) -> None:
        nonlocal active_dev
        active_dev = handle_dev_command(
            ctx=ctx,
            argument=arg,
            active_dev=active_dev,
            run_streaming_turn=_run_streaming_turn,
        )

    dispatcher.register("/dev", _handle_dev)
    while True:
        try:
            composer_result = _prompt_in_box(current_session)
        except KeyboardInterrupt:
            console.print("\n[dim]Leaving chat shell.[/dim]")
            return
        raw = composer_result.text.strip()
        attachments = composer_result.attachments
        if not raw:
            continue
        if raw.startswith("/"):
            parts = raw.split(maxsplit=1)
            command = parts[0]
            argument = parts[1].strip() if len(parts) > 1 else ""
            ctx = ChatContext(
                console=console,
                paths=RepoPaths.discover(),
                service=service,
                session=current_session,
            )

            if command == "/exit":
                console.print("[dim]Leaving chat shell.[/dim]")
                return
            if dispatcher.handle(command, ctx, argument):
                continue
            if command == "/help":
                render_shell_help(console)
                continue
            if command == "/list":
                list_chats(json_output=False)
                continue
            if command == "/show":
                render_session_summary(console,current_session)
                console.print("")
                render_transcript(console,service.transcript(current_session.session_id))
                continue
            if command == "/mode":
                if not argument:
                    render_session_summary(console,current_session)
                    continue
                mode_str = argument.strip().lower()
                try:
                    new_mode = ChatMode(mode_str)
                except ValueError:
                    console.print(f"[bold red]Invalid mode:[/bold red] {mode_str}. Use single, round_robin, or parallel.")
                    continue
                if new_mode == current_session.mode:
                    console.print(f"[dim]Already in {new_mode.value} mode.[/dim]")
                    continue
                current_session = service.update_mode(current_session.session_id, new_mode)
                console.print(f"[bold #0080ff]Switched to {new_mode.value} mode.[/bold #0080ff]")
                if new_mode != ChatMode.SINGLE:
                    names = ", ".join(p.name for p in current_session.participants)
                    console.print(f"[dim]Participants: {names}[/dim]")
                continue
            if command == "/swap":
                if len(current_session.participants) < 2:
                    console.print("[dim]Need at least 2 participants to swap.[/dim]")
                    continue
                old_order = ", ".join(p.name for p in current_session.participants)
                current_session = service.swap_participants(current_session.session_id)
                new_order = ", ".join(p.name for p in current_session.participants)
                console.print(f"[bold #0080ff]Swapped participant order:[/bold #0080ff] {old_order} → {new_order}")
                continue
            if command == "/mute":
                if not argument:
                    if muted_next_turn:
                        console.print(f"[dim]Muted for next turn: {', '.join(muted_next_turn)}[/dim]")
                    else:
                        names = ", ".join(p.name for p in current_session.participants)
                        console.print(f"[dim]Usage: /mute <agent_name>  (participants: {names})[/dim]")
                    continue
                target = argument.strip().lower()
                matched = [p for p in current_session.participants if p.name.lower() == target]
                if not matched:
                    names = ", ".join(p.name for p in current_session.participants)
                    console.print(f"[bold red]Unknown agent:[/bold red] {target}. Participants: {names}")
                    continue
                agent_name = matched[0].name
                if agent_name in muted_next_turn:
                    muted_next_turn.discard(agent_name)
                    console.print(f"[bold #0080ff]Unmuted {agent_name}.[/bold #0080ff]")
                else:
                    # Don't allow muting all participants
                    active_count = len([p for p in current_session.participants if p.name not in muted_next_turn])
                    if active_count <= 1:
                        console.print("[bold red]Error:[/bold red] Cannot mute all participants.")
                        continue
                    muted_next_turn.add(agent_name)
                    console.print(f"[bold #0080ff]{agent_name} muted for next turn.[/bold #0080ff] (auto-unmutes after one turn)")
                continue
            if command == "/doc":
                doc_parts = argument.split(maxsplit=1)
                doc_action = doc_parts[0].strip().lower() if doc_parts else "status"
                doc_argument = doc_parts[1].strip() if len(doc_parts) > 1 else ""
                if doc_action in {"status", ""}:
                    if active_doc is None:
                        console.print("[dim]No active document session. Use /doc start <title> or /doc open <id>.[/dim]")
                    else:
                        render_doc_status(console,active_doc)
                    continue
                if doc_action == "done":
                    if active_doc is None:
                        console.print("[dim]No active document session.[/dim]")
                    else:
                        try:
                            _document_service().end_session(active_doc)
                        except Exception:
                            pass
                        console.print(
                            f"[green]Document session closed.[/green] "
                            f"[dim]{active_doc.doc_id} · {active_doc.document_path} ({active_doc.status.value})[/dim]"
                        )
                        active_doc = None
                    continue
                if doc_action == "publish":
                    publish_target = doc_argument.strip()
                    if not publish_target:
                        if active_doc is None:
                            console.print("[bold red]Usage:[/bold red] /doc publish <doc_id>")
                            continue
                        publish_target = active_doc.doc_id
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                        continue
                    try:
                        published_doc = _document_service().publish_document(
                            project=current_session.project,
                            doc_id=publish_target,
                            source_session=current_session,
                        )
                        if active_doc is not None and active_doc.doc_id == published_doc.doc_id:
                            active_doc = published_doc
                        console.print(f"[green]Document published.[/green] [dim]{published_doc.doc_id} → active[/dim]")
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                if doc_action == "list":
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                        continue
                    try:
                        docs = _document_service().list_documents(current_session.project)
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    if not docs:
                        console.print("[dim]No documents found.[/dim]")
                    else:
                        from rich.table import Table as RichTable

                        t = RichTable(title="Documents", border_style="dim")
                        t.add_column("ID", style="bold")
                        t.add_column("Title")
                        t.add_column("Status")
                        t.add_column("Updated")
                        for d in docs:
                            t.add_row(
                                d.get("doc_id", "?"),
                                d.get("title", "?"),
                                d.get("status", "?"),
                                d.get("updated_at", "?"),
                            )
                        console.print(t)
                    continue
                if doc_action == "open":
                    doc_id = doc_argument.strip()
                    if not doc_id:
                        console.print("[bold red]Usage:[/bold red] /doc open <doc_id>")
                        continue
                    if not current_session.project:
                        console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                        continue
                    if active_doc is not None:
                        console.print("[bold red]Error:[/bold red] A document session is already active. Use /doc done first.")
                        continue
                    try:
                        active_doc = _document_service().open_document(
                            project=current_session.project,
                            doc_id=doc_id,
                            session=current_session,
                        )
                    except Exception as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                        continue
                    status_note = ""
                    if active_doc.status == DocStatus.DRAFT:
                        status_note = " (demoted to draft for editing)"
                    console.print(
                        Panel(
                            (
                                f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                                f"[bold]Title[/bold]: {active_doc.title}\n"
                                f"[bold]Status[/bold]: {active_doc.status.value}{status_note}\n"
                                f"[bold]Document[/bold]: {active_doc.document_path}\n\n"
                                    ),
                            title="[bold green]Document opened[/bold green]",
                            border_style="green",
                        )
                    )
                    print_doc_mode_hints(console, current_session)
                    continue
                if doc_action == "auto":
                    if active_doc is None:
                        console.print("[bold red]Error:[/bold red] No active document. Use /doc start or /doc open first.")
                        continue
                    # Parse round count
                    max_rounds = 5
                    if doc_argument.strip():
                        try:
                            max_rounds = int(doc_argument.strip())
                        except ValueError:
                            console.print("[bold red]Usage:[/bold red] /doc auto [N]  (N = number of rounds, default 5, max 10)")
                            continue
                    max_rounds = min(max(max_rounds, 1), 10)

                    doc_service = _document_service()
                    drafter = _doc_drafter()
                    author = current_session.participants[0]
                    reviewer = (
                        current_session.participants[1]
                        if current_session.mode == ChatMode.ROUND_ROBIN and len(current_session.participants) >= 2
                        else None
                    )

                    console.print(f"[bold yellow]Auto-iteration starting: up to {max_rounds} rounds. Ctrl+C to stop.[/bold yellow]")
                    # Initial instruction for first round: use reviewer's last review or generic
                    auto_instruction = "Review the document and improve it. Fix any issues, improve clarity, and strengthen the content."

                    interrupted = False
                    for round_num in range(1, max_rounds + 1):
                        console.print(f"\n[bold]── Round {round_num}/{max_rounds} ──[/bold]")
                        try:
                            old_markdown = doc_service.read_document(active_doc)

                            # Author revises
                            with console.status(f"[bold blue]{author.name} revising (round {round_num})...[/bold blue]"):
                                update = drafter.revise_document(
                                    session=current_session,
                                    transcript=service.transcript(current_session.session_id),
                                    context_snapshot=service.context_snapshot(current_session.session_id),
                                    doc_title=active_doc.title,
                                    current_markdown=old_markdown,
                                    user_instruction=auto_instruction,
                                    interaction_log=doc_service.interaction_excerpt(active_doc),
                                    author_name=author.name,
                                    provider=author.provider,
                                )
                                active_doc = doc_service.revise_document(
                                    doc_session=active_doc,
                                    update=update,
                                    user_instruction=auto_instruction,
                                )
                            console.print(
                                Panel(
                                    f"[bold]Iteration[/bold]: {active_doc.iteration}\n[bold]Summary[/bold]: {update.summary}",
                                    title=f"[bold green]{author.name} · Round {round_num}[/bold green]",
                                    border_style="green",
                                )
                            )

                            # Reviewer reviews (round-robin) or self-review (single)
                            if reviewer is not None:
                                from labit.documents.drafter import compute_changed_sections

                                new_markdown = doc_service.read_document(active_doc)
                                changed_sections = compute_changed_sections(old_markdown, new_markdown)

                                with console.status(f"[bold cyan]{reviewer.name} reviewing (round {round_num})...[/bold cyan]"):
                                    review_update = drafter.review_document(
                                        current_markdown=new_markdown,
                                        revision_summary=update.summary,
                                        user_instruction=auto_instruction,
                                        reviewer_name=reviewer.name,
                                        changed_sections=changed_sections,
                                        provider=reviewer.provider,
                                    )
                                    active_doc = doc_service.record_review(
                                        doc_session=active_doc,
                                        update=review_update,
                                        reviewer_name=reviewer.name,
                                    )
                                console.print(
                                    Panel(
                                        f"[bold]Review[/bold]: {review_update.summary}",
                                        title=f"[bold cyan]{reviewer.name} · Review[/bold cyan]",
                                        border_style="cyan",
                                    )
                                )
                                # Use reviewer feedback as next round's instruction
                                auto_instruction = review_update.summary
                            else:
                                # Single agent: use own revision summary as next instruction
                                auto_instruction = f"Continue improving. Previous changes: {update.summary}"

                            # Convergence check: all review blocks closed + no new open reviews
                            from labit.documents.drafter import count_open_reviews

                            current_md = doc_service.read_document(active_doc)
                            open_count = count_open_reviews(current_md)
                            if open_count == 0:
                                console.print(f"[bold green]Converged at round {round_num} — all review blocks resolved, no open issues remaining.[/bold green]")
                                break
                            else:
                                console.print(f"[dim]  {open_count} open review(s) remaining[/dim]")

                        except KeyboardInterrupt:
                            console.print(f"\n[bold yellow]Auto-iteration interrupted at round {round_num}.[/bold yellow]")
                            interrupted = True
                            break
                        except Exception as exc:
                            console.print(f"[bold red]Error in round {round_num}:[/bold red] {exc}")
                            break

                    if not interrupted:
                        console.print(f"[bold green]Auto-iteration complete. {active_doc.iteration} total iterations.[/bold green]")
                    print_doc_mode_hints(console, current_session)
                    try:
                        service.record_session_event(
                            session_id=current_session.session_id,
                            kind=SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
                            actor="labit",
                            summary=f"Document auto-iterated: {active_doc.title}",
                            payload={
                                "doc_id": active_doc.doc_id,
                                "title": active_doc.title,
                                "iteration": active_doc.iteration,
                            },
                            evidence_refs=_session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
                        )
                    except Exception:
                        pass
                    continue
                if doc_action != "start":
                    console.print("[bold red]Usage:[/bold red] /doc start <title> | /doc open <id> | /doc auto [N] | /doc status | /doc done | /doc publish <id> | /doc list")
                    continue
                title = doc_argument
                if not title:
                    console.print("[bold red]Usage:[/bold red] /doc start <title>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                if active_doc is not None:
                    console.print("[bold red]Error:[/bold red] A document session is already active. Use /doc done first.")
                    continue

                doc_service = _document_service()
                try:
                    with console.status(f"[bold blue]{current_session.participants[0].name} writing document draft...[/bold blue]"):
                        update = _doc_drafter().draft_from_session(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            title=title,
                            provider=current_session.participants[0].provider,
                        )
                        active_doc = doc_service.start_document(
                            project=current_session.project,
                            title=title,
                            update=update,
                            session=current_session,
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print(
                    Panel(
                        (
                            f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                            f"[bold]Document[/bold]: {active_doc.document_path}\n"
                            f"[bold]Interaction log[/bold]: {active_doc.log_path}\n"
                            f"[bold]Summary[/bold]: {update.summary}\n\n"
                            ),
                        title="[bold green]Document draft saved[/bold green]",
                        border_style="green",
                    )
                )
                print_doc_mode_hints(console, current_session)
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_DOCUMENT_CREATED,
                        actor="labit",
                        summary=f"Document draft created: {update.title}",
                        payload={
                            "doc_id": active_doc.doc_id,
                            "title": update.title,
                            "document_path": active_doc.document_path,
                            "log_path": active_doc.log_path,
                        },
                        evidence_refs=_session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
                    )
                except Exception:
                    pass
                continue
            if command in {"/paste-image", "/image"}:
                query = argument.strip() or "Please inspect the attached image and describe anything important."
                try:
                    attachment = capture_clipboard_image(
                        paths=RepoPaths.discover(),
                        session_id=current_session.session_id,
                    )
                except ClipboardImageError as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print(
                    Panel(
                        f"[bold]Saved[/bold]: {attachment.label or attachment.path}\n[bold]Path[/bold]: {attachment.path}",
                        title="[bold green]Clipboard image attached[/bold green]",
                        border_style="green",
                    )
                )
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=[attachment],
                )
                if result is not None:
                    current_session = result.session
                continue
            if command == "/think":
                query = argument.strip()
                if not query:
                    console.print("[bold red]Usage:[/bold red] /think <question>")
                    continue
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=attachments,
                    reasoning_effort=ChatService.THINK_REASONING_EFFORT,
                )
                if result is not None:
                    current_session = result.session
                continue
            if command in {"/think-long-term", "/think-ltm"}:
                query = argument.strip()
                if not query:
                    console.print("[bold red]Usage:[/bold red] /think-long-term <question>")
                    continue
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=attachments,
                    force_deep_context=True,
                    reasoning_effort=ChatService.THINK_REASONING_EFFORT,
                )
                if result is not None:
                    current_session = result.session
                continue
            if command in {"/long-term-memory", "/ltm"}:
                query = argument.strip()
                if not query:
                    console.print("[bold red]Usage:[/bold red] /long-term-memory <question>")
                    continue
                result = _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=query,
                    attachments=attachments,
                    force_deep_context=True,
                )
                if result is not None:
                    current_session = result.session
                continue
            if command == "/synthesize":
                try:
                    with console.status("[bold blue]Synthesizing current discussion...[/bold blue]"):
                        draft = _discussion_synthesizer().synthesize_from_session(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            user_intent=argument,
                            provider=current_session.participants[0].provider,
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                render_synthesis_preview(console,draft)
                if not _confirm_in_shell("Save this synthesis to working memory?", default=True):
                    console.print("[dim]Cancelled synthesis.[/dim]")
                    continue

                try:
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=draft.summary,
                        consensus=draft.consensus,
                        disagreements=draft.disagreements,
                        followups=draft.followups,
                        evidence_refs=_session_evidence_refs(current_session),
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("[green]Discussion synthesis saved.[/green]")
                continue
            if command == "/investigate":
                topic = argument.strip()
                if not topic:
                    console.print("[bold red]Usage:[/bold red] /investigate <topic>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                transcript = service.transcript(current_session.session_id)
                snapshot = service.context_snapshot(current_session.session_id)
                investigation_service = _investigation_service()
                try:
                    related = investigation_service.find_related_reports(current_session.project, topic)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                if related:
                    console.print("")
                    render_related_reports(console,related)
                    if not _confirm_in_shell("Investigate further?", default=True):
                        console.print("[dim]Cancelled investigation.[/dim]")
                        continue

                primary_provider = current_session.participants[0].provider.value
                second_provider = (
                    current_session.participants[1].provider.value
                    if len(current_session.participants) > 1
                    else primary_provider
                )

                try:
                    with console.status("[bold blue]Investigating current topic...[/bold blue]"):
                        result = investigation_service.investigate(
                            project=current_session.project,
                            topic=topic,
                            mode=current_session.mode,
                            provider=primary_provider,
                            second_provider=second_provider,
                            source_session_id=current_session.session_id,
                            session_title=current_session.title,
                            transcript_excerpt=_transcript_excerpt(transcript),
                            session_context=_context_snapshot_excerpt(snapshot),
                        )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                render_investigation_result(console,result)
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_REPORT_CREATED,
                        actor="labit",
                        summary=f"Investigation report created: {result.title}",
                        payload={
                            "title": result.title,
                            "topic": result.topic,
                            "mode": result.mode.value,
                            "run_id": result.run_id,
                            "report_path": result.report_path,
                            "summary": result.summary,
                        },
                        evidence_refs=_session_evidence_refs(current_session) + [f"report:{result.report_path}"],
                    )
                except Exception:
                    pass
                try:
                    consensus = [result.summary] if result.summary else [f"Investigation completed on topic: {result.topic}"]
                    followups = ["Review the report and decide whether follow-up experiments are needed."]
                    disagreements: list[str] = []
                    if result.mode == ChatMode.ROUND_ROBIN:
                        disagreements.append("Round-robin investigation revised an earlier draft; inspect the run artifacts for any unresolved differences.")
                    elif result.mode == ChatMode.PARALLEL:
                        disagreements.append("Parallel investigation merged two independent drafts; inspect the run artifacts for competing framings.")
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=f"Investigation discussion synthesized around topic: {result.topic}",
                        consensus=consensus,
                        disagreements=disagreements,
                        followups=followups,
                        evidence_refs=_session_evidence_refs(current_session) + [f"report:{result.report_path}"],
                    )
                except Exception:
                    pass
                continue
            if command == "/launch-exp":
                sub_arg = argument.strip()
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                experiment_service = _experiment_service()

                # ── Sub-commands when in planning mode ──
                if sub_arg == "status":
                    if active_launch_exp is None:
                        console.print("[dim]Not in experiment planning mode.[/dim]")
                    else:
                        render_launch_exp_status(console,active_launch_exp)
                    continue

                if sub_arg == "approve-tasks":
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.TASK_BREAKDOWN:
                        console.print("[bold red]Error:[/bold red] Not in task breakdown phase.")
                        continue
                    if not active_launch_exp.task_plans:
                        console.print("[bold red]Error:[/bold red] No tasks to approve.")
                        continue
                    dep_err = experiment_service.validate_dependency_graph(active_launch_exp.task_plans)
                    if dep_err:
                        console.print(f"[bold red]Dependency error:[/bold red] {dep_err}")
                        continue
                    active_launch_exp = experiment_service.approve_task_list(active_launch_exp)
                    ct = active_launch_exp.current_task
                    console.print(
                        Panel(
                            f"Task list approved ({len(active_launch_exp.task_plans)} tasks).\n"
                            f"Now planning task details. Starting with: [bold]{ct.id}: {ct.name}[/bold]" if ct else "All tasks already approved.",
                            title="[bold green]Phase: Task Planning[/bold green]",
                            border_style="green",
                        )
                    )
                    print_launch_exp_hints(console, active_launch_exp)
                    continue

                if sub_arg.startswith("approve-task"):
                    task_id = sub_arg.replace("approve-task", "").strip()
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.TASK_PLANNING:
                        console.print("[bold red]Error:[/bold red] Not in task planning phase.")
                        continue
                    if not task_id:
                        ct = active_launch_exp.current_task
                        task_id = ct.id if ct else ""
                    if not task_id:
                        console.print("[bold red]Error:[/bold red] No task to approve.")
                        continue
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
                        print_launch_exp_hints(console, active_launch_exp)
                    else:
                        ct = active_launch_exp.current_task
                        console.print(f"[green]Task {task_id} approved.[/green] Next: [bold]{ct.id}: {ct.name}[/bold]" if ct else f"[green]Task {task_id} approved.[/green]")
                        print_launch_exp_hints(console, active_launch_exp)
                    continue

                if sub_arg.startswith("reopen-task"):
                    task_id = sub_arg.replace("reopen-task", "").strip()
                    if active_launch_exp is None:
                        console.print("[bold red]Error:[/bold red] Not in experiment planning mode.")
                        continue
                    if not task_id:
                        console.print("[bold red]Usage:[/bold red] /launch-exp reopen-task <task_id>")
                        continue
                    try:
                        active_launch_exp = experiment_service.reopen_task(active_launch_exp, task_id)
                        console.print(f"[yellow]Task {task_id} reopened.[/yellow] Now re-planning it.")
                    except ValueError as exc:
                        console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                if sub_arg == "generate-script":
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
                        console.print("[bold red]Error:[/bold red] Not in script generation phase. Approve all tasks first.")
                        continue
                    try:
                        hyp_detail = _hypothesis_service().load_hypothesis(
                            current_session.project,
                            active_launch_exp.hypothesis_id,
                        )
                        code_context = experiment_service.get_code_context(current_session.project)
                        planner = ExperimentPlanner(RepoPaths.discover())
                        first_participant = current_session.participants[0] if current_session.participants else None
                        provider = first_participant.provider if first_participant else None
                        # Get runtime context for the script prompt
                        try:
                            exec_profile = experiment_service.build_default_execution_profile(current_session.project)
                            workdir = exec_profile.workdir or ""
                            setup_summary = exec_profile.setup_script or ""
                        except Exception:
                            workdir = ""
                            setup_summary = ""
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
                    continue

                if sub_arg.startswith("run-task"):
                    task_id = sub_arg.replace("run-task", "").strip()
                    if active_launch_exp is None or active_launch_exp.phase != LaunchExpPhase.SCRIPT_GENERATION:
                        console.print("[bold red]Error:[/bold red] Not in script generation phase. Use /launch-exp resume <experiment_id> first for an existing experiment.")
                        continue
                    if not task_id:
                        console.print("[bold red]Usage:[/bold red] /launch-exp run-task <task_id>")
                        continue
                    known_task_ids = {t.id for t in active_launch_exp.task_plans}
                    if known_task_ids and task_id not in known_task_ids:
                        console.print(f"[bold red]Error:[/bold red] Unknown task '{task_id}'. Known tasks: {', '.join(sorted(known_task_ids))}")
                        continue
                    if not active_launch_exp.run_sh_content:
                        console.print("[bold red]Error:[/bold red] No run.sh available. Use /launch-exp generate-script first.")
                        continue

                    try:
                        hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
                        code_context = experiment_service.get_code_context(current_session.project)
                        planner = ExperimentPlanner(RepoPaths.discover())
                        first_participant = current_session.participants[0] if current_session.participants else None
                        provider = first_participant.provider if first_participant else None
                        tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
                        try:
                            exec_profile = experiment_service.build_default_execution_profile(current_session.project)
                            workdir = exec_profile.workdir or ""
                            setup_summary = exec_profile.setup_script or ""
                        except Exception:
                            workdir = ""
                            setup_summary = ""

                        resume_issues = _run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
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
                            resume_issues = _run_sh_resume_contract_issues(active_launch_exp.run_sh_content)
                            if resume_issues:
                                console.print(
                                    "[bold red]Error:[/bold red] Agent revision did not add the required task-level resume controls. "
                                    "Revise run.sh manually or try again.\n"
                                    f"[dim]Still missing: {', '.join(resume_issues)}[/dim]"
                                )
                                continue
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
                        submit_ok = _submit_and_monitor(
                            console=console,
                            experiment_service=experiment_service,
                            active_launch_exp=active_launch_exp,
                            current_session=current_session,
                            service=service,
                            planner=planner,
                            _hypothesis_service=_hypothesis_service,
                            first_participant=first_participant,
                            env_overrides=env_overrides,
                        )
                        if submit_ok:
                            active_launch_exp = None
                        else:
                            console.print("[yellow]Task retry was not confirmed stable; keeping launch-exp session active for revision.[/yellow]")
                    except Exception as exc:
                        console.print(f"[bold red]Error submitting task retry:[/bold red] {exc}")
                    continue

                if sub_arg == "done":
                    if active_launch_exp is None:
                        console.print("[dim]Not in experiment planning mode.[/dim]")
                        continue
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
                            # Submit + monitor loop
                            submit_ok = _submit_and_monitor(
                                console=console,
                                experiment_service=experiment_service,
                                active_launch_exp=active_launch_exp,
                                current_session=current_session,
                                service=service,
                                planner=ExperimentPlanner(RepoPaths.discover()),
                                _hypothesis_service=_hypothesis_service,
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
                                service.record_session_event(
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
                                    evidence_refs=_session_evidence_refs(current_session)
                                    + [f"hypothesis:{active_launch_exp.hypothesis_id}", f"experiment:{detail.record.experiment_id}"],
                                )
                            except Exception:
                                pass
                        except Exception as exc:
                            console.print(f"[bold red]Error finalizing:[/bold red] {exc}")
                    else:
                        console.print("[dim]No script generated. Experiment not finalized.[/dim]")
                    if submit_ok:
                        active_launch_exp = None
                    continue

                # ── Resume existing experiment ──
                if sub_arg.startswith("resume"):
                    exp_id = sub_arg.replace("resume", "").strip()
                    if not exp_id:
                        console.print("[bold red]Usage:[/bold red] /launch-exp resume <experiment_id>")
                        continue
                    if active_launch_exp is not None:
                        console.print(f"[bold red]Error:[/bold red] Already planning experiment for {active_launch_exp.hypothesis_id}. Use /launch-exp done first.")
                        continue
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
                    continue

                # ── Start new planning session ──
                hypothesis_id = sub_arg
                if not hypothesis_id:
                    console.print("[bold red]Usage:[/bold red] /launch-exp <hypothesis_id>")
                    continue
                if active_launch_exp is not None:
                    console.print(f"[bold red]Error:[/bold red] Already planning experiment for {active_launch_exp.hypothesis_id}. Use /launch-exp done first.")
                    continue

                try:
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, hypothesis_id)
                    active_launch_exp = experiment_service.start_launch_exp_session(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                    )
                    code_tree = experiment_service.get_code_tree(current_session.project)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                # Draft initial task breakdown
                try:
                    planner = ExperimentPlanner(RepoPaths.discover())
                    first_participant = current_session.participants[0] if current_session.participants else None
                    provider = first_participant.provider if first_participant else None
                    with console.status("[bold cyan]Drafting task breakdown...[/bold cyan]"):
                        tasks = planner.draft_task_breakdown(
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            hypothesis_title=hyp_detail.record.title,
                            hypothesis_claim=hyp_detail.record.claim,
                            experiment_plan_md=hyp_detail.experiment_plan_markdown,
                            code_tree=code_tree,
                            provider=provider,
                        )
                    active_launch_exp = experiment_service.save_task_plans(active_launch_exp, tasks)
                except Exception as exc:
                    console.print(f"[bold red]Error drafting tasks:[/bold red] {exc}")
                    active_launch_exp = None
                    continue

                # Display task breakdown
                render_task_breakdown(console,active_launch_exp)
                print_launch_exp_hints(console, active_launch_exp)
                continue
            if command == "/debrief":
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue
                experiment_service = _experiment_service()
                try:
                    experiments = experiment_service.list_experiments(current_session.project)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                rows: list[str] = []
                rows_by_experiment: dict[str, list[str]] = {}
                executor = SSHExecutor(RepoPaths.discover())
                seen_launch_ids: set[str] = set()
                max_rows = 10
                for summary in experiments:
                    detail = experiment_service.load_experiment(current_session.project, summary.experiment_id)

                    if len(rows) >= max_rows:
                        break

                    # ── Scan experiment-level launches (only latest per experiment) ──
                    has_experiment_launch = False
                    launches_dir = experiment_service.tasks_dir(
                        current_session.project, detail.record.experiment_id
                    ) / "launches"
                    if launches_dir.is_dir():
                        # Only show the most recent launch (sorted reverse = newest first)
                        for launch_subdir in sorted(launches_dir.iterdir(), reverse=True):
                            launch_yaml = launch_subdir / "launch.yaml"
                            if not launch_yaml.exists():
                                continue
                            try:
                                artifact = experiment_service.load_launch_artifact(
                                    current_session.project,
                                    detail.record.experiment_id,
                                    launch_subdir.name,
                                )
                            except Exception:
                                continue
                            if not artifact.submission or not artifact.submission.accepted:
                                continue
                            seen_launch_ids.add(artifact.launch_id)
                            try:
                                collected = executor.collect(artifact)
                            except Exception:
                                collected = {}
                            collected_status = str(collected.get("status", "unknown")).strip() or "unknown"
                            log_tail = str(collected.get("log_tail", ""))[:2000]
                            status_label = collected_status
                            if collected_status == "stopped":
                                has_output = collected.get("output_dir_exists") or collected.get("artifact_refs")
                                status_label = "completed" if has_output else "failed"

                            progress_hint = _extract_log_hint(log_tail)
                            error_hint = _task_error_from_collect(collected) or progress_hint

                            # Build display based on status
                            prefix = f"- {detail.record.experiment_id}/{artifact.launch_id}"
                            if status_label == "running":
                                # Show last meaningful log line as progress
                                if progress_hint:
                                    row = f"{prefix} · [green]running[/green] · {progress_hint}"
                                else:
                                    pid_hint = artifact.submission.pid if artifact.submission else None
                                    log_hint = artifact.submission.log_path if artifact.submission else None
                                    if pid_hint and log_hint:
                                        row = f"{prefix} · [green]running[/green] · pid {pid_hint} · log {log_hint}"
                                    elif pid_hint:
                                        row = f"{prefix} · [green]running[/green] · pid {pid_hint}"
                                    else:
                                        row = f"{prefix} · [green]running[/green] · (starting up...)"
                            elif status_label == "failed":
                                # Show error summary
                                error_line = error_hint or "(no log)"
                                row = f"{prefix} · [red]failed[/red] · {error_line}"
                            elif status_label == "completed":
                                # Show metrics if available, otherwise last log line
                                metrics_hint = ""
                                for file_content in collected.get("files", {}).values():
                                    try:
                                        file_data = json.loads(file_content) if isinstance(file_content, str) else file_content
                                    except (json.JSONDecodeError, TypeError):
                                        continue
                                    if isinstance(file_data, dict):
                                        # Check for nested "metrics" dict (experiment_results.json format)
                                        metrics_dict = file_data.get("metrics", {}) if "metrics" in file_data else file_data
                                        if isinstance(metrics_dict, dict):
                                            for k in ("auroc", "accuracy", "loss", "f1", "eval_loss"):
                                                if k in metrics_dict:
                                                    v = metrics_dict[k]
                                                    metrics_hint += f"{k}={v:.4f} " if isinstance(v, float) else f"{k}={v} "
                                        # Prefer conclusion over raw metrics
                                        conclusion = file_data.get("conclusion", "")
                                        if conclusion:
                                            metrics_hint = (conclusion[:80] + (" | " + metrics_hint.strip() if metrics_hint else "")).strip()
                                            break
                                if metrics_hint:
                                    row = f"{prefix} · [blue]completed[/blue] · {metrics_hint.strip()}"
                                elif progress_hint:
                                    row = f"{prefix} · [blue]completed[/blue] · {progress_hint}"
                                else:
                                    row = f"{prefix} · [blue]completed[/blue]"
                            else:
                                row = f"{prefix} · {status_label}"
                                if progress_hint:
                                    row += f" · {progress_hint}"

                            rows.append(row)
                            rows_by_experiment.setdefault(detail.record.experiment_id, []).append(row)
                            has_experiment_launch = True
                            break  # only show latest launch per experiment

                    # ── Scan task-level launches ──
                    if has_experiment_launch or len(rows) >= max_rows:
                        continue
                    for task in detail.tasks:
                        if not task.latest_launch_id:
                            continue
                        if task.latest_launch_id in seen_launch_ids:
                            continue
                        task_record = experiment_service.load_task(
                            current_session.project,
                            detail.record.experiment_id,
                            task.task_id,
                        )
                        artifact = experiment_service.load_launch_artifact(
                            current_session.project,
                            detail.record.experiment_id,
                            task.latest_launch_id,
                        )
                        if not artifact.submission or not artifact.submission.accepted:
                            continue
                        collected = executor.collect(artifact)
                        collected_status = str(collected.get("status", "unknown")).strip() or "unknown"
                        metrics = _collect_task_metrics(collected)
                        summary_text = _task_summary_from_collect(task_record, collected, metrics)
                        error_text = _task_error_from_collect(collected)

                        next_status = task_record.status
                        runtime_updates = {
                            "started_at": task_record.runtime.started_at or (
                                utc_now_iso() if collected_status == "running" else task_record.runtime.started_at
                            ),
                        }
                        if collected_status == "running":
                            next_status = TaskStatus.RUNNING
                        elif collected_status == "stopped":
                            next_status = (
                                TaskStatus.COMPLETED
                                if metrics or collected.get("output_dir_exists") or collected.get("artifact_refs")
                                else TaskStatus.FAILED
                            )
                            runtime_updates["finished_at"] = task_record.runtime.finished_at or utc_now_iso()
                        updated_task = task_record.model_copy(
                            update={
                                "status": next_status,
                                "runtime": task_record.runtime.model_copy(update=runtime_updates),
                                "results": task_record.results.model_copy(
                                    update={
                                        "metrics": metrics or task_record.results.metrics,
                                        "artifact_refs": list(dict.fromkeys(
                                            [*task_record.results.artifact_refs, *(collected.get("artifact_refs", []) or [])]
                                        )),
                                        "summary": summary_text,
                                        "error": error_text,
                                    }
                                ),
                            }
                        )
                        try:
                            experiment_service.save_task_record(
                                project=current_session.project,
                                task=updated_task,
                            )
                        except Exception:
                            pass

                        row = (
                            f"- {detail.record.experiment_id}/{task.task_id}/{artifact.launch_id} · "
                            f"{next_status.value} · {summary_text}"
                        )
                        rows.append(f"[bold]{row.split(' · ', 1)[0]}[/bold] · {row.split(' · ', 1)[1]}")
                        rows_by_experiment.setdefault(detail.record.experiment_id, []).append(row)
                        if len(rows) >= max_rows:
                            break

                console.print("[bold]Debrief[/bold]")
                if not rows:
                    console.print("[dim]No active launches found.[/dim]")
                else:
                    for row in rows:
                        console.print(row)
                    for experiment_id, experiment_rows in rows_by_experiment.items():
                        try:
                            experiment_service.writedebrief_markdown(
                                project=current_session.project,
                                experiment_id=experiment_id,
                                content=debrief_markdown(
                                    experiment_id=experiment_id,
                                    rows=experiment_rows,
                                ),
                            )
                        except Exception:
                            pass
                continue
            if command == "/review-results":
                hypothesis_id = argument.strip()
                if not hypothesis_id:
                    console.print("[bold red]Usage:[/bold red] /review-results <hypothesis_id>")
                    continue
                if not current_session.project:
                    console.print("[bold red]Error:[/bold red] This session is not attached to a project.")
                    continue

                experiment_service = _experiment_service()
                hypothesis_service = _hypothesis_service()

                # --- Step 1: Collect remote results ---
                try:
                    hypothesis_detail = hypothesis_service.load_hypothesis(current_session.project, hypothesis_id)
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                with console.status("[bold blue]Collecting experiment results from remote...[/bold blue]"):
                    try:
                        collected_results = experiment_service.collect_experiment_results(
                            project=current_session.project,
                            hypothesis_id=hypothesis_id,
                        )
                    except Exception as exc:
                        console.print(f"[bold red]Error collecting results:[/bold red] {exc}")
                        collected_results = []

                if not collected_results:
                    console.print("[yellow]No experiments found for this hypothesis.[/yellow]")
                    continue

                # --- Step 2: Show results and let agent assess ---
                from labit.experiments.models import ExperimentAssessment
                results_summary_parts: list[str] = []
                assessable_ids: list[str] = []  # only completed experiments get assessed
                for cr in collected_results:
                    exp_id = cr["experiment_id"]
                    exp_title = cr["title"]
                    collected = cr.get("collected") or {}
                    remote_status = collected.get("status", "unknown")
                    log_tail = collected.get("log_tail", "")
                    files = collected.get("files", {})

                    # Determine if this experiment is assessable (has results)
                    is_running = remote_status == "running"
                    has_files = bool(files)
                    has_log = bool(log_tail and log_tail.strip())

                    # Display each experiment's results
                    status_color = {"running": "blue", "stopped": "yellow"}.get(remote_status, "dim")
                    console.print(f"  [bold]{exp_id}[/bold] ({exp_title}) — [{status_color}]{remote_status}[/{status_color}]")

                    if is_running:
                        console.print(f"    [dim](still running — skipping assessment)[/dim]")
                        continue
                    if not has_files and not has_log:
                        console.print(f"    [dim](no results available — skipping assessment)[/dim]")
                        continue

                    # Check for standardized experiment_results.json first
                    std_results = None
                    if files:
                        for fpath, fcontent in files.items():
                            if fpath.endswith("experiment_results.json"):
                                try:
                                    std_results = json.loads(fcontent) if isinstance(fcontent, str) else fcontent
                                except (json.JSONDecodeError, TypeError):
                                    pass
                                break

                    # Only assess experiments that completed with results, not failed ones
                    exp_failed = False
                    if std_results:
                        exp_status = std_results.get("status", "unknown")
                        exp_metrics = std_results.get("metrics", {})
                        if exp_status == "failed" and not exp_metrics:
                            exp_failed = True

                    # Build text summary for agent
                    part = f"## Experiment {exp_id}: {exp_title}\n"
                    part += f"Remote status: {remote_status}\n"

                    if std_results:
                        # Use the standardized results file
                        exp_status = std_results.get("status", "unknown")
                        metrics = std_results.get("metrics", {})
                        conclusion = std_results.get("conclusion", "")
                        error = std_results.get("error", "")
                        artifacts = std_results.get("artifacts", [])

                        part += f"Experiment status: {exp_status}\n"
                        if metrics:
                            metrics_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
                            part += f"Metrics: {metrics_str}\n"
                            console.print(f"    [green]metrics:[/green] {metrics_str}")
                        if conclusion:
                            part += f"Conclusion: {conclusion}\n"
                            console.print(f"    [cyan]conclusion:[/cyan] {conclusion}")
                        if error:
                            part += f"Error: {error}\n"
                            console.print(f"    [red]error:[/red] {error}")
                        if artifacts:
                            part += f"Artifacts: {', '.join(artifacts)}\n"
                    elif files:
                        # Limit to 2 most relevant files to avoid prompt bloat
                        file_items = list(files.items())
                        # Prioritize results/summary/metrics files
                        def _file_priority(item: tuple) -> int:
                            name = item[0].lower()
                            for i, kw in enumerate(["result", "summary", "metric", "eval"]):
                                if kw in name:
                                    return i
                            return 99
                        file_items.sort(key=_file_priority)
                        for fpath, fcontent in file_items[:2]:
                            fname = fpath.rsplit("/", 1)[-1] if "/" in fpath else fpath
                            content_preview = fcontent[:3000] if len(fcontent) > 3000 else fcontent
                            part += f"\n### {fname}\n```json\n{content_preview}\n```\n"
                            console.print(f"    [dim]{fname}[/dim]: {fcontent[:200]}{'...' if len(fcontent) > 200 else ''}")
                    elif log_tail:
                        last_lines = "\n".join(log_tail.strip().splitlines()[-5:])
                        part += f"\n### Log tail\n```\n{last_lines}\n```\n"
                        console.print(f"    [dim]log:[/dim] {log_tail.strip().splitlines()[-1][:120] if log_tail.strip() else '(empty)'}")
                    else:
                        part += "No results or logs available yet.\n"
                        console.print("    [dim](no results yet)[/dim]")
                    results_summary_parts.append(part)
                    if exp_failed:
                        console.print(f"    [dim](failed without metrics — marking as invalid, skipping assessment)[/dim]")
                    else:
                        assessable_ids.append(exp_id)

                console.print("")

                if not assessable_ids:
                    console.print("[yellow]No completed experiments with results to assess. Wait for running experiments to finish.[/yellow]")
                    continue

                # Cap to most recent 5 experiments to avoid prompt bloat
                if len(results_summary_parts) > 5:
                    results_summary_parts = results_summary_parts[:5]
                    assessable_ids = assessable_ids[:5]
                    console.print(f"[dim](showing 5 most recent experiments out of {len(collected_results)})[/dim]")

                # --- Step 3: Ask agent to assess each experiment ---
                h_rec = hypothesis_detail.record
                assessment_prompt = (
                    f"You are reviewing experiment results for hypothesis {h_rec.hypothesis_id}: \"{h_rec.title}\"\n\n"
                    f"**Claim**: {h_rec.claim}\n"
                    f"**Success criteria**: {h_rec.success_criteria or '(not specified)'}\n"
                    f"**Failure criteria**: {h_rec.failure_criteria or '(not specified)'}\n\n"
                    f"## Collected Results\n\n"
                    + "\n".join(results_summary_parts)
                    + "\n\n---\n\n"
                    f"Assess ONLY the following experiments: {', '.join(assessable_ids)}\n\n"
                    "For each experiment, provide an assessment: `supports`, `contradicts`, `inconclusive`, or `invalid`.\n"
                    "Also provide a brief rationale for each.\n\n"
                    "Format your response as:\n"
                    "ASSESSMENT <experiment_id> <supports|contradicts|inconclusive|invalid>\n"
                    "RATIONALE <experiment_id> <brief explanation>\n\n"
                    "Then provide an overall summary of the evidence."
                )

                console.print("[bold blue]Asking agent to assess results...[/bold blue]")
                _run_streaming_turn(
                    service=service,
                    session=current_session,
                    query=assessment_prompt,
                )

                # --- Step 4: Parse agent's assessments from transcript ---
                transcript = service.transcript(current_session.session_id)
                last_agent_msg = ""
                for msg in reversed(transcript):
                    if msg.role != "user":
                        last_agent_msg = msg.text
                        break

                assessment_map = {
                    "supports": ExperimentAssessment.SUPPORTS,
                    "contradicts": ExperimentAssessment.CONTRADICTS,
                    "inconclusive": ExperimentAssessment.INCONCLUSIVE,
                    "invalid": ExperimentAssessment.INVALID,
                }
                assessed_count = 0
                # Try strict format first: ASSESSMENT <id> <label>
                for match in re.finditer(r"ASSESSMENT\s+(\S+)\s+(supports|contradicts|inconclusive|invalid)", last_agent_msg, re.IGNORECASE):
                    exp_id = match.group(1)
                    assessment_val = match.group(2).lower()
                    if assessment_val in assessment_map:
                        try:
                            experiment_service.update_assessment(
                                project=current_session.project,
                                experiment_id=exp_id,
                                assessment=assessment_map[assessment_val],
                            )
                            assessed_count += 1
                            console.print(f"  [green]Updated {exp_id} → {assessment_val}[/green]")
                        except Exception as exc:
                            console.print(f"  [yellow]Could not update {exp_id}: {exc}[/yellow]")
                # Fallback: try looser patterns like "e001: supports" or "**e001** — supports"
                if assessed_count == 0:
                    for match in re.finditer(r"[*`]*(e[-\w]+)[*`]*\s*[:—\-]\s*(supports|contradicts|inconclusive|invalid)", last_agent_msg, re.IGNORECASE):
                        exp_id = match.group(1)
                        assessment_val = match.group(2).lower()
                        if assessment_val in assessment_map:
                            try:
                                experiment_service.update_assessment(
                                    project=current_session.project,
                                    experiment_id=exp_id,
                                    assessment=assessment_map[assessment_val],
                                )
                                assessed_count += 1
                                console.print(f"  [green]Updated {exp_id} → {assessment_val}[/green]")
                            except Exception as exc:
                                console.print(f"  [yellow]Could not update {exp_id}: {exc}[/yellow]")

                if assessed_count == 0:
                    console.print("[yellow]Could not parse assessments from agent response. You can set them manually.[/yellow]")
                    # Let user set assessments interactively
                    for cr in collected_results:
                        if cr["assessment"] == "pending":
                            exp_id = cr["experiment_id"]
                            choice = Prompt.ask(
                                f"  Assessment for {exp_id}",
                                choices=["supports", "contradicts", "inconclusive", "invalid", "skip"],
                                default="skip",
                            )
                            if choice != "skip" and choice in assessment_map:
                                try:
                                    experiment_service.update_assessment(
                                        project=current_session.project,
                                        experiment_id=exp_id,
                                        assessment=assessment_map[choice],
                                    )
                                    console.print(f"  [green]Updated {exp_id} → {choice}[/green]")
                                except Exception as exc:
                                    console.print(f"  [yellow]Could not update {exp_id}: {exc}[/yellow]")

                # --- Step 5: Generate review suggestion (now with assessments) ---
                try:
                    suggestion = experiment_service.suggest_hypothesis_review(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                console.print("")
                render_review_suggestion(console,suggestion)
                if not _confirm_in_shell("Write this review decision back to the hypothesis?", default=False):
                    console.print("[dim]Kept as suggestion only.[/dim]")
                    continue

                try:
                    now = utc_now_iso()
                    next_state = HypothesisState(suggestion.suggested_state)
                    next_resolution = HypothesisResolution(suggestion.suggested_resolution)
                    updated_record = hypothesis_detail.record.model_copy(
                        update={
                            "state": next_state,
                            "resolution": next_resolution,
                            "result_summary": suggestion.result_summary,
                            "decision_rationale": suggestion.decision_rationale,
                            "supporting_experiment_ids": suggestion.supporting_experiment_ids,
                            "contradicting_experiment_ids": suggestion.contradicting_experiment_ids,
                            "closed_at": (
                                (hypothesis_detail.record.closed_at or now)
                                if next_state == HypothesisState.CLOSED
                                else None
                            ),
                            "updated_at": now,
                        }
                    )
                    saved = hypothesis_service.update_hypothesis_record(
                        project=current_session.project,
                        hypothesis_id=hypothesis_id,
                        record=updated_record,
                    )
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue

                review_markdown = review_markdown(
                    hypothesis_id=hypothesis_id,
                    suggestion=suggestion,
                    saved=saved,
                )
                for experiment_id in suggestion.reviewed_experiment_ids:
                    try:
                        experiment_service.writereview_markdown(
                            project=current_session.project,
                            experiment_id=experiment_id,
                            content=review_markdown,
                        )
                    except Exception:
                        pass

                console.print(
                    Panel(
                        (
                            f"[bold]Hypothesis[/bold]: {saved.record.hypothesis_id}\n"
                            f"[bold]State[/bold]: {saved.record.state.value}\n"
                            f"[bold]Resolution[/bold]: {saved.record.resolution.value}\n"
                            f"[bold]Result[/bold]: {saved.record.result_summary or '(blank)'}"
                        ),
                        title="[bold green]Review saved[/bold green]",
                        border_style="green",
                    )
                )
                try:
                    service.record_session_event(
                        session_id=current_session.session_id,
                        kind=SessionEventKind.ARTIFACT_HYPOTHESIS_REVIEWED,
                        actor="labit",
                        summary=f"Hypothesis reviewed: {saved.record.hypothesis_id} -> {saved.record.state.value}/{saved.record.resolution.value}",
                        payload={
                            "hypothesis_id": saved.record.hypothesis_id,
                            "state": saved.record.state.value,
                            "resolution": saved.record.resolution.value,
                            "supporting_experiment_ids": saved.record.supporting_experiment_ids,
                            "contradicting_experiment_ids": saved.record.contradicting_experiment_ids,
                        },
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{saved.record.hypothesis_id}"]
                        + [f"experiment:{item}" for item in suggestion.reviewed_experiment_ids],
                    )
                except Exception:
                    pass
                try:
                    service.record_discussion_synthesis(
                        session_id=current_session.session_id,
                        summary=(
                            f"Hypothesis {saved.record.hypothesis_id} reviewed as "
                            f"{saved.record.state.value}/{saved.record.resolution.value}."
                        ),
                        consensus=[saved.record.result_summary] if saved.record.result_summary else [],
                        disagreements=[],
                        followups=suggestion.next_steps,
                        evidence_refs=_session_evidence_refs(current_session)
                        + [f"hypothesis:{saved.record.hypothesis_id}"]
                        + [f"experiment:{item}" for item in suggestion.reviewed_experiment_ids],
                    )
                except Exception:
                    pass
                continue
            if command == "/new":
                title = _prompt_optional("Title", default="Free Conversation")
                mode = _prompt_mode()
                provider = _prompt_optional("Primary provider", default="auto") or "auto"
                second_provider = "auto"
                if mode != ChatMode.SINGLE:
                    second_provider = _prompt_optional("Secondary provider", default="auto") or "auto"
                try:
                    current_session = service.open_session(
                        title=title,
                        mode=mode,
                        provider=provider,
                        second_provider=second_provider,
                        project=_project_service().active_project_name(),
                    )
                    active_doc = None
                    active_hypothesis = None
                    active_launch_exp = None
                    active_dev = None
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                render_shell_header(console,current_session)
                continue
            if command == "/switch":
                if not argument:
                    console.print("[bold red]Usage:[/bold red] /switch <session_id>")
                    continue
                try:
                    current_session = service.load_session(argument)
                    active_doc = None
                    active_hypothesis = None
                    active_launch_exp = None
                    active_dev = None
                except Exception as exc:
                    console.print(f"[bold red]Error:[/bold red] {exc}")
                    continue
                console.print("")
                render_shell_header(console,current_session)
                console.print("")
                render_recent_messages(console,service.transcript(current_session.session_id), count=8)
                continue

            console.print(f"[bold red]Unknown command:[/bold red] {command}")
            continue

        # ── Dev loop decision handling ──
        if active_dev is not None and active_dev.status == "waiting_decision":
            # User is answering a decision question
            decision = active_dev.pending_decision
            if decision:
                # Check if it's a letter choice
                choice = raw.strip().upper()
                if len(choice) == 1 and "A" <= choice <= chr(ord("A") + len(decision.options) - 1):
                    idx = ord(choice) - ord("A")
                    active_dev.user_decision = f"Option {choice}: {decision.options[idx]}"
                else:
                    active_dev.user_decision = raw.strip()

                active_dev.pending_decision = None
                active_dev.status = "active"
                console.print(f"[bold green]Decision recorded:[/bold green] {active_dev.user_decision}")
                active_dev = run_dev_loop(
                    service=service,
                    session=current_session,
                    dev_session=active_dev,
                    console=console,
                    run_streaming_turn=_run_streaming_turn,
                )
            continue

        # ── Launch-exp planning mode ──
        if active_launch_exp is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Experiment planning mode does not support image attachments yet.")
                continue
            exp_service = _experiment_service()
            planner = ExperimentPlanner(RepoPaths.discover())
            first_participant = current_session.participants[0] if current_session.participants else None
            provider = first_participant.provider if first_participant else None
            phase = active_launch_exp.phase

            try:
                exp_service.log_user_instruction(active_launch_exp, raw)

                if phase == LaunchExpPhase.TASK_BREAKDOWN:
                    # User is iterating on task list
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
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
                    exp_service.log_agent_revision(active_launch_exp, f"Revised task breakdown: {len(revised_tasks)} tasks", first_participant.name if first_participant else "")
                    render_task_breakdown(console,active_launch_exp)

                elif phase == LaunchExpPhase.TASK_PLANNING:
                    # User is iterating on current task's detail
                    ct = active_launch_exp.current_task
                    if ct is None:
                        console.print("[dim]No task to plan. Use /launch-exp approve-tasks or /launch-exp done.[/dim]")
                        continue
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
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
                    exp_service.log_agent_revision(active_launch_exp, f"Planned {ct.id}: {ct.name}", first_participant.name if first_participant else "")
                    render_task_detail(console,detailed_task)

                elif phase == LaunchExpPhase.SCRIPT_GENERATION:
                    # User is iterating on run.sh
                    hyp_detail = _hypothesis_service().load_hypothesis(current_session.project, active_launch_exp.hypothesis_id)
                    code_tree = exp_service.get_code_context(current_session.project)
                    tasks_json = json.dumps([t.model_dump() for t in active_launch_exp.task_plans], indent=2)
                    # Get runtime context for revision prompt
                    try:
                        exec_profile = exp_service.build_default_execution_profile(current_session.project)
                        workdir = exec_profile.workdir or ""
                        setup_summary = exec_profile.setup_script or ""
                    except Exception:
                        workdir = ""
                        setup_summary = ""
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
                    exp_service.log_agent_revision(active_launch_exp, result["summary"], first_participant.name if first_participant else "")
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
            continue

        # ── Hypothesis editing mode ──
        if active_hypothesis is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Hypothesis mode does not support image attachments yet.")
                continue
            h_id, h_proj, h_draft = active_hypothesis
            hyp_drafter = _hypothesis_drafter()
            hyp_svc = _hypothesis_service()
            author = current_session.participants[0]
            try:
                # Log user instruction
                hyp_svc.log_event(h_proj, h_id, "user_instruction", content=raw)

                # Step 1: Author revises hypothesis
                with console.status(f"[bold blue]{author.name} revising hypothesis...[/bold blue]"):
                    revised_draft = hyp_drafter.revise_hypothesis(
                        current_draft=h_draft,
                        session=current_session,
                        transcript=service.transcript(current_session.session_id),
                        context_snapshot=service.context_snapshot(current_session.session_id),
                        user_instruction=raw,
                        interaction_log=hyp_svc.interaction_excerpt(h_proj, h_id),
                        provider=author.provider,
                    )
                    detail = hyp_svc.revise_hypothesis_files(
                        project=h_proj,
                        hypothesis_id=h_id,
                        draft=revised_draft,
                        user_instruction=raw,
                        agent_name=author.name,
                    )

                # Update active state
                active_hypothesis = (h_id, h_proj, revised_draft)

                # Show revision summary
                console.print(
                    Panel(
                        (
                            f"[bold]ID[/bold]: {h_id}\n"
                            f"[bold]Title[/bold]: {revised_draft.title}\n"
                            f"[bold]Claim[/bold]: {revised_draft.claim}\n"
                            f"[bold]Success criteria[/bold]: {revised_draft.success_criteria or '(blank)'}\n"
                            f"[bold]Failure criteria[/bold]: {revised_draft.failure_criteria or '(blank)'}"
                        ),
                        title=f"[bold green]{author.name} · Hypothesis revised[/bold green]",
                        border_style="green",
                    )
                )

                # Step 2: Reviewer refines (round-robin only)
                if (
                    current_session.mode == ChatMode.ROUND_ROBIN
                    and len(current_session.participants) >= 2
                ):
                    reviewer = current_session.participants[1]
                    with console.status(f"[bold cyan]{reviewer.name} reviewing hypothesis...[/bold cyan]"):
                        refined_draft = hyp_drafter.refine_draft(
                            draft=revised_draft,
                            session=current_session,
                            transcript=service.transcript(current_session.session_id),
                            context_snapshot=service.context_snapshot(current_session.session_id),
                            user_intent=raw,
                            provider=reviewer.provider,
                        )
                        hyp_svc.revise_hypothesis_files(
                            project=h_proj,
                            hypothesis_id=h_id,
                            draft=refined_draft,
                            user_instruction=f"Review refinement based on: {raw}",
                            agent_name=reviewer.name,
                        )
                    active_hypothesis = (h_id, h_proj, refined_draft)

                    # Show what changed
                    changes: list[str] = []
                    if refined_draft.claim != revised_draft.claim:
                        changes.append(f"Claim: {refined_draft.claim}")
                    if refined_draft.success_criteria != revised_draft.success_criteria:
                        changes.append(f"Success criteria: {refined_draft.success_criteria}")
                    if refined_draft.failure_criteria != revised_draft.failure_criteria:
                        changes.append(f"Failure criteria: {refined_draft.failure_criteria}")
                    change_text = "\n".join(changes) if changes else "Minor refinements only."
                    console.print(
                        Panel(
                            change_text,
                            title=f"[bold cyan]{reviewer.name} · Review refinement[/bold cyan]",
                            border_style="cyan",
                        )
                    )
            except Exception as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                continue
            try:
                service.record_session_event(
                    session_id=current_session.session_id,
                    kind=SessionEventKind.ARTIFACT_HYPOTHESIS_UPDATED,
                    actor="labit",
                    summary=f"Hypothesis revised: {h_id}",
                    payload={"hypothesis_id": h_id},
                    evidence_refs=_session_evidence_refs(current_session) + [f"hypothesis:{h_id}"],
                )
            except Exception:
                pass
            continue

        if active_doc is not None:
            if attachments:
                console.print("[bold red]Error:[/bold red] Document mode does not support image attachments yet.")
                continue
            doc_service = _document_service()
            drafter = _doc_drafter()
            author = current_session.participants[0]
            # Round-robin: second participant is reviewer
            reviewer = (
                current_session.participants[1]
                if current_session.mode == ChatMode.ROUND_ROBIN and len(current_session.participants) >= 2
                else None
            )
            try:
                # Capture pre-revision markdown for diff
                old_markdown = doc_service.read_document(active_doc)

                # Step 1: Author revises
                with console.status(f"[bold blue]{author.name} updating document...[/bold blue]"):
                    update = drafter.revise_document(
                        session=current_session,
                        transcript=service.transcript(current_session.session_id),
                        context_snapshot=service.context_snapshot(current_session.session_id),
                        doc_title=active_doc.title,
                        current_markdown=old_markdown,
                        user_instruction=raw,
                        interaction_log=doc_service.interaction_excerpt(active_doc),
                        author_name=author.name,
                        provider=author.provider,
                    )
                    active_doc = doc_service.revise_document(
                        doc_session=active_doc,
                        update=update,
                        user_instruction=raw,
                    )

                console.print(
                    Panel(
                        (
                            f"[bold]ID[/bold]: {active_doc.doc_id}\n"
                            f"[bold]Document[/bold]: {active_doc.document_path}\n"
                            f"[bold]Iteration[/bold]: {active_doc.iteration}\n"
                            f"[bold]Summary[/bold]: {update.summary}"
                        ),
                        title=f"[bold green]{author.name} · Document updated[/bold green]",
                        border_style="green",
                    )
                )

                # Step 2: Reviewer adds inline review blocks (round-robin only)
                if reviewer is not None:
                    from labit.documents.drafter import compute_changed_sections

                    new_markdown = doc_service.read_document(active_doc)
                    changed_sections = compute_changed_sections(old_markdown, new_markdown)

                    with console.status(f"[bold cyan]{reviewer.name} reviewing document...[/bold cyan]"):
                        review_update = drafter.review_document(
                            current_markdown=new_markdown,
                            revision_summary=update.summary,
                            user_instruction=raw,
                            reviewer_name=reviewer.name,
                            changed_sections=changed_sections,
                            provider=reviewer.provider,
                        )
                        active_doc = doc_service.record_review(
                            doc_session=active_doc,
                            update=review_update,
                            reviewer_name=reviewer.name,
                        )
                    console.print(
                        Panel(
                            f"[bold]Review[/bold]: {review_update.summary}",
                            title=f"[bold cyan]{reviewer.name} · Review[/bold cyan]",
                            border_style="cyan",
                        )
                    )
            except Exception as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                continue
            try:
                service.record_session_event(
                    session_id=current_session.session_id,
                    kind=SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
                    actor="labit",
                    summary=f"Document updated: {active_doc.title}",
                    payload={
                        "doc_id": active_doc.doc_id,
                        "title": active_doc.title,
                        "document_path": active_doc.document_path,
                        "log_path": active_doc.log_path,
                        "iteration": active_doc.iteration,
                        "agent_summary": update.summary,
                    },
                    evidence_refs=_session_evidence_refs(current_session) + [f"document:{active_doc.document_path}"],
                )
            except Exception:
                pass
            continue

        result = _run_streaming_turn(
            service=service,
            session=current_session,
            query=raw,
            attachments=attachments,
            skip_participants=muted_next_turn if muted_next_turn else None,
        )
        if muted_next_turn:
            muted_next_turn.clear()
        if result is not None:
            current_session = result.session


@chat_app.callback()
def chat_callback(
    ctx: typer.Context,
    title: str | None = typer.Option(None, "--title", help="Optional title when opening the shell."),
    mode: ChatMode | None = typer.Option(None, "--mode", help="Optional mode when opening the shell."),
    provider: str = typer.Option("auto", "--provider", help="Primary provider when opening the shell."),
    second_provider: str = typer.Option("auto", "--second-provider", help="Secondary provider when opening the shell."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    service = _chat_service()
    try:
        session, created = _open_default_session(
            service=service,
            title=title,
            mode=mode,
            provider=provider,
            second_provider=second_provider,
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=False))
    if created:
        console.print("[dim]Created a new chat session.[/dim]")
    else:
        console.print("[dim]Resuming the most recent chat session.[/dim]")
    run_chat_shell(session=session, service=service)


@chat_app.command("open")
def open_chat(
    title: str | None = typer.Option(None, "--title", help="Optional session title."),
    mode: ChatMode | None = typer.Option(None, "--mode", help="single, round_robin, or parallel."),
    provider: str = typer.Option("auto", "--provider", help="Primary provider: auto, claude, or codex."),
    second_provider: str = typer.Option("auto", "--second-provider", help="Secondary provider for multi-agent modes."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    project = _project_service().active_project_name()
    if not json_output:
        console.print("[bold]Open Chat[/bold]")
        console.print(f"[dim]Active project: {project or '(none)'}[/dim]")

    resolved_title = title or _prompt_optional("Title", default="Free Conversation")
    resolved_mode = mode or _prompt_mode()

    service = _chat_service()
    try:
        session = service.open_session(
            title=resolved_title,
            mode=resolved_mode,
            provider=provider,
            second_provider=second_provider,
            project=project,
        )
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(session.model_dump(mode="json"), as_json=True)
        return
    render_session_summary(console,session)
    run_chat_shell(session=session, service=service)


@chat_app.command("list")
def list_chats(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    sessions = _chat_service().list_sessions()
    if json_output:
        _emit([session.model_dump(mode="json") for session in sessions], as_json=True)
        return
    if not sessions:
        console.print("[dim]No chat sessions yet.[/dim]")
        return
    table = Table(title="Chat Sessions", show_header=True, header_style=f"bold {COMMAND_COLOR}")
    table.add_column("Session")
    table.add_column("Title")
    table.add_column("Mode")
    table.add_column("Project")
    table.add_column("Participants")
    table.add_column("Status")
    for session in sessions:
        participants = ", ".join(item.name for item in session.participants)
        table.add_row(
            session.session_id,
            session.title,
            session.mode.value,
            session.project or "(none)",
            participants,
            session.status.value,
        )
    console.print(table)


@chat_app.command("show")
def show_chat(
    session_id: str = typer.Argument(..., help="Session id."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _chat_service()
    try:
        session = service.load_session(session_id)
        transcript = service.transcript(session_id)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "session": session.model_dump(mode="json"),
                "transcript": [message.model_dump(mode="json") for message in transcript],
            },
            as_json=True,
        )
        return

    render_session_summary(console,session)
    console.print("")
    render_transcript(console,transcript)


@chat_app.command("ask")
def ask_chat(
    session_id: str = typer.Argument(..., help="Session id."),
    message: str | None = typer.Argument(None, help="Optional user message."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _chat_service()
    resolved_message = message or _prompt_text("Message")
    try:
        result = service.ask(session_id=session_id, content=resolved_message)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "session": result.session.model_dump(mode="json"),
                "user_message": result.user_message.model_dump(mode="json"),
                "replies": [
                    {
                        "participant": reply.participant.model_dump(mode="json"),
                        "message": reply.message.model_dump(mode="json"),
                    }
                    for reply in result.replies
                ],
                "context": result.context_snapshot.model_dump(mode="json"),
            },
            as_json=True,
        )
        return

    render_session_summary(console,result.session)
    console.print("")
    console.print(f"[bold][turn {result.user_message.turn_index}] user[/bold]")
    console.print(result.user_message.content)
    console.print("")
    for reply in result.replies:
        console.print(f"[cyan][turn {reply.message.turn_index}] {reply.participant.name}[/cyan]")
        console.print(md(reply.message.content))
        console.print("")


@chat_app.command("resume")
def resume_chat(
    session_id: str = typer.Argument(..., help="Session id."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    service = _chat_service()
    try:
        session = service.load_session(session_id)
        transcript = service.transcript(session_id)
    except Exception as exc:
        raise typer.Exit(code=_fail(str(exc), as_json=json_output))

    if json_output:
        _emit(
            {
                "session": session.model_dump(mode="json"),
                "transcript": [message.model_dump(mode="json") for message in transcript],
            },
            as_json=True,
        )
        return

    console.print(f"[dim]Resuming chat session {session_id}.[/dim]")
    run_chat_shell(session=session, service=service)
