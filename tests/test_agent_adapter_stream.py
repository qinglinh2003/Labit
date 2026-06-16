from __future__ import annotations

import sys
import threading
import time

import pytest

from labit.agents.adapters.base import StreamCancelled, stream_subprocess_lines


def test_stream_subprocess_lines_observes_cancel_while_process_is_silent() -> None:
    cancel_event = threading.Event()
    timer = threading.Timer(0.2, cancel_event.set)

    started_at = time.monotonic()
    timer.start()
    try:
        with pytest.raises(StreamCancelled):
            stream_subprocess_lines(
                [sys.executable, "-c", "import time; time.sleep(3)"],
                cancel_event=cancel_event,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started_at < 2.0
