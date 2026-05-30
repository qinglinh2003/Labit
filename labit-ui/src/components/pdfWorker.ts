export const pdfWorkerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

let preloadStarted = false;

export function preloadPdfWorker(): void {
  if (preloadStarted || typeof document === "undefined") {
    return;
  }
  preloadStarted = true;

  const link = document.createElement("link");
  link.rel = "modulepreload";
  link.href = pdfWorkerSrc;
  document.head.appendChild(link);

  void fetch(pdfWorkerSrc, { cache: "force-cache" }).catch(() => undefined);
}
