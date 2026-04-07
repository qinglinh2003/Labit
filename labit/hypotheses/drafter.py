from __future__ import annotations

import json

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot
from labit.hypotheses.models import HypothesisDraft
from labit.paths import RepoPaths


class HypothesisDrafter:
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
    ) -> HypothesisDraft:
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
            output_schema=self._draft_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Hypothesis drafter returned an invalid payload.")

        known_papers = self._known_paper_ids(session=session, context_snapshot=context_snapshot)
        draft = HypothesisDraft.model_validate(payload)
        if known_papers:
            merged = list(draft.source_paper_ids)
            for paper_id in known_papers:
                if paper_id not in merged:
                    merged.append(paper_id)
            draft = draft.model_copy(update={"source_paper_ids": merged})
        return draft

    def revise_hypothesis(
        self,
        *,
        current_draft: HypothesisDraft,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_instruction: str,
        interaction_log: str = "",
        provider: str | ProviderKind | None = None,
    ) -> HypothesisDraft:
        """Revise an existing hypothesis based on user feedback."""
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_revise_prompt(
                current_draft=current_draft,
                session=session,
                transcript=transcript,
                context_snapshot=context_snapshot,
                user_instruction=user_instruction,
                interaction_log=interaction_log,
            ),
            cwd=str(self.paths.root),
            output_schema=self._draft_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Hypothesis reviser returned an invalid payload.")

        revised = HypothesisDraft.model_validate(payload)
        # Preserve source_paper_ids from original
        merged = list(revised.source_paper_ids)
        for paper_id in current_draft.source_paper_ids:
            if paper_id not in merged:
                merged.append(paper_id)
        if merged != list(revised.source_paper_ids):
            revised = revised.model_copy(update={"source_paper_ids": merged})
        return revised

    def refine_draft(
        self,
        *,
        draft: HypothesisDraft,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str = "",
        provider: str | ProviderKind | None = None,
    ) -> HypothesisDraft:
        """Ask a second agent to review and refine an existing draft."""
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_refine_prompt(
                draft=draft,
                session=session,
                transcript=transcript,
                context_snapshot=context_snapshot,
                user_intent=user_intent,
            ),
            cwd=str(self.paths.root),
            output_schema=self._draft_schema(),
            extra_args=self._extra_args(provider_kind),
        )
        response = self.registry.get(provider_kind).run(request)
        payload = response.structured_output
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Hypothesis refiner returned an invalid payload.")

        refined = HypothesisDraft.model_validate(payload)
        # Preserve source_paper_ids from original draft
        merged = list(refined.source_paper_ids)
        for paper_id in draft.source_paper_ids:
            if paper_id not in merged:
                merged.append(paper_id)
        if merged != list(refined.source_paper_ids):
            refined = refined.model_copy(update={"source_paper_ids": merged})
        return refined

    def _build_refine_prompt(
        self,
        *,
        draft: HypothesisDraft,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str = "",
    ) -> str:
        context_blocks = self._format_context_blocks(context_snapshot)
        transcript_text = self._format_transcript(transcript)
        intent_text = user_intent.strip() or "(none)"

        draft_json = draft.model_dump_json(indent=2)

        return f"""You are reviewing and refining a research hypothesis draft for LABIT.

Another agent drafted the hypothesis below from a shared conversation. Your job is to critically review it and return an improved version. Be faithful to the conversation context — do not invent claims or evidence not present in the transcript.

Return JSON only. Do not add markdown fences or commentary.

Focus your review on:
- Is the `claim` actually testable and specific enough?
- Are `success_criteria` and `failure_criteria` concrete and measurable?
- Are `independent_variable` and `dependent_variable` correctly identified?
- Is the `experiment_plan_markdown` actionable — could someone execute it?
- Is the `rationale_markdown` well-reasoned and grounded in the discussion?

If the draft is already good, make only minor improvements. Do not change things just to change them.

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}
- Explicit user intent: {intent_text}

Context:
{context_blocks}

Transcript:
{transcript_text}

Current draft to review and refine:
{draft_json}
"""

    def _build_revise_prompt(
        self,
        *,
        current_draft: HypothesisDraft,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_instruction: str,
        interaction_log: str = "",
    ) -> str:
        context_blocks = self._format_context_blocks(context_snapshot)
        transcript_text = self._format_transcript(transcript)
        draft_json = current_draft.model_dump_json(indent=2)
        log_text = interaction_log.strip() or "(none)"

        return f"""You are revising an existing research hypothesis for LABIT based on user feedback.

Return JSON only. Do not add markdown fences or commentary.

The user has reviewed the current hypothesis and wants changes. Apply their feedback precisely. Do not change fields the user did not mention unless the change logically follows from their feedback.

Rules:
- `title`: keep short and specific.
- `claim`: must remain a testable research statement. If the user asks to change the claim, make sure the new claim is still testable.
- `motivation`: update if the user's feedback changes the reasoning.
- `independent_variable` / `dependent_variable`: update if the user redefines what changes or what is measured.
- `success_criteria` / `failure_criteria`: update if the user specifies new thresholds or conditions.
- `rationale_markdown`: update to reflect the revised reasoning. Include the user's feedback rationale.
- `experiment_plan_markdown`: update if the user changes the experimental approach.
- `source_paper_ids`: preserve existing paper ids unless the user explicitly removes one.

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}

Context:
{context_blocks}

Transcript (recent):
{transcript_text}

Prior iteration log:
{log_text}

Current hypothesis to revise:
{draft_json}

User's revision instruction:
{user_instruction}
"""

    def _build_prompt(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        context_snapshot: ContextSnapshot,
        user_intent: str = "",
    ) -> str:
        context_blocks = self._format_context_blocks(context_snapshot)
        transcript_text = self._format_transcript(transcript)
        known_papers = self._known_paper_ids(session=session, context_snapshot=context_snapshot)
        known_paper_text = ", ".join(known_papers) if known_papers else "(none)"
        intent_text = user_intent.strip() or "(none)"

        return f"""You are drafting a research hypothesis for LABIT from a shared conversation.

Return JSON only. Do not add markdown fences or commentary.

The goal is to turn an ongoing discussion into a testable, project-scoped hypothesis. Be conservative and faithful to the transcript and context. If a detail is unavailable, return an empty string instead of inventing it.

Requirements:
- `title`: short and specific.
- `claim`: a testable research statement.
- `motivation`: why this is worth testing now.
- `independent_variable`: what changes.
- `dependent_variable`: what is measured.
- `success_criteria`: explicit validation rule or threshold when available.
- `failure_criteria`: explicit rejection rule or threshold when available.
- `rationale_markdown`: concise markdown explaining the evidence and reasoning from the conversation.
- `experiment_plan_markdown`: concise markdown plan with sections for what to run, data, models, comparisons, code changes, outputs, and success criteria. Use `Unavailable` where needed.
- `source_paper_ids`: list only paper ids that are explicitly present in the provided context.

Session:
- Title: {session.title}
- Project: {session.project or "(none)"}
- Mode: {session.mode.value}
- Participants: {", ".join(item.name for item in session.participants)}
- Known paper ids: {known_paper_text}
- Explicit user intent for this hypothesis draft: {intent_text}

Context:
{context_blocks}

Transcript:
{transcript_text}
"""

    def _format_context_blocks(self, snapshot: ContextSnapshot) -> str:
        parts: list[str] = []
        total_budget = 6000
        for block in snapshot.blocks[:6]:
            block_budget = 1500
            if "Paper Source Excerpt" in block.title:
                block_budget = 1200
            elif "Project Summary" in block.title:
                block_budget = 1800
            content = self._clip(block.content, min(block_budget, total_budget))
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
        recent = transcript[-16:]
        lines: list[str] = []
        for message in recent:
            provider = f" ({message.provider.value})" if message.provider else ""
            lines.append(f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}")
        return self._clip("\n".join(lines), 8000)

    def _known_paper_ids(self, *, session: ChatSession, context_snapshot: ContextSnapshot) -> list[str]:
        paper_ids: list[str] = []
        for binding in session.context_bindings:
            if binding.provider != "paper_focus":
                continue
            paper_id = str(binding.config.get("paper_id", "")).strip()
            if paper_id and paper_id not in paper_ids:
                paper_ids.append(paper_id)

        for block in context_snapshot.blocks:
            if "Paper Metadata" not in block.title:
                continue
            for line in block.content.splitlines():
                if not line.startswith("Paper ID:"):
                    continue
                paper_id = line.split(":", 1)[1].strip()
                if paper_id and paper_id not in paper_ids:
                    paper_ids.append(paper_id)
        return paper_ids

    def _clip(self, text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"

    def _draft_schema(self) -> dict:
        properties = {
            "title": {"type": "string"},
            "claim": {"type": "string"},
            "motivation": {"type": "string"},
            "independent_variable": {"type": "string"},
            "dependent_variable": {"type": "string"},
            "success_criteria": {"type": "string"},
            "failure_criteria": {"type": "string"},
            "rationale_markdown": {"type": "string"},
            "experiment_plan_markdown": {"type": "string"},
            "source_paper_ids": {"type": "array", "items": {"type": "string"}},
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
