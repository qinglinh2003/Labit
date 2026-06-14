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
  tags: string[];
  starred: boolean;
  status: "unread" | "reading" | "read" | "rejected";
  status_updated_at: string;
  submitted_date: string;
  added_at: string;
}

export interface ArtifactRecord {
  name: string;
  path: string;
  relative_path: string;
  size_bytes: number;
}

// ---------------------------------------------------------------------------
// Reader manifest types (server-side rendered PDF pages)
// ---------------------------------------------------------------------------

export interface ReaderPageInfo {
  page: number;
  width_pt: number;
  height_pt: number;
  css_width: number;
  css_height: number;
  first_tile_css_height: number;
  thumb: string;
  preview: string;
  retina: string;
  first_viewport_tile: string;
}

export interface ReaderManifest {
  page_count: number;
  pages: ReaderPageInfo[];
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export function listProjects(includeArchived = false): Promise<ProjectListResponse> {
  const qs = includeArchived ? "?include_archived=true" : "";
  return getJson<ProjectListResponse>(`/api/projects${qs}`);
}

export async function archiveProject(project: string): Promise<{ name: string; archived: boolean; changed: boolean }> {
  const response = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/archive`, { method: "PUT" });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<{ name: string; archived: boolean; changed: boolean }>;
}

export async function unarchiveProject(project: string): Promise<{ name: string; archived: boolean; changed: boolean }> {
  const response = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/archive`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<{ name: string; archived: boolean; changed: boolean }>;
}

export interface CreateProjectPayload {
  name: string;
  description?: string;
  repo?: string;
  keywords?: string[];
  relevance_criteria?: string;
}

export async function createProject(payload: CreateProjectPayload): Promise<{ name: string }> {
  const response = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: payload.name,
      description: payload.description ?? "",
      repo: payload.repo || null,
      keywords: payload.keywords ?? [],
      relevance_criteria: payload.relevance_criteria ?? "",
    }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<{ name: string }>;
}

export function listPapers(project: string, includeRejected = false): Promise<PaperRecord[]> {
  const params = includeRejected ? "?include_rejected=true" : "";
  return getJson<PaperRecord[]>(`/api/projects/${encodeURIComponent(project)}/papers${params}`);
}

export async function togglePaperStar(project: string, paperId: string): Promise<PaperRecord> {
  const response = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/star`,
    { method: "PUT" }
  );
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<PaperRecord>;
}

export async function updatePaperStatus(project: string, paperId: string, status: string): Promise<PaperRecord> {
  const response = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/status`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }
  );
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<PaperRecord>;
}

export async function updatePaperTags(project: string, paperId: string, tags: string[]): Promise<PaperRecord> {
  const response = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/tags`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags }),
    }
  );
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<PaperRecord>;
}

export interface NoteResponse {
  content: string;
}

export async function getNote(project: string, paperId: string): Promise<NoteResponse> {
  return getJson<NoteResponse>(
    `/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/note`
  );
}

export async function saveNote(project: string, paperId: string, content: string): Promise<NoteResponse> {
  const response = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/note`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<NoteResponse>;
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
// Reader manifest + render URLs
// ---------------------------------------------------------------------------

export function fetchReaderManifest(project: string, paperId: string): Promise<ReaderManifest> {
  const key = manifestCacheKey(project, paperId);
  const cached = _manifestCache.get(key);
  if (cached) return Promise.resolve(cached);
  const inflight = _manifestInflight.get(key);
  if (inflight) {
    return inflight.then((manifest) => {
      if (!manifest) throw new Error("Failed to load reader manifest");
      return manifest;
    });
  }

  return getJson<ReaderManifest>(
    `/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/reader-manifest`
  ).then((manifest) => {
    _manifestCache.set(key, manifest);
    return manifest;
  });
}

export function renderUrl(project: string, paperId: string, name: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(
    paperId
  )}/renders/${encodeURIComponent(name)}`;
}

export function pageImageUrl(project: string, paperId: string, page: number, w: number = 1600): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(
    paperId
  )}/pages/${page}/image?w=${w}`;
}

/** Prefetch manifest + first viewport tile on hover (best-effort). */
export function prefetchReaderManifest(project: string, paperId: string): void {
  const key = manifestCacheKey(project, paperId);
  if (_manifestCache.has(key) || _manifestInflight.has(key)) return;
  const promise = fetchReaderManifest(project, paperId)
    .then((manifest) => {
      _manifestCache.set(key, manifest);
      // Also prefetch the first viewport tile image
      if (manifest.pages.length > 0) {
        const tile = manifest.pages[0].first_viewport_tile;
        const img = new window.Image();
        img.src = renderUrl(project, paperId, tile);
      }
      return manifest;
    })
    .catch(() => null)
    .finally(() => {
      _manifestInflight.delete(key);
    });
  _manifestInflight.set(key, promise);
}

function manifestCacheKey(project: string, paperId: string): string {
  return `${project}\0${paperId}`;
}

const _manifestCache = new Map<string, ReaderManifest>();
const _manifestInflight = new Map<string, Promise<ReaderManifest | null>>();
