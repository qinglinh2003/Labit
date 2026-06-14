from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.agents.adapters.base import AgentAdapter, AgentAdapterError, stream_subprocess_lines
from labit.agents.models import AgentRequest, AgentResponse, ProviderKind


class CodexAdapter(AgentAdapter):
    provider = ProviderKind.CODEX

    def run(self, request: AgentRequest) -> AgentResponse:
        prompt = request.prompt
        if request.system_prompt:
            prompt = f"{request.system_prompt}\n\n{request.prompt}"

        with (
            NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as out_handle,
            NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as schema_handle,
        ):
            out_path = Path(out_handle.name)
            schema_path = Path(schema_handle.name)

            cmd = [
                _codex_executable(),
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "--ephemeral",
                "--color",
                "never",
                "--output-last-message",
                str(out_path),
            ]

            if request.cwd:
                cmd[2:2] = ["-C", request.cwd]

            if request.output_schema:
                schema_handle.write(json.dumps(request.output_schema))
                schema_handle.flush()
                cmd.extend(["--output-schema", str(schema_path)])

            if request.image_paths:
                for image_path in request.image_paths:
                    cmd.extend(["--image", image_path])

            if request.extra_args:
                cmd.extend(request.extra_args)

            cmd.append("-")

            try:
                subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=request.cwd,
                    check=True,
                    timeout=None,
                )
            except subprocess.TimeoutExpired as exc:
                raise AgentAdapterError("Codex adapter timed out.") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                raise AgentAdapterError(f"Codex adapter failed: {detail}") from exc

        raw_output = out_path.read_text().strip()
        structured_output = None
        if request.output_schema:
            try:
                structured_output = json.loads(raw_output)
            except json.JSONDecodeError:
                structured_output = raw_output

        out_path.unlink(missing_ok=True)
        schema_path.unlink(missing_ok=True)

        return AgentResponse(
            provider=self.provider,
            raw_output=raw_output,
            structured_output=structured_output,
            session_id=request.session_id,
            command=cmd,
        )

    def run_stream(
        self,
        request: AgentRequest,
        *,
        on_text: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AgentResponse:
        if request.output_schema:
            return super().run_stream(
                request,
                on_text=on_text,
                on_status=on_status,
                cancel_event=cancel_event,
            )

        prompt = request.prompt
        if request.system_prompt:
            prompt = f"{request.system_prompt}\n\n{request.prompt}"

        cmd = [
            _codex_executable(),
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "--json",
        ]
        if request.cwd:
            cmd[2:2] = ["-C", request.cwd]
        if request.image_paths:
            for image_path in request.image_paths:
                cmd.extend(["--image", image_path])
        if request.extra_args:
            cmd.extend(request.extra_args)

        out_handle = NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8")
        out_path = Path(out_handle.name)
        out_handle.close()

        cmd.extend(["--output-last-message", str(out_path), "-"])

        raw_output = ""
        session_id = request.session_id
        # Track which item IDs had their text already streamed via deltas,
        # so we don't double-emit on item.completed.
        delta_emitted_items: set[str] = set()
        current_delta_item: str | None = None

        def _emit_status(message: str) -> None:
            if on_status is not None and message:
                on_status(message)

        def _handle_stdout(line: str) -> None:
            nonlocal raw_output, session_id, current_delta_item
            stripped = line.strip()
            if not stripped:
                return
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                return

            payload_type = payload.get("type")
            if payload_type == "thread.started":
                session_id = str(payload.get("thread_id") or session_id)
                _emit_status("started")
                return

            if payload_type == "turn.started":
                _emit_status("thinking")
                return

            if payload_type == "turn.completed":
                _emit_status("finishing")
                return

            if payload_type in {"message.delta", "agent_message.delta", "response.delta"}:
                chunk = _extract_codex_text_delta(payload)
                if chunk and on_text is not None:
                    on_text(chunk)
                    # Mark current item as already streamed via deltas
                    if current_delta_item:
                        delta_emitted_items.add(current_delta_item)
                return

            if payload_type in {"item.created", "item.started"}:
                item = payload.get("item") or {}
                item_id = str(item.get("id", ""))
                if item.get("type") == "agent_message":
                    current_delta_item = item_id
                _emit_status(_describe_codex_item(item, prefix="running"))
                return

            if payload_type == "item.completed":
                item = payload.get("item") or {}
                if item.get("type") != "agent_message":
                    _emit_status(_describe_codex_item(item, prefix="completed"))
                    return
                item_id = str(item.get("id", ""))
                text = _extract_codex_item_text(item)
                raw_output = text.strip() or raw_output
                # Emit if this item wasn't already streamed via deltas
                if on_text is not None and text and item_id not in delta_emitted_items:
                    on_text(text)
                current_delta_item = None

        try:
            result = stream_subprocess_lines(
                cmd,
                cwd=request.cwd,
                input_text=prompt,
                on_stdout_line=_handle_stdout,
                cancel_event=cancel_event,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentAdapterError("Codex adapter timed out.") from exc

        final_output = out_path.read_text().strip() if out_path.exists() else ""
        out_path.unlink(missing_ok=True)

        if result.returncode != 0:
            detail = "".join(result.stderr_lines).strip() or "".join(result.stdout_lines).strip()
            raise AgentAdapterError(f"Codex adapter failed: {detail}")

        raw_output = final_output or raw_output

        return AgentResponse(
            provider=self.provider,
            raw_output=raw_output,
            structured_output=None,
            session_id=session_id,
            command=cmd,
        )


def _codex_executable() -> str:
    # Prefer user-installed version (typically newer) over system-wide
    npm_global = Path.home() / ".npm-global" / "bin" / "codex"
    if npm_global.exists():
        return str(npm_global)

    executable = shutil.which("codex")
    if executable:
        return executable

    return "codex"


def _describe_codex_item(item: object, *, prefix: str) -> str:
    if not isinstance(item, dict):
        return prefix

    item_type = str(item.get("type") or "item").replace("_", " ")
    if item_type == "command execution":
        command = _compact_status_text(str(item.get("command") or "command"))
        return f"{prefix} command: {command}"
    if item_type == "function call":
        name = str(item.get("name") or item.get("function") or "tool")
        return f"{prefix} tool: {name}"
    if item_type == "function call output":
        return f"{prefix} tool output"
    if item_type == "reasoning":
        return f"{prefix} reasoning"
    if item_type == "agent message":
        return f"{prefix} response"
    return f"{prefix} {item_type}"


def _compact_status_text(value: str, *, limit: int = 80) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}..."


def _extract_codex_text_delta(payload: dict) -> str:
    for key in ("text", "delta", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("text") or value.get("content")
            if isinstance(nested, str):
                return nested
    return ""


def _extract_codex_item_text(item: dict) -> str:
    value = item.get("text")
    if isinstance(value, str):
        return value

    value = item.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    value = item.get("message")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return text

    return ""
