from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.agents.adapters.base import AgentAdapter, AgentAdapterError
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
