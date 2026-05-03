from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def launch_dashboard(*, port: int = 8765, address: str = "127.0.0.1") -> int:
    """Launch the Streamlit dashboard in a subprocess."""
    try:
        import streamlit  # noqa: F401
    except ModuleNotFoundError:
        print("Streamlit is not installed. Install it with: pip install -e '.[web]'", file=sys.stderr)
        return 1

    dashboard = Path(__file__).with_name("dashboard.py")
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(dashboard),
        "--server.address",
        address,
        "--server.port",
        str(port),
        "--browser.gatherUsageStats",
        "false",
    ]
    return subprocess.call(command)

