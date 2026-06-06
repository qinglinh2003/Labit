import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Copy, Download, FilePlus2, Send, Square, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import type { ChatArtifact, ChatMessage, ChatMode, ChatRecord, SSEEvent } from "../api/chat";
import { artifactDownloadUrl, askStream, getActiveTask, getChat, reconnectStream, stopTask, updateChat } from "../api/chat";
import { createDoc } from "../docs/api";
import { ClaudeIcon, CodexIcon } from "./AgentIcons";
import ModeSwapBar from "./ModeSwapBar";

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

function hasAssistantMessage(messages: ChatMessage[], agent: string, content: string): boolean {
  return messages.some((msg) =>
    msg.role === "assistant" &&
    msg.agent === agent &&
    msg.content === content
  );
}

function mergeMissingLocalMessages(serverChat: ChatRecord, localChat: ChatRecord | null): ChatRecord {
  if (!localChat) return serverChat;
  const missing = localChat.messages.filter((msg) =>
    msg.role === "user" &&
    msg.id.startsWith("local_") &&
    !serverChat.messages.some((serverMsg) => serverMsg.role === "user" && serverMsg.content === msg.content)
  );
  if (missing.length === 0) return serverChat;
  return { ...serverChat, messages: [...serverChat.messages, ...missing] };
}

function AgentAvatar({ agent, pulse }: { agent: string; pulse?: boolean }) {
  const style = agentStyle(agent);
  const cls = `flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-white border ${style.accent} ${pulse ? "animate-pulse" : ""}`;
  if (agent === "claude") {
    return <div className={cls}><ClaudeIcon size={14} className={style.icon} /></div>;
  }
  if (agent === "codex") {
    return <div className={cls}><CodexIcon size={14} className={style.icon} /></div>;
  }
  return <div className={cls}><span className={`text-xs font-bold ${style.icon}`}>{agent[0]?.toUpperCase()}</span></div>;
}

/** Markdown renderer with prose styling */
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

export default function ChatPanel({
  project,
  paperId,
  chatId,
}: {
  project: string;
  paperId: string;
  chatId: string;
}) {
  const [chat, setChat] = useState<ChatRecord | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingAgents, setStreamingAgents] = useState<StreamingAgent[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const composingRef = useRef(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  // Build SSE event handlers for both initial ask and reconnect.
  // Returns { onEvent, onDone } that drive streamingAgents state.
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
        setStreamingAgents((prev) =>
          prev.map((a) =>
            a.agent === event.agent ? { ...a, status: event.status ?? a.status } : a
          )
        );
      } else if (event.type === "text" && event.agent && event.text) {
        if (activeStreams[event.agent]) {
          activeStreams[event.agent].push(event.text);
        }
        setStreamingAgents((prev) =>
          prev.map((a) =>
            a.agent === event.agent
              ? { ...a, chunks: [...(activeStreams[event.agent] || [])], hasText: true, status: "generating" }
              : a
          )
        );
      } else if ((event.type === "done" || event.type === "error") && event.agent) {
        const finalText = event.full_text ?? (activeStreams[event.agent] || []).join("");
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
      const partialMessages = Object.entries(activeStreams)
        .map(([agent, chunks]) => ({ agent, content: chunks.join("") }))
        .filter((entry) => entry.content.trim());
      if (partialMessages.length > 0) {
        setChat((prev) => {
          if (!prev) return prev;
          const additions = partialMessages
            .filter((entry) => !hasAssistantMessage(prev.messages, entry.agent, entry.content))
            .map((entry) => ({
              id: `partial_${entry.agent}_${Date.now()}`,
              role: "assistant" as const,
              content: entry.content,
              agent: entry.agent,
              artifacts: [] as ChatArtifact[],
              created_at: new Date().toISOString(),
            }));
          return additions.length ? { ...prev, messages: [...prev.messages, ...additions] } : prev;
        });
      }
      setStreaming(false);
      setStreamingAgents([]);
      // Re-fetch to get server-saved messages
      getChat(project, paperId, chatId).then((serverChat) => {
        setChat((localChat) => mergeMissingLocalMessages(serverChat, localChat));
      });
    };

    return { onEvent, onDone };
  }, [project, paperId, chatId]);

  // Load chat on mount + check for active background task to reconnect
  useEffect(() => {
    let cancelled = false;

    getChat(project, paperId, chatId).then((c) => {
      if (cancelled) return;
      setChat(c);

      // Check if there's a running background task we should reconnect to
      getActiveTask(project, paperId, chatId).then((status) => {
        if (cancelled || !status.active) return;
        setStreaming(true);
        setStreamingAgents([]);
        const { onEvent, onDone } = buildStreamHandlers();
        const controller = reconnectStream(project, paperId, chatId, onEvent, onDone);
        abortRef.current = controller;
      });
    });

    return () => {
      cancelled = true;
      // Only abort the SSE read — the backend task keeps running
      abortRef.current?.abort();
    };
  }, [project, paperId, chatId, buildStreamHandlers]);

  const scrollToBottom = useCallback(() => {
    const el = messagesContainerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  useEffect(scrollToBottom, [chat?.messages.length, streamingAgents, scrollToBottom]);

  const handleSend = useCallback(() => {
    if (!input.trim() || streaming || !chat) return;
    const content = input.trim();
    setInput("");
    setStreaming(true);
    setStreamingAgents([]);

    const userMsg: ChatMessage = {
      id: `local_${Date.now()}`,
      role: "user",
      content,
      agent: null,
      artifacts: [],
      created_at: new Date().toISOString(),
    };
    setChat((prev) => prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev);

    const { onEvent, onDone } = buildStreamHandlers();
    const controller = askStream(project, paperId, chatId, content, onEvent, onDone);
    abortRef.current = controller;
  }, [input, streaming, chat, project, paperId, chatId, buildStreamHandlers]);

  const handleStop = useCallback(() => {
    // Tell the backend to cancel the agent subprocess
    void stopTask(project, paperId, chatId);
  }, [project, paperId, chatId]);

  const handleModeChange = useCallback((mode: ChatMode) => {
    if (!chat) return;
    updateChat(project, paperId, chatId, { mode }).then(setChat);
  }, [chat, project, paperId, chatId]);

  const handleSwap = useCallback(() => {
    if (!chat) return;
    const next = chat.first_agent === "claude" ? "codex" : "claude";
    updateChat(project, paperId, chatId, { first_agent: next }).then(setChat);
  }, [chat, project, paperId, chatId]);

  if (!chat) {
    return <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading chat...</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <ModeSwapBar
        mode={chat.mode}
        firstAgent={chat.first_agent}
        onModeChange={handleModeChange}
        onSwap={handleSwap}
        disabled={streaming}
      />

      {/* Messages */}
      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {chat.messages.length === 0 && streamingAgents.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-slate-400">Ask a question about this paper</p>
          </div>
        )}
        {chat.messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} project={project} paperId={paperId} chatId={chatId} />
        ))}
        {streamingAgents.map((sa) => (
          <StreamingBubble key={`stream-${sa.agent}`} agent={sa} />
        ))}
      </div>

      {/* Input */}
      <div className="border-t border-slate-200 p-3">
        <div className="flex gap-2">
          <textarea
            rows={1}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400/30 resize-none overflow-hidden"
            placeholder="Ask about this paper... (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              // Auto-resize
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
            }}
            onCompositionStart={() => {
              composingRef.current = true;
            }}
            onCompositionEnd={() => {
              composingRef.current = false;
            }}
            onKeyDown={(e) => {
              const nativeEvent = e.nativeEvent as KeyboardEvent & {
                isComposing?: boolean;
                keyCode?: number;
              };
              const isComposing =
                composingRef.current ||
                nativeEvent.isComposing ||
                nativeEvent.keyCode === 229;

              if (e.key === "Enter" && !e.shiftKey && !isComposing) {
                e.preventDefault();
                handleSend();
              }
            }}
            disabled={streaming}
          />
          {streaming ? (
            <button
              type="button"
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors"
              onClick={handleStop}
              title="Stop generating"
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-40 transition-colors"
              onClick={handleSend}
              disabled={!input.trim()}
            >
              <Send size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ArtifactCard({
  artifact,
  downloadUrl,
  project,
}: {
  artifact: ChatArtifact;
  downloadUrl: string;
  project: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const lines = artifact.content.split("\n").length;

  const handleCopy = () => {
    navigator.clipboard.writeText(artifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
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

  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <ChevronDown size={14} className="text-slate-400 flex-shrink-0" /> : <ChevronRight size={14} className="text-slate-400 flex-shrink-0" />}
          <span className="text-sm font-medium text-slate-700 truncate">{artifact.title}</span>
          <span className="text-[10px] text-slate-400 truncate min-w-0 max-w-[120px]" title={artifact.filename}>{artifact.filename}</span>
          {artifact.language && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-200 text-slate-500 flex-shrink-0">{artifact.language}</span>
          )}
        </button>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={handleCopy}
            className="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-slate-600 transition-colors"
            title="Copy content"
          >
            <Copy size={13} />
          </button>
          {copied && <span className="text-[10px] text-green-600">Copied</span>}
          <button
            onClick={handleSaveAsDoc}
            disabled={saving}
            className="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-emerald-600 transition-colors disabled:opacity-50"
            title={saved ? "Saved to docs!" : "Save as Doc"}
          >
            {saved ? <Check size={13} className="text-emerald-500" /> : <FilePlus2 size={13} />}
          </button>
          <a
            href={downloadUrl}
            download={artifact.filename}
            className="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-slate-600 transition-colors"
            title="Download"
          >
            <Download size={13} />
          </a>
        </div>
      </div>
      {!expanded && (
        <div className="px-3 pb-2 text-[10px] text-slate-400">{lines} lines</div>
      )}
      {expanded && (
        <div className="border-t border-slate-200 max-h-96 overflow-y-auto">
          {artifact.language === "markdown" ? (
            <div className="px-3 py-2">
              <Markdown>{artifact.content}</Markdown>
            </div>
          ) : (
            <pre className="px-3 py-2 text-xs text-slate-700 whitespace-pre-wrap">{artifact.content}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message, project, paperId, chatId }: { message: ChatMessage; project: string; paperId: string; chatId: string }) {
  const isUser = message.role === "user";

  if (isUser) {
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
                downloadUrl={artifactDownloadUrl(project, paperId, chatId, art.id)}
                project={project}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StreamingBubble({ agent: sa }: { agent: StreamingAgent }) {
  const style = agentStyle(sa.agent);
  const text = sa.chunks.join("");

  return (
    <div className="flex items-start gap-2.5">
      <AgentAvatar agent={sa.agent} pulse={!sa.hasText} />
      <div className={`max-w-[85%] rounded-2xl rounded-tl-md border ${style.accent} bg-white px-3.5 py-2.5`}>
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{sa.agent}</span>
          {!sa.hasText && (
            <span className="text-[10px] text-slate-400">
              <ThinkingIndicator status={sa.status} />
            </span>
          )}
        </div>
        {sa.hasText ? (
          <div>
            <Markdown>{text}</Markdown>
            <span className="inline-block w-1.5 h-4 ml-0.5 -mb-0.5 bg-slate-400 animate-blink rounded-sm" />
          </div>
        ) : (
          <ThinkingDots status={sa.status} />
        )}
      </div>
    </div>
  );
}

function ThinkingIndicator({ status }: { status: string }) {
  const label = formatStatus(status);
  return <span className="animate-pulse">{label}</span>;
}

function ThinkingDots({ status }: { status: string }) {
  const label = formatStatus(status);
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex items-center gap-1">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: "300ms" }} />
      </div>
      <span className="text-xs text-slate-400">{label}</span>
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
  if (s === "content block stop") return "Finishing...";
  if (s === "finishing") return "Finishing...";
  if (s.startsWith("running ") || s.startsWith("completed ")) return status;
  return status;
}
