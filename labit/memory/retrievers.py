from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
import re
from dataclasses import dataclass

from labit.memory.models import MemoryKind, MemoryNamespace, MemoryRecord, MemoryType
from labit.memory.store import MemoryStore

logger = logging.getLogger(__name__)


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
        if not query_tokens and not ref_set:
            return []
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


class MemPalaceRetriever:
    """Retriever backed by the upstream mempalace package.

    Uses Layer1 for wake-up and Layer3 for semantic search.
    Falls back to the legacy MemoryRetriever if mempalace is not available.
    """

    def __init__(self, palace_path: str | Path, fallback: MemoryRetriever | None = None):
        self.palace_path = str(palace_path)
        self.fallback = fallback
        self._available: bool | None = None

    def _check_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from labit.memory.palace.layers import Layer3  # noqa: F401
            palace = Path(self.palace_path)
            if not palace.is_dir():
                self._available = False
                return False
            self._available = True
        except ImportError:
            logger.debug("MemPalace dependencies not available, using fallback")
            self._available = False
        return self._available

    def retrieve(
        self,
        *,
        project: str,
        query: str,
        evidence_refs: list[str] | None = None,
        limit: int = 6,
    ) -> list[MemoryRecord]:
        if not self._check_available():
            if self.fallback:
                return self.fallback.retrieve(
                    project=project, query=query,
                    evidence_refs=evidence_refs, limit=limit,
                )
            return []

        try:
            from labit.memory.palace.layers import Layer3
            l3 = Layer3(palace_path=self.palace_path)
            hits = l3.search_raw(query, wing=project or None, n_results=limit)
        except Exception as exc:
            logger.warning("MemPalace search failed: %s", exc)
            if self.fallback:
                return self.fallback.retrieve(
                    project=project, query=query,
                    evidence_refs=evidence_refs, limit=limit,
                )
            return []

        records: list[MemoryRecord] = []
        for hit in hits:
            room = hit.get("room", "general")
            source_file = hit.get("source_file", "")
            similarity = hit.get("similarity", 0.0)
            title = f"[verbatim:{room}] {source_file}" if source_file else f"[verbatim:{room}]"
            source_refs = [f"file:{source_file}"] if source_file else []
            records.append(MemoryRecord(
                project=project or "unknown",
                namespace=MemoryNamespace(parts=(project or "unknown", room)),
                kind=MemoryKind.VERBATIM_RECALL,
                memory_type=MemoryType.EPISODIC,
                title=title,
                summary=hit.get("text", ""),
                source_artifact_refs=source_refs,
                confidence="medium",
                promotion_score=int(similarity * 10),
            ))
        return records

    def wake_up(self, *, wing: str | None = None) -> str:
        """Generate L0+L1 wake-up text (~600-900 tokens). Returns empty string if unavailable."""
        if not self._check_available():
            return ""
        try:
            from labit.memory.palace.layers import MemoryStack
            stack = MemoryStack(palace_path=self.palace_path)
            return stack.wake_up(wing=wing)
        except Exception as exc:
            logger.debug("wake_up failed: %s", exc)
            return ""
