from __future__ import annotations

import typer

from labit.commands.chat import chat_app
from labit.commands.hypothesis import hypothesis_app
from labit.commands.memory import memory_app
from labit.commands.paper import paper_app
from labit.commands.project import project_app

app = typer.Typer(help="LABIT: local-first control plane for research workflows.")
app.add_typer(project_app, name="project")
app.add_typer(paper_app, name="paper")
app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(memory_app, name="memory")
app.add_typer(chat_app, name="chat")


@app.callback()
def main() -> None:
    """LABIT CLI."""
