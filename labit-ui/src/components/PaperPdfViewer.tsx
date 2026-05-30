import { useEffect, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { paperPdfUrl, PaperRecord } from "../api/client";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

export default function PaperPdfViewer({ project, paper }: { project: string; paper: PaperRecord }) {
  const [pageCount, setPageCount] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);

  useEffect(() => {
    setPageNumber(1);
  }, [paper.id]);

  return (
    <div className="rounded-md border border-slate-200 bg-white">
      <div className="flex h-11 items-center justify-between border-b border-slate-200 px-3">
        <div className="text-sm font-semibold">PDF</div>
        <div className="flex items-center gap-2 text-sm">
          <button
            className="h-8 rounded-md border border-slate-300 px-3 disabled:opacity-40"
            disabled={pageNumber <= 1}
            onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
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
            onClick={() => setPageNumber((value) => Math.min(pageCount || value + 1, value + 1))}
            type="button"
          >
            Next
          </button>
        </div>
      </div>
      <div className="flex h-[72vh] overflow-auto bg-slate-100 p-4">
        <Document
          file={paperPdfUrl(project, paper.id)}
          loading={<EmptyState label="Loading PDF" />}
          onLoadSuccess={({ numPages }) => setPageCount(numPages)}
        >
          <Page pageNumber={pageNumber} width={840} />
        </Document>
      </div>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-sm text-slate-500">
      {label}
    </div>
  );
}
