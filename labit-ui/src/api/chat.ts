const API_BASE = import.meta.env.VITE_LABIT_API_BASE ?? "";

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
  paper_id: string;
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

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

function chatBase(project: string, paperId: string): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/papers/${encodeURIComponent(paperId)}/chats`;
}

export async function createChat(
  project: string,
  paperId: string,
  opts: { title?: string; mode?: ChatMode; first_agent?: string } = {},
): Promise<ChatRecord> {
  const res = await fetch(chatBase(project, paperId), {
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

export async function listChats(project: string, paperId: string): Promise<ChatListItem[]> {
  const res = await fetch(chatBase(project, paperId));
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getChat(project: string, paperId: string, chatId: string): Promise<ChatRecord> {
  const res = await fetch(`${chatBase(project, paperId)}/${encodeURIComponent(chatId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateChat(
  project: string,
  paperId: string,
  chatId: string,
  updates: { mode?: ChatMode; first_agent?: string; title?: string },
): Promise<ChatRecord> {
  const res = await fetch(`${chatBase(project, paperId)}/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteChat(project: string, paperId: string, chatId: string): Promise<void> {
  const res = await fetch(`${chatBase(project, paperId)}/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
}

// ---------------------------------------------------------------------------
// SSE streaming ask
// ---------------------------------------------------------------------------

export interface SSEEvent {
  type: "start" | "text" | "status" | "done" | "error" | "end";
  agent?: string;
  text?: string;
  status?: string;
  error?: string;
  full_text?: string;
}

/** Parse SSE from a ReadableStream, calling onEvent for each parsed event. */
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
        if (done) {
          onDone();
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        let currentEventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const data = line.slice(6);
            try {
              const parsed = JSON.parse(data) as SSEEvent;
              parsed.type = (currentEventType || parsed.type) as SSEEvent["type"];
              onEvent(parsed);
            } catch {
              // ignore parse errors
            }
            currentEventType = "";
          }
          // Ignore comment lines (keepalive)
        }
        pump();
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          onEvent({ type: "error", error: String(err) });
        }
        onDone();
      });
  }
  pump();
}

export function askStream(
  project: string,
  paperId: string,
  chatId: string,
  content: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();
  const url = `${chatBase(project, paperId)}/${encodeURIComponent(chatId)}/ask`;

  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        onEvent({ type: "error", error: await res.text() });
        onDone();
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        onDone();
        return;
      }
      consumeSSE(reader, onEvent, onDone);
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        onEvent({ type: "error", error: String(err) });
      }
      onDone();
    });

  return controller;
}

// ---------------------------------------------------------------------------
// Background task reconnect
// ---------------------------------------------------------------------------

export interface ActiveTaskResponse {
  active: boolean;
  event_count?: number;
}

export async function getActiveTask(
  project: string,
  paperId: string,
  chatId: string,
): Promise<ActiveTaskResponse> {
  const res = await fetch(
    `${chatBase(project, paperId)}/${encodeURIComponent(chatId)}/active-task`,
  );
  if (!res.ok) return { active: false };
  return res.json();
}

/** Reconnect to an in-progress background task's SSE stream. */
export function reconnectStream(
  project: string,
  paperId: string,
  chatId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();
  const url = `${chatBase(project, paperId)}/${encodeURIComponent(chatId)}/active-task/stream`;

  fetch(url, { signal: controller.signal })
    .then(async (res) => {
      if (!res.ok) {
        onDone();
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        onDone();
        return;
      }
      consumeSSE(reader, onEvent, onDone);
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        onEvent({ type: "error", error: String(err) });
      }
      onDone();
    });

  return controller;
}

/** Explicitly stop a running background task. */
export async function stopTask(
  project: string,
  paperId: string,
  chatId: string,
): Promise<void> {
  await fetch(
    `${chatBase(project, paperId)}/${encodeURIComponent(chatId)}/stop`,
    { method: "POST" },
  );
}

/** Get the download URL for a chat artifact. */
export function artifactDownloadUrl(
  project: string,
  paperId: string,
  chatId: string,
  artifactId: string,
): string {
  return `${chatBase(project, paperId)}/${encodeURIComponent(chatId)}/artifacts/${encodeURIComponent(artifactId)}/download`;
}
