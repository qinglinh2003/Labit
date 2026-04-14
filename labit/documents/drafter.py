from __future__ import annotations

import json
import re

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot
from labit.documents.models import DocUpdate, ReviewAction
from labit.paths import RepoPaths


def _excerpt(text: str, max_chars: int = 200) -> str:
    """Return the first meaningful lines of text, trimmed to max_chars."""
    text = text.strip()
    if not text:
        return "(empty)"
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"


_REVIEW_OPEN_RE = re.compile(r"<!--\s*review:\w+:\w+:open\s*-->")
_REVIEW_CLOSED_RE = re.compile(r"<!--\s*review:\w+:\w+:closed\s*-->")


def count_open_reviews(markdown: str) -> int:
    """Count the number of open review blocks in a document."""
    return len(_REVIEW_OPEN_RE.findall(markdown))


def compute_changed_sections(old_md: str, new_md: str) -> list[dict[str, str]]:
    """Compare two markdown docs section-by-section, return list of changed sections.

    Each entry includes:
    - heading: the section heading
    - change: added|modified|removed
    - before_excerpt: first ~200 chars of the old section body (for modified/removed)
    - after_excerpt: first ~200 chars of the new section body (for added/modified)
    """

    def _split_sections(md: str) -> dict[str, str]:
        """Split markdown into {heading: body} pairs. Top-level content uses '' key."""
        sections: dict[str, str] = {}
        current_heading = ""
        current_lines: list[str] = []
        for line in md.splitlines():
            if re.match(r"^#{1,6}\s", line):
                sections[current_heading] = "\n".join(current_lines).strip()
                current_heading = line.strip()
                current_lines = []
            else:
                current_lines.append(line)
        sections[current_heading] = "\n".join(current_lines).strip()
        return sections

    old_sections = _split_sections(old_md)
    new_sections = _split_sections(new_md)
    changes: list[dict[str, str]] = []

    for heading in new_sections:
        if heading not in old_sections:
            if heading:  # skip unnamed top-level
                changes.append({
                    "heading": heading,
                    "change": "added",
                    "after_excerpt": _excerpt(new_sections[heading]),
                })
        elif new_sections[heading] != old_sections[heading]:
            changes.append({
                "heading": heading or "(top-level)",
                "change": "modified",
                "before_excerpt": _excerpt(old_sections[heading]),
                "after_excerpt": _excerpt(new_sections[heading]),
            })

    for heading in old_sections:
        if heading not in new_sections and heading:
            changes.append({
                "heading": heading,
                "change": "removed",
                "before_excerpt": _excerpt(old_sections[heading]),
            })

    return changes


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
        author_name: str = "author",
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
                author_name=author_name,
            ),
            cwd=str(self.paths.root),
            output_schema=self._schema(),
            extra_args=self._extra_args(provider_kind),
        )
        return self._run(provider_kind, request)

    def review_document(
        self,
        *,
        current_markdown: str,
        revision_summary: str,
        user_instruction: str,
        reviewer_name: str,
        changed_sections: list[dict[str, str]] | None = None,
        provider: str | ProviderKind | None = None,
    ) -> DocUpdate:
        """Reviewer reads the updated document and inserts inline review blocks."""
        provider_kind = resolve_provider_kind(provider)
        actions = ", ".join(a.value for a in ReviewAction)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_review_prompt(
                current_markdown=current_markdown,
                revision_summary=revision_summary,
                user_instruction=user_instruction,
                reviewer_name=reviewer_name,
                actions=actions,
                changed_sections=changed_sections or [],
            ),
            cwd=str(self.paths.root),
            output_schema=self._schema(),
            extra_args=self._extra_args(provider_kind),
        )
        return self._run(provider_kind, request)

    def _build_review_prompt(
        self,
        *,
        current_markdown: str,
        revision_summary: str,
        user_instruction: str,
        reviewer_name: str,
        actions: str,
        changed_sections: list[dict[str, str]],
    ) -> str:
        # Build structured changeset block with excerpts
        if changed_sections:
            changeset_lines = ["## Structured changeset (from diff)"]
            changeset_lines.append("The following sections were changed in this round. Review ONLY these sections (plus any still-open reviews from earlier rounds).\n")
            for cs in changed_sections:
                changeset_lines.append(f"### {cs['heading']} — {cs['change']}")
                if cs.get("before_excerpt"):
                    changeset_lines.append(f"**Before**: {cs['before_excerpt']}")
                if cs.get("after_excerpt"):
                    changeset_lines.append(f"**After**: {cs['after_excerpt']}")
                changeset_lines.append("")
            changeset_lines.append("Focus your review on the **After** content in each changed section. Do NOT review unchanged sections unless they have unresolved `:open` reviews from previous rounds.")
            changeset_block = "\n".join(changeset_lines)
        else:
            changeset_block = "No structured changeset available. Use the author's revision summary to identify what changed."

        return f"""You are a peer reviewer for a LABIT research design document.

Return JSON only. Do not add markdown fences or commentary outside JSON.

Your job is to review the document and insert inline review comments using HTML comment blocks. You must NOT change the original text — only add review blocks after relevant sections/paragraphs.

## Review block format

All review blocks use HTML comments so they don't break markdown rendering.

New review (always starts as `open`):
<!-- review:{reviewer_name}:<action>:open -->
Your comment here.
<!-- /review -->

For agreement (body optional):
<!-- review:{reviewer_name}:agree:open -->
<!-- /review -->

Closing a previously open review you left (only when the author has adequately addressed it):
Change `:open` to `:closed` on the existing block. Do not duplicate the block.

Available review actions: {actions}

{changeset_block}

## Delta-based review rules

This document may already contain review blocks from previous rounds. Follow these rules strictly:

1. **Your own `:open` reviews**: Check if the author addressed them (look at nearby text changes and `<!-- response:... -->` blocks). If adequately addressed, change the status to `:closed`. If NOT addressed or insufficiently addressed, keep `:open` and optionally add a follow-up comment.
2. **Your own `:closed` reviews**: Leave them alone. Do not re-open unless the author's changes broke something.
3. **New content from this round**: Review ONLY the sections listed in the structured changeset above. Add new `:open` review blocks as needed.
4. **Unchanged content with no existing review**: Do NOT review it. Do not add `agree` blocks to unchanged sections.
5. **`<!-- response:... -->` blocks**: These are the author's responses to your reviews. Read them to decide whether to close or keep open.

## Guidelines
- Be specific and constructive. Reference the exact content you're commenting on.
- Use `question` when something is unclear or needs justification.
- Use `supplement` to add missing context, caveats, or related information.
- Use `discuss` for design choices that have valid alternatives worth considering.
- Use `oppose` only when something is factually wrong or will cause problems.
- Do NOT rewrite the document text. Only add/update review blocks.
- Place each new review block immediately after the paragraph or section it refers to.

Return:
- `title`: the document title (unchanged).
- `summary`: one sentence summarizing your review (e.g. "closed 2, kept 1 open, added 1 new question on reward loss").
- `markdown`: the complete document with review blocks updated/inserted.

Author's revision summary: {revision_summary}
User's original instruction: {user_instruction}

Current document:
{self._clip(current_markdown, 20000)}
"""

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
        author_name: str = "author",
    ) -> str:
        return f"""You are revising an existing LABIT research design document.

Return JSON only. Do not add markdown fences or commentary outside JSON.

This is a document editing session. The `markdown` field will replace the current on-disk document, so return the complete updated markdown, not a patch and not only the changed section.

Follow the user's latest instruction while preserving useful existing content. Do not invent implementation details. If the instruction exposes uncertainty, update Open Questions or Next Steps instead of pretending it is settled.

## Handling review blocks

The document may contain reviewer comments in this format:
<!-- review:<reviewer>:<action>:<status> -->
comment text
<!-- /review -->

Rules:
- Do NOT change the status of any review block (`:open` or `:closed`). Only the reviewer can close their own reviews.
- Do NOT delete review blocks. They are permanent decision rationale.
- When you address a reviewer's concern, add a response block immediately after the review:
<!-- response:{author_name} -->
Your response explaining how you addressed the concern.
<!-- /response -->
- If the user explicitly says to ignore a review comment, you may skip adding a response, but still do NOT delete the review block.

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
