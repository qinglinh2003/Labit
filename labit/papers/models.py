from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PaperRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    source: str = "arxiv"
    arxiv_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    url: str = ""
    source_url: str
    html_url: str = ""
    html_fetch_error: str = ""
    pdf_url: str
    local_pdf_path: str = ""
    local_html_path: str = ""
    local_metadata_path: str = ""
    artifact_dir_path: str = ""
    added_at: str


class ArxivPaperMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    arxiv_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    url: str = ""
    pdf_url: str = ""
