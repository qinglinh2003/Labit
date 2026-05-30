const API_BASE = import.meta.env.VITE_LABIT_API_BASE ?? "";

export interface ProjectListResponse {
  projects: string[];
  active_project: string | null;
}

export interface PaperRecord {
  id: string;
  source: string;
  arxiv_id: string;
  title: string;
  authors: string[];
  abstract: string;
  url: string;
  source_url: string;
  pdf_url: string;
  local_pdf_path: string;
  local_metadata_path: string;
  artifact_dir_path: string;
  added_at: string;
}

export interface ArtifactRecord {
  name: string;
  path: string;
  relative_path: string;
  size_bytes: number;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export function listProjects(): Promise<ProjectListResponse> {
  return getJson<ProjectListResponse>("/api/projects");
}

export function listPapers(project: string): Promise<PaperRecord[]> {
  return getJson<PaperRecord[]>(`/api/projects/${encodeURIComponent(project)}/papers`);
}

export function listArtifacts(project: string, paperId: string): Promise<ArtifactRecord[]> {
  return getJson<ArtifactRecord[]>(
    `/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/artifacts`
  );
}

export function paperPdfUrl(project: string, paperId: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(
    paperId
  )}/pdf`;
}

// ---------------------------------------------------------------------------
// PDF blob cache – fetch full PDF once, serve from memory thereafter.
// Hover triggers prefetch; by click-time the blob is usually ready.
// We cache as Blob (not ArrayBuffer) because PDF.js transfers the
// ArrayBuffer to its worker, detaching the original.  Each render gets
// a fresh ArrayBuffer via blob.arrayBuffer().
// ---------------------------------------------------------------------------

const pdfBlobCache = new Map<string, Blob>();
const pdfBlobInflight = new Map<string, Promise<Blob>>();

function pdfCacheKey(project: string, paperId: string): string {
  return `${project}\0${paperId}`;
}

/** Start fetching the full PDF in the background (idempotent). */
export function prefetchPaperPdf(project: string, paperId: string): void {
  const key = pdfCacheKey(project, paperId);
  if (pdfBlobCache.has(key) || pdfBlobInflight.has(key)) return;
  const promise = fetch(paperPdfUrl(project, paperId))
    .then((r) => {
      if (!r.ok) throw new Error(`PDF fetch failed: ${r.status}`);
      return r.blob();
    })
    .then((blob) => {
      pdfBlobCache.set(key, blob);
      pdfBlobInflight.delete(key);
      return blob;
    })
    .catch((err) => {
      pdfBlobInflight.delete(key);
      // Swallow – prefetch is best-effort; fetchPaperPdf will retry.
      console.warn("PDF prefetch failed:", err);
      return new Blob(); // satisfy type; won't be cached (size 0)
    });
  pdfBlobInflight.set(key, promise);
}

/** Check whether the PDF blob is already cached. */
export function hasCachedPdf(project: string, paperId: string): boolean {
  const blob = pdfBlobCache.get(pdfCacheKey(project, paperId));
  return blob != null && blob.size > 0;
}

/** Return a fresh ArrayBuffer copy from the cached blob (safe to transfer). */
export async function getCachedPdfData(project: string, paperId: string): Promise<ArrayBuffer | null> {
  const blob = pdfBlobCache.get(pdfCacheKey(project, paperId));
  if (!blob || blob.size === 0) return null;
  return blob.arrayBuffer(); // always a new copy
}

/** Return PDF as ArrayBuffer – from cache if available, otherwise fetch. */
export async function fetchPaperPdf(project: string, paperId: string): Promise<ArrayBuffer> {
  const key = pdfCacheKey(project, paperId);
  const cached = pdfBlobCache.get(key);
  if (cached && cached.size > 0) return cached.arrayBuffer();

  let inflight = pdfBlobInflight.get(key);
  if (!inflight) {
    prefetchPaperPdf(project, paperId);
    inflight = pdfBlobInflight.get(key);
  }
  if (inflight) {
    const blob = await inflight;
    if (blob.size > 0) return blob.arrayBuffer();
  }
  // Retry once if prefetch silently failed
  const r = await fetch(paperPdfUrl(project, paperId));
  if (!r.ok) throw new Error(`PDF fetch failed: ${r.status}`);
  const blob = await r.blob();
  pdfBlobCache.set(key, blob);
  return blob.arrayBuffer();
}
