from __future__ import annotations

import typer

from labit.commands.project import project_app

app = typer.Typer(help="LABIT: local-first control plane for research workflows.")
app.add_typer(project_app, name="project")


@app.callback()
def main() -> None:
    """LABIT CLI."""
