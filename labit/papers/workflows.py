from __future__ import annotations

from dataclasses import dataclass

from labit.papers.arxiv import ArxivClient, ArxivPaperSource
from labit.papers.models import GlobalPaperMeta
from labit.papers.service import PaperService
from labit.papers.summarizer import PaperSummarizer
from labit.paths import RepoPaths


@dataclass
class PaperAssetBundle:
    source: ArxivPaperSource
    meta: GlobalPaperMeta
    html_content: str | None
    pdf_bytes: bytes | None


class PaperWorkflowService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        paper_service: PaperService | None = None,
        arxiv_client: ArxivClient | None = None,
        summarizer: PaperSummarizer | None = None,
    ):
        self.paths = paths
        self.paper_service = paper_service or PaperService(paths)
        self.arxiv_client = arxiv_client or ArxivClient()
        self.summarizer = summarizer or PaperSummarizer(paths)

    def resolve_assets(self, reference: str) -> PaperAssetBundle:
        source = self.arxiv_client.resolve(reference)
        html_content = self.arxiv_client.download_html(source)
        pdf_bytes = self.arxiv_client.download_pdf(source)
        if html_content is None and pdf_bytes is None:
            raise RuntimeError("Could not download either HTML or PDF for this paper.")

        meta = GlobalPaperMeta(
            paper_id=source.canonical_paper_id,
            title=source.title,
            authors=source.authors,
            year=source.year,
            source="arXiv",
            url=source.abs_url,
            html_url=source.html_url,
            pdf_url=source.pdf_url,
            external_ids={"arxiv": source.arxiv_id},
        )
        return PaperAssetBundle(
            source=source,
            meta=meta,
            html_content=html_content,
            pdf_bytes=pdf_bytes,
        )

    def pull(self, *, project: str, reference: str) -> dict:
        bundle = self.resolve_assets(reference)
        result = self.paper_service.pull_paper(
            project=project,
            meta=bundle.meta,
            html_content=bundle.html_content,
            pdf_bytes=bundle.pdf_bytes,
        )
        result["downloaded_html"] = bundle.html_content is not None
        result["downloaded_pdf"] = bundle.pdf_bytes is not None
        result["title"] = bundle.meta.title
        result["source_url"] = bundle.source.abs_url
        return result

    def ingest(self, *, project: str, reference: str, provider: str | None = None) -> dict:
        bundle = self.resolve_assets(reference)
        summary_markdown, run_id = self.summarizer.summarize(
            project=project,
            meta=bundle.meta,
            html_content=bundle.html_content,
            provider=provider,
        )
        result = self.paper_service.ingest_paper(
            project=project,
            meta=bundle.meta,
            summary_markdown=summary_markdown,
            html_content=bundle.html_content,
            pdf_bytes=bundle.pdf_bytes,
        )
        result["downloaded_html"] = bundle.html_content is not None
        result["downloaded_pdf"] = bundle.pdf_bytes is not None
        result["title"] = bundle.meta.title
        result["source_url"] = bundle.source.abs_url
        result["summary_run_id"] = run_id
        return result
