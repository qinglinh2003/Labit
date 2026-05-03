from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from labit.chat.models import ChatSession
from labit.chat.service import ChatService
from labit.paths import RepoPaths


@dataclass(slots=True)
class ChatContext:
    """Shared state passed from the chat shell into command handlers."""

    console: Console
    paths: RepoPaths
    service: ChatService
    session: ChatSession

    @property
    def project(self) -> str | None:
        return self.session.project


def session_evidence_refs(session: ChatSession) -> list[str]:
    refs: list[str] = []
    if session.project:
        refs.append(f"project:{session.project}")
    for binding in session.context_bindings:
        if binding.provider != "paper_focus":
            continue
        paper_id = str(binding.config.get("paper_id", "")).strip()
        if paper_id:
            refs.append(f"paper:{paper_id}")
    return refs
