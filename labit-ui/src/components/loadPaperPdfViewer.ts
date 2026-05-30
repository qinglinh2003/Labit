import { preloadPdfWorker } from "./pdfWorker";

export function loadPaperPdfViewer() {
  preloadPdfWorker();
  return import("./PaperPdfViewer");
}
