import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, ClipboardCopy, Download, FilePlus2, FileText, ImagePlus, MessageSquarePlus, Plus, Send, Square, Trash2, User, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import {
  artifactDownloadUrl,
  askStream,
  attachmentUrl,
  createChat,
  deleteChat,
  getActiveTask,
  getChat,
  listChats,
  reconnectStream,
  stopTask,
  updateChat,
  uploadAttachment,
  type ChatArtifact,
  type ChatAttachment,
  type ChatListItem,
  type ChatMessage,
  type ChatMode,
  type ChatRecord,
  type SSEEvent,
} from "./api";
import { createDoc } from "../docs/api";
import { ClaudeIcon, CodexIcon } from "../components/AgentIcons";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StreamingAgent {
  agent: string;
  status: string;
  chunks: string[];
  hasText: boolean;
}

const AGENT_COLORS: Record<string, { accent: string; bg: string; icon: string }> = {
  claude: { accent: "border-orange-200", bg: "bg-orange-50", icon: "text-orange-500" },
  codex: { accent: "border-emerald-200", bg: "bg-emerald-50", icon: "text-emerald-500" },
};

function agentStyle(agent: string) {
  return AGENT_COLORS[agent] ?? { accent: "border-slate-200", bg: "bg-slate-50", icon: "text-slate-500" };
}

// ---------------------------------------------------------------------------
// Mode selector + Swap bar
// ---------------------------------------------------------------------------

const MODES: { value: ChatMode; label: string; desc: string }[] = [
  { value: "single", label: "single", desc: "Single agent" },
  { value: "parallel", label: "parallel", desc: "Parallel agents" },
  { value: "round_robin", label: "round robin", desc: "Round Robin" },
];

function ModeSwapBar({
  mode,
  firstAgent,
  onModeChange,
  onSwap,
  disabled,
}: {
  mode: ChatMode;
  firstAgent: string;
  onModeChange: (mode: ChatMode) => void;
  onSwap: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-1.5 px-3 py-2 border-b border-slate-200 bg-white">
      <div className="flex shrink-0 rounded-lg border border-slate-200 text-xs overflow-hidden">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            disabled={disabled}
            title={m.desc}
            aria-label={m.desc}
            className={`h-8 px-2.5 font-medium whitespace-nowrap transition-colors ${
              mode === m.value
                ? "bg-slate-800 text-white"
                : "bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700"
            } disabled:opacity-40`}
            onClick={() => onModeChange(m.value)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        disabled={disabled}
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 text-xs font-medium hover:bg-slate-50 disabled:opacity-40 transition-colors"
        onClick={onSwap}
        title={`First agent: ${firstAgent === "claude" ? "Claude" : "Codex"}. Click to switch.`}
        aria-label={`First agent: ${firstAgent === "claude" ? "Claude" : "Codex"}. Click to switch.`}
      >
        {firstAgent === "claude" ? (
          <ClaudeIcon size={13} className="text-orange-500" />
        ) : (
          <CodexIcon size={13} className="text-emerald-500" />
        )}
        <span className="hidden min-[520px]:inline">{firstAgent === "claude" ? "Claude" : "Codex"}</span>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent avatar
// ---------------------------------------------------------------------------

function AgentAvatar({ agent, pulse }: { agent: string; pulse?: boolean }) {
  const style = agentStyle(agent);
  const cls = `flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border ${style.accent} ${style.bg} ${pulse ? "animate-pulse" : ""}`;
  if (agent === "claude") return <div className={cls}><ClaudeIcon size={15} className={style.icon} /></div>;
  if (agent === "codex") return <div className={cls}><CodexIcon size={15} className={style.icon} /></div>;
  return <div className={cls}><span className={`text-xs font-bold ${style.icon}`}>{agent[0]?.toUpperCase()}</span></div>;
}

// ---------------------------------------------------------------------------
// Markdown renderer
// ---------------------------------------------------------------------------

function Markdown({ children }: { children: string }) {
  return (
    <div className="prose prose-sm prose-slate max-w-none break-words
      prose-p:my-1.5 prose-p:leading-relaxed
      prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:font-semibold
      prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5
      prose-pre:my-2 prose-pre:rounded-lg prose-pre:bg-slate-800 prose-pre:text-slate-100
      prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:text-slate-800 prose-code:before:content-none prose-code:after:content-none
      prose-table:text-sm prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1
      prose-blockquote:border-slate-300 prose-blockquote:text-slate-600
      prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline
      prose-img:rounded-lg"
    >
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{children}</ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message bubbles
// ---------------------------------------------------------------------------

function MessageImages({ message, project, chatId }: { message: ChatMessage; project: string; chatId: string }) {
  const atts = message.attachments?.filter((a) => a.kind === "image") ?? [];
  if (atts.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 mt-1.5">
      {atts.map((att) => (
        <a
          key={att.id}
          href={attachmentUrl(project, chatId, att.id)}
          target="_blank"
          rel="noopener noreferrer"
          className="block"
        >
          <img
            src={attachmentUrl(project, chatId, att.id)}
            alt={att.filename}
            className="rounded-lg max-h-48 max-w-xs border border-slate-200 hover:border-slate-400 transition-colors cursor-pointer"
          />
        </a>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Artifact card
// ---------------------------------------------------------------------------

function ArtifactCard({ artifact, project, chatId }: { artifact: ChatArtifact; project: string; chatId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleCopy = () => {
    void navigator.clipboard.writeText(artifact.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleSaveAsDoc = async () => {
    const filename = prompt("Save as document:", artifact.filename);
    if (!filename) return;
    setSaving(true);
    try {
      await createDoc(project, filename, artifact.content);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      alert(`Failed to save: ${msg}`);
    } finally {
      setSaving(false);
    }
  };

  const downloadUrl = artifactDownloadUrl(project, chatId, artifact.id);

  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-white border-b border-slate-100">
        <FileText size={15} className="text-slate-400 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-slate-700 truncate">{artifact.title}</div>
          <div className="text-[11px] text-slate-400 truncate" title={artifact.filename}>{artifact.filename} · {artifact.language}</div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
            onClick={handleCopy}
            title={copied ? "Copied!" : "Copy content"}
          >
            <ClipboardCopy size={13} />
          </button>
          <button
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-emerald-600 transition-colors"
            onClick={handleSaveAsDoc}
            disabled={saving}
            title={saved ? "Saved to docs!" : "Save as Doc"}
          >
            {saved ? <Check size={13} className="text-emerald-500" /> : <FilePlus2 size={13} />}
          </button>
          <a
            href={downloadUrl}
            download={artifact.filename}
            className="flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
            title="Download"
          >
            <Download size={13} />
          </a>
          <button
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
            onClick={() => setExpanded(!expanded)}
            title={expanded ? "Collapse" : "Preview"}
          >
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
        </div>
      </div>
      {/* Preview */}
      {expanded && (
        <div className="max-h-96 overflow-auto">
          {artifact.language === "markdown" ? (
            <div className="px-4 py-3">
              <Markdown>{artifact.content}</Markdown>
            </div>
          ) : (
            <pre className="px-4 py-3 text-xs text-slate-700 font-mono whitespace-pre-wrap">{artifact.content}</pre>
          )}
        </div>
      )}
      {/* Collapsed hint */}
      {!expanded && (
        <div className="px-4 py-1.5 text-[11px] text-slate-400">
          Click preview to expand · {artifact.content.split("\n").length} lines
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message, project, chatId }: { message: ChatMessage; project: string; chatId: string }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex items-start gap-3 max-w-[70%]">
          <div className="rounded-2xl rounded-tr-sm bg-slate-800 px-4 py-3 text-sm text-white">
            <MessageImages message={message} project={project} chatId={chatId} />
            <div className="whitespace-pre-wrap leading-relaxed">{message.content}</div>
          </div>
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-slate-200">
            <User size={14} className="text-slate-600" />
          </div>
        </div>
      </div>
    );
  }

  const agent = message.agent ?? "assistant";
  const artifacts = message.artifacts ?? [];
  return (
    <div className="flex items-start gap-3 max-w-[70%]">
      <AgentAvatar agent={agent} />
      <div className="min-w-0">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{agent}</div>
        <Markdown>{message.content}</Markdown>
        {artifacts.map((art) => (
          <ArtifactCard key={art.id} artifact={art} project={project} chatId={chatId} />
        ))}
      </div>
    </div>
  );
}

function StreamingBubble({ agent: sa }: { agent: StreamingAgent }) {
  const text = sa.chunks.join("");
  return (
    <div className="flex items-start gap-3 max-w-[70%]">
      <AgentAvatar agent={sa.agent} pulse={!sa.hasText} />
      <div className="min-w-0">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{sa.agent}</span>
          {!sa.hasText && <span className="text-[11px] text-slate-400 animate-pulse">{formatStatus(sa.status)}</span>}
        </div>
        {sa.hasText ? (
          <div>
            <Markdown>{text}</Markdown>
            <span className="inline-block w-1.5 h-4 ml-0.5 -mb-0.5 bg-slate-400 animate-blink rounded-sm" />
          </div>
        ) : (
          <div className="flex items-center gap-2 py-1">
            <div className="flex items-center gap-1">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
            <span className="text-xs text-slate-400">{formatStatus(sa.status)}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function formatStatus(status: string): string {
  if (!status) return "Thinking...";
  const s = status.toLowerCase();
  if (s === "thinking" || s === "started") return "Thinking...";
  if (s === "generating") return "Writing...";
  if (s === "message start") return "Thinking...";
  if (s === "content block start") return "Starting to write...";
  if (s === "finishing") return "Finishing...";
  if (s.startsWith("running ") || s.startsWith("completed ")) return status;
  return status;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hasAssistantMessage(messages: ChatMessage[], agent: string, content: string): boolean {
  return messages.some((m) => m.role === "assistant" && m.agent === agent && m.content === content);
}

function mergeMissing(server: ChatRecord, local: ChatRecord | null): ChatRecord {
  if (!local) return server;
  // Find assistant messages from the current turn on the server (after the last user message).
  const lastUserIdx = (() => { for (let i = server.messages.length - 1; i >= 0; i--) { if (server.messages[i].role === "user") return i; } return -1; })();
  const serverTurnAgents = new Set(
    server.messages.slice(lastUserIdx + 1).filter((m) => m.role === "assistant").map((m) => m.agent),
  );
  const missing: ChatMessage[] = [];
  for (const m of local.messages) {
    if (m.role === "user" && m.id.startsWith("local_") && !server.messages.some((sm) => sm.role === "user" && sm.content === m.content)) {
      missing.push(m);
    } else if (m.role === "assistant" && (m.id.startsWith("done_") || m.id.startsWith("partial_"))) {
      // If the server already has an assistant message from this agent in the current turn,
      // the server version wins (it has cleaned content with artifacts properly extracted).
      // Only preserve the local version if the server has no response from this agent yet.
      if (!serverTurnAgents.has(m.agent)) {
        missing.push(m);
      }
    }
  }
  if (missing.length === 0) return server;
  return { ...server, messages: [...server.messages, ...missing] };
}

// ---------------------------------------------------------------------------
// Chat sidebar
// ---------------------------------------------------------------------------

function ChatSidebar({
  chats,
  activeChatId,
  runningChatIds,
  onSelect,
  onCreate,
  onDelete,
}: {
  chats: ChatListItem[];
  activeChatId: string;
  runningChatIds: Set<string>;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex h-full flex-col bg-slate-50 border-r border-slate-200">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
        <span className="text-sm font-semibold text-slate-700">Chats</span>
        <button
          type="button"
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-800 text-white hover:bg-slate-700 transition-colors"
          onClick={onCreate}
          title="New chat"
        >
          <Plus size={16} />
        </button>
      </div>

      {/* Chat list */}
      <div className="flex-1 overflow-y-auto">
        {chats.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-400">
            No chats yet. Create one to get started.
          </div>
        ) : (
          chats.map((chat) => {
            const active = chat.chat_id === activeChatId;
            return (
              <button
                key={chat.chat_id}
                type="button"
                className={`group flex w-full items-center gap-2 px-4 py-3 text-left border-b border-slate-100 transition-colors ${
                  active ? "bg-white border-l-2 border-l-slate-800" : "hover:bg-white"
                }`}
                onClick={() => onSelect(chat.chat_id)}
              >
                <div className="min-w-0 flex-1">
                  <div className={`flex items-center gap-1.5 truncate text-sm ${active ? "font-semibold text-slate-800" : "font-medium text-slate-600"}`}>
                    {runningChatIds.has(chat.chat_id) && (
                      <span className="inline-block h-2 w-2 flex-shrink-0 rounded-full bg-orange-400 animate-pulse" title="Agent is generating..." />
                    )}
                    <span className="truncate">{chat.title}</span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className="capitalize">{chat.mode.replace("_", " ")}</span>
                    <span>·</span>
                    <span>{chat.message_count} msgs</span>
                    {runningChatIds.has(chat.chat_id) && (
                      <>
                        <span>·</span>
                        <span className="text-orange-500 font-medium">Running</span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  className="hidden h-6 w-6 flex-shrink-0 items-center justify-center rounded hover:bg-slate-200 group-hover:flex"
                  onClick={(e) => { e.stopPropagation(); onDelete(chat.chat_id); }}
                  title="Delete chat"
                >
                  <Trash2 size={13} className="text-slate-400" />
                </button>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Welcome screen (no chat selected)
// ---------------------------------------------------------------------------

function WelcomeScreen({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 text-center">
      <div className="flex items-center gap-4">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-orange-50 border border-orange-200">
          <ClaudeIcon size={28} className="text-orange-500" />
        </div>
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 border border-emerald-200">
          <CodexIcon size={28} className="text-emerald-500" />
        </div>
      </div>
      <div>
        <h2 className="text-xl font-semibold text-slate-800">Labit Chat</h2>
        <p className="mt-1.5 text-sm text-slate-500 max-w-sm">
          Chat with Claude and Codex. Use single, parallel, or round-robin mode.
        </p>
      </div>
      <button
        type="button"
        className="flex items-center gap-2 rounded-xl bg-slate-800 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-700 transition-colors"
        onClick={onCreate}
      >
        <MessageSquarePlus size={16} />
        New Chat
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ChatPage
// ---------------------------------------------------------------------------

export default function ChatPage({ project, activeChatId, onActiveChatIdChange }: { project: string; activeChatId: string; onActiveChatIdChange: (id: string) => void }) {
  const [chats, setChats] = useState<ChatListItem[]>([]);
  const setActiveChatId = onActiveChatIdChange;
  const [chat, setChat] = useState<ChatRecord | null>(null);
  const [input, setInput] = useState("");
  const [defaultMode, setDefaultMode] = useState<ChatMode>("single");
  const [defaultFirstAgent, setDefaultFirstAgent] = useState("claude");
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingAgents, setStreamingAgents] = useState<StreamingAgent[]>([]);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [runningChatIds, setRunningChatIds] = useState<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);
  const composingRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load chat list
  const refreshList = useCallback(() => {
    if (!project) return;
    listChats(project).then(setChats).catch(() => {});
  }, [project]);

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  // Poll for active tasks across all chats (to show running indicators)
  useEffect(() => {
    if (!project || chats.length === 0) return;
    let cancelled = false;
    const checkRunning = async () => {
      const running = new Set<string>();
      await Promise.all(
        chats.map(async (c) => {
          try {
            const status = await getActiveTask(project, c.chat_id);
            if (status.active) running.add(c.chat_id);
          } catch { /* ignore */ }
        }),
      );
      if (!cancelled) setRunningChatIds(running);
    };
    void checkRunning();
    const interval = setInterval(checkRunning, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [project, chats]);

  // Load active chat
  useEffect(() => {
    if (!project || !activeChatId) { setChat(null); return; }
    let cancelled = false;
    getChat(project, activeChatId).then((c) => {
      if (cancelled) return;
      setChat(c);
      // Check for active background task
      getActiveTask(project, activeChatId).then((status) => {
        if (cancelled || !status.active) return;
        setStreaming(true);
        const { onEvent, onDone } = buildStreamHandlers();
        abortRef.current = reconnectStream(project, activeChatId, onEvent, onDone);
      });
    });
    return () => { cancelled = true; abortRef.current?.abort(); };
  }, [project, activeChatId]);

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat?.messages.length, streamingAgents]);

  // Stream handlers
  const buildStreamHandlers = useCallback(() => {
    const activeStreams: Record<string, string[]> = {};

    const onEvent = (event: SSEEvent) => {
      if (event.type === "start" && event.agent) {
        activeStreams[event.agent] = [];
        setStreamingAgents((prev) => [
          ...prev.filter((a) => a.agent !== event.agent),
          { agent: event.agent!, status: "thinking", chunks: [], hasText: false },
        ]);
      } else if (event.type === "status" && event.agent) {
        setStreamingAgents((prev) => prev.map((a) => a.agent === event.agent ? { ...a, status: event.status ?? a.status } : a));
      } else if (event.type === "text" && event.agent && event.text) {
        if (activeStreams[event.agent]) activeStreams[event.agent].push(event.text);
        setStreamingAgents((prev) => prev.map((a) =>
          a.agent === event.agent ? { ...a, chunks: [...(activeStreams[event.agent] || [])], hasText: true, status: "generating" } : a,
        ));
      } else if ((event.type === "done" || event.type === "error") && event.agent) {
        let finalText = event.full_text ?? (activeStreams[event.agent] || []).join("");
        if (!finalText.trim() && event.type === "error" && event.error) {
          finalText = `${event.agent} error: ${event.error}`;
        }
        if (finalText.trim()) {
          const msg: ChatMessage = {
            id: `done_${event.agent}_${Date.now()}`,
            role: "assistant",
            content: finalText,
            agent: event.agent,
            artifacts: [],
            created_at: new Date().toISOString(),
          };
          setChat((prev) => {
            if (!prev || hasAssistantMessage(prev.messages, event.agent!, finalText)) return prev;
            return { ...prev, messages: [...prev.messages, msg] };
          });
          delete activeStreams[event.agent];
        }
        setStreamingAgents((prev) => prev.filter((a) => a.agent !== event.agent));
      }
    };

    const onDone = () => {
      // Save partial messages
      const partials = Object.entries(activeStreams)
        .map(([agent, chunks]) => ({ agent, content: chunks.join("") }))
        .filter((e) => e.content.trim());
      if (partials.length > 0) {
        setChat((prev) => {
          if (!prev) return prev;
          const additions = partials
            .filter((e) => !hasAssistantMessage(prev.messages, e.agent, e.content))
            .map((e) => ({ id: `partial_${e.agent}_${Date.now()}`, role: "assistant" as const, content: e.content, agent: e.agent, created_at: new Date().toISOString() }));
          return additions.length ? { ...prev, messages: [...prev.messages, ...additions] } : prev;
        });
      }
      setStreaming(false);
      setStreamingAgents([]);
      setRunningChatIds((prev) => { const next = new Set(prev); next.delete(activeChatId); return next; });
      // Re-fetch to get server state
      if (project && activeChatId) {
        const chatIdCapture = activeChatId;
        getChat(project, chatIdCapture).then((server) => {
          const merged = mergeMissing(server, null);
          setChat((local) => {
            const result = mergeMissing(server, local);
            if (result.messages.length > merged.messages.length) {
              setTimeout(() => {
                getChat(project, chatIdCapture).then((retry) => setChat((prev) => mergeMissing(retry, prev)));
              }, 2000);
            }
            return result;
          });
        });
      }
      refreshList();
    };

    return { onEvent, onDone };
  }, [project, activeChatId, refreshList]);

  // Create chat
  const handleCreate = useCallback(async () => {
    if (!project) return;
    setError("");
    try {
      const c = await createChat(project, { mode: defaultMode, first_agent: defaultFirstAgent });
      setChat(c);
      setActiveChatId(c.chat_id);
      refreshList();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [project, defaultMode, defaultFirstAgent, refreshList]);

  // Delete chat
  const handleDelete = useCallback(async (id: string) => {
    if (!project) return;
    await deleteChat(project, id);
    if (activeChatId === id) { setActiveChatId(""); setChat(null); }
    refreshList();
  }, [project, activeChatId, refreshList]);

  // Upload images — ensures chat exists, returns updated chat + attachment
  const handleUploadFiles = useCallback(async (files: File[]) => {
    if (!project || files.length === 0) return;
    setUploading(true);
    setError("");

    let targetChat = chat;
    let targetChatId = activeChatId;

    // Auto-create chat if needed
    if (!targetChat) {
      try {
        targetChat = await createChat(project, { mode: defaultMode, first_agent: defaultFirstAgent });
        targetChatId = targetChat.chat_id;
        setChat(targetChat);
        setActiveChatId(targetChat.chat_id);
        refreshList();
      } catch (err) {
        setUploading(false);
        setError(err instanceof Error ? err.message : String(err));
        return;
      }
    }

    try {
      const newAtts: ChatAttachment[] = [];
      for (const file of files.slice(0, 4)) {
        const att = await uploadAttachment(project, targetChatId, file);
        newAtts.push(att);
      }
      setPendingAttachments((prev) => [...prev, ...newAtts].slice(0, 4));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }, [project, chat, activeChatId, defaultMode, defaultFirstAgent, refreshList]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
    if (files.length > 0) {
      e.preventDefault();
      void handleUploadFiles(files);
    }
  }, [handleUploadFiles]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/"));
    if (files.length > 0) {
      void handleUploadFiles(files);
    }
  }, [handleUploadFiles]);

  const removePendingAttachment = useCallback((id: string) => {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  // Send message
  const handleSend = useCallback(async () => {
    if ((!input.trim() && pendingAttachments.length === 0) || streaming || !project) return;
    setError("");
    const content = input.trim();
    const attIds = pendingAttachments.map((a) => a.id);
    const attSnapshots = [...pendingAttachments];
    setInput("");
    setPendingAttachments([]);
    setStreaming(true);
    setStreamingAgents([]);

    let targetChat = chat;
    let targetChatId = activeChatId;

    if (!targetChat) {
      try {
        targetChat = await createChat(project, { mode: defaultMode, first_agent: defaultFirstAgent });
        targetChatId = targetChat.chat_id;
        setChat(targetChat);
        setActiveChatId(targetChat.chat_id);
        refreshList();
      } catch (err) {
        setStreaming(false);
        setError(err instanceof Error ? err.message : String(err));
        setInput(content);
        setPendingAttachments(attSnapshots);
        return;
      }
    }

    const userMsg: ChatMessage = {
      id: `local_${Date.now()}`,
      role: "user",
      content,
      agent: null,
      attachments: attSnapshots,
      created_at: new Date().toISOString(),
    };
    setChat((prev) => prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev);

    const { onEvent, onDone } = buildStreamHandlers();
    abortRef.current = askStream(project, targetChatId, content, onEvent, onDone, attIds.length > 0 ? attIds : undefined);
  }, [input, pendingAttachments, streaming, chat, project, activeChatId, defaultMode, defaultFirstAgent, refreshList, buildStreamHandlers]);

  // Stop
  const handleStop = useCallback(() => {
    if (project && activeChatId) void stopTask(project, activeChatId);
  }, [project, activeChatId]);

  // Mode / swap
  const handleModeChange = useCallback((mode: ChatMode) => {
    if (!chat || !project) return;
    updateChat(project, activeChatId, { mode }).then((c) => { setChat(c); refreshList(); });
  }, [chat, project, activeChatId, refreshList]);

  const handleSwap = useCallback(() => {
    if (!chat || !project) return;
    const next = chat.first_agent === "claude" ? "codex" : "claude";
    updateChat(project, activeChatId, { first_agent: next }).then((c) => { setChat(c); refreshList(); });
  }, [chat, project, activeChatId, refreshList]);

  const handleDefaultSwap = useCallback(() => {
    setDefaultFirstAgent((current) => current === "claude" ? "codex" : "claude");
  }, []);

  const renderComposer = () => (
    <div
      className="border-t border-slate-200 bg-white"
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
      onDrop={handleDrop}
    >
      <div className="mx-auto max-w-3xl px-6 py-4">
        {error && (
          <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}
        {/* Pending attachment previews */}
        {pendingAttachments.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {pendingAttachments.map((att) => (
              <div key={att.id} className="relative group">
                <img
                  src={attachmentUrl(project, activeChatId || chat?.chat_id || "", att.id)}
                  alt={att.filename}
                  className="h-16 w-16 rounded-lg object-cover border border-slate-200"
                />
                <button
                  type="button"
                  className="absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-slate-700 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                  onClick={() => removePendingAttachment(att.id)}
                >
                  <X size={10} />
                </button>
              </div>
            ))}
            {uploading && (
              <div className="flex h-16 w-16 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
              </div>
            )}
          </div>
        )}
        <div className="flex gap-3">
          {/* Upload button */}
          <button
            type="button"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl border border-slate-300 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 disabled:opacity-40 transition-colors"
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming || uploading}
            title="Attach image"
          >
            <ImagePlus size={16} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length > 0) void handleUploadFiles(files);
              e.target.value = "";
            }}
          />
          <textarea
            rows={1}
            className="flex-1 rounded-xl border border-slate-300 px-4 py-3 text-sm focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400/30 resize-none overflow-hidden"
            placeholder="Send a message... (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
            }}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            onPaste={handlePaste}
            onKeyDown={(e) => {
              const ne = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
              const isComposing = composingRef.current || ne.isComposing || ne.keyCode === 229;
              if (e.key === "Enter" && !e.shiftKey && !isComposing) {
                e.preventDefault();
                void handleSend();
              }
            }}
            disabled={streaming}
          />
          {streaming ? (
            <button
              type="button"
              className="flex h-11 w-11 items-center justify-center rounded-xl bg-red-500 text-white hover:bg-red-600 transition-colors"
              onClick={handleStop}
              title="Stop generating"
            >
              <Square size={16} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              className="flex h-11 w-11 items-center justify-center rounded-xl bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-40 transition-colors"
              onClick={() => void handleSend()}
              disabled={!input.trim() && pendingAttachments.length === 0}
            >
              <Send size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex flex-1 min-h-0">
      {/* Sidebar */}
      <div className="w-[280px] flex-shrink-0">
        <ChatSidebar
          chats={chats}
          activeChatId={activeChatId}
          runningChatIds={runningChatIds}
          onSelect={setActiveChatId}
          onCreate={handleCreate}
          onDelete={handleDelete}
        />
      </div>

      {/* Main area */}
      <div className="flex flex-1 flex-col min-w-0 bg-white">
        {chat ? (
          <>
            <ModeSwapBar
              mode={chat.mode}
              firstAgent={chat.first_agent}
              onModeChange={handleModeChange}
              onSwap={handleSwap}
              disabled={streaming}
            />

            {/* Messages */}
            <div className="flex-1 overflow-y-auto">
              <div className="mx-auto max-w-3xl px-6 py-6 space-y-5">
                {chat.messages.length === 0 && streamingAgents.length === 0 && (
                  <div className="flex h-[50vh] items-center justify-center">
                    <p className="text-sm text-slate-400">Send a message to start the conversation</p>
                  </div>
                )}
                {chat.messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} project={project} chatId={chat.chat_id} />
                ))}
                {streamingAgents.map((sa) => (
                  <StreamingBubble key={`stream-${sa.agent}`} agent={sa} />
                ))}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {renderComposer()}
          </>
        ) : (
          <>
            <ModeSwapBar
              mode={defaultMode}
              firstAgent={defaultFirstAgent}
              onModeChange={setDefaultMode}
              onSwap={handleDefaultSwap}
              disabled={streaming}
            />
            <div className="flex-1 min-h-0">
              <WelcomeScreen onCreate={handleCreate} />
            </div>
            {renderComposer()}
          </>
        )}
      </div>
    </div>
  );
}
