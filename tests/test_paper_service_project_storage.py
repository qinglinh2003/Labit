from __future__ import annotations

from pathlib import Path

import yaml

from labit.papers.models import ArxivPaperMetadata
from labit.papers.service import PaperService
from labit.paths import RepoPaths


def _paths(root: Path) -> RepoPaths:
    return RepoPaths(
        root=root,
        labit_dir=root / ".labit",
        runs_dir=root / ".labit" / "runs",
        conversations_dir=root / ".labit" / "conversations",
        context_dir=root / ".labit" / "context",
        configs_dir=root / "configs",
        project_configs_dir=root / "configs" / "projects",
        active_project_path=root / "configs" / "active_project",
        vault_dir=root / "vault",
        vault_projects_dir=root / "vault" / "projects",
    )


def _create_project(root: Path, name: str = "Labit") -> RepoPaths:
    paths = _paths(root)
    paths.project_configs_dir.mkdir(parents=True)
    paths.vault_projects_dir.mkdir(parents=True)
    (paths.project_configs_dir / f"{name}.yaml").write_text(
        yaml.safe_dump({"name": name}),
        encoding="utf-8",
    )
    (paths.vault_projects_dir / name).mkdir()
    return paths


def test_import_arxiv_pdf_writes_project_scoped_paper_directory(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    service = PaperService(paths)

    record = service.import_arxiv_pdf(
        project="Labit",
        metadata=ArxivPaperMetadata(
            arxiv_id="2401.12345",
            title="A Useful Paper",
            authors=["Ada Lovelace", "Grace Hopper"],
            abstract="A compact abstract.",
            url="https://arxiv.org/abs/2401.12345",
            pdf_url="https://arxiv.org/pdf/2401.12345",
            submitted_date="2024-01-02",
        ),
        pdf_content=b"%PDF-1.7\n",
    )

    paper_dir = tmp_path / "vault" / "projects" / "Labit" / "papers" / "arxiv-2401.12345"
    assert record.id == "arxiv:2401.12345"
    assert record.submitted_date == "2024-01-02"
    assert record.local_pdf_path == "vault/projects/Labit/papers/arxiv-2401.12345/paper.pdf"
    assert record.artifact_dir_path == "vault/projects/Labit/papers/arxiv-2401.12345/artifacts"
    assert (paper_dir / "paper.yaml").exists()
    assert (paper_dir / "paper.pdf").read_bytes() == b"%PDF-1.7\n"
    assert (paper_dir / "artifacts" / "notes.md").exists()


def test_project_paper_import_is_idempotent_and_lists_latest(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    service = PaperService(paths)
    metadata = ArxivPaperMetadata(arxiv_id="2401.12345", title="Original")

    first = service.import_arxiv_pdf(project="Labit", metadata=metadata, pdf_content=b"first")
    second = service.import_arxiv_pdf(
        project="Labit",
        metadata=metadata.model_copy(update={"title": "Updated"}),
        pdf_content=b"second",
    )

    records = service.list_papers("Labit")
    assert len(records) == 1
    assert records[0].title == "Updated"
    assert second.added_at == first.added_at
    assert service.pdf_path(project="Labit", paper_id="arxiv-2401.12345").read_bytes() == b"second"
    assert service.list_artifacts(project="Labit", paper_id="2401.12345")[0]["relative_path"] == "notes.md"


def test_project_paper_records_ignore_future_metadata_fields(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    service = PaperService(paths)
    service.import_arxiv_pdf(
        project="Labit",
        metadata=ArxivPaperMetadata(arxiv_id="2401.12345", title="A Useful Paper"),
        pdf_content=b"%PDF-1.7\n",
    )

    metadata_path = (
        tmp_path / "vault" / "projects" / "Labit" / "papers" / "arxiv-2401.12345" / "paper.yaml"
    )
    raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    raw["future_field"] = {"review_state": "candidate"}
    metadata_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    record = service.get_paper(project="Labit", paper_id="arxiv-2401.12345")

    assert record.title == "A Useful Paper"
    assert not hasattr(record, "future_field")
