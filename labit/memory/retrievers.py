from __future__ import annotations

from datetime import UTC, datetime
import re
from dataclasses import dataclass

from labit.memory.models import MemoryKind, MemoryRecord
from labit.memory.store import MemoryStore


@dataclass(frozen=True)
class RetrievedMemory:
    record: MemoryRecord
    score: int


class MemoryRetriever:
    def __init__(self, store: MemoryStore):
        self.store = store

    def retrieve(
        self,
        *,
        project: str,
        query: str,
        evidence_refs: list[str] | None = None,
        limit: int = 6,
    ) -> list[MemoryRecord]:
        records = self.store.list_records(project)
        if not records:
            return []

        query_tokens = self._tokenize(query)
        ref_set = set(evidence_refs or [])
        scored: list[RetrievedMemory] = []

        for record in records:
            score = 0
            haystack = " ".join(
                [
                    record.title,
                    record.summary,
                    record.kind.value,
                    record.namespace.render(),
                    " ".join(record.evidence_refs),
                    " ".join(record.source_artifact_refs),
                ]
            )
            tokens = self._tokenize(haystack)
            lexical_overlap = len(query_tokens & tokens)
            namespace_tokens = self._tokenize(record.namespace.render())
            score += lexical_overlap * 4
            score += len(query_tokens & namespace_tokens) * 5
            score += len(ref_set & set(record.evidence_refs)) * 8
            score += len(ref_set & set(record.source_artifact_refs)) * 6
            score += self._kind_prior(record.kind)
            score += self._confidence_boost(record.confidence)
            score += self._promotion_boost(record.promotion_score)
            score += self._recency_boost(record.updated_at)
            if score <= 0:
                continue
            scored.append(RetrievedMemory(record=record, score=score))

        scored.sort(key=lambda item: (-item.score, -self._updated_at_epoch(item.record.updated_at), item.record.title))
        return [item.record for item in self._diversify(scored, limit=limit)]

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9_:-]+", text.lower()) if len(token) >= 3}

    def _kind_prior(self, kind: MemoryKind) -> int:
        if kind == MemoryKind.OPEN_LOOP:
            return 5
        if kind == MemoryKind.DECISION:
            return 4
        if kind == MemoryKind.INVESTIGATION_FINDING:
            return 3
        if kind == MemoryKind.PAPER_TAKEAWAY:
            return 2
        if kind == MemoryKind.DISCUSSION_TAKEAWAY:
            return 1
        if kind == MemoryKind.PROJECT_FRAME:
            return 3
        return 0

    def _confidence_boost(self, confidence: str) -> int:
        rank = {"low": 0, "medium": 2, "high": 4}
        return rank.get(confidence, 1)

    def _promotion_boost(self, promotion_score: int) -> int:
        return max(0, min(promotion_score // 2, 6))

    def _recency_boost(self, updated_at: str) -> int:
        try:
            when = datetime.fromisoformat(updated_at)
        except ValueError:
            return 0
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - when.astimezone(UTC)).total_seconds() / 86400.0)
        if age_days <= 7:
            return 4
        if age_days <= 30:
            return 3
        if age_days <= 90:
            return 2
        if age_days <= 180:
            return 1
        return 0

    def _updated_at_epoch(self, updated_at: str) -> float:
        try:
            when = datetime.fromisoformat(updated_at)
        except ValueError:
            return 0.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when.astimezone(UTC).timestamp()

    def _diversify(self, scored: list[RetrievedMemory], *, limit: int) -> list[RetrievedMemory]:
        selected: list[RetrievedMemory] = []
        namespace_seen: set[str] = set()

        for item in scored:
            namespace = item.record.namespace.render()
            if namespace in namespace_seen:
                continue
            selected.append(item)
            namespace_seen.add(namespace)
            if len(selected) >= limit:
                return selected

        for item in scored:
            if any(existing.record.memory_id == item.record.memory_id for existing in selected):
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected
