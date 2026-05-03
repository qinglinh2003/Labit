from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable

from labit.agents.adapters.base import AgentAdapter, AgentAdapterError, stream_subprocess_lines
from labit.agents.models import AgentRequest, AgentResponse, ProviderKind


class ClaudeAdapter(AgentAdapter):
    provider = ProviderKind.CLAUDE

    def run(self, request: AgentRequest) -> AgentResponse:
        request = request.model_copy(update={"prompt": self._augment_prompt(request)})
        cmd = self._build_command(request, stream=False)

        try:
            result = subprocess.run(
                cmd,
                input=request.prompt,
                capture_output=True,
                text=True,
                cwd=request.cwd,
                check=True,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentAdapterError(
                f"Claude adapter timed out after {request.timeout_seconds}s."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise AgentAdapterError(f"Claude adapter failed: {detail}") from exc

        raw_output = result.stdout.strip()
        structured_output = None
        parsed = None
        if request.output_schema:
            try:
                parsed = json.loads(raw_output)
                if isinstance(parsed, dict) and parsed.get("is_error"):
                    raise AgentAdapterError(
                        str(parsed.get("result", "Claude returned an error"))
                    )
                if isinstance(parsed, dict) and "structured_output" in parsed:
                    structured_output = parsed.get("structured_output")
                else:
                    structured_output = parsed
            except json.JSONDecodeError:
                structured_output = raw_output

        return AgentResponse(
            provider=self.provider,
            raw_output=raw_output,
            structured_output=structured_output,
            session_id=parsed.get("session_id") if isinstance(parsed, dict) else request.session_id,
            command=cmd,
        )

    def run_stream(
        self,
        request: AgentRequest,
        *,
        on_text: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AgentResponse:
        if request.output_schema:
            return super().run_stream(request, on_text=on_text, cancel_event=cancel_event)

        request = request.model_copy(update={"prompt": self._augment_prompt(request)})
        cmd = self._build_command(request, stream=True)
        final_text = ""
        streamed_parts: list[str] = []
        session_id = request.session_id

        stream_error: str | None = None

        def _handle_stdout(line: str) -> None:
            nonlocal final_text, session_id, stream_error
            stripped = line.strip()
            if not stripped:
                return
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                return

            if isinstance(payload, dict) and payload.get("session_id"):
                session_id = payload.get("session_id") or session_id

            payload_type = payload.get("type")
            if payload_type == "stream_event":
                event = payload.get("event") or {}
                if event.get("type") != "content_block_delta":
                    return
                delta = event.get("delta") or {}
                chunk = str(delta.get("text", "")).strip("\n")
                if not chunk:
                    return
                streamed_parts.append(chunk)
                if on_text is not None:
                    on_text(chunk)
                return

            if payload_type == "assistant":
                message = payload.get("message") or {}
                content = message.get("content") or []
                text_blocks = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
                final_text = "".join(text_blocks).strip() or final_text
                return

            if payload_type == "result":
                if payload.get("is_error"):
                    stream_error = str(payload.get("result", "Claude returned an error"))
                final_text = str(payload.get("result", "")).strip() or final_text

        try:
            result = stream_subprocess_lines(
                cmd,
                cwd=request.cwd,
                timeout_seconds=request.timeout_seconds,
                input_text=request.prompt,
                on_stdout_line=_handle_stdout,
                cancel_event=cancel_event,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentAdapterError(
                f"Claude adapter timed out after {request.timeout_seconds}s."
            ) from exc

        if stream_error:
            raise AgentAdapterError(stream_error)

        if result.returncode != 0:
            detail = "".join(result.stderr_lines).strip() or "".join(result.stdout_lines).strip()
            raise AgentAdapterError(f"Claude adapter failed: {detail}")

        raw_output = "".join(streamed_parts).strip() or final_text.strip()
        return AgentResponse(
            provider=self.provider,
            raw_output=raw_output,
            structured_output=None,
            session_id=session_id,
            command=cmd,
        )

    def _build_command(self, request: AgentRequest, *, stream: bool) -> list[str]:
        cmd = ["claude", "-p", "--input-format", "text", "--effort", "max"]

        if request.system_prompt:
            cmd.extend(["--system-prompt", request.system_prompt])
        if request.output_schema and not stream:
            cmd.extend(["--output-format", "json", "--json-schema", json.dumps(request.output_schema)])
        elif stream:
            cmd.extend(["--verbose", "--output-format", "stream-json", "--include-partial-messages"])
        else:
            cmd.extend(["--output-format", "text"])
        if request.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(request.allowed_tools)])
        if request.session_id:
            cmd.extend(["--session-id", request.session_id])
        if request.extra_args:
            cmd.extend(request.extra_args)
        return cmd

    def _augment_prompt(self, request: AgentRequest) -> str:
        if not request.image_paths:
            return request.prompt
        image_lines = "\n".join(f"- {path}" for path in request.image_paths)
        return (
            f"{request.prompt}\n\n"
            "Attached image file(s):\n"
            f"{image_lines}\n\n"
            "Treat these image files as part of the user input. Inspect them directly when answering."
        )
