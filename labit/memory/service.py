from __future__ import annotations

import re
from dataclasses import dataclass

from labit.chat.models import utc_now_iso
from labit.context.events import SessionEvent, SessionEventKind
from labit.memory.models import MemoryKind, MemoryNamespace, MemoryRecord, MemoryStatus, MemoryType
from labit.memory.store import MemoryStore
from labit.paths import RepoPaths


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    score: int
    reasons: list[str]


class MemoryService:
    def __init__(self, paths: RepoPaths, *, store: MemoryStore | None = None):
        self.paths = paths
        self.store = store or MemoryStore(paths)

    def promote_event(self, event: SessionEvent) -> MemoryRecord | None:
        if not event.project:
            return None

        record = self._record_from_event(event)
        if record is None:
            return None
        decision = self._promotion_decision(event, record)
        if not decision.promote:
            return None

        candidate = record.model_copy(
            update={
                "promotion_score": decision.score,
                "promotion_reasons": decision.reasons,
            }
        )

        existing = self._find_active_match(candidate)
        if existing is not None:
            if candidate.kind == MemoryKind.DISCUSSION_TAKEAWAY and not self._should_merge_discussion(existing, candidate):
                self.store.archive_record(existing.project, existing.memory_id)
            else:
                merged = self._merge_records(existing, candidate)
                self.store.write_record(merged)
                return merged

        self._apply_supersession_policy(candidate, event=event)
        self.store.write_record(candidate)
        return candidate

    def _record_from_event(self, event: SessionEvent) -> MemoryRecord | None:
        if event.kind == SessionEventKind.DISCUSSION_SYNTHESIS:
            return MemoryRecord(
                project=event.project or "",
                namespace=MemoryNamespace(parts=("conversation", event.session_id)),
                kind=MemoryKind.DISCUSSION_TAKEAWAY,
                memory_type=MemoryType.EPISODIC,
                title=self._title_from_summary(event.summary, prefix="Discussion"),
                summary=self._discussion_summary(event),
                evidence_refs=event.evidence_refs,
                source_event_ids=[event.event_id],
                source_artifact_refs=[f"conversation:{event.session_id}"],
                confidence="medium",
            )

        return None

    def _promotion_decision(self, event: SessionEvent, record: MemoryRecord) -> PromotionDecision:
        reasons: list[str] = []
        score = 0

        if event.kind == SessionEventKind.DISCUSSION_SYNTHESIS:
            payload = event.payload if isinstance(event.payload, dict) else {}
            consensus = self._coerce_list(payload.get("consensus"))
            disagreements = self._coerce_list(payload.get("disagreements"))
            followups = self._coerce_list(payload.get("followups"))
            if consensus:
                score += 4
                reasons.append("has_consensus")
            if followups:
                score += 3
                reasons.append("has_followups")
            if event.evidence_refs:
                score += 2
                reasons.append("has_evidence_refs")
            if disagreements:
                score += 1
                reasons.append("captures_disagreements")
            if len(event.summary.split()) >= 8:
                score += 1
                reasons.append("nontrivial_summary")
            return PromotionDecision(promote=score >= 6, score=score, reasons=reasons or ["low_signal"])

        return PromotionDecision(promote=False, score=0, reasons=["unsupported_event"])

    def _find_active_match(self, candidate: MemoryRecord) -> MemoryRecord | None:
        for record in self.store.list_records(candidate.project, include_inactive=False):
            if record.kind == candidate.kind and record.namespace == candidate.namespace:
                return record
        return None

    def _merge_records(self, existing: MemoryRecord, candidate: MemoryRecord) -> MemoryRecord:
        return existing.model_copy(
            update={
                "title": candidate.title,
                "summary": candidate.summary,
                "evidence_refs": self._merge_lists(existing.evidence_refs, candidate.evidence_refs),
                "source_event_ids": self._merge_lists(existing.source_event_ids, candidate.source_event_ids),
                "source_artifact_refs": self._merge_lists(existing.source_artifact_refs, candidate.source_artifact_refs),
                "confidence": self._stronger_confidence(existing.confidence, candidate.confidence),
                "promotion_score": max(existing.promotion_score, candidate.promotion_score),
                "promotion_reasons": self._merge_lists(existing.promotion_reasons, candidate.promotion_reasons),
                "updated_at": utc_now_iso(),
                "status": MemoryStatus.ACTIVE,
                "superseded_by": None,
                "superseded_at": None,
            }
        )

    def _apply_supersession_policy(self, candidate: MemoryRecord, *, event: SessionEvent) -> None:
        if candidate.kind not in {MemoryKind.OPEN_LOOP, MemoryKind.INVESTIGATION_FINDING}:
            return

        conversation_ref = f"conversation:{event.session_id}"
        for record in self.store.list_records(candidate.project, include_inactive=False):
            if record.memory_id == candidate.memory_id:
                continue
            if record.kind != MemoryKind.DISCUSSION_TAKEAWAY:
                continue
            if conversation_ref not in record.source_artifact_refs:
                continue
            self.store.supersede_record(candidate.project, record.memory_id, superseded_by=candidate.memory_id)

    def _should_merge_discussion(self, existing: MemoryRecord, candidate: MemoryRecord) -> bool:
        if set(existing.evidence_refs) & set(candidate.evidence_refs):
            return True
        existing_tokens = self._tokenize(existing.summary)
        candidate_tokens = self._tokenize(candidate.summary)
        if not existing_tokens or not candidate_tokens:
            return False
        overlap = len(existing_tokens & candidate_tokens) / max(1, len(existing_tokens | candidate_tokens))
        return overlap >= 0.35

    def _discussion_summary(self, event: SessionEvent) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        consensus = self._coerce_list(payload.get("consensus"))
        disagreements = self._coerce_list(payload.get("disagreements"))
        followups = self._coerce_list(payload.get("followups"))
        lines = [event.summary]
        if consensus:
            lines.append("Consensus:")
            lines.extend(f"- {item}" for item in consensus)
        if disagreements:
            lines.append("Disagreements:")
            lines.extend(f"- {item}" for item in disagreements)
        if followups:
            lines.append("Follow-ups:")
            lines.extend(f"- {item}" for item in followups)
        return "\n".join(lines)

    def _title_from_summary(self, summary: str, *, prefix: str) -> str:
        text = " ".join(summary.split()).strip()
        if len(text) > 72:
            text = f"{text[:71]}…"
        return f"{prefix}: {text}" if text else prefix

    def _coerce_list(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    def _merge_lists(self, left: list[str], right: list[str]) -> list[str]:
        merged: list[str] = []
        for item in [*left, *right]:
            text = str(item).strip()
            if text and text not in merged:
                merged.append(text)
        return merged

    def _stronger_confidence(self, left: str, right: str) -> str:
        rank = {"low": 0, "medium": 1, "high": 2}
        return left if rank.get(left, 1) >= rank.get(right, 1) else right

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9_:-]+", text.lower()) if len(token) >= 3}
