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
