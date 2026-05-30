import { useEffect, useMemo, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { fetchPaperPdf, PaperRecord } from "../api/client";
import { pdfWorkerSrc } from "./pdfWorker";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerSrc;

export default function PaperPdfViewer({ project, paper }: { project: string; paper: PaperRecord }) {
  const [pdfData, setPdfData] = useState<ArrayBuffer | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [firstPageReady, setFirstPageReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Fetch PDF (from blob cache or network) whenever paper changes.
  // fetchPaperPdf always returns a fresh ArrayBuffer copy safe to transfer.
  useEffect(() => {
    setPageNumber(1);
    setPageCount(0);
    setFirstPageReady(false);
    setLoadError(null);
    setPdfData(null);

    let canceled = false;
    fetchPaperPdf(project, paper.id)
      .then((buf) => {
        if (!canceled) setPdfData(buf);
      })
      .catch((err: Error) => {
        if (!canceled) setLoadError(err.message || "Unable to fetch PDF");
      });
    return () => {
      canceled = true;
    };
  }, [paper.id, project]);

  // Pass raw bytes to PDF.js — zero network roundtrips during render.
  // Each ArrayBuffer from fetchPaperPdf is a fresh copy, safe for transfer.
  const file = useMemo(() => {
    if (!pdfData) return null;
    return { data: new Uint8Array(pdfData) };
  }, [pdfData]);

  const statusLabel = loadError
    ? loadError
    : firstPageReady
      ? null
      : !pdfData
        ? "Fetching PDF"
        : "Rendering first page";

  return (
    <div className="rounded-md border border-slate-200 bg-white">
      <div className="flex h-11 items-center justify-between border-b border-slate-200 px-3">
        <div className="text-sm font-semibold">PDF</div>
        <div className="flex items-center gap-2 text-sm">
          <button
            className="h-8 rounded-md border border-slate-300 px-3 disabled:opacity-40"
            disabled={pageNumber <= 1}
            onClick={() => setPageNumber((v) => Math.max(1, v - 1))}
            type="button"
          >
            Prev
          </button>
          <span className="min-w-20 text-center text-slate-600">
            {pageNumber} / {pageCount || "-"}
          </span>
          <button
            className="h-8 rounded-md border border-slate-300 px-3 disabled:opacity-40"
            disabled={pageCount > 0 && pageNumber >= pageCount}
            onClick={() => setPageNumber((v) => Math.min(pageCount || v + 1, v + 1))}
            type="button"
          >
            Next
          </button>
        </div>
      </div>
      <div className="relative flex h-[72vh] overflow-auto bg-slate-100 p-4">
        {statusLabel ? <PdfLoadingOverlay label={statusLabel} error={Boolean(loadError)} /> : null}
        {file ? (
          <Document
            file={file}
            loading={null}
            onLoadError={(error) => setLoadError(error.message || "Unable to load PDF")}
            onLoadSuccess={({ numPages }) => setPageCount(numPages)}
          >
            <Page
              devicePixelRatio={1}
              loading={null}
              onRenderSuccess={() => setFirstPageReady(true)}
              pageNumber={pageNumber}
              renderAnnotationLayer={false}
              renderTextLayer={false}
              width={840}
            />
          </Document>
        ) : null}
      </div>
    </div>
  );
}

function PdfLoadingOverlay({ label, error }: { label: string; error?: boolean }) {
  return (
    <div className="absolute inset-0 z-10 flex items-start justify-center bg-slate-100/90 p-8">
      <div className="w-full max-w-3xl rounded-md border border-slate-200 bg-white p-5 shadow-sm">
        <div className="h-3 w-28 rounded bg-slate-200" />
        <div className="mt-4 h-3 w-full rounded bg-slate-100" />
        <div className="mt-2 h-3 w-5/6 rounded bg-slate-100" />
        <div className={`mt-5 text-sm ${error ? "text-red-600" : "text-slate-500"}`}>{label}</div>
      </div>
    </div>
  );
}
