from __future__ import annotations

import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from labit.hypotheses.models import (
    HypothesisDetail,
    HypothesisDraft,
    HypothesisRecord,
    HypothesisResolution,
    HypothesisState,
    HypothesisStatus,
    HypothesisSummary,
)
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class HypothesisService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def list_hypotheses(self, project: str) -> list[HypothesisSummary]:
        resolved = self._require_project(project)
        hypotheses_dir = self.hypotheses_dir(resolved)
        if not hypotheses_dir.exists():
            return []

        summaries_by_id: dict[str, HypothesisSummary] = {}

        for path in sorted(hypotheses_dir.glob("h*/hypothesis.yaml")):
            detail = self._load_structured_detail(path.parent)
            summaries_by_id[detail.record.hypothesis_id] = HypothesisSummary(
                hypothesis_id=detail.record.hypothesis_id,
                title=detail.record.title,
                state=detail.record.state,
                resolution=detail.record.resolution,
                status=detail.record.status,
                result_summary=detail.record.result_summary,
                source_session_id=detail.record.source_session_id,
                source_paper_ids=detail.record.source_paper_ids,
                updated_at=detail.record.updated_at,
                path=detail.path,
                legacy=False,
            )

        for path in sorted(hypotheses_dir.glob("h*.yaml")):
            hypothesis_id = path.stem
            if hypothesis_id in summaries_by_id:
                continue
            detail = self._load_legacy_detail(resolved, path)
            summaries_by_id[hypothesis_id] = HypothesisSummary(
                hypothesis_id=detail.record.hypothesis_id,
                title=detail.record.title,
                state=detail.record.state,
                resolution=detail.record.resolution,
                status=detail.record.status,
                result_summary=detail.record.result_summary,
                source_session_id=detail.record.source_session_id,
                source_paper_ids=detail.record.source_paper_ids,
                updated_at=detail.record.updated_at,
                path=detail.path,
                legacy=True,
            )

        return sorted(
            summaries_by_id.values(),
            key=lambda item: self._hypothesis_sort_key(item.hypothesis_id),
            reverse=True,
        )

    def load_hypothesis(self, project: str, hypothesis_id: str) -> HypothesisDetail:
        resolved = self._require_project(project)
        structured_dir = self.hypothesis_dir(resolved, hypothesis_id)
        if (structured_dir / "hypothesis.yaml").exists():
            return self._load_structured_detail(structured_dir)

        legacy_path = self.hypotheses_dir(resolved) / f"{hypothesis_id}.yaml"
        if legacy_path.exists():
            return self._load_legacy_detail(resolved, legacy_path)

        raise FileNotFoundError(f"Hypothesis '{hypothesis_id}' not found in project '{resolved}'.")

    def next_hypothesis_id(self, project: str) -> str:
        resolved = self._require_project(project)
        hypotheses_dir = self.hypotheses_dir(resolved)
        highest = 0
        if hypotheses_dir.exists():
            for path in hypotheses_dir.iterdir():
                match = re.fullmatch(r"h(\d+)(?:\.yaml)?", path.name)
                if not match:
                    continue
                highest = max(highest, int(match.group(1)))
        return f"h{highest + 1:03d}"

    def create_hypothesis(
        self,
        *,
        project: str,
        draft: HypothesisDraft,
        source_session_id: str | None = None,
    ) -> HypothesisDetail:
        resolved = self._require_project(project)
        hypothesis_id = self.next_hypothesis_id(resolved)
        hypothesis_dir = self.hypothesis_dir(resolved, hypothesis_id)
        hypothesis_dir.mkdir(parents=True, exist_ok=False)

        record = draft.to_record(
            project=resolved,
            hypothesis_id=hypothesis_id,
            source_session_id=source_session_id,
        )

        self._atomic_write_yaml(hypothesis_dir / "hypothesis.yaml", record.model_dump(mode="json"))
        self._atomic_write_text(hypothesis_dir / "rationale.md", self._normalize_markdown(draft.rationale_markdown))
        self._atomic_write_text(
            hypothesis_dir / "experiment_plan.md",
            self._normalize_markdown(draft.experiment_plan_markdown),
        )

        return self.load_hypothesis(resolved, hypothesis_id)

    def update_hypothesis_record(
        self,
        *,
        project: str,
        hypothesis_id: str,
        record: HypothesisRecord,
        rationale_markdown: str | None = None,
        experiment_plan_markdown: str | None = None,
    ) -> HypothesisDetail:
        resolved = self._require_project(project)
        detail = self.load_hypothesis(resolved, hypothesis_id)
        hypothesis_dir = self.hypothesis_dir(resolved, hypothesis_id)
        hypothesis_dir.mkdir(parents=True, exist_ok=True)

        self._atomic_write_yaml(hypothesis_dir / "hypothesis.yaml", record.model_dump(mode="json"))
        if rationale_markdown is not None:
            self._atomic_write_text(hypothesis_dir / "rationale.md", self._normalize_markdown(rationale_markdown))
        elif detail.legacy:
            self._atomic_write_text(hypothesis_dir / "rationale.md", self._normalize_markdown(detail.rationale_markdown))

        if experiment_plan_markdown is not None:
            self._atomic_write_text(
                hypothesis_dir / "experiment_plan.md",
                self._normalize_markdown(experiment_plan_markdown),
            )
        elif detail.legacy:
            self._atomic_write_text(
                hypothesis_dir / "experiment_plan.md",
                self._normalize_markdown(detail.experiment_plan_markdown),
            )

        return self.load_hypothesis(resolved, hypothesis_id)

    def hypotheses_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "hypotheses"

    def hypothesis_dir(self, project: str, hypothesis_id: str) -> Path:
        return self.hypotheses_dir(project) / hypothesis_id

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _load_structured_detail(self, hypothesis_dir: Path) -> HypothesisDetail:
        record = HypothesisRecord.model_validate(
            yaml.safe_load((hypothesis_dir / "hypothesis.yaml").read_text(encoding="utf-8")) or {}
        )
        rationale = self._safe_read(hypothesis_dir / "rationale.md")
        experiment_plan = self._safe_read(hypothesis_dir / "experiment_plan.md")
        return HypothesisDetail(
            record=record,
            rationale_markdown=rationale,
            experiment_plan_markdown=experiment_plan,
            path=str(hypothesis_dir.relative_to(self.paths.root)),
            legacy=False,
        )

    def _load_legacy_detail(self, project: str, path: Path) -> HypothesisDetail:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        hypothesis_id = path.stem
        status = self._map_legacy_status(raw.get("status"))
        state, resolution = self._map_legacy_decision_fields(raw.get("status"))
        source_papers = self._normalize_source_papers(raw.get("source_paper"))
        created_at = str(raw.get("date", "")).strip()
        notes = str(raw.get("notes", "") or "").strip()
        actual_result = str(raw.get("actual_result", "") or "").strip()

        rationale_parts = [
            "Legacy flat hypothesis imported into LABIT structured view.",
        ]
        if notes:
            rationale_parts.append("")
            rationale_parts.append(notes)

        experiment_lines: list[str] = []
        for label, key in (
            ("Config", "config"),
            ("Branch", "branch"),
            ("GPU", "gpu"),
            ("Baseline metric", "baseline_metric"),
            ("Expected improvement", "expected_improvement"),
            ("Actual result", "actual_result"),
            ("W&B run", "wandb_run_id"),
        ):
            value = raw.get(key)
            text = "" if value is None else str(value).strip()
            if text:
                experiment_lines.append(f"- {label}: {text}")
        experiment_plan = "\n".join(experiment_lines)

        record = HypothesisRecord(
            hypothesis_id=hypothesis_id,
            project=project,
            title=str(raw.get("title", hypothesis_id)).strip() or hypothesis_id,
            claim=str(raw.get("hypothesis", raw.get("title", hypothesis_id))).strip() or hypothesis_id,
            state=state,
            resolution=resolution,
            status=status,
            motivation="",
            independent_variable="",
            dependent_variable="",
            success_criteria=str(raw.get("expected_improvement", "") or "").strip(),
            failure_criteria="",
            result_summary=actual_result,
            decision_rationale="",
            supporting_experiment_ids=[],
            contradicting_experiment_ids=[],
            closed_at=created_at if state == HypothesisState.CLOSED else None,
            source_session_id=None,
            source_paper_ids=source_papers,
            created_at=created_at or "",
            updated_at=created_at or "",
        )

        return HypothesisDetail(
            record=record,
            rationale_markdown="\n".join(rationale_parts).strip(),
            experiment_plan_markdown=experiment_plan,
            path=str(path.relative_to(self.paths.root)),
            legacy=True,
            raw_legacy=raw if isinstance(raw, dict) else {},
        )

    def _map_legacy_status(self, value: object) -> HypothesisStatus:
        text = str(value or "").strip().lower()
        mapping = {
            "proposed": HypothesisStatus.DRAFT,
            "draft": HypothesisStatus.DRAFT,
            "in-progress": HypothesisStatus.ACTIVE,
            "active": HypothesisStatus.ACTIVE,
            "validated": HypothesisStatus.SUPPORTED,
            "supported": HypothesisStatus.SUPPORTED,
            "rejected": HypothesisStatus.REJECTED,
            "inconclusive": HypothesisStatus.INCONCLUSIVE,
            "archived": HypothesisStatus.ARCHIVED,
        }
        return mapping.get(text, HypothesisStatus.DRAFT)

    def _map_legacy_decision_fields(self, value: object) -> tuple[HypothesisState, HypothesisResolution]:
        text = str(value or "").strip().lower()
        if text in {"validated", "supported"}:
            return HypothesisState.CLOSED, HypothesisResolution.VALIDATED
        if text == "rejected":
            return HypothesisState.CLOSED, HypothesisResolution.REJECTED
        if text in {"inconclusive", "archived"}:
            return HypothesisState.CLOSED, HypothesisResolution.INCONCLUSIVE
        return HypothesisState.OPEN, HypothesisResolution.PENDING

    def _normalize_source_papers(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    def _normalize_markdown(self, text: str) -> str:
        normalized = text.strip()
        return normalized + "\n" if normalized else ""

    def _safe_read(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def _hypothesis_sort_key(self, hypothesis_id: str) -> tuple[int, str]:
        match = re.fullmatch(r"h(\d+)", hypothesis_id)
        if match:
            return int(match.group(1)), hypothesis_id
        return 0, hypothesis_id

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)

    def _atomic_write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        self._atomic_write_text(path, text)
