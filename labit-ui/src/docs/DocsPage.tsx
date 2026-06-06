import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardCopy,
  Download,
  Edit3,
  Eye,
  FileText,
  FolderOpen,
  MessageSquare,
  Plus,
  Save,
  Search,
  Send,
  Square,
  Trash2,
  User,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import {
  applyArtifact,
  artifactDownloadUrl,
  askStream,
  createChat,
  createDoc,
  deleteChat,
  deleteDoc,
  getActiveTask,
  getChat,
  getDocContent,
  listChats,
  listDocs,
  reconnectStream,
  saveDocContent,
  stopTask,
  updateChat,
  type ChatArtifact,
  type ChatListItem,
  type ChatMessage,
  type ChatMode,
  type ChatRecord,
  type DocRecord,
  type SSEEvent,
} from "./api";
import { ClaudeIcon, CodexIcon } from "../components/AgentIcons";
import ModeSwapBar from "../components/ModeSwapBar";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StreamingAgent {
  agent: string;
  status: string;
  chunks: string[];
  hasText: boolean;
}

const AGENT_COLORS: Record<string, { accent: string; icon: string }> = {
  claude: { accent: "border-orange-200", icon: "text-orange-500" },
  codex: { accent: "border-emerald-200", icon: "text-emerald-500" },
};

function agentStyle(agent: string) {
  return AGENT_COLORS[agent] ?? { accent: "border-slate-200", icon: "text-slate-500" };
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
// Doc list (left sidebar)
// ---------------------------------------------------------------------------

function DocList({
  docs,
  selectedDocId,
  onSelect,
  onCreate,
  onDelete,
}: {
  docs: DocRecord[];
  selectedDocId: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  const [search, setSearch] = useState("");

  // Group by directory
  const filtered = docs.filter((d) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return d.title.toLowerCase().includes(q) || d.filename.toLowerCase().includes(q) || d.path.toLowerCase().includes(q);
  });

  const groups = new Map<string, DocRecord[]>();
  for (const doc of filtered) {
    const dir = doc.path.includes("/") ? doc.path.split("/").slice(0, -1).join("/") : "";
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(doc);
  }
  const sortedDirs = [...groups.keys()].sort();

  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  return (
    <div className="flex h-full flex-col bg-slate-50 border-r border-slate-200">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-slate-500" />
          <span className="text-sm font-semibold text-slate-700">Docs</span>
          <span className="text-xs text-slate-400">{filtered.length}</span>
        </div>
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-800 text-white hover:bg-slate-700 transition-colors"
          onClick={onCreate}
          title="New document"
        >
          <Plus size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {sortedDirs.map((dir) => {
          const items = groups.get(dir)!;
          const isRoot = dir === "";
          const isCollapsed = collapsed[dir] ?? false;
          return (
            <div key={dir || "__root__"}>
              {!isRoot && (
                <button
                  type="button"
                  className="flex w-full items-center gap-1.5 px-4 py-1.5 text-[11px] font-semibold text-slate-500 uppercase tracking-wider hover:bg-slate-100"
                  onClick={() => setCollapsed((prev) => ({ ...prev, [dir]: !isCollapsed }))}
                >
                  {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  <FolderOpen size={12} />
                  {dir}
                </button>
              )}
              {!isCollapsed && items.map((doc) => {
                const active = doc.id === selectedDocId;
                return (
                  <button
                    key={doc.id}
                    type="button"
                    className={`group flex w-full items-center gap-2 px-4 py-2.5 text-left border-b border-slate-100 transition-colors ${
                      active ? "bg-white border-l-2 border-l-slate-800" : "hover:bg-white"
                    }`}
                    onClick={() => onSelect(doc.id)}
                  >
                    <div className="min-w-0 flex-1">
                      <div className={`truncate text-sm ${active ? "font-semibold text-slate-800" : "font-medium text-slate-600"}`}>
                        {doc.title}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-400">
                        <span>{doc.filename}</span>
                        <span>&middot;</span>
                        <span>{doc.format}</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="hidden h-6 w-6 flex-shrink-0 items-center justify-center rounded hover:bg-slate-200 group-hover:flex"
                      onClick={(e) => { e.stopPropagation(); onDelete(doc.id); }}
                      title="Delete"
                    >
                      <Trash2 size={12} className="text-slate-400" />
                    </button>
                  </button>
                );
              })}
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-slate-400">No documents found</div>
        )}
      </div>

      <div className="border-t border-slate-200 px-3 py-2">
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
          <Search size={13} className="text-slate-400" />
          <input
            className="flex-1 text-xs bg-transparent border-0 outline-none placeholder:text-slate-400"
            placeholder="Search docs..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Doc editor / preview (center panel)
// ---------------------------------------------------------------------------

function DocEditor({
  project,
  doc,
  onContentSaved,
}: {
  project: string;
  doc: DocRecord;
  onContentSaved: () => void;
}) {
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentRef = useRef("");
  const dirtyRef = useRef(false);

  const dirty = content !== savedContent;

  // Load content when doc changes
  useEffect(() => {
    setLoading(true);
    setMode("preview");
    getDocContent(project, doc.id).then((c) => {
      setContent(c);
      setSavedContent(c);
      contentRef.current = c;
      dirtyRef.current = false;
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [project, doc.id]);

  const doSave = useCallback(async (text: string) => {
    setSaving(true);
    try {
      await saveDocContent(project, doc.id, text);
      setSavedContent(text);
      dirtyRef.current = false;
      onContentSaved();
    } finally {
      setSaving(false);
    }
  }, [project, doc.id, onContentSaved]);

  // Auto-save after 2s of inactivity
  const handleChange = useCallback((value: string) => {
    setContent(value);
    contentRef.current = value;
    dirtyRef.current = true;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => void doSave(value), 2000);
  }, [doSave]);

  // Flush on unmount or doc change
  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      if (dirtyRef.current) {
        void saveDocContent(project, doc.id, contentRef.current);
      }
    };
  }, [project, doc.id]);

  // Reload content when external apply happens
  const reloadContent = useCallback(() => {
    getDocContent(project, doc.id).then((c) => {
      setContent(c);
      setSavedContent(c);
      contentRef.current = c;
      dirtyRef.current = false;
    });
  }, [project, doc.id]);

  // Expose reload on window for the apply button to call
  useEffect(() => {
    (window as any).__docEditorReload = reloadContent;
    return () => { delete (window as any).__docEditorReload; };
  }, [reloadContent]);

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading...</div>;
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2 bg-white">
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 overflow-hidden">
            <button
              type="button"
              className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                mode === "preview" ? "bg-slate-800 text-white" : "bg-white text-slate-500 hover:bg-slate-50"
              }`}
              onClick={() => setMode("preview")}
            >
              <Eye size={12} />
              Preview
            </button>
            {doc.editable && (
              <button
                type="button"
                className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  mode === "edit" ? "bg-slate-800 text-white" : "bg-white text-slate-500 hover:bg-slate-50"
                }`}
                onClick={() => setMode("edit")}
              >
                <Edit3 size={12} />
                Edit
              </button>
            )}
          </div>
          <span className="text-xs text-slate-400 truncate max-w-[200px]">{doc.path}</span>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <span className="text-[10px] text-amber-500 font-medium">unsaved</span>}
          {saving && <span className="text-[10px] text-slate-400">saving...</span>}
          {!dirty && !saving && content && <span className="text-[10px] text-green-500">saved</span>}
          {dirty && (
            <button
              type="button"
              className="flex items-center gap-1 rounded-lg bg-slate-800 text-white px-2.5 py-1 text-xs font-medium hover:bg-slate-700"
              onClick={() => void doSave(content)}
              disabled={saving}
            >
              <Save size={11} />
              Save
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {mode === "edit" ? (
          <textarea
            className="h-full w-full resize-none border-0 bg-white p-6 text-sm leading-relaxed text-slate-800 focus:outline-none font-mono"
            value={content}
            onChange={(e) => handleChange(e.target.value)}
            placeholder="Start writing..."
            spellCheck={false}
          />
        ) : (
          <div className="h-full overflow-y-auto p-6">
            {content ? (
              doc.format === "markdown" ? (
                <div className="prose prose-slate max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                    {content}
                  </ReactMarkdown>
                </div>
              ) : (
                <pre className="text-sm text-slate-700 font-mono whitespace-pre-wrap">{content}</pre>
              )
            ) : (
              <p className="text-sm text-slate-400 italic">Empty document. Switch to Edit mode to start writing.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent avatar
// ---------------------------------------------------------------------------

function AgentAvatar({ agent, pulse }: { agent: string; pulse?: boolean }) {
  const style = agentStyle(agent);
  const cls = `flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-white border ${style.accent} ${pulse ? "animate-pulse" : ""}`;
  if (agent === "claude") return <div className={cls}><ClaudeIcon size={14} className={style.icon} /></div>;
  if (agent === "codex") return <div className={cls}><CodexIcon size={14} className={style.icon} /></div>;
  return <div className={cls}><span className={`text-xs font-bold ${style.icon}`}>{agent[0]?.toUpperCase()}</span></div>;
}

// ---------------------------------------------------------------------------
// Artifact card (with Apply to Doc)
// ---------------------------------------------------------------------------

function ArtifactCard({
  artifact,
  project,
  docId,
  chatId,
  onApplied,
}: {
  artifact: ChatArtifact;
  project: string;
  docId: string;
  chatId: string;
  onApplied: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);
  const lines = artifact.content.split("\n").length;

  const handleCopy = () => {
    navigator.clipboard.writeText(artifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleApply = async () => {
    if (!confirm("Apply this artifact to the document? This will replace the current content.")) return;
    setApplying(true);
    try {
      await applyArtifact(project, docId, chatId, artifact.id);
      setApplied(true);
      onApplied();
      // Trigger editor reload
      (window as any).__docEditorReload?.();
    } catch (err) {
      alert("Failed to apply: " + (err instanceof Error ? err.message : String(err)));
    } finally {
      setApplying(false);
    }
  };

  const downloadUrl = artifactDownloadUrl(project, docId, chatId, artifact.id);

  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button className="flex items-center gap-1.5 flex-1 min-w-0 text-left" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ChevronDown size={14} className="text-slate-400 flex-shrink-0" /> : <ChevronRight size={14} className="text-slate-400 flex-shrink-0" />}
          <span className="text-sm font-medium text-slate-700 truncate">{artifact.title}</span>
          <span className="text-[10px] text-slate-400 truncate min-w-0 max-w-[100px]" title={artifact.filename}>{artifact.filename}</span>
        </button>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={handleApply}
            disabled={applying || applied}
            className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              applied
                ? "bg-green-100 text-green-700"
                : "bg-blue-100 text-blue-700 hover:bg-blue-200"
            } disabled:opacity-50`}
            title="Apply to document"
          >
            {applied ? <Check size={11} /> : <FileText size={11} />}
            {applied ? "Applied" : "Apply"}
          </button>
          <button onClick={handleCopy} className="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-slate-600" title="Copy">
            <ClipboardCopy size={12} />
          </button>
          <a href={downloadUrl} download={artifact.filename} className="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-slate-600" title="Download">
            <Download size={12} />
          </a>
        </div>
      </div>
      {!expanded && <div className="px-3 pb-2 text-[10px] text-slate-400">{lines} lines</div>}
      {expanded && (
        <div className="border-t border-slate-200 max-h-64 overflow-y-auto">
          {artifact.language === "markdown" ? (
            <div className="px-3 py-2"><Markdown>{artifact.content}</Markdown></div>
          ) : (
            <pre className="px-3 py-2 text-xs text-slate-700 whitespace-pre-wrap">{artifact.content}</pre>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------

function MessageBubble({ message, project, docId, chatId, onArtifactApplied }: {
  message: ChatMessage;
  project: string;
  docId: string;
  chatId: string;
  onArtifactApplied: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex items-start gap-2.5 justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-slate-800 px-3.5 py-2.5 text-sm text-white">
          <div className="whitespace-pre-wrap leading-relaxed">{message.content}</div>
        </div>
        <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-slate-200">
          <User size={14} className="text-slate-600" />
        </div>
      </div>
    );
  }

  const agent = message.agent ?? "assistant";
  const style = agentStyle(agent);

  return (
    <div className="flex items-start gap-2.5">
      <AgentAvatar agent={agent} />
      <div className={`max-w-[85%] rounded-2xl rounded-tl-md border ${style.accent} bg-white px-3.5 py-2.5`}>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">{agent}</div>
        <Markdown>{message.content}</Markdown>
        {message.artifacts && message.artifacts.length > 0 && (
          <div className="mt-2 space-y-2">
            {message.artifacts.map((art) => (
              <ArtifactCard
                key={art.id}
                artifact={art}
                project={project}
                docId={docId}
                chatId={chatId}
                onApplied={onArtifactApplied}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Streaming bubble
// ---------------------------------------------------------------------------

function StreamingBubble({ agent: sa }: { agent: StreamingAgent }) {
  const style = agentStyle(sa.agent);
  const text = sa.chunks.join("");
  return (
    <div className="flex items-start gap-2.5">
      <AgentAvatar agent={sa.agent} pulse={!sa.hasText} />
      <div className={`max-w-[85%] rounded-2xl rounded-tl-md border ${style.accent} bg-white px-3.5 py-2.5`}>
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{sa.agent}</span>
          {!sa.hasText && <span className="text-[10px] text-slate-400 animate-pulse">{formatStatus(sa.status)}</span>}
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
  const missing = local.messages.filter(
    (m) => m.role === "user" && m.id.startsWith("local_") && !server.messages.some((sm) => sm.role === "user" && sm.content === m.content),
  );
  if (missing.length === 0) return server;
  return { ...server, messages: [...server.messages, ...missing] };
}

// ---------------------------------------------------------------------------
// Doc Chat Panel (right sidebar)
// ---------------------------------------------------------------------------

function DocChatPanel({
  project,
  docId,
  chatId,
  onArtifactApplied,
}: {
  project: string;
  docId: string;
  chatId: string;
  onArtifactApplied: () => void;
}) {
  const [chat, setChat] = useState<ChatRecord | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingAgents, setStreamingAgents] = useState<StreamingAgent[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const composingRef = useRef(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const buildStreamHandlers = useCallback(() => {
    const activeStreams: Record<string, string[]> = {};
    const onEvent = (event: SSEEvent) => {
      if (event.type === "start" && event.agent) {
        activeStreams[event.agent] = [];
        setStreamingAgents((prev) => [...prev.filter((a) => a.agent !== event.agent), { agent: event.agent!, status: "thinking", chunks: [], hasText: false }]);
      } else if (event.type === "status" && event.agent) {
        setStreamingAgents((prev) => prev.map((a) => a.agent === event.agent ? { ...a, status: event.status ?? a.status } : a));
      } else if (event.type === "text" && event.agent && event.text) {
        if (activeStreams[event.agent]) activeStreams[event.agent].push(event.text);
        setStreamingAgents((prev) => prev.map((a) =>
          a.agent === event.agent ? { ...a, chunks: [...(activeStreams[event.agent] || [])], hasText: true, status: "generating" } : a));
      } else if ((event.type === "done" || event.type === "error") && event.agent) {
        const finalText = event.full_text ?? (activeStreams[event.agent] || []).join("");
        if (finalText.trim()) {
          const msg: ChatMessage = { id: `done_${event.agent}_${Date.now()}`, role: "assistant", content: finalText, agent: event.agent, artifacts: [], created_at: new Date().toISOString() };
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
      setStreaming(false);
      setStreamingAgents([]);
      getChat(project, docId, chatId).then((server) => setChat((local) => mergeMissing(server, local)));
    };
    return { onEvent, onDone };
  }, [project, docId, chatId]);

  useEffect(() => {
    let cancelled = false;
    getChat(project, docId, chatId).then((c) => {
      if (cancelled) return;
      setChat(c);
      getActiveTask(project, docId, chatId).then((status) => {
        if (cancelled || !status.active) return;
        setStreaming(true);
        const { onEvent, onDone } = buildStreamHandlers();
        abortRef.current = reconnectStream(project, docId, chatId, onEvent, onDone);
      });
    });
    return () => { cancelled = true; abortRef.current?.abort(); };
  }, [project, docId, chatId, buildStreamHandlers]);

  const scrollToBottom = useCallback(() => {
    const el = messagesContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(scrollToBottom, [chat?.messages.length, streamingAgents, scrollToBottom]);

  const handleSend = useCallback(() => {
    if (!input.trim() || streaming || !chat) return;
    const content = input.trim();
    setInput("");
    setStreaming(true);
    setStreamingAgents([]);
    const userMsg: ChatMessage = { id: `local_${Date.now()}`, role: "user", content, agent: null, artifacts: [], created_at: new Date().toISOString() };
    setChat((prev) => prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev);
    const { onEvent, onDone } = buildStreamHandlers();
    abortRef.current = askStream(project, docId, chatId, content, onEvent, onDone);
  }, [input, streaming, chat, project, docId, chatId, buildStreamHandlers]);

  const handleStop = useCallback(() => {
    void stopTask(project, docId, chatId);
  }, [project, docId, chatId]);

  const handleModeChange = useCallback((mode: ChatMode) => {
    if (!chat) return;
    updateChat(project, docId, chatId, { mode }).then(setChat);
  }, [chat, project, docId, chatId]);

  const handleSwap = useCallback(() => {
    if (!chat) return;
    const next = chat.first_agent === "claude" ? "codex" : "claude";
    updateChat(project, docId, chatId, { first_agent: next }).then(setChat);
  }, [chat, project, docId, chatId]);

  if (!chat) {
    return <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading chat...</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <ModeSwapBar mode={chat.mode} firstAgent={chat.first_agent} onModeChange={handleModeChange} onSwap={handleSwap} disabled={streaming} />

      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {chat.messages.length === 0 && streamingAgents.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-slate-400">Ask about this document or request changes</p>
          </div>
        )}
        {chat.messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} project={project} docId={docId} chatId={chatId} onArtifactApplied={onArtifactApplied} />
        ))}
        {streamingAgents.map((sa) => (
          <StreamingBubble key={`stream-${sa.agent}`} agent={sa} />
        ))}
      </div>

      <div className="border-t border-slate-200 p-3">
        <div className="flex gap-2">
          <textarea
            rows={1}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400/30 resize-none overflow-hidden"
            placeholder="Discuss or modify this doc... (Enter to send)"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
            }}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            onKeyDown={(e) => {
              const ne = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
              if (e.key === "Enter" && !e.shiftKey && !(composingRef.current || ne.isComposing || ne.keyCode === 229)) {
                e.preventDefault();
                handleSend();
              }
            }}
            disabled={streaming}
          />
          {streaming ? (
            <button type="button" className="flex h-9 w-9 items-center justify-center rounded-lg bg-red-500 text-white hover:bg-red-600" onClick={handleStop} title="Stop">
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button type="button" className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-40" onClick={handleSend} disabled={!input.trim()}>
              <Send size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right sidebar (chat + notes tabs)
// ---------------------------------------------------------------------------

function RightSidebar({
  project,
  docId,
  onCollapse,
  onArtifactApplied,
}: {
  project: string;
  docId: string;
  onCollapse: () => void;
  onArtifactApplied: () => void;
}) {
  const [tab, setTab] = useState<"chat" | "details">("chat");
  const [activeChatId, setActiveChatId] = useState("");
  const [chats, setChats] = useState<ChatListItem[]>([]);

  const refreshChats = useCallback(() => {
    listChats(project, docId).then(setChats).catch(() => {});
  }, [project, docId]);

  useEffect(() => {
    refreshChats();
    setActiveChatId("");
  }, [project, docId, refreshChats]);

  useEffect(() => {
    if (!activeChatId && chats.length > 0) setActiveChatId(chats[0].chat_id);
  }, [activeChatId, chats]);

  const handleCreateChat = useCallback(async () => {
    const chat = await createChat(project, docId);
    setActiveChatId(chat.chat_id);
    refreshChats();
  }, [project, docId, refreshChats]);

  const handleDeleteChat = useCallback(async (chatId: string) => {
    await deleteChat(project, docId, chatId);
    if (activeChatId === chatId) setActiveChatId("");
    refreshChats();
  }, [project, docId, activeChatId, refreshChats]);

  return (
    <div className="flex h-full flex-col">
      {/* Tab bar */}
      <div className="flex h-12 items-center border-b border-slate-200">
        <button onClick={onCollapse} className="flex h-6 w-6 items-center justify-center rounded hover:bg-slate-100 ml-2" title="Collapse" type="button">
          <ChevronRight size={14} />
        </button>
        <button
          type="button"
          className={`flex h-full flex-1 items-center justify-center gap-1.5 text-xs font-semibold ${tab === "chat" ? "border-b-2 border-slate-800 text-slate-800" : "text-slate-400 hover:text-slate-600"}`}
          onClick={() => setTab("chat")}
        >
          <MessageSquare size={14} />
          Chat
        </button>
        <button
          type="button"
          className={`flex h-full flex-1 items-center justify-center gap-1.5 text-xs font-semibold ${tab === "details" ? "border-b-2 border-slate-800 text-slate-800" : "text-slate-400 hover:text-slate-600"}`}
          onClick={() => setTab("details")}
        >
          <FileText size={14} />
          Details
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === "chat" ? (
          activeChatId ? (
            <div className="flex h-full flex-col">
              <div className="flex items-center gap-1 border-b border-slate-200 px-2 py-1.5">
                <button type="button" className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100" onClick={() => setActiveChatId("")}>
                  &larr; All chats
                </button>
                <span className="flex-1 truncate text-center text-xs font-medium text-slate-600">
                  {chats.find((c) => c.chat_id === activeChatId)?.title ?? "Chat"}
                </span>
                <button
                  type="button"
                  className="rounded px-2 py-1 text-xs text-red-500 hover:bg-red-50"
                  onClick={() => void handleDeleteChat(activeChatId)}
                >
                  <Trash2 size={12} />
                </button>
              </div>
              <div className="flex-1 overflow-hidden">
                <DocChatPanel project={project} docId={docId} chatId={activeChatId} onArtifactApplied={onArtifactApplied} />
              </div>
            </div>
          ) : (
            <div className="flex h-full flex-col">
              <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200">
                <span className="text-xs font-semibold text-slate-600">Chats</span>
                <button type="button" className="flex h-6 w-6 items-center justify-center rounded bg-slate-800 text-white hover:bg-slate-700" onClick={handleCreateChat}>
                  <Plus size={12} />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto">
                {chats.length === 0 ? (
                  <div className="px-3 py-8 text-center text-sm text-slate-400">No chats yet</div>
                ) : chats.map((c) => (
                  <button
                    key={c.chat_id}
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-2.5 text-left border-b border-slate-100 hover:bg-slate-50"
                    onClick={() => setActiveChatId(c.chat_id)}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-600">{c.title}</div>
                      <div className="text-[11px] text-slate-400">{c.message_count} msgs</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )
        ) : (
          <div className="p-4 text-sm text-slate-500">Document metadata and tags will appear here.</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// New doc dialog
// ---------------------------------------------------------------------------

function NewDocDialog({ onClose, onCreate }: { onClose: () => void; onCreate: (filename: string) => void }) {
  const [filename, setFilename] = useState("");
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = filename.trim();
    if (!trimmed) return;
    // Add .md extension if none provided
    const final = trimmed.includes(".") ? trimmed : `${trimmed}.md`;
    onCreate(final);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <form
        className="bg-white rounded-xl shadow-xl p-6 w-[400px]"
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <h3 className="text-lg font-semibold text-slate-800 mb-4">New Document</h3>
        <input
          autoFocus
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
          placeholder="e.g. ideas/new-idea.md"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
        />
        <p className="mt-1.5 text-[11px] text-slate-400">Use / to create subdirectories. Extension defaults to .md</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-700" onClick={onClose}>Cancel</button>
          <button type="submit" className="px-4 py-1.5 text-sm font-medium bg-slate-800 text-white rounded-lg hover:bg-slate-700" disabled={!filename.trim()}>Create</button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main DocsPage
// ---------------------------------------------------------------------------

export default function DocsPage({ project }: { project: string }) {
  const [docs, setDocs] = useState<DocRecord[]>([]);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [showNewDoc, setShowNewDoc] = useState(false);
  const [rightOpen, setRightOpen] = useState(true);

  const refreshDocs = useCallback(() => {
    if (!project) return;
    listDocs(project).then(setDocs).catch(() => {});
  }, [project]);

  useEffect(() => {
    refreshDocs();
    setSelectedDocId("");
  }, [refreshDocs]);

  const selectedDoc = docs.find((d) => d.id === selectedDocId);

  const handleCreate = useCallback(async (filename: string) => {
    try {
      const doc = await createDoc(project, filename);
      setSelectedDocId(doc.id);
      refreshDocs();
    } catch (err) {
      alert("Failed to create: " + (err instanceof Error ? err.message : String(err)));
    }
  }, [project, refreshDocs]);

  const handleDelete = useCallback(async (docId: string) => {
    if (!confirm("Delete this document?")) return;
    await deleteDoc(project, docId);
    if (selectedDocId === docId) setSelectedDocId("");
    refreshDocs();
  }, [project, selectedDocId, refreshDocs]);

  return (
    <div className="flex flex-1 min-h-0">
      {/* Left sidebar: doc list */}
      <div className="w-[280px] flex-shrink-0">
        <DocList
          docs={docs}
          selectedDocId={selectedDocId}
          onSelect={setSelectedDocId}
          onCreate={() => setShowNewDoc(true)}
          onDelete={handleDelete}
        />
      </div>

      {/* Center: doc editor/preview */}
      <div className="flex-1 min-w-0 bg-white border-r border-slate-200">
        {selectedDoc ? (
          <DocEditor project={project} doc={selectedDoc} onContentSaved={refreshDocs} />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            Select a document or create a new one
          </div>
        )}
      </div>

      {/* Right sidebar: chat */}
      {rightOpen && selectedDoc ? (
        <div className="w-[360px] flex-shrink-0 border-l border-slate-200 bg-white">
          <RightSidebar
            project={project}
            docId={selectedDocId}
            onCollapse={() => setRightOpen(false)}
            onArtifactApplied={refreshDocs}
          />
        </div>
      ) : selectedDoc && !rightOpen ? (
        <button
          onClick={() => setRightOpen(true)}
          className="absolute right-0 top-16 z-20 flex h-7 w-7 items-center justify-center rounded-l-md border border-r-0 border-slate-300 bg-white shadow-sm hover:bg-slate-100"
          title="Show chat"
          type="button"
        >
          <ChevronLeft size={16} />
        </button>
      ) : null}

      {showNewDoc && <NewDocDialog onClose={() => setShowNewDoc(false)} onCreate={handleCreate} />}
    </div>
  );
}
