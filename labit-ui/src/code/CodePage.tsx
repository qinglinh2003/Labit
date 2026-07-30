import { useCallback, useEffect, useRef, useState } from "react";
import {
  Braces,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  Database,
  Download,
  Edit3,
  Eye,
  FileCode,
  FileCode2,
  FileJson,
  FilePlus2,
  FileText,
  FileType,
  FolderOpen,
  Globe,
  Hash,
  ImagePlus,
  MessageSquare,
  NotebookPen,
  Palette,
  Save,
  Search,
  Send,
  Settings,
  Square,
  Terminal,
  User,
  X,
} from "lucide-react";

// CodeMirror
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { defaultKeymap, indentWithTab } from "@codemirror/commands";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json as jsonLang } from "@codemirror/lang-json";
import { markdown as markdownLang } from "@codemirror/lang-markdown";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { rust } from "@codemirror/lang-rust";
import { java } from "@codemirror/lang-java";
import { cpp } from "@codemirror/lang-cpp";
import { go } from "@codemirror/lang-go";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";
import { oneDark } from "@codemirror/theme-one-dark";
import { syntaxHighlighting, defaultHighlightStyle, indentOnInput, bracketMatching, foldGutter, foldKeymap } from "@codemirror/language";
import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";

import {
  applyArtifact,
  artifactDownloadUrl,
  askStream,
  attachmentUrl,
  codeFilePreviewPdfUrl,
  createChat,
  deleteChat,
  encodeFileId,
  getActiveTask,
  getChat,
  getFileContent,
  getTree,
  listChats,
  reconnectStream,
  saveFileContent,
  stopTask,
  updateChat,
  uploadAttachment,
  type ChatArtifact,
  type ChatAttachment,
  type ChatListItem,
  type ChatMessage,
  type ChatMode,
  type ChatRecord,
  type CodeTreeEntry,
  type SSEEvent,
} from "./api";
import { ClaudeIcon, CodexIcon } from "../components/AgentIcons";
import ChatList from "../components/ChatList";
import Markdown from "../components/Markdown";
import ModeSwapBar from "../components/ModeSwapBar";
import { createDoc } from "../docs/api";

// ---------------------------------------------------------------------------
// Language support mapping
// ---------------------------------------------------------------------------

function getLangExtension(lang: string) {
  switch (lang) {
    case "python": return python();
    case "javascript": return javascript();
    case "typescript": return javascript({ typescript: true });
    case "json": return jsonLang();
    case "markdown": return markdownLang();
    case "html": return html();
    case "css": return css();
    case "rust": return rust();
    case "java": return java();
    case "cpp": case "c": return cpp();
    case "go": return go();
    case "sql": return sql();
    case "xml": return xml();
    case "yaml": return yaml();
    default: return [];
  }
}

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
// Drag-to-resize hook (same as Paper module)
// ---------------------------------------------------------------------------

function useDragResize(
  initialWidth: number,
  minWidth: number,
  side: "left" | "right",
  getMaxWidth?: () => number,
) {
  const [width, setWidth] = useState(initialWidth);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startW.current = width;

    const onMouseMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const dx = ev.clientX - startX.current;
      const newW = side === "left" ? startW.current + dx : startW.current - dx;
      const maxWidth = getMaxWidth?.();
      const bounded = maxWidth === undefined ? newW : Math.min(maxWidth, newW);
      const lowerBound = maxWidth === undefined ? minWidth : Math.min(minWidth, maxWidth);
      setWidth(Math.max(lowerBound, bounded));
    };

    const onMouseUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [width, minWidth, side, getMaxWidth]);

  return { width, setWidth, onMouseDown };
}

// ---------------------------------------------------------------------------
// File tree (left sidebar)
// ---------------------------------------------------------------------------

function fileIcon(name: string, isSelected: boolean): React.ReactNode {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  const size = 13;
  const cls = `flex-shrink-0 ${isSelected ? "" : "opacity-70"}`;

  // Distinct icon + color per language family
  switch (ext) {
    // Python
    case "py":
    case "pyi":
    case "pyx":
      return <FileCode2 size={size} className={`${cls} text-blue-500`} />;
    // JavaScript
    case "js":
    case "jsx":
    case "mjs":
    case "cjs":
      return <Braces size={size} className={`${cls} text-yellow-500`} />;
    // TypeScript
    case "ts":
    case "tsx":
      return <FileType size={size} className={`${cls} text-blue-400`} />;
    // Rust
    case "rs":
      return <Settings size={size} className={`${cls} text-orange-600`} />;
    // Go
    case "go":
      return <Code2 size={size} className={`${cls} text-cyan-500`} />;
    // Java / Kotlin
    case "java":
    case "kt":
    case "kts":
      return <Hash size={size} className={`${cls} text-red-500`} />;
    // C / C++
    case "c":
    case "h":
    case "cpp":
    case "cc":
    case "cxx":
    case "hpp":
      return <FileCode size={size} className={`${cls} text-indigo-500`} />;
    // Ruby
    case "rb":
      return <FileCode2 size={size} className={`${cls} text-red-600`} />;
    // Shell
    case "sh":
    case "bash":
    case "zsh":
      return <Terminal size={size} className={`${cls} text-green-600`} />;
    // HTML
    case "html":
    case "htm":
      return <Globe size={size} className={`${cls} text-orange-500`} />;
    // CSS / SCSS
    case "css":
    case "scss":
    case "sass":
    case "less":
      return <Palette size={size} className={`${cls} text-purple-500`} />;
    // JSON
    case "json":
    case "jsonc":
      return <FileJson size={size} className={`${cls} text-yellow-600`} />;
    // YAML / TOML / config
    case "yaml":
    case "yml":
    case "toml":
    case "ini":
    case "cfg":
      return <Settings size={size} className={`${cls} text-slate-500`} />;
    // Markdown / text
    case "md":
    case "mdx":
    case "txt":
    case "rst":
      return <FileText size={size} className={`${cls} text-slate-400`} />;
    // SQL
    case "sql":
      return <Database size={size} className={`${cls} text-emerald-500`} />;
    // Docker / Makefile (no extension match — handle by name below)
    default: {
      const lower = name.toLowerCase();
      if (lower === "dockerfile" || lower.startsWith("dockerfile."))
        return <Code2 size={size} className={`${cls} text-blue-500`} />;
      if (lower === "makefile" || lower === "cmakelists.txt")
        return <Settings size={size} className={`${cls} text-orange-400`} />;
      if (lower.startsWith(".env"))
        return <FileJson size={size} className={`${cls} text-yellow-600`} />;
      return <FileCode size={size} className={`${cls} text-slate-400`} />;
    }
  }
}

function FileTreeNode({
  entry,
  depth,
  selectedPath,
  onSelect,
  expanded,
  onToggle,
}: {
  entry: CodeTreeEntry;
  depth: number;
  selectedPath: string;
  onSelect: (path: string) => void;
  expanded: Record<string, boolean>;
  onToggle: (path: string) => void;
}) {
  const isExpanded = expanded[entry.path] ?? (depth === 0);
  const isSelected = entry.path === selectedPath;

  if (entry.is_dir) {
    return (
      <div>
        <button
          type="button"
          className={`flex w-full items-center gap-1.5 py-1 text-left hover:bg-slate-100 transition-colors ${isSelected ? "bg-slate-100" : ""}`}
          style={{ paddingLeft: `${depth * 16 + 8}px` }}
          onClick={() => onToggle(entry.path)}
        >
          {isExpanded ? <ChevronDown size={12} className="text-slate-400 flex-shrink-0" /> : <ChevronRight size={12} className="text-slate-400 flex-shrink-0" />}
          <FolderOpen size={13} className="text-amber-500 flex-shrink-0" />
          <span className="text-xs font-medium text-slate-600 truncate">{entry.name}</span>
        </button>
        {isExpanded && entry.children && entry.children.map((child) => (
          <FileTreeNode
            key={child.path}
            entry={child}
            depth={depth + 1}
            selectedPath={selectedPath}
            onSelect={onSelect}
            expanded={expanded}
            onToggle={onToggle}
          />
        ))}
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`flex w-full items-center gap-1.5 py-1 text-left transition-colors ${
        isSelected ? "bg-white border-l-2 border-l-slate-800 font-semibold" : "hover:bg-slate-100"
      }`}
      style={{ paddingLeft: `${depth * 16 + 8 + 16}px` }}
      onClick={() => onSelect(entry.path)}
    >
      {fileIcon(entry.name, isSelected)}
      <span className={`text-xs truncate ${isSelected ? "text-slate-800" : "text-slate-600"}`}>{entry.name}</span>
    </button>
  );
}

function FileTree({
  tree,
  selectedPath,
  onSelect,
  onCollapse,
  onResizeMouseDown,
  width,
}: {
  tree: CodeTreeEntry[];
  selectedPath: string;
  onSelect: (path: string) => void;
  onCollapse: () => void;
  onResizeMouseDown: (e: React.MouseEvent) => void;
  width: number;
}) {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const handleToggle = useCallback((path: string) => {
    setExpanded((prev) => ({ ...prev, [path]: !prev[path] }));
  }, []);

  // Auto-expand to selected file
  useEffect(() => {
    if (!selectedPath) return;
    const parts = selectedPath.split("/");
    const newExpanded: Record<string, boolean> = {};
    for (let i = 1; i < parts.length; i++) {
      newExpanded[parts.slice(0, i).join("/")] = true;
    }
    setExpanded((prev) => ({ ...prev, ...newExpanded }));
  }, [selectedPath]);

  // Filter tree by search
  const filterTree = useCallback((entries: CodeTreeEntry[], q: string): CodeTreeEntry[] => {
    if (!q) return entries;
    const lower = q.toLowerCase();
    return entries.reduce<CodeTreeEntry[]>((acc, entry) => {
      if (entry.is_dir) {
        const filtered = filterTree(entry.children ?? [], q);
        if (filtered.length > 0) {
          acc.push({ ...entry, children: filtered });
        }
      } else if (entry.name.toLowerCase().includes(lower) || entry.path.toLowerCase().includes(lower)) {
        acc.push(entry);
      }
      return acc;
    }, []);
  }, []);

  const filtered = filterTree(tree, search);

  // Count total files
  const countFiles = (entries: CodeTreeEntry[]): number =>
    entries.reduce((n, e) => n + (e.is_dir ? countFiles(e.children ?? []) : 1), 0);
  const fileCount = countFiles(filtered);

  return (
    <aside className="relative flex-shrink-0 flex flex-col h-full bg-white" style={{ width: `${width}px`, borderRight: "1px solid var(--lb-line, #e1e9f4)" }}>
      {/* Header — matches Paper's lb-head */}
      <div className="flex items-center gap-2 px-4 h-14 border-b border-slate-200">
        <h2 className="text-sm font-bold text-slate-800">Code</h2>
        <span className="text-[11px] text-slate-400 font-medium">{fileCount}</span>
        <span className="flex-1" />
        <button type="button" className="flex h-6 w-6 items-center justify-center rounded hover:bg-slate-100" title="Collapse" onClick={onCollapse}>
          <ChevronLeft size={15} />
        </button>
      </div>

      {/* Scroll area */}
      <div className="flex-1 overflow-y-auto py-1">
        {filtered.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-400">No files found</div>
        ) : (
          filtered.map((entry) => (
            <FileTreeNode
              key={entry.path}
              entry={entry}
              depth={0}
              selectedPath={selectedPath}
              onSelect={onSelect}
              expanded={expanded}
              onToggle={handleToggle}
            />
          ))
        )}
      </div>

      {/* Footer search — matches Paper's lb-foot */}
      <div className="border-t border-slate-200 px-3 py-2">
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5">
          <Search size={14} className="text-slate-400" />
          <input
            className="flex-1 text-xs bg-transparent border-0 outline-none placeholder:text-slate-400"
            placeholder="Search files..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Drag handle */}
      <div
        className="absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/40 active:bg-blue-500/50 z-10"
        onMouseDown={onResizeMouseDown}
      />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// CodeMirror editor component
// ---------------------------------------------------------------------------

function CodeMirrorEditor({
  content,
  language,
  readOnly,
  onChange,
}: {
  content: string;
  language: string;
  readOnly: boolean;
  onChange?: (value: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Track the latest content we set programmatically to avoid update loops
  const lastSetContentRef = useRef(content);

  useEffect(() => {
    if (!containerRef.current) return;

    const langExt = getLangExtension(language);

    const extensions = [
      lineNumbers(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      foldGutter(),
      indentOnInput(),
      bracketMatching(),
      closeBrackets(),
      highlightSelectionMatches(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      keymap.of([
        ...defaultKeymap,
        ...searchKeymap,
        ...closeBracketsKeymap,
        ...foldKeymap,
        indentWithTab,
      ]),
      ...(Array.isArray(langExt) ? langExt : [langExt]),
      EditorView.theme({
        "&": { height: "100%", fontSize: "13px" },
        ".cm-scroller": { overflow: "auto", fontFamily: "'IBM Plex Mono', 'Fira Code', monospace" },
        ".cm-gutters": { backgroundColor: "#f8fafc", borderRight: "1px solid #e2e8f0" },
        ".cm-activeLineGutter": { backgroundColor: "#e2e8f0" },
        ".cm-activeLine": { backgroundColor: "#f1f5f9" },
      }),
      EditorView.lineWrapping,
    ];

    if (readOnly) {
      extensions.push(EditorState.readOnly.of(true));
    } else {
      extensions.push(
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            const val = update.state.doc.toString();
            lastSetContentRef.current = val;
            onChangeRef.current?.(val);
          }
        }),
      );
    }

    const state = EditorState.create({
      doc: content,
      extensions,
    });

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });

    viewRef.current = view;
    lastSetContentRef.current = content;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  // Re-create editor when language or readOnly changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language, readOnly]);

  // Update content when it changes externally
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    if (content === lastSetContentRef.current) return;
    lastSetContentRef.current = content;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: content },
    });
  }, [content]);

  return <div ref={containerRef} className="h-full w-full overflow-hidden" />;
}

// ---------------------------------------------------------------------------
// Code viewer/editor (center panel)
// ---------------------------------------------------------------------------

function CodeEditor({
  project,
  filePath,
  language,
  onContentSaved,
}: {
  project: string;
  filePath: string;
  language: string;
  onContentSaved: () => void;
}) {
  const isMarkdown = /\.(md|mdx|markdown)$/i.test(filePath);
  const [mode, setMode] = useState<"view" | "edit" | "preview">("view");
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const contentRef = useRef("");
  const dirtyRef = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fileId = encodeFileId(filePath);
  const dirty = content !== savedContent;

  useEffect(() => {
    setLoading(true);
    setMode("view");
    getFileContent(project, fileId).then((c) => {
      setContent(c);
      setSavedContent(c);
      contentRef.current = c;
      dirtyRef.current = false;
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [project, fileId]);

  const doSave = useCallback(async (text: string) => {
    setSaving(true);
    try {
      await saveFileContent(project, fileId, text);
      setSavedContent(text);
      dirtyRef.current = false;
      onContentSaved();
    } finally {
      setSaving(false);
    }
  }, [project, fileId, onContentSaved]);

  const handleChange = useCallback((value: string) => {
    setContent(value);
    contentRef.current = value;
    dirtyRef.current = true;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => void doSave(value), 2000);
  }, [doSave]);

  // Flush on unmount
  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      if (dirtyRef.current) {
        void saveFileContent(project, fileId, contentRef.current);
      }
    };
  }, [project, fileId]);

  // Reload content when external apply happens
  const reloadContent = useCallback(() => {
    getFileContent(project, fileId).then((c) => {
      setContent(c);
      setSavedContent(c);
      contentRef.current = c;
      dirtyRef.current = false;
    });
  }, [project, fileId]);

  useEffect(() => {
    (window as any).__codeEditorReload = reloadContent;
    return () => { delete (window as any).__codeEditorReload; };
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
                mode === "view" ? "bg-slate-800 text-white" : "bg-white text-slate-500 hover:bg-slate-50"
              }`}
              onClick={() => setMode("view")}
            >
              <Eye size={12} />
              View
            </button>
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
            {isMarkdown && (
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
            )}
          </div>
          <span className="text-xs text-slate-400 truncate max-w-[300px]">{filePath}</span>
          {language && <span className="text-[10px] text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">{language}</span>}
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

      {/* Editor / Preview */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {mode === "preview" && isMarkdown ? (
          <iframe
            src={codeFilePreviewPdfUrl(project, fileId)}
            className="h-full w-full border-0"
            title="Markdown PDF preview"
          />
        ) : (
          <CodeMirrorEditor
            content={content}
            language={language}
            readOnly={mode === "view"}
            onChange={mode === "edit" ? handleChange : undefined}
          />
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
// Artifact card (with Apply to File)
// ---------------------------------------------------------------------------

function ArtifactCard({
  artifact,
  project,
  fileId,
  chatId,
  onApplied,
}: {
  artifact: ChatArtifact;
  project: string;
  fileId: string;
  chatId: string;
  onApplied: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const lines = artifact.content.split("\n").length;

  const handleCopy = () => {
    navigator.clipboard.writeText(artifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleApply = async () => {
    if (!confirm("Apply this artifact to the file? This will replace the current content.")) return;
    setApplying(true);
    try {
      await applyArtifact(project, fileId, chatId, artifact.id);
      setApplied(true);
      onApplied();
      (window as any).__codeEditorReload?.();
    } catch (err) {
      alert("Failed to apply: " + (err instanceof Error ? err.message : String(err)));
    } finally {
      setApplying(false);
    }
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
          {fileId && (
            <button
              onClick={handleApply}
              disabled={applying || applied}
              className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                applied ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700 hover:bg-blue-200"
              } disabled:opacity-50`}
              title="Apply to file"
            >
              {applied ? <Check size={11} /> : <FileCode size={11} />}
              {applied ? "Applied" : "Apply"}
            </button>
          )}
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

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------

function MessageImages({ message, project, chatId }: { message: ChatMessage; project: string; chatId: string }) {
  const atts = message.attachments?.filter((a) => a.kind === "image") ?? [];
  if (atts.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-1">
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
            className="rounded-lg max-h-32 max-w-[180px] border border-slate-600 hover:border-slate-400 transition-colors cursor-pointer"
          />
        </a>
      ))}
    </div>
  );
}

function MessageBubble({ message, project, fileId, chatId, onArtifactApplied }: {
  message: ChatMessage;
  project: string;
  fileId: string;
  chatId: string;
  onArtifactApplied: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex items-start gap-2.5 justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-slate-800 px-3.5 py-2.5 text-sm text-white">
          <MessageImages message={message} project={project} chatId={chatId} />
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
                fileId={fileId}
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hasAssistantMessage(messages: ChatMessage[], agent: string, content: string): boolean {
  return messages.some((m) => m.role === "assistant" && m.agent === agent && m.content === content);
}

function mergeMissing(server: ChatRecord, local: ChatRecord | null): ChatRecord {
  if (!local) return server;
  const lastUserIdx = (() => { for (let i = server.messages.length - 1; i >= 0; i--) { if (server.messages[i].role === "user") return i; } return -1; })();
  const serverTurnAgents = new Set(
    server.messages.slice(lastUserIdx + 1).filter((m) => m.role === "assistant").map((m) => m.agent),
  );
  const missing: ChatMessage[] = [];
  for (const m of local.messages) {
    if (m.role === "user" && m.id.startsWith("local_") && !server.messages.some((sm) => sm.role === "user" && sm.content === m.content)) {
      missing.push(m);
    } else if (m.role === "assistant" && (m.id.startsWith("done_") || m.id.startsWith("partial_"))) {
      if (!serverTurnAgents.has(m.agent)) {
        missing.push(m);
      }
    }
  }
  if (missing.length === 0) return server;
  return { ...server, messages: [...server.messages, ...missing] };
}

// ---------------------------------------------------------------------------
// Code Chat Panel
// ---------------------------------------------------------------------------

function CodeChatPanel({
  project,
  filePath,
  chatId,
  onArtifactApplied,
}: {
  project: string;
  filePath: string;  // currently open file (may be empty)
  chatId: string;
  onArtifactApplied: () => void;
}) {
  const [chat, setChat] = useState<ChatRecord | null>(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingAgents, setStreamingAgents] = useState<StreamingAgent[]>([]);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const composingRef = useRef(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fileId = filePath ? encodeFileId(filePath) : "";

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
        let finalText = event.full_text ?? (activeStreams[event.agent] || []).join("");
        if (!finalText.trim() && event.type === "error" && event.error) {
          finalText = `${event.agent} error: ${event.error}`;
        }
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
      // Re-fetch server state. If local assistant messages survive mergeMissing
      // (i.e. save was in-flight or failed), retry after a delay to pick up
      // the saved version with artifacts attached.
      getChat(project, chatId).then((server) => {
        const merged = mergeMissing(server, null);  // just get server
        setChat((local) => {
          const result = mergeMissing(server, local);
          // If we had to preserve local assistant messages, schedule a retry
          if (result.messages.length > merged.messages.length) {
            setTimeout(() => {
              getChat(project, chatId).then((retry) => setChat((prev) => mergeMissing(retry, prev)));
            }, 2000);
          }
          return result;
        });
      });
    };
    return { onEvent, onDone };
  }, [project, chatId]);

  useEffect(() => {
    let cancelled = false;
    getChat(project, chatId).then((c) => {
      if (cancelled) return;
      setChat(c);
      getActiveTask(project, chatId).then((status) => {
        if (cancelled || !status.active) return;
        setStreaming(true);
        const { onEvent, onDone } = buildStreamHandlers();
        abortRef.current = reconnectStream(project, chatId, onEvent, onDone);
      });
    });
    return () => { cancelled = true; abortRef.current?.abort(); };
  }, [project, chatId, buildStreamHandlers]);

  const scrollToBottom = useCallback(() => {
    const el = messagesContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(scrollToBottom, [chat?.messages.length, streamingAgents, scrollToBottom]);

  const handleUploadFiles = useCallback(async (files: File[]) => {
    if (!chat || files.length === 0) return;
    setUploading(true);
    try {
      const newAtts: ChatAttachment[] = [];
      for (const file of files.slice(0, 4)) {
        const att = await uploadAttachment(project, chatId, file);
        newAtts.push(att);
      }
      setPendingAttachments((prev) => [...prev, ...newAtts].slice(0, 4));
    } catch { /* ignore */ } finally {
      setUploading(false);
    }
  }, [chat, project, chatId]);

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
    if (files.length > 0) void handleUploadFiles(files);
  }, [handleUploadFiles]);

  const removePendingAttachment = useCallback((id: string) => {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const handleSend = useCallback(() => {
    if ((!input.trim() && pendingAttachments.length === 0) || streaming || !chat) return;
    const content = input.trim();
    const attIds = pendingAttachments.map((a) => a.id);
    const attSnapshots = [...pendingAttachments];
    setInput("");
    setPendingAttachments([]);
    setStreaming(true);
    setStreamingAgents([]);
    const userMsg: ChatMessage = { id: `local_${Date.now()}`, role: "user", content, agent: null, attachments: attSnapshots, artifacts: [], created_at: new Date().toISOString() };
    setChat((prev) => prev ? { ...prev, messages: [...prev.messages, userMsg] } : prev);
    const { onEvent, onDone } = buildStreamHandlers();
    abortRef.current = askStream(project, chatId, content, onEvent, onDone, attIds.length > 0 ? attIds : undefined);
  }, [input, pendingAttachments, streaming, chat, project, chatId, buildStreamHandlers]);

  const handleStop = useCallback(() => {
    void stopTask(project, chatId);
    abortRef.current?.abort();
    abortRef.current = null;
  }, [project, chatId]);

  const handleModeChange = useCallback((mode: ChatMode) => {
    if (!chat) return;
    updateChat(project, chatId, { mode }).then(setChat);
  }, [chat, project, chatId]);

  const handleSwap = useCallback(() => {
    if (!chat) return;
    const next = chat.first_agent === "claude" ? "codex" : "claude";
    updateChat(project, chatId, { first_agent: next }).then(setChat);
  }, [chat, project, chatId]);

  if (!chat) {
    return <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading chat...</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <ModeSwapBar mode={chat.mode} firstAgent={chat.first_agent} onModeChange={handleModeChange} onSwap={handleSwap} disabled={streaming} />

      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {chat.messages.length === 0 && streamingAgents.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-slate-400">Ask about this project or request code changes</p>
          </div>
        )}
        {chat.messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} project={project} fileId={fileId} chatId={chatId} onArtifactApplied={onArtifactApplied} />
        ))}
        {streamingAgents.map((sa) => (
          <StreamingBubble key={`stream-${sa.agent}`} agent={sa} />
        ))}
      </div>

      <div
        className="border-t border-slate-200 p-3"
        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
        onDrop={handleDrop}
      >
        {/* Pending attachment previews */}
        {pendingAttachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {pendingAttachments.map((att) => (
              <div key={att.id} className="relative group">
                <img
                  src={attachmentUrl(project, chatId, att.id)}
                  alt={att.filename}
                  className="h-12 w-12 rounded-lg object-cover border border-slate-200"
                />
                <button
                  type="button"
                  className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-slate-700 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                  onClick={() => removePendingAttachment(att.id)}
                >
                  <X size={8} />
                </button>
              </div>
            ))}
            {uploading && (
              <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50">
                <div className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
              </div>
            )}
          </div>
        )}
        <div className="flex gap-2">
          {/* Upload button */}
          <button
            type="button"
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 disabled:opacity-40 transition-colors"
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming || uploading}
            title="Attach image"
          >
            <ImagePlus size={14} />
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
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-400/30 resize-none overflow-hidden"
            placeholder="Discuss this project or request code changes... (Enter to send)"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
            }}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            onPaste={handlePaste}
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
            <button type="button" className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-40" onClick={handleSend} disabled={!input.trim() && pendingAttachments.length === 0}>
              <Send size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right sidebar (project-level chats)
// ---------------------------------------------------------------------------

function RightSidebar({
  project,
  filePath,
  onCollapse,
  onResizeMouseDown,
  width,
  onArtifactApplied,
}: {
  project: string;
  filePath: string;  // currently open file (may be empty)
  onCollapse: () => void;
  onResizeMouseDown: (e: React.MouseEvent) => void;
  width: number;
  onArtifactApplied: () => void;
}) {
  const [tab, setTab] = useState<"chat" | "notes" | "details">("chat");
  const [activeChatId, setActiveChatId] = useState("");
  const [chats, setChats] = useState<ChatListItem[]>([]);
  const [noteContent, setNoteContent] = useState("");
  const [noteDirty, setNoteDirty] = useState(false);
  const [noteSaving, setNoteSaving] = useState(false);
  const [noteMode, setNoteMode] = useState<"edit" | "preview">("edit");
  const noteContentRef = useRef("");
  const noteDirtyRef = useRef(false);
  const noteSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshChats = useCallback(() => {
    if (!project) return;
    listChats(project).then(setChats).catch(() => {});
  }, [project]);

  useEffect(() => {
    refreshChats();
  }, [project, refreshChats]);

  useEffect(() => {
    if (!activeChatId && chats.length > 0) setActiveChatId(chats[0].chat_id);
  }, [activeChatId, chats]);

  const handleCreateChat = useCallback(async () => {
    const chat = await createChat(project);
    setActiveChatId(chat.chat_id);
    refreshChats();
  }, [project, refreshChats]);

  const handleDeleteChat = useCallback(async (chatId: string) => {
    await deleteChat(project, chatId);
    if (activeChatId === chatId) setActiveChatId("");
    refreshChats();
  }, [project, activeChatId, refreshChats]);

  return (
    <aside className="relative flex-shrink-0 border-l border-slate-200 bg-white" style={{ width: `${width}px` }}>
      {/* Drag handle for resizing */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/40 active:bg-blue-500/50 z-10"
        onMouseDown={onResizeMouseDown}
      />

      {/* Tab bar — matches Paper's RightSidebar */}
      <div className="flex h-14 items-center border-b border-slate-200">
        <button
          onClick={onCollapse}
          className="flex h-6 w-6 items-center justify-center rounded hover:bg-slate-100 ml-2"
          title="Collapse panel"
          type="button"
        >
          <ChevronRight size={14} />
        </button>
        <button
          type="button"
          className={`flex h-full flex-1 items-center justify-center gap-1.5 text-xs font-semibold ${
            tab === "chat" ? "border-b-2 border-slate-800 text-slate-800" : "text-slate-400 hover:text-slate-600"
          }`}
          onClick={() => setTab("chat")}
        >
          <MessageSquare size={14} />
          Chat
        </button>
        <button
          type="button"
          className={`flex h-full flex-1 items-center justify-center gap-1.5 text-xs font-semibold ${
            tab === "notes" ? "border-b-2 border-slate-800 text-slate-800" : "text-slate-400 hover:text-slate-600"
          }`}
          onClick={() => setTab("notes")}
        >
          <NotebookPen size={14} />
          Notes
        </button>
        <button
          type="button"
          className={`flex h-full flex-1 items-center justify-center gap-1.5 text-xs font-semibold ${
            tab === "details" ? "border-b-2 border-slate-800 text-slate-800" : "text-slate-400 hover:text-slate-600"
          }`}
          onClick={() => setTab("details")}
        >
          <Settings size={14} />
          Details
        </button>
      </div>

      <div className="h-[calc(100%-3.5rem)]">
        {tab === "chat" ? (
          activeChatId ? (
            <div className="flex h-full flex-col">
              {/* Chat switcher bar */}
              <div className="flex items-center gap-1 border-b border-slate-200 px-2 py-1.5">
                <button
                  type="button"
                  className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
                  onClick={() => setActiveChatId("")}
                >
                  ← All chats
                </button>
                <span className="flex-1 truncate text-center text-xs font-medium text-slate-600">
                  {chats.find((c) => c.chat_id === activeChatId)?.title ?? "Chat"}
                </span>
              </div>
              <div className="flex-1 overflow-hidden">
                <CodeChatPanel project={project} filePath={filePath} chatId={activeChatId} onArtifactApplied={onArtifactApplied} />
              </div>
            </div>
          ) : (
            <ChatList
              chats={chats}
              activeChatId={activeChatId}
              onSelect={setActiveChatId}
              onCreate={handleCreateChat}
              onDelete={handleDeleteChat}
            />
          )
        ) : tab === "notes" ? (
          <div className="flex h-full flex-col">
            {/* Notes toolbar — matches Paper's NoteEditor */}
            <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className={`rounded px-2 py-1 text-xs font-medium ${noteMode === "edit" ? "bg-slate-200 text-slate-800" : "text-slate-500 hover:bg-slate-100"}`}
                  onClick={() => setNoteMode("edit")}
                >
                  <Edit3 size={12} className="inline mr-1" />
                  Edit
                </button>
                <button
                  type="button"
                  className={`rounded px-2 py-1 text-xs font-medium ${noteMode === "preview" ? "bg-slate-200 text-slate-800" : "text-slate-500 hover:bg-slate-100"}`}
                  onClick={() => setNoteMode("preview")}
                >
                  <Eye size={12} className="inline mr-1" />
                  Preview
                </button>
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              {noteMode === "edit" ? (
                <textarea
                  className="h-full w-full resize-none border-0 bg-white p-4 text-sm leading-relaxed text-slate-800 focus:outline-none font-mono"
                  value={noteContent}
                  onChange={(e) => setNoteContent(e.target.value)}
                  placeholder="Write your notes here... (Markdown supported)"
                  spellCheck={false}
                />
              ) : (
                <div className="h-full overflow-y-auto p-4">
                  {noteContent ? (
                    <Markdown>{noteContent}</Markdown>
                  ) : (
                    <p className="text-sm text-slate-400 italic">No notes yet.</p>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="h-full overflow-y-auto p-4">
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">File Info</h4>
            {filePath ? (
              <dl className="space-y-2 text-xs">
                <div className="grid grid-cols-[70px_minmax(0,1fr)] gap-2">
                  <dt className="text-slate-500">Path</dt>
                  <dd className="text-slate-800 break-all">{filePath}</dd>
                </div>
                <div className="grid grid-cols-[70px_minmax(0,1fr)] gap-2">
                  <dt className="text-slate-500">Language</dt>
                  <dd className="text-slate-800">{filePath.split(".").pop() ?? "-"}</dd>
                </div>
              </dl>
            ) : (
              <p className="text-xs text-slate-400">No file selected.</p>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Main CodePage
// ---------------------------------------------------------------------------

export default function CodePage({ project }: { project: string }) {
  const containerRef = useRef<HTMLElement>(null);
  const [tree, setTree] = useState<CodeTreeEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [language, setLanguage] = useState("");

  const leftResize = useDragResize(300, 200, "left", () => {
    const total = containerRef.current?.clientWidth ?? window.innerWidth;
    return Math.max(200, total - (rightOpen ? rightResize.width : 0));
  });
  const rightResize = useDragResize(360, 240, "right", () => {
    const total = containerRef.current?.clientWidth ?? window.innerWidth;
    return Math.max(240, total - (leftOpen ? leftResize.width : 0));
  });

  // Clamp widths on window resize
  useEffect(() => {
    const clampWidths = () => {
      const total = containerRef.current?.clientWidth ?? window.innerWidth;
      if (leftOpen && rightOpen && leftResize.width + rightResize.width > total) {
        const overflow = leftResize.width + rightResize.width - total;
        const shrinkRight = Math.min(overflow, Math.max(0, rightResize.width - 240));
        rightResize.setWidth(rightResize.width - shrinkRight);
        const remainingOverflow = overflow - shrinkRight;
        if (remainingOverflow > 0) {
          leftResize.setWidth((width) => Math.max(Math.min(200, total - 240), width - remainingOverflow));
        }
      }
    };

    clampWidths();
    window.addEventListener("resize", clampWidths);
    return () => window.removeEventListener("resize", clampWidths);
  }, [leftOpen, rightOpen, leftResize.width, rightResize.width, leftResize.setWidth, rightResize.setWidth]);

  const refreshTree = useCallback(() => {
    if (!project) return;
    getTree(project).then(setTree).catch(() => {});
  }, [project]);

  useEffect(() => {
    refreshTree();
    setSelectedPath("");
  }, [refreshTree]);

  // Detect language from extension
  useEffect(() => {
    if (!selectedPath) { setLanguage(""); return; }
    const ext = selectedPath.split(".").pop()?.toLowerCase() ?? "";
    const langMap: Record<string, string> = {
      py: "python", js: "javascript", jsx: "javascript",
      ts: "typescript", tsx: "typescript",
      rs: "rust", go: "go", java: "java",
      c: "c", cpp: "cpp", h: "c", hpp: "cpp",
      md: "markdown", json: "json", yaml: "yaml", yml: "yaml", toml: "yaml",
      html: "html", css: "css", scss: "css",
      sql: "sql", xml: "xml",
      sh: "bash", bash: "bash",
      tex: "latex",
    };
    setLanguage(langMap[ext] ?? "");
  }, [selectedPath]);

  return (
    <section ref={containerRef} className="relative flex flex-1 min-h-0 overflow-hidden">
      {/* Left expand button (visible when collapsed) */}
      {!leftOpen && (
        <button
          onClick={() => setLeftOpen(true)}
          className="absolute left-0 top-3 z-20 flex h-7 w-7 items-center justify-center rounded-r-md border border-l-0 border-slate-300 bg-white shadow-sm hover:bg-slate-100"
          title="Show files"
          type="button"
        >
          <ChevronRight size={16} />
        </button>
      )}

      {/* Left sidebar: file tree */}
      {leftOpen && (
        <FileTree
          tree={tree}
          selectedPath={selectedPath}
          onSelect={setSelectedPath}
          onCollapse={() => setLeftOpen(false)}
          onResizeMouseDown={leftResize.onMouseDown}
          width={leftResize.width}
        />
      )}

      {/* Center: code viewer/editor */}
      <div className="flex-1 min-w-0 bg-white">
        {selectedPath ? (
          <CodeEditor
            project={project}
            filePath={selectedPath}
            language={language}
            onContentSaved={refreshTree}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            Select a file from the tree
          </div>
        )}
      </div>

      {/* Right sidebar: chat */}
      {rightOpen && (
        <RightSidebar
          project={project}
          filePath={selectedPath}
          onCollapse={() => setRightOpen(false)}
          onResizeMouseDown={rightResize.onMouseDown}
          width={rightResize.width}
          onArtifactApplied={refreshTree}
        />
      )}

      {/* Right expand button (visible when collapsed) */}
      {!rightOpen && (
        <button
          onClick={() => setRightOpen(true)}
          className="absolute right-0 top-3 z-20 flex h-7 w-7 items-center justify-center rounded-l-md border border-r-0 border-slate-300 bg-white shadow-sm hover:bg-slate-100"
          title="Show details"
          type="button"
        >
          <ChevronLeft size={16} />
        </button>
      )}
    </section>
  );
}
