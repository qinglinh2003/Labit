from __future__ import annotations

import json
import subprocess
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
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
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
                    timeout=request.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise AgentAdapterError(
                    f"Codex adapter timed out after {request.timeout_seconds}s."
                ) from exc
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
    ) -> AgentResponse:
        if request.output_schema:
            return super().run_stream(request, on_text=on_text)

        prompt = request.prompt
        if request.system_prompt:
            prompt = f"{request.system_prompt}\n\n{request.prompt}"

        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
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
        cmd.append("-")

        raw_output = ""
        session_id = request.session_id
        emitted = False

        def _handle_stdout(line: str) -> None:
            nonlocal raw_output, session_id, emitted
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
                return

            if payload_type == "item.completed":
                item = payload.get("item") or {}
                if item.get("type") != "agent_message":
                    return
                text = str(item.get("text", ""))
                raw_output = text.strip() or raw_output
                if on_text is not None and text and not emitted:
                    on_text(text)
                    emitted = True

        try:
            result = stream_subprocess_lines(
                cmd,
                cwd=request.cwd,
                timeout_seconds=request.timeout_seconds,
                input_text=prompt,
                on_stdout_line=_handle_stdout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentAdapterError(
                f"Codex adapter timed out after {request.timeout_seconds}s."
            ) from exc

        if result.returncode != 0:
            detail = "".join(result.stderr_lines).strip() or "".join(result.stdout_lines).strip()
            raise AgentAdapterError(f"Codex adapter failed: {detail}")

        return AgentResponse(
            provider=self.provider,
            raw_output=raw_output,
            structured_output=None,
            session_id=session_id,
            command=cmd,
        )
