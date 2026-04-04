from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from labit.context.budget import TokenBudget, TokenBudgetDecision
from labit.context.events import WorkingMemorySnapshot
from labit.memory.models import MemoryRecord


class ContextSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    content: str
    source: str
    priority: int = 0


class AssembledContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[ContextSection] = Field(default_factory=list)
    budget: TokenBudgetDecision

    def render(self) -> str:
        if not self.sections:
            return "(no assembled context)"
        chunks: list[str] = []
        for section in self.sections:
            chunks.append(f"### {section.title} ({section.source})")
            chunks.append(section.content.strip())
        return "\n\n".join(chunks)


class ContextAssembler:
    def __init__(self, *, budget: TokenBudget | None = None):
        self.budget = budget or TokenBudget()

    def assemble(
        self,
        *,
        task_header: str,
        bound_sections: list[ContextSection],
        recent_sections: list[ContextSection],
        working_memory: WorkingMemorySnapshot | None = None,
        memories: list[MemoryRecord] | None = None,
        map_sections: list[ContextSection] | None = None,
    ) -> AssembledContext:
        sections: list[ContextSection] = [ContextSection(title="Task", content=task_header, source="task", priority=100)]
        sections.extend(bound_sections)
        sections.extend(recent_sections)

        if working_memory is not None:
            sections.append(
                ContextSection(
                    title="Working Memory",
                    source="working_memory",
                    priority=70,
                    content=self._render_working_memory(working_memory),
                )
            )

        for record in memories or []:
            sections.append(
                ContextSection(
                    title=record.title,
                    source=f"memory:{record.kind.value}",
                    priority=50,
                    content=record.summary,
                )
            )

        sections.extend(map_sections or [])
        sections = sorted(sections, key=lambda section: section.priority, reverse=True)

        rendered: list[ContextSection] = []
        included_tokens = 0
        limit = self.budget.usable_tokens

        for section in sections:
            rendered_section = f"### {section.title} ({section.source})\n\n{section.content.strip()}"
            section_tokens = self._estimate_tokens(rendered_section)
            if rendered and included_tokens + section_tokens > limit:
                break
            rendered.append(section)
            included_tokens += section_tokens

        return AssembledContext(
            sections=rendered,
            budget=TokenBudgetDecision(
                included_tokens=included_tokens,
                truncated=len(rendered) < len(sections),
                reason="budgeted_assembly",
            ),
        )

    def _render_working_memory(self, snapshot: WorkingMemorySnapshot) -> str:
        lines: list[str] = []
        if snapshot.current_goal:
            lines.append(f"Current goal: {snapshot.current_goal}")
        if snapshot.active_artifacts:
            lines.append(f"Active artifacts: {', '.join(snapshot.active_artifacts)}")
        if snapshot.decisions_made:
            lines.append("Decisions:")
            lines.extend(f"- {item}" for item in snapshot.decisions_made)
        if snapshot.open_questions:
            lines.append("Open questions:")
            lines.extend(f"- {item}" for item in snapshot.open_questions)
        if snapshot.discussion_state.consensus:
            lines.append("Consensus:")
            lines.extend(f"- {item}" for item in snapshot.discussion_state.consensus)
        if snapshot.discussion_state.disagreements:
            lines.append("Disagreements:")
            lines.extend(f"- {item}" for item in snapshot.discussion_state.disagreements)
        if snapshot.followups:
            lines.append("Follow-ups:")
            lines.extend(f"- {item}" for item in snapshot.followups)
        if not lines:
            return "(empty working memory)"
        return "\n".join(lines)

    def clip_to_tokens(self, text: str, *, max_tokens: int) -> str:
        text = text.strip()
        if self._estimate_tokens(text) <= max_tokens:
            return text
        approx_chars = max(1, max_tokens * 4)
        clipped = text[:approx_chars].rstrip()
        if len(clipped) < len(text):
            clipped = f"{clipped}…"
        return clipped

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / 4))
