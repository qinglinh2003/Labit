const API = import.meta.env.VITE_API_URL ?? "";

export interface ComputeProfile {
  name: string;
  user: string;
  host: string;
  port: number;
  identity_file: string | null;
  workdir: string;
  notes: string;
  ssh_display: string;
}

export interface SaveProfileRequest {
  user: string;
  host: string;
  port?: number;
  identity_file?: string | null;
  workdir?: string;
  notes?: string;
}

export interface TestResult {
  success: boolean;
  message: string;
}

export async function listComputeProfiles(project: string): Promise<ComputeProfile[]> {
  const res = await fetch(`${API}/api/projects/${project}/compute`);
  if (!res.ok) throw new Error(`Failed to list compute profiles: ${res.status}`);
  return res.json();
}

export async function getComputeProfile(project: string, name: string): Promise<ComputeProfile> {
  const res = await fetch(`${API}/api/projects/${project}/compute/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Failed to get compute profile: ${res.status}`);
  return res.json();
}

export async function saveComputeProfile(project: string, name: string, body: SaveProfileRequest): Promise<ComputeProfile> {
  const res = await fetch(`${API}/api/projects/${project}/compute/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Failed to save compute profile: ${res.status}`);
  return res.json();
}

export async function deleteComputeProfile(project: string, name: string): Promise<void> {
  const res = await fetch(`${API}/api/projects/${project}/compute/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete compute profile: ${res.status}`);
}

export async function testComputeProfile(project: string, name: string): Promise<TestResult> {
  const res = await fetch(`${API}/api/projects/${project}/compute/${encodeURIComponent(name)}/test`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to test compute profile: ${res.status}`);
  return res.json();
}

export interface SyncCodeResponse {
  success: boolean;
  profile_name: string;
  local_path: string;
  remote_path: string;
  stdout: string;
  stderr: string;
}

export interface GpuCheckResponse {
  success: boolean;
  output: string;
}

export async function syncCode(project: string, name: string): Promise<SyncCodeResponse> {
  const res = await fetch(`${API}/api/projects/${project}/compute/${encodeURIComponent(name)}/sync-code`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to sync code: ${res.status}`);
  return res.json();
}

export async function checkGpu(project: string, name: string): Promise<GpuCheckResponse> {
  const res = await fetch(`${API}/api/projects/${project}/compute/${encodeURIComponent(name)}/check-gpu`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to check GPU: ${res.status}`);
  return res.json();
}
