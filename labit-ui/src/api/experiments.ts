const API = import.meta.env.VITE_API_URL ?? "";

export interface ExperimentInfo {
  experiment_id: string;
  name: string;
  description: string;
  profile: string;
  tags: string[];
  has_run_sh: boolean;
}

export interface RunInfo {
  run_id: string;
  experiment_id: string;
  name: string;
  profile: string;
  script: string;
  command: string;
  status: string;
  pid: number | null;
  exit_code: number | null;
  remote_workdir: string;
  remote_run_dir: string;
  local_commit: string;
  dirty: boolean;
  created_at: string;
  started_at: string;
  finished_at: string;
  notes: string;
  error: string;
}

export interface ExperimentWithRun {
  experiment: ExperimentInfo;
  latest_run: RunInfo | null;
}

export async function listExperiments(project: string): Promise<ExperimentWithRun[]> {
  const res = await fetch(`${API}/api/projects/${project}/experiments`);
  if (!res.ok) throw new Error(`Failed to list experiments: ${res.status}`);
  return res.json();
}

export async function launchExperiment(
  project: string,
  experimentId: string,
  body: { profile?: string; notes?: string } = {},
): Promise<RunInfo> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/launch`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Launch failed: ${detail}`);
  }
  return res.json();
}

export async function refreshRun(
  project: string,
  experimentId: string,
  runId: string,
): Promise<RunInfo> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/refresh`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Refresh failed: ${res.status}`);
  return res.json();
}

export async function getRunLogs(
  project: string,
  experimentId: string,
  runId: string,
  stream: "stdout" | "stderr" = "stdout",
  tail: number = 200,
): Promise<{ content: string; stream: string; tail: number }> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/logs?stream=${stream}&tail=${tail}`,
  );
  if (!res.ok) throw new Error(`Failed to get logs: ${res.status}`);
  return res.json();
}

export async function stopRun(
  project: string,
  experimentId: string,
  runId: string,
): Promise<RunInfo> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/stop`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Stop failed: ${res.status}`);
  return res.json();
}

export async function updateRunStatus(
  project: string,
  experimentId: string,
  runId: string,
  body: { status: string; exit_code?: number | null },
): Promise<RunInfo> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/status`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
  if (!res.ok) throw new Error(`Update status failed: ${res.status}`);
  return res.json();
}

export interface LabItEvent {
  __labit__: boolean;
  type: string;
  step?: number;
  epoch?: number;
  data?: Record<string, unknown>;
  status?: string;
  message?: string;
  benchmark?: string;
  name?: string;
  path?: string;
  severity?: string;
  split?: string;
  [key: string]: unknown;
}

export interface EventsResponse {
  events: LabItEvent[];
  plain_lines: number;
  total_lines: number;
}

export interface MetricPoint {
  step: number;
  value: number;
}

export interface MetricsResponse {
  metrics: Record<string, MetricPoint[]>;
  steps: number[];
}

export async function getRunEvents(
  project: string,
  experimentId: string,
  runId: string,
): Promise<EventsResponse> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/events`,
  );
  if (!res.ok) throw new Error(`Failed to get events: ${res.status}`);
  return res.json();
}

export async function getRunMetrics(
  project: string,
  experimentId: string,
  runId: string,
): Promise<MetricsResponse> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/metrics`,
  );
  if (!res.ok) throw new Error(`Failed to get metrics: ${res.status}`);
  return res.json();
}

// ── Sync & Results ─────────────────────────────────────────────────

export interface SyncResult {
  logs_synced_at: string;
  results_synced_at: string;
  last_sync_status: string;
  last_sync_error: string;
  files_synced: number;
  bytes_synced: number;
  excluded_patterns: string[];
  artifact_sources: string[];
}

export async function syncRun(
  project: string,
  experimentId: string,
  runId: string,
  mode: "logs" | "results" | "all" = "logs",
): Promise<SyncResult> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/sync`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );
  if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
  return res.json();
}

export interface ResultEntry {
  path: string;
  size: number;
  modified: string;
}

export async function listResults(
  project: string,
  experimentId: string,
  runId: string,
): Promise<ResultEntry[]> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/results`,
  );
  if (!res.ok) throw new Error(`Failed to list results: ${res.status}`);
  return res.json();
}

export interface ResultPreview {
  kind: "text" | "json" | "csv" | "image" | "download";
  path: string;
  size: number;
  content: string | null;
  truncated: boolean;
  download_url: string;
}

export async function getResultPreview(
  project: string,
  experimentId: string,
  runId: string,
  filePath: string,
): Promise<ResultPreview> {
  const res = await fetch(
    `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(filePath)}/preview`,
  );
  if (!res.ok) throw new Error(`Failed to preview: ${res.status}`);
  return res.json();
}

export function getResultDownloadUrl(
  project: string,
  experimentId: string,
  runId: string,
  filePath: string,
): string {
  return `${API}/api/projects/${project}/experiments/${encodeURIComponent(experimentId)}/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(filePath)}`;
}
