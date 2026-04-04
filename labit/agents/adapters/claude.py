from __future__ import annotations

import json
import subprocess

from labit.agents.adapters.base import AgentAdapter, AgentAdapterError
from labit.agents.models import AgentRequest, AgentResponse, ProviderKind


class ClaudeAdapter(AgentAdapter):
    provider = ProviderKind.CLAUDE

    def run(self, request: AgentRequest) -> AgentResponse:
        cmd = ["claude", "-p", request.prompt]

        if request.system_prompt:
            cmd.extend(["--system-prompt", request.system_prompt])
        if request.output_schema:
            cmd.extend(["--output-format", "json", "--json-schema", json.dumps(request.output_schema)])
        else:
            cmd.extend(["--output-format", "text"])
        if request.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(request.allowed_tools)])
        if request.session_id:
            cmd.extend(["--session-id", request.session_id])
        if request.extra_args:
            cmd.extend(request.extra_args)

        try:
            result = subprocess.run(
                cmd,
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
        if request.output_schema:
            try:
                structured_output = json.loads(raw_output)
            except json.JSONDecodeError:
                structured_output = raw_output

        return AgentResponse(
            provider=self.provider,
            raw_output=raw_output,
            structured_output=structured_output,
            session_id=request.session_id,
            command=cmd,
        )
