from __future__ import annotations

import json

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.capture.models import IdeaDraft
from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot
from labit.paths import RepoPaths


class IdeaDrafter:
    def __init__(self, paths: RepoPaths, *, registry: ProviderRegistry | None = None):
        self.paths = paths
        self.registry = registry or ProviderRegistry.default()

    def draft_from_session(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str = "",
        provider: str | ProviderKind | None = None,
    ) -> IdeaDraft:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_prompt(
                session=session,
                transcript=transcript,
                context_snapshot=context_snapshot,
                user_intent=user_intent,
            ),
            cwd=str(self.paths.root),
            output_schema=self._schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Idea drafter returned an invalid payload.")
        return IdeaDraft.model_validate(payload)

    def _build_prompt(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str,
    ) -> str:
        return f"""You are drafting a lightweight research idea note for LABIT.

Return JSON only. Do not add markdown fences or commentary.

This is for a lightweight idea note. Be concise and faithful to the conversation. Prefer one clear idea over a broad brainstorm.

Return:
- `title`: short and specific
- `summary_markdown`: 1 to 3 short paragraphs explaining the idea and why it might matter
- `key_question`: the one question that determines whether this idea is worth pursuing

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}
- Mode: {session.mode.value}
- Explicit user hint: {user_intent.strip() or "(none)"}

Context:
{self._format_context(context_snapshot)}

Recent transcript:
{self._format_transcript(transcript)}
"""

    def _format_context(self, snapshot: ContextSnapshot) -> str:
        parts: list[str] = []
        budget = 4000
        for block in snapshot.blocks[:4]:
            content = self._clip(block.content, min(1200, budget))
            parts.append(f"## {block.title}\n{content}")
            budget -= len(content)
            if budget <= 0:
                break
        return "\n\n".join(parts) if parts else "(none)"

    def _format_transcript(self, transcript: list[ChatMessage]) -> str:
        if not transcript:
            return "(empty)"
        lines: list[str] = []
        for message in transcript[-12:]:
            provider = f" ({message.provider.value})" if message.provider else ""
            lines.append(f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}")
        return self._clip("\n".join(lines), 6000)

    def _clip(self, text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"

    def _schema(self) -> dict:
        properties = {
            "title": {"type": "string"},
            "summary_markdown": {"type": "string"},
            "key_question": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties.keys()),
        }

    def _extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "low"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="low"']
        return []
