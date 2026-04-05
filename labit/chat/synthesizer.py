from __future__ import annotations

import json

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot, DiscussionSynthesisDraft
from labit.paths import RepoPaths


class DiscussionSynthesizer:
    def __init__(self, paths: RepoPaths, *, registry: ProviderRegistry | None = None):
        self.paths = paths
        self.registry = registry or ProviderRegistry.default()

    def synthesize_from_session(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str = "",
        provider: str | ProviderKind | None = None,
    ) -> DiscussionSynthesisDraft:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.SYNTHESIZER,
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
            raise ValueError("Discussion synthesizer returned an invalid payload.")
        return DiscussionSynthesisDraft.model_validate(payload)

    def _build_prompt(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str,
    ) -> str:
        return f"""You are synthesizing a LABIT research discussion into reusable working memory.

Return JSON only. Do not add markdown fences or commentary.

Goal:
- summarize the current state of the discussion
- capture only the most important consensus points
- preserve the most important unresolved disagreements
- record concrete follow-up actions

Be conservative. Do not invent evidence. If the discussion does not support a field, return an empty list.

Return:
- `summary`: 1 to 3 sentences
- `consensus`: short bullet-sized statements
- `disagreements`: short bullet-sized statements
- `followups`: concrete next actions or questions

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}
- Mode: {session.mode.value}
- Participants: {", ".join(item.name for item in session.participants)}
- Explicit user intent for synthesis: {user_intent.strip() or "(none)"}

Context:
{self._format_context(context_snapshot)}

Recent transcript:
{self._format_transcript(transcript)}
"""

    def _format_context(self, snapshot: ContextSnapshot) -> str:
        parts: list[str] = []
        budget = 3500
        for block in snapshot.blocks[:4]:
            content = self._clip(block.content, min(1000, budget))
            parts.append(f"## {block.title}\n{content}")
            budget -= len(content)
            if budget <= 0:
                break
        if budget > 0:
            for block in snapshot.memory[:3]:
                content = self._clip(block.content, min(700, budget))
                parts.append(f"## {block.title}\n{content}")
                budget -= len(content)
                if budget <= 0:
                    break
        return "\n\n".join(parts) if parts else "(none)"

    def _format_transcript(self, transcript: list[ChatMessage]) -> str:
        if not transcript:
            return "(empty)"
        lines: list[str] = []
        for message in transcript[-16:]:
            provider = f" ({message.provider.value})" if message.provider else ""
            lines.append(f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}")
        return self._clip("\n".join(lines), 7000)

    def _clip(self, text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"

    def _schema(self) -> dict:
        properties = {
            "summary": {"type": "string"},
            "consensus": {"type": "array", "items": {"type": "string"}},
            "disagreements": {"type": "array", "items": {"type": "string"}},
            "followups": {"type": "array", "items": {"type": "string"}},
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
