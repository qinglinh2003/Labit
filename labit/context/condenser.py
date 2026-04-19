from __future__ import annotations

from dataclasses import dataclass

from labit.context.events import SessionEvent, SessionEventKind, WorkingMemorySnapshot


@dataclass(frozen=True)
class CondenserDecision:
    should_condense: bool
    reason: str


class SessionCondenser:
    def should_condense(self, events: list[SessionEvent]) -> CondenserDecision:
        raise NotImplementedError

    def condense(
        self,
        *,
        session_id: str,
        project: str | None,
        events: list[SessionEvent],
        existing: WorkingMemorySnapshot | None = None,
    ) -> WorkingMemorySnapshot:
        raise NotImplementedError


class NoOpCondenser(SessionCondenser):
    def should_condense(self, events: list[SessionEvent]) -> CondenserDecision:
        return CondenserDecision(should_condense=False, reason="noop")

    def condense(
        self,
        *,
        session_id: str,
        project: str | None,
        events: list[SessionEvent],
        existing: WorkingMemorySnapshot | None = None,
    ) -> WorkingMemorySnapshot:
        return existing or WorkingMemorySnapshot(session_id=session_id, project=project)


class ResearchRollingCondenser(SessionCondenser):
    def __init__(self, *, max_events: int = 40):
        self.max_events = max_events

    def should_condense(self, events: list[SessionEvent]) -> CondenserDecision:
        if len(events) >= self.max_events:
            return CondenserDecision(should_condense=True, reason="event_threshold")
        return CondenserDecision(should_condense=False, reason="below_threshold")

    def condense(
        self,
        *,
        session_id: str,
        project: str | None,
        events: list[SessionEvent],
        existing: WorkingMemorySnapshot | None = None,
    ) -> WorkingMemorySnapshot:
        snapshot = existing or WorkingMemorySnapshot(session_id=session_id, project=project)
        relevant = events[-self.max_events :]

        decisions: list[str] = []
        followups: list[str] = []
        evidence_refs: list[str] = []
        active_artifacts: list[str] = []
        open_questions: list[str] = list(snapshot.open_questions)
        consensus: list[str] = list(snapshot.discussion_state.consensus)
        disagreements: list[str] = list(snapshot.discussion_state.disagreements)

        for event in relevant:
            evidence_refs.extend(event.evidence_refs)
            if event.kind in {
                SessionEventKind.ARTIFACT_HYPOTHESIS_CREATED,
                SessionEventKind.ARTIFACT_HYPOTHESIS_UPDATED,
                SessionEventKind.ARTIFACT_REPORT_CREATED,
                SessionEventKind.ARTIFACT_DOCUMENT_CREATED,
                SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
                SessionEventKind.DISCUSSION_SYNTHESIS,
            }:
                decisions.append(event.summary)
            if event.kind == SessionEventKind.ARTIFACT_FOCUS_BOUND:
                paper_id = ""
                config = event.payload.get("config") if isinstance(event.payload, dict) else None
                if isinstance(config, dict):
                    paper_id = str(config.get("paper_id", "")).strip()
                if paper_id:
                    active_artifacts.append(f"paper:{paper_id}")
            if event.kind in {
                SessionEventKind.ARTIFACT_HYPOTHESIS_CREATED,
                SessionEventKind.ARTIFACT_HYPOTHESIS_UPDATED,
            }:
                hypothesis_id = str(event.payload.get("hypothesis_id", "")).strip()
                if hypothesis_id:
                    active_artifacts.append(f"hypothesis:{hypothesis_id}")
            if event.kind == SessionEventKind.ARTIFACT_REPORT_CREATED:
                report_path = str(event.payload.get("report_path", "")).strip()
                if report_path:
                    active_artifacts.append(f"report:{report_path}")
            if event.kind in {
                SessionEventKind.ARTIFACT_DOCUMENT_CREATED,
                SessionEventKind.ARTIFACT_DOCUMENT_UPDATED,
            }:
                doc_path = ""
                if isinstance(event.payload, dict):
                    doc_path = str(event.payload.get("document_path", "")).strip()
                if doc_path:
                    active_artifacts.append(f"document:{doc_path}")
            if event.kind in {
                SessionEventKind.ARTIFACT_TODO_CREATED,
                SessionEventKind.ARTIFACT_NOTE_CREATED,
                SessionEventKind.ARTIFACT_IDEA_CREATED,
            }:
                followups.append(event.summary)
            if event.kind == SessionEventKind.ARTIFACT_TODO_CREATED:
                open_questions.append(event.summary)
            if event.kind == SessionEventKind.DISCUSSION_SYNTHESIS:
                payload = event.payload if isinstance(event.payload, dict) else {}
                consensus.extend(self._coerce_list(payload.get("consensus")))
                disagreements.extend(self._coerce_list(payload.get("disagreements")))
                followups.extend(self._coerce_list(payload.get("followups")))

        discussion = snapshot.discussion_state.model_copy(
            update={
                "consensus": (consensus or decisions)[-3:],
                "disagreements": disagreements[-3:],
                "followups": followups[-5:],
            }
        )

        return snapshot.model_copy(
            update={
                "active_artifacts": list(dict.fromkeys(active_artifacts))[-8:],
                "decisions_made": decisions[-5:],
                "open_questions": list(dict.fromkeys(open_questions))[-8:],
                "followups": followups[-8:],
                "evidence_refs": list(dict.fromkeys(evidence_refs))[-12:],
                "discussion_state": discussion,
                "built_from_event_ids": [event.event_id for event in relevant],
            }
        )

    def _coerce_list(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned
