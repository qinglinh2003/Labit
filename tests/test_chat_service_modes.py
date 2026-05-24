from __future__ import annotations

from pathlib import Path

from labit.agents.adapters.base import AgentAdapter
from labit.agents.models import AgentRequest, AgentResponse, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.chat.models import ChatMode
from labit.chat.service import ChatService
from labit.paths import RepoPaths


def _paths(root: Path) -> RepoPaths:
    return RepoPaths(
        root=root,
        labit_dir=root / ".labit",
        runs_dir=root / ".labit" / "runs",
        conversations_dir=root / ".labit" / "conversations",
        context_dir=root / ".labit" / "context",
        configs_dir=root / "configs",
        project_configs_dir=root / "configs" / "projects",
        active_project_path=root / "configs" / "active_project",
        vault_dir=root / "vault",
        vault_projects_dir=root / "vault" / "projects",
    )


class RecordingAdapter(AgentAdapter):
    def __init__(self, provider: ProviderKind):
        self.provider = provider
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        return AgentResponse(
            provider=self.provider,
            raw_output=f"{self.provider.value} reply",
            command=[self.provider.value],
        )


def test_switching_to_single_mode_trims_participants_and_dispatches_one_agent(tmp_path: Path) -> None:
    codex = RecordingAdapter(ProviderKind.CODEX)
    claude = RecordingAdapter(ProviderKind.CLAUDE)
    service = ChatService(
        _paths(tmp_path),
        registry=ProviderRegistry(
            adapters={
                ProviderKind.CODEX: codex,
                ProviderKind.CLAUDE: claude,
            }
        ),
    )
    session = service.open_session(
        title="Mode test",
        mode=ChatMode.ROUND_ROBIN,
        provider=ProviderKind.CODEX,
        second_provider=ProviderKind.CLAUDE,
    )

    updated = service.update_mode(session.session_id, ChatMode.SINGLE)
    result = service.ask(session_id=session.session_id, content="Only one agent should answer.")

    assert updated.mode == ChatMode.SINGLE
    assert [participant.name for participant in updated.participants] == ["codex"]
    assert [reply.participant.name for reply in result.replies] == ["codex"]
    assert len(codex.requests) == 1
    assert claude.requests == []


def test_single_mode_dispatches_only_first_participant_even_with_legacy_session(tmp_path: Path) -> None:
    codex = RecordingAdapter(ProviderKind.CODEX)
    claude = RecordingAdapter(ProviderKind.CLAUDE)
    service = ChatService(
        _paths(tmp_path),
        registry=ProviderRegistry(
            adapters={
                ProviderKind.CODEX: codex,
                ProviderKind.CLAUDE: claude,
            }
        ),
    )
    session = service.open_session(
        title="Legacy mode test",
        mode=ChatMode.ROUND_ROBIN,
        provider=ProviderKind.CODEX,
        second_provider=ProviderKind.CLAUDE,
    )
    legacy_single = session.model_copy(update={"mode": ChatMode.SINGLE})
    service.store.write_session(legacy_single)

    result = service.ask(session_id=session.session_id, content="Single mode should be enforced.")

    assert [reply.participant.name for reply in result.replies] == ["codex"]
    assert len(codex.requests) == 1
    assert claude.requests == []
