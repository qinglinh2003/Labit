const API_BASE = import.meta.env.VITE_LABIT_API_BASE ?? "";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CodeTreeEntry {
  name: string;
  path: string;
  is_dir: boolean;
  children?: CodeTreeEntry[];
}

export interface CodeFileRecord {
  name: string;
  path: string;
  is_dir: boolean;
  size_bytes: number;
  language: string;
}

export type ChatMode = "single" | "parallel" | "round_robin";

export interface ChatArtifact {
  id: string;
  title: string;
  filename: string;
  language: string;
  mime_type: string;
  content: string;
  file_path: string | null;
}

export interface ChatAttachment {
  id: string;
  kind: string;
  filename: string;
  mime_type: string;
  path: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  agent: string | null;
  attachments?: ChatAttachment[];
  artifacts: ChatArtifact[];
  created_at: string;
}

export interface ChatRecord {
  chat_id: string;
  title: string;
  file_path: string | null;
  mode: ChatMode;
  first_agent: string;
  participants: string[];
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface ChatListItem {
  chat_id: string;
  title: string;
  mode: ChatMode;
  first_agent: string;
  updated_at: string;
  message_count: number;
}

export interface SSEEvent {
  type: "start" | "text" | "status" | "done" | "error" | "end";
  agent?: string;
  text?: string;
  status?: string;
  error?: string;
  full_text?: string;
}

// ---------------------------------------------------------------------------
// File browsing
// ---------------------------------------------------------------------------

function codeBase(project: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/code`;
}

export async function getTree(project: string, path: string = ""): Promise<CodeTreeEntry[]> {
  const url = `${codeBase(project)}/tree${path ? `?path=${encodeURIComponent(path)}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getFile(project: string, fileId: string): Promise<CodeFileRecord> {
  const res = await fetch(`${codeBase(project)}/files/${encodeURIComponent(fileId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getFileContent(project: string, fileId: string): Promise<string> {
  const res = await fetch(`${codeBase(project)}/files/${encodeURIComponent(fileId)}/content`);
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  return data.content;
}

export async function saveFileContent(project: string, fileId: string, content: string): Promise<CodeFileRecord> {
  const res = await fetch(`${codeBase(project)}/files/${encodeURIComponent(fileId)}/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function codeFilePreviewPdfUrl(project: string, fileId: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/code/files/${encodeURIComponent(fileId)}/preview.pdf`;
}

export async function applyArtifact(project: string, fileId: string, chatId: string, artifactId: string): Promise<CodeFileRecord> {
  const res = await fetch(`${codeBase(project)}/files/${encodeURIComponent(fileId)}/apply-artifact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, artifact_id: artifactId }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ---------------------------------------------------------------------------
// Code Chat CRUD
// ---------------------------------------------------------------------------

function chatBase(project: string): string {
  return `${codeBase(project)}/chats`;
}

export async function createChat(project: string, filePath?: string, opts: { title?: string; mode?: ChatMode; first_agent?: string } = {}): Promise<ChatRecord> {
  const body: Record<string, string> = { title: opts.title ?? "", mode: opts.mode ?? "single", first_agent: opts.first_agent ?? "claude" };
  if (filePath) body.file_path = filePath;
  const res = await fetch(chatBase(project), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listChats(project: string, filePath?: string): Promise<ChatListItem[]> {
  const url = filePath ? `${chatBase(project)}?file_path=${encodeURIComponent(filePath)}` : chatBase(project);
  const res = await fetch(url);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getChat(project: string, chatId: string): Promise<ChatRecord> {
  const res = await fetch(`${chatBase(project)}/${encodeURIComponent(chatId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateChat(project: string, chatId: string, updates: { mode?: ChatMode; first_agent?: string; title?: string }): Promise<ChatRecord> {
  const res = await fetch(`${chatBase(project)}/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteChat(project: string, chatId: string): Promise<void> {
  const res = await fetch(`${chatBase(project)}/${encodeURIComponent(chatId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
}

// ---------------------------------------------------------------------------
// Attachments
// ---------------------------------------------------------------------------

export async function uploadAttachment(
  project: string,
  chatId: string,
  file: File,
): Promise<ChatAttachment> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${chatBase(project)}/${encodeURIComponent(chatId)}/attachments`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function attachmentUrl(
  project: string,
  chatId: string,
  attId: string,
): string {
  return `${chatBase(project)}/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(attId)}`;
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

function consumeSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): void {
  const decoder = new TextDecoder();
  let buffer = "";
  function pump(): void {
    reader.read().then(({ done, value }) => {
      if (done) { onDone(); return; }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      let currentEventType = "";
      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          try {
            const parsed = JSON.parse(line.slice(6)) as SSEEvent;
            parsed.type = (currentEventType || parsed.type) as SSEEvent["type"];
            onEvent(parsed);
          } catch { /* ignore */ }
          currentEventType = "";
        }
      }
      pump();
    }).catch((err) => {
      if (err.name !== "AbortError") onEvent({ type: "error", error: String(err) });
      onDone();
    });
  }
  pump();
}

export function askStream(
  project: string, chatId: string,
  content: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  attachmentIds?: string[],
): AbortController {
  const controller = new AbortController();
  const url = `${chatBase(project)}/${encodeURIComponent(chatId)}/ask`;
  const payload: Record<string, unknown> = { content };
  if (attachmentIds && attachmentIds.length > 0) {
    payload.attachment_ids = attachmentIds;
  }
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  }).then(async (res) => {
    if (!res.ok) { onEvent({ type: "error", error: await res.text() }); onDone(); return; }
    const reader = res.body?.getReader();
    if (!reader) { onDone(); return; }
    consumeSSE(reader, onEvent, onDone);
  }).catch((err) => {
    if (err.name !== "AbortError") onEvent({ type: "error", error: String(err) });
    onDone();
  });
  return controller;
}

export async function getActiveTask(project: string, chatId: string): Promise<{ active: boolean; event_count?: number }> {
  const res = await fetch(`${chatBase(project)}/${encodeURIComponent(chatId)}/active-task`);
  if (!res.ok) return { active: false };
  return res.json();
}

export function reconnectStream(
  project: string, chatId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();
  fetch(`${chatBase(project)}/${encodeURIComponent(chatId)}/active-task/stream`, { signal: controller.signal })
    .then(async (res) => {
      if (!res.ok) { onDone(); return; }
      const reader = res.body?.getReader();
      if (!reader) { onDone(); return; }
      consumeSSE(reader, onEvent, onDone);
    }).catch((err) => {
      if (err.name !== "AbortError") onEvent({ type: "error", error: String(err) });
      onDone();
    });
  return controller;
}

export async function stopTask(project: string, chatId: string): Promise<void> {
  await fetch(`${chatBase(project)}/${encodeURIComponent(chatId)}/stop`, { method: "POST" });
}

export function artifactDownloadUrl(project: string, chatId: string, artifactId: string): string {
  return `${chatBase(project)}/${encodeURIComponent(chatId)}/artifacts/${encodeURIComponent(artifactId)}/download`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function encodeFileId(relPath: string): string {
  // base64url encode
  const encoded = btoa(unescape(encodeURIComponent(relPath)));
  return encoded.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
