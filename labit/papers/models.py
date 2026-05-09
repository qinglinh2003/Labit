from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PaperRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arxiv_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    source_url: str
    html_url: str
    pdf_url: str
    local_html_path: str = ""
    local_metadata_path: str = ""
    added_at: str
