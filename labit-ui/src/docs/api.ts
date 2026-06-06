const API_BASE = import.meta.env.VITE_LABIT_API_BASE ?? "";

// ---------------------------------------------------------------------------
// Document types
// ---------------------------------------------------------------------------

export interface DocRecord {
  id: string;
  title: string;
  filename: string;
  path: string;
  format: string;
  editable: boolean;
  size_bytes: number;
  modified_at: string;
  tags: string[];
}

export interface DocContent {
  content: string;
}

// ---------------------------------------------------------------------------
// Chat types
// ---------------------------------------------------------------------------

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

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  agent: string | null;
  artifacts: ChatArtifact[];
  created_at: string;
}

export interface ChatRecord {
  chat_id: string;
  title: string;
  doc_id: string;
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
// Document CRUD
// ---------------------------------------------------------------------------

function docsBase(project: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/docs`;
}

export async function listDocs(project: string): Promise<DocRecord[]> {
  const res = await fetch(docsBase(project));
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDoc(project: string, docId: string): Promise<DocRecord> {
  const res = await fetch(`${docsBase(project)}/${encodeURIComponent(docId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDocContent(project: string, docId: string): Promise<string> {
  const res = await fetch(`${docsBase(project)}/${encodeURIComponent(docId)}/content`);
  if (!res.ok) throw new Error(await res.text());
  const data: DocContent = await res.json();
  return data.content;
}

export async function saveDocContent(project: string, docId: string, content: string): Promise<DocRecord> {
  const res = await fetch(`${docsBase(project)}/${encodeURIComponent(docId)}/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createDoc(project: string, filename: string, content: string = ""): Promise<DocRecord> {
  const res = await fetch(docsBase(project), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, content }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteDoc(project: string, docId: string): Promise<void> {
  const res = await fetch(`${docsBase(project)}/${encodeURIComponent(docId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function applyArtifact(project: string, docId: string, chatId: string, artifactId: string): Promise<DocRecord> {
  const res = await fetch(`${docsBase(project)}/${encodeURIComponent(docId)}/apply-artifact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, artifact_id: artifactId }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ---------------------------------------------------------------------------
// Doc Chat CRUD
// ---------------------------------------------------------------------------

function chatBase(project: string, docId: string): string {
  return `${docsBase(project)}/${encodeURIComponent(docId)}/chats`;
}

export async function createChat(project: string, docId: string, opts: { title?: string; mode?: ChatMode; first_agent?: string } = {}): Promise<ChatRecord> {
  const res = await fetch(chatBase(project, docId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: opts.title ?? "", mode: opts.mode ?? "single", first_agent: opts.first_agent ?? "claude" }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listChats(project: string, docId: string): Promise<ChatListItem[]> {
  const res = await fetch(chatBase(project, docId));
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getChat(project: string, docId: string, chatId: string): Promise<ChatRecord> {
  const res = await fetch(`${chatBase(project, docId)}/${encodeURIComponent(chatId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateChat(project: string, docId: string, chatId: string, updates: { mode?: ChatMode; first_agent?: string; title?: string }): Promise<ChatRecord> {
  const res = await fetch(`${chatBase(project, docId)}/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteChat(project: string, docId: string, chatId: string): Promise<void> {
  const res = await fetch(`${chatBase(project, docId)}/${encodeURIComponent(chatId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
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
  project: string, docId: string, chatId: string,
  content: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();
  const url = `${chatBase(project, docId)}/${encodeURIComponent(chatId)}/ask`;
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
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

export async function getActiveTask(project: string, docId: string, chatId: string): Promise<{ active: boolean; event_count?: number }> {
  const res = await fetch(`${chatBase(project, docId)}/${encodeURIComponent(chatId)}/active-task`);
  if (!res.ok) return { active: false };
  return res.json();
}

export function reconnectStream(
  project: string, docId: string, chatId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();
  fetch(`${chatBase(project, docId)}/${encodeURIComponent(chatId)}/active-task/stream`, { signal: controller.signal })
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

export async function stopTask(project: string, docId: string, chatId: string): Promise<void> {
  await fetch(`${chatBase(project, docId)}/${encodeURIComponent(chatId)}/stop`, { method: "POST" });
}

export function artifactDownloadUrl(project: string, docId: string, chatId: string, artifactId: string): string {
  return `${chatBase(project, docId)}/${encodeURIComponent(chatId)}/artifacts/${encodeURIComponent(artifactId)}/download`;
}
