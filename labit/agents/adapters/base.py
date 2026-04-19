from __future__ import annotations

import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from queue import Empty, Queue

from pydantic import BaseModel, ConfigDict, Field

from labit.agents.models import AgentRequest, AgentResponse, ProviderKind


class AgentAdapterError(RuntimeError):
    """Raised when an agent backend fails."""


class StreamCancelled(Exception):
    """Raised when a streaming operation is cancelled (e.g. user pressed ESC)."""


class StreamProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returncode: int
    stdout_lines: list[str] = Field(default_factory=list)
    stderr_lines: list[str] = Field(default_factory=list)


def stream_subprocess_lines(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    input_text: str | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> StreamProcessResult:
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    if input_text is not None and process.stdin is not None:
        process.stdin.write(input_text)
        process.stdin.close()

    queue: Queue[tuple[str, str | None]] = Queue()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _reader(stream, stream_name: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                queue.put((stream_name, line))
        finally:
            stream.close()
            queue.put((stream_name, None))

    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, "stdout"), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(process.stderr, "stderr"), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    finished_streams: set[str] = set()
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None

    try:
        while len(finished_streams) < 2:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                stdout_thread.join(timeout=0.2)
                stderr_thread.join(timeout=0.2)
                raise StreamCancelled("Stream cancelled by user")

            if deadline is not None and time.monotonic() > deadline:
                process.kill()
                stdout_thread.join(timeout=0.2)
                stderr_thread.join(timeout=0.2)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_seconds)

            try:
                stream_name, payload = queue.get(timeout=0.1)
            except Empty:
                continue

            if payload is None:
                finished_streams.add(stream_name)
                continue

            if stream_name == "stdout":
                stdout_lines.append(payload)
                if on_stdout_line is not None:
                    on_stdout_line(payload)
            else:
                stderr_lines.append(payload)
    except KeyboardInterrupt:
        process.kill()
        stdout_thread.join(timeout=0.2)
        stderr_thread.join(timeout=0.2)
        raise StreamCancelled("Stream cancelled by user")

    returncode = process.wait()
    stdout_thread.join(timeout=0.2)
    stderr_thread.join(timeout=0.2)
    return StreamProcessResult(returncode=returncode, stdout_lines=stdout_lines, stderr_lines=stderr_lines)


class AgentAdapter(ABC):
    provider: ProviderKind

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError

    def run_stream(
        self,
        request: AgentRequest,
        *,
        on_text: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AgentResponse:
        response = self.run(request)
        if on_text is not None and response.raw_output:
            on_text(response.raw_output)
        return response
