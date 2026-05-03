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
    md,
    message_body,
    print_doc_mode_hints,
    render_compact_transcript,
    render_console_header,
    render_message_block,
    render_recent_messages,
    render_review_suggestion,
    render_session_summary,
    render_shell_header,
    render_shell_help,
    render_transcript,
    render_user_shell_message,
    review_markdown,
    sanitize_markdown,
    transcript_preview_text,
)
from labit.chat.composer import ComposerResult, prompt_toolkit_available, prompt_with_clipboard_image
from labit.chat.models import ChatMode
from labit.chat.service import ChatService
from labit.context.events import SessionEventKind
from labit.documents.commands import handle_document_command
from labit.documents.drafter import DocDrafter
from labit.documents.models import DocSession
from labit.documents.service import DocumentService
from labit.experiments.executors.ssh import SSHExecutor
from labit.experiments.models import (
    TaskStatus,
)
from labit.experiments.service import ExperimentService
from labit.hypotheses.models import HypothesisResolution, HypothesisState, utc_now_iso
from labit.hypotheses.service import HypothesisService
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


def _experiment_service() -> ExperimentService:
    return ExperimentService(RepoPaths.discover())


def _doc_drafter() -> DocDrafter:
    return DocDrafter(RepoPaths.discover())


def _document_service() -> DocumentService:
    return DocumentService(RepoPaths.discover())


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
    muted_next_turn: set[str] = set()  # agent names to skip on next turn only
    dispatcher = SlashCommandDispatcher()
    dispatcher.register("/auto", lambda ctx, arg: handle_auto_command(ctx=ctx, argument=arg))

    for capture_command in ("/idea", "/todo"):
        dispatcher.register(
            capture_command,
            lambda ctx, arg, command=capture_command: handle_capture_command(
                ctx=ctx,
                command=command,
                argument=arg,
            ),
        )

    dispatcher.register("/memory", lambda ctx, arg: handle_memory_command(ctx=ctx, argument=arg))

    def _handle_document(ctx: ChatContext, arg: str) -> None:
        nonlocal active_doc
        result = handle_document_command(
            ctx=ctx,
            argument=arg,
            active_doc=active_doc,
        )
        active_doc = result.active_doc

    dispatcher.register("/doc", _handle_document)
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
