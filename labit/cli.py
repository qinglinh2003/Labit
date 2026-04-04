from __future__ import annotations

import typer

from labit import __version__
from labit.commands.chat import chat_app
from labit.commands.daily_summary import daily_summary_app
from labit.commands.experiment import experiment_app
from labit.commands.hypothesis import hypothesis_app
from labit.commands.memory import memory_app
from labit.commands.paper import paper_app
from labit.commands.project import project_app
from labit.commands.sync import sync_app

app = typer.Typer(help="LABIT: local-first control plane for research workflows.", invoke_without_command=True)
app.add_typer(project_app, name="project")
app.add_typer(paper_app, name="paper")
app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(experiment_app, name="experiment")
app.add_typer(memory_app, name="memory")
app.add_typer(sync_app, name="sync")
app.add_typer(chat_app, name="chat")
app.add_typer(daily_summary_app)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the LABIT version and exit.",
        is_eager=True,
    ),
) -> None:
    """LABIT CLI."""
    if version:
        typer.echo(__version__)
        raise typer.Exit()
