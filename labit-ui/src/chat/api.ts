const API_BASE = import.meta.env.VITE_LABIT_API_BASE ?? "";

export type ChatMode = "single" | "parallel" | "round_robin";

export interface ChatAttachment {
  id: string;
  kind: string;
  filename: string;
  mime_type: string;
  path: string;
}

export interface ChatArtifact {
  id: string;
  title: string;
  filename: string;
  language: string;
  mime_type: string;
  content: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  agent: string | null;
  attachments?: ChatAttachment[];
  artifacts?: ChatArtifact[];
  created_at: string;
}

export interface ChatRecord {
  chat_id: string;
  title: string;
  project: string;
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
// CRUD
// ---------------------------------------------------------------------------

function base(project: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/chats`;
}

export async function createChat(
  project: string,
  opts: { title?: string; mode?: ChatMode; first_agent?: string } = {},
): Promise<ChatRecord> {
  const res = await fetch(base(project), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: opts.title ?? "",
      mode: opts.mode ?? "single",
      first_agent: opts.first_agent ?? "claude",
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listChats(project: string): Promise<ChatListItem[]> {
  const res = await fetch(base(project));
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getChat(project: string, chatId: string): Promise<ChatRecord> {
  const res = await fetch(`${base(project)}/${encodeURIComponent(chatId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateChat(
  project: string,
  chatId: string,
  updates: { mode?: ChatMode; first_agent?: string; title?: string },
): Promise<ChatRecord> {
  const res = await fetch(`${base(project)}/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteChat(project: string, chatId: string): Promise<void> {
  const res = await fetch(`${base(project)}/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
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
    `${base(project)}/${encodeURIComponent(chatId)}/attachments`,
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
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/chats/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(attId)}`;
}

export function artifactDownloadUrl(
  project: string,
  chatId: string,
  artifactId: string,
): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/chats/${encodeURIComponent(chatId)}/artifacts/${encodeURIComponent(artifactId)}/download`;
}

// ---------------------------------------------------------------------------
// SSE streaming
// ---------------------------------------------------------------------------

function consumeSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): void {
  const decoder = new TextDecoder();
  let buffer = "";

  function pump(): void {
    reader
      .read()
      .then(({ done, value }) => {
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
      })
      .catch((err) => {
        if (err.name !== "AbortError") onEvent({ type: "error", error: String(err) });
        onDone();
      });
  }
  pump();
}

export function askStream(
  project: string,
  chatId: string,
  content: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  attachmentIds?: string[],
): AbortController {
  const controller = new AbortController();
  const url = `${base(project)}/${encodeURIComponent(chatId)}/ask`;

  const payload: Record<string, unknown> = { content };
  if (attachmentIds && attachmentIds.length > 0) {
    payload.attachment_ids = attachmentIds;
  }

  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) { onEvent({ type: "error", error: await res.text() }); onDone(); return; }
      const reader = res.body?.getReader();
      if (!reader) { onDone(); return; }
      consumeSSE(reader, onEvent, onDone);
    })
    .catch((err) => {
      if (err.name !== "AbortError") onEvent({ type: "error", error: String(err) });
      onDone();
    });

  return controller;
}

export async function getActiveTask(project: string, chatId: string): Promise<{ active: boolean; event_count?: number }> {
  const res = await fetch(`${base(project)}/${encodeURIComponent(chatId)}/active-task`);
  if (!res.ok) return { active: false };
  return res.json();
}

export function reconnectStream(
  project: string,
  chatId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();
  const url = `${base(project)}/${encodeURIComponent(chatId)}/active-task/stream`;

  fetch(url, { signal: controller.signal })
    .then(async (res) => {
      if (!res.ok) { onDone(); return; }
      const reader = res.body?.getReader();
      if (!reader) { onDone(); return; }
      consumeSSE(reader, onEvent, onDone);
    })
    .catch((err) => {
      if (err.name !== "AbortError") onEvent({ type: "error", error: String(err) });
      onDone();
    });

  return controller;
}

export async function stopTask(project: string, chatId: string): Promise<void> {
  await fetch(`${base(project)}/${encodeURIComponent(chatId)}/stop`, { method: "POST" });
}
