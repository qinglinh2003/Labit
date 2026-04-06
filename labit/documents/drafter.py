from __future__ import annotations

import json

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot
from labit.documents.models import DocUpdate
from labit.paths import RepoPaths


class DocDrafter:
    def __init__(self, paths: RepoPaths, *, registry: ProviderRegistry | None = None):
        self.paths = paths
        self.registry = registry or ProviderRegistry.default()

    def draft_from_session(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        title: str,
        provider: str | ProviderKind | None = None,
    ) -> DocUpdate:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_initial_prompt(
                session=session,
                transcript=transcript,
                context_snapshot=context_snapshot,
                title=title,
            ),
            cwd=str(self.paths.root),
            output_schema=self._schema(),
            extra_args=self._extra_args(provider_kind),
        )
        return self._run(provider_kind, request)

    def revise_document(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        doc_title: str,
        current_markdown: str,
        user_instruction: str,
        interaction_log: str,
        provider: str | ProviderKind | None = None,
    ) -> DocUpdate:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_revision_prompt(
                session=session,
                transcript=transcript,
                context_snapshot=context_snapshot,
                doc_title=doc_title,
                current_markdown=current_markdown,
                user_instruction=user_instruction,
                interaction_log=interaction_log,
            ),
            cwd=str(self.paths.root),
            output_schema=self._schema(),
            extra_args=self._extra_args(provider_kind),
        )
        return self._run(provider_kind, request)

    def _run(self, provider: ProviderKind, request: AgentRequest) -> DocUpdate:
        response = self.registry.get(provider).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Document drafter returned an invalid payload.")
        return DocUpdate.model_validate(payload)

    def _build_initial_prompt(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        title: str,
    ) -> str:
        return f"""You are writing a durable research design document for LABIT.

Return JSON only. Do not add markdown fences or commentary outside JSON.

This is not a chat answer. The `markdown` field will be written directly to disk as the document. Write a coherent first draft based on the conversation and available context. Be faithful to what was discussed; distinguish open questions from settled decisions.

Return:
- `title`: the document title.
- `summary`: one concise sentence describing what you wrote or changed.
- `markdown`: the complete markdown document.

The markdown should use this shape unless the topic clearly calls for something else:
# <Title>

**Date**: current session date if known
**Source**: chat:{session.title} · {session.session_id}
**Status**: draft
**Type**: design

## Summary
## Context
## Decisions
## Implementation Details
## Interfaces / Commands
## Open Questions
## Next Steps
## References

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}
- Mode: {session.mode.value}
- Requested document title: {title.strip()}

Context:
{self._format_context(context_snapshot)}

Recent transcript:
{self._format_transcript(transcript)}
"""

    def _build_revision_prompt(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        doc_title: str,
        current_markdown: str,
        user_instruction: str,
        interaction_log: str,
    ) -> str:
        return f"""You are revising an existing LABIT research design document.

Return JSON only. Do not add markdown fences or commentary outside JSON.

This is a document editing session. The `markdown` field will replace the current on-disk document, so return the complete updated markdown, not a patch and not only the changed section.

Follow the user's latest instruction while preserving useful existing content. Do not invent implementation details. If the instruction exposes uncertainty, update Open Questions or Next Steps instead of pretending it is settled.

Return:
- `title`: the document title.
- `summary`: one concise sentence describing the document update.
- `markdown`: the complete updated markdown document.

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}
- Mode: {session.mode.value}
- Document title: {doc_title}

Latest user instruction:
{user_instruction.strip()}

Recent document interaction log:
{interaction_log or "(none)"}

Current markdown:
{self._clip(current_markdown, 20000)}

Context:
{self._format_context(context_snapshot)}

Recent transcript:
{self._format_transcript(transcript)}
"""

    def _format_context(self, snapshot: ContextSnapshot) -> str:
        parts: list[str] = []
        total_budget = 6000
        for block in snapshot.blocks[:6]:
            content = self._clip(block.content, min(1400, total_budget))
            parts.append(f"## {block.title}\n{content}")
            total_budget -= len(content)
            if total_budget <= 0:
                break
        if total_budget > 0:
            for block in snapshot.memory[:4]:
                content = self._clip(block.content, min(700, total_budget))
                parts.append(f"## {block.title}\n{content}")
                total_budget -= len(content)
                if total_budget <= 0:
                    break
        return "\n\n".join(parts) if parts else "(none)"

    def _format_transcript(self, transcript: list[ChatMessage]) -> str:
        if not transcript:
            return "(empty)"
        lines: list[str] = []
        for message in transcript[-16:]:
            provider = f" ({message.provider.value})" if message.provider else ""
            lines.append(f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}")
        return self._clip("\n".join(lines), 8000)

    def _clip(self, text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"

    def _schema(self) -> dict:
        properties = {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "markdown": {"type": "string"},
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
