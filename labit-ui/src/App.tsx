import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { BookOpen, ChevronDown, ChevronLeft, ChevronRight, Edit3, Eye, FileText, FolderOpen, MessageSquare, NotebookPen, Plus, RefreshCw, Search, Settings, Star, Tag, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import {
  getNote,
  listArtifacts,
  listPapers,
  listProjects,
  PaperRecord,
  prefetchReaderManifest,
  saveNote,
  togglePaperStar,
  updatePaperStatus,
  updatePaperTags,
} from "./api/client";
import { createChat, deleteChat, listChats, type ChatListItem } from "./api/chat";
import PaperImageReader from "./components/PaperImageReader";
import ChatList from "./components/ChatList";
import ChatPanel from "./components/ChatPanel";
import TodoPage from "./todo/TodoPage";
import ChatPage from "./chat/ChatPage";
import DocsPage from "./docs/DocsPage";
import CodePage from "./code/CodePage";
import ComputePage from "./compute/ComputePage";
import {
  IconBook, IconPaper, IconCheckSquare, IconFlask, IconTerminal, IconCapture,
  IconRefresh, IconCloud, IconChevronDown, IconChat, IconFileText, IconCode, IconCompute,
} from "./todo/icons";

type ModuleTab = "papers" | "docs" | "code" | "todos" | "chat" | "compute";

interface UiState {
  project: string;
  selectedPaperId: string;
  activeTab: ModuleTab;
  chatActiveChatId: string;
  setProject: (project: string) => void;
  setSelectedPaperId: (paperId: string) => void;
  setActiveTab: (tab: ModuleTab) => void;
  setChatActiveChatId: (chatId: string) => void;
}

const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      project: "",
      selectedPaperId: "",
      activeTab: "papers" as ModuleTab,
      chatActiveChatId: "",
      setProject: (project) => set({ project, selectedPaperId: "" }),
      setSelectedPaperId: (paperId) => set({ selectedPaperId: paperId }),
      setActiveTab: (activeTab) => set({ activeTab }),
      setChatActiveChatId: (chatActiveChatId) => set({ chatActiveChatId }),
    }),
    { name: "labit-ui-state" }
  )
);

const NAV_TABS: { id: ModuleTab; label: string; Icon: React.ComponentType<any> }[] = [
  { id: "papers", label: "Papers", Icon: IconPaper },
  { id: "docs", label: "Docs", Icon: IconFileText },
  { id: "code", label: "Code", Icon: IconCode },
  { id: "chat", label: "Chat", Icon: IconChat },
  { id: "todos", label: "Todos", Icon: IconCheckSquare },
  { id: "compute", label: "Compute", Icon: IconCompute },
];

export function App() {
  const { project, selectedPaperId, activeTab, chatActiveChatId, setProject, setSelectedPaperId, setActiveTab, setChatActiveChatId } = useUiStore();
  const [spinning, setSpinning] = useState(false);
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const projects = projectsQuery.data?.projects ?? [];

  useEffect(() => {
    if (projects.length > 0 && (!project || !projects.includes(project))) {
      setProject(projectsQuery.data?.active_project ?? projects[0]);
    }
  }, [project, projects, projectsQuery.data?.active_project, setProject]);

  const papersQuery = useQuery({
    queryKey: ["papers", project],
    queryFn: () => listPapers(project),
    enabled: Boolean(project),
    refetchInterval: 5000,
  });
  const papers = papersQuery.data ?? [];
  const selectedPaper = papers.find((paper) => paper.id === selectedPaperId) ?? papers[0];

  useEffect(() => {
    if (selectedPaper && selectedPaperId !== selectedPaper.id) {
      setSelectedPaperId(selectedPaper.id);
    }
  }, [selectedPaper, selectedPaperId, setSelectedPaperId]);

  useEffect(() => {
    if (project && selectedPaper?.local_pdf_path) {
      prefetchReaderManifest(project, selectedPaper.id);
    }
  }, [project, selectedPaper?.id, selectedPaper?.local_pdf_path]);

  const refresh = () => {
    setSpinning(true);
    void projectsQuery.refetch();
    void papersQuery.refetch();
    window.setTimeout(() => setSpinning(false), 650);
  };

  return (
    <main className="h-screen flex flex-col bg-[var(--bg,#eef3fb)] text-[var(--text,#0d1b2e)] font-[var(--font-sans,'IBM_Plex_Sans',system-ui,sans-serif)]">
      {/* NavBar */}
      <header className="flex-none flex items-center justify-between gap-3 px-3 lg:px-[22px] h-[52px] bg-[var(--surface,#fff)] border-b border-[var(--border,#e1e9f4)]">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-[9px]">
            <span className="grid place-items-center w-[30px] h-[30px] rounded-lg bg-[var(--ink,#0e72ed)] text-[var(--ink-text,#fff)]"><IconBook size={19} stroke={2} /></span>
            <span className="font-bold text-[18px] tracking-[0.08em]">LABIT</span>
          </div>
          <nav aria-label="Modules" className="flex items-center gap-0.5 p-[3px] rounded-[11px] bg-[var(--surface-3,#e9f1fb)]">
            {NAV_TABS.map((t) => {
              const active = t.id === activeTab;
              return (
                <button key={t.id} onClick={() => setActiveTab(t.id)} aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-[5px] py-[6px] px-[10px] rounded-lg text-[13px] font-medium transition-colors ${active ? "bg-[var(--ink,#0e72ed)] text-[var(--ink-text,#fff)] shadow-[0_1px_2px_rgba(0,0,0,.18)]" : "text-[var(--muted,#5a6b82)] hover:text-[var(--text,#0d1b2e)] hover:bg-[var(--surface,#fff)]"}`}>
                  <t.Icon size={14} stroke={1.9} />
                  <span className="whitespace-nowrap hidden sm:inline">{t.label}</span>
                </button>
              );
            })}
          </nav>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <div title="Local-first · saved to vault/" className="hidden lg:flex items-center gap-1.5 text-[var(--faint,#97a5ba)] text-[12.5px]">
            <IconCloud size={15} stroke={1.9} className="text-[var(--accent,#0e72ed)] opacity-80" />
            <span className="font-mono whitespace-nowrap">saved to vault</span>
          </div>
          <select
            className="h-8 rounded-[9px] border border-[var(--border,#e1e9f4)] bg-[var(--surface-2,#f4f8fd)] px-2 text-[13px] font-medium max-w-[140px]"
            value={project}
            onChange={(event) => setProject(event.target.value)}
          >
            {projects.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <button onClick={refresh} title="Refresh"
            className="flex items-center justify-center w-8 h-8 rounded-[9px] bg-[var(--ink,#0e72ed)] text-[var(--ink-text,#fff)] hover:opacity-90">
            <IconRefresh size={15} stroke={1.9} className={spinning ? "animate-[spin_0.65s_ease]" : ""} />
          </button>
        </div>
      </header>

      {/* Module content */}
      {activeTab === "chat" ? (
        <ChatPage project={project} activeChatId={chatActiveChatId} onActiveChatIdChange={setChatActiveChatId} />
      ) : activeTab === "code" ? (
        <CodePage project={project} />
      ) : activeTab === "docs" ? (
        <DocsPage project={project} />
      ) : activeTab === "compute" ? (
        <ComputePage project={project} />
      ) : activeTab === "todos" ? (
        <TodoPage project={project} />
      ) : (
        <ContentArea
          papers={papers}
          project={project}
          selectedPaper={selectedPaper}
          isLoading={papersQuery.isLoading}
          onSelect={setSelectedPaperId}
        />
      )}
    </main>
  );
}

/** Hook for drag-to-resize a sidebar panel */
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

function ContentArea({
  papers,
  project,
  selectedPaper,
  isLoading,
  onSelect,
}: {
  papers: PaperRecord[];
  project: string;
  selectedPaper?: PaperRecord;
  isLoading: boolean;
  onSelect: (paperId: string) => void;
}) {
  const containerRef = useRef<HTMLElement>(null);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);

  const leftResize = useDragResize(360, 200, "left", () => {
    const total = containerRef.current?.clientWidth ?? window.innerWidth;
    return Math.max(200, total - (rightOpen ? rightResize.width : 0));
  });
  const rightResize = useDragResize(360, 240, "right", () => {
    const total = containerRef.current?.clientWidth ?? window.innerWidth;
    return Math.max(240, total - (leftOpen ? leftResize.width : 0));
  });

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
      if (leftOpen && leftResize.width > total) {
        leftResize.setWidth(total);
      }
      if (rightOpen && rightResize.width > total) {
        rightResize.setWidth(total);
      }
    };

    clampWidths();
    window.addEventListener("resize", clampWidths);
    return () => window.removeEventListener("resize", clampWidths);
  }, [leftOpen, rightOpen, leftResize.width, rightResize.width, leftResize.setWidth, rightResize.setWidth]);

  return (
    <section ref={containerRef} className="relative flex flex-1 min-h-0 overflow-hidden">
      {/* Left expand button (visible when collapsed) */}
      {!leftOpen && (
        <button
          onClick={() => setLeftOpen(true)}
          className="absolute left-0 top-3 z-20 flex h-7 w-7 items-center justify-center rounded-r-md border border-l-0 border-slate-300 bg-white shadow-sm hover:bg-slate-100"
          title="Show papers"
          type="button"
        >
          <ChevronRight size={16} />
        </button>
      )}

      {leftOpen && (
        <PaperList
          papers={papers}
          project={project}
          selectedPaperId={selectedPaper?.id ?? ""}
          isLoading={isLoading}
          onSelect={onSelect}
          onCollapse={() => setLeftOpen(false)}
          onResizeMouseDown={leftResize.onMouseDown}
          width={leftResize.width}
        />
      )}
      <div className="min-w-0 flex-1">
        <PaperDetail project={project} paper={selectedPaper} showSidebar={false} />
      </div>
      {rightOpen && (
        <RightSidebar
          project={project}
          paper={selectedPaper}
          onCollapse={() => setRightOpen(false)}
          onResizeMouseDown={rightResize.onMouseDown}
          width={rightResize.width}
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

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* ── Status glyphs (matching SidebarC spec) ── */
function StatusGlyph({ status }: { status: string }) {
  if (status === "read")
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M8.5 12.2l2.5 2.5 4.5-5" />
      </svg>
    );
  if (status === "reading")
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none" />
      </svg>
    );
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

const STATUS_META: Record<string, { label: string; color: string; soft: string; ink: string; pill: string }> = {
  reading: { label: "Reading", color: "var(--lb-blue)", soft: "var(--lb-blue-soft)", ink: "var(--lb-blue)", pill: "var(--lb-blue)" },
  unread:  { label: "Unread",  color: "var(--lb-faintest)", soft: "#f6f6f7", ink: "#52525b", pill: "#8a8a93" },
  read:    { label: "Read",    color: "var(--lb-green)", soft: "var(--lb-green-soft)", ink: "var(--lb-green)", pill: "var(--lb-green)" },
};

function getPaperStatus(paper: PaperRecord): "reading" | "unread" | "read" {
  const s = paper.status;
  if (s === "reading" || s === "read") return s;
  return "unread";
}

const STATUS_CYCLE: Record<string, "reading" | "unread" | "read"> = {
  unread: "reading",
  reading: "read",
  read: "unread",
};

function PaperList({
  papers,
  project,
  selectedPaperId,
  isLoading,
  onSelect,
  onCollapse,
  onResizeMouseDown,
  width,
}: {
  papers: PaperRecord[];
  project: string;
  selectedPaperId: string;
  isLoading: boolean;
  onSelect: (paperId: string) => void;
  onCollapse: () => void;
  onResizeMouseDown: (e: React.MouseEvent) => void;
  width: number;
}) {
  const [mode, setMode] = useState<"time" | "topic" | "status">("status");
  const [stOpen, setStOpen] = useState<Record<string, boolean>>({ reading: true, unread: true, read: false });
  const [searchQuery, setSearchQuery] = useState("");
  const [starredOnly, setStarredOnly] = useState(false);

  const queryClient = useQueryClient();

  const warmPaper = (paper: PaperRecord) => {
    if (!project || !paper.local_pdf_path) return;
    prefetchReaderManifest(project, paper.id);
  };

  const cycleStatus = useCallback(
    (paper: PaperRecord) => {
      if (!project) return;
      const next = STATUS_CYCLE[getPaperStatus(paper)];
      // Optimistic update
      queryClient.setQueryData<PaperRecord[]>(["papers", project], (old) =>
        old?.map((p) => (p.id === paper.id ? { ...p, status: next } : p)),
      );
      void updatePaperStatus(project, paper.id, next).catch(() => {
        // Rollback on failure
        void queryClient.invalidateQueries({ queryKey: ["papers", project] });
      });
    },
    [project, queryClient],
  );

  const toggleStar = useCallback(
    (paper: PaperRecord) => {
      if (!project) return;
      queryClient.setQueryData<PaperRecord[]>(["papers", project], (old) =>
        old?.map((p) => (p.id === paper.id ? { ...p, starred: !p.starred } : p)),
      );
      void togglePaperStar(project, paper.id).catch(() => {
        void queryClient.invalidateQueries({ queryKey: ["papers", project] });
      });
    },
    [project, queryClient],
  );

  // Filter by search and starred
  const filtered = useMemo(() => {
    let result = papers;
    if (starredOnly) {
      result = result.filter((p) => p.starred);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.authors.join(", ").toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q) ||
          (p.tags ?? []).some((t) => t.toLowerCase().includes(q)),
      );
    }
    return result;
  }, [papers, searchQuery, starredOnly]);

  // Render a paper card
  const Card = useCallback(
    (p: PaperRecord, opts: { chips?: boolean; hideDot?: boolean } = {}) => {
      const status = getPaperStatus(p);
      return (
        <button
          key={p.id}
          type="button"
          className={`lb-card s-${status}${selectedPaperId === p.id ? " sel" : ""}`}
          onClick={() => onSelect(p.id)}
          onFocus={() => warmPaper(p)}
          onMouseEnter={() => warmPaper(p)}
        >
          <div className="lb-ct">{p.title}</div>
          <div className="lb-ca">{p.authors.join(", ")}</div>
          {opts.chips && (p.tags ?? []).length > 0 && (
            <div className="lb-chips">
              {(p.tags ?? []).map((t) => (
                <span key={t} className="lb-chip">{t}</span>
              ))}
            </div>
          )}
          <div className="lb-cf">
            {!opts.hideDot && (
              <span
                className={`lb-dot ${status}`}
                title={`${STATUS_META[status].label} — click to change`}
                role="button"
                tabIndex={-1}
                onClick={(e) => {
                  e.stopPropagation();
                  cycleStatus(p);
                }}
              />
            )}
            <span className="lb-cid">{p.arxiv_id ? `arXiv:${p.arxiv_id}` : p.id}</span>
            <span className="lb-cf-sp" />
            <span
              className={`lb-star${p.starred ? " starred" : ""}`}
              title={p.starred ? "Unstar" : "Star as quality paper"}
              role="button"
              tabIndex={-1}
              onClick={(e) => {
                e.stopPropagation();
                toggleStar(p);
              }}
            >
              <Star size={12} />
            </span>
          </div>
        </button>
      );
    },
    [selectedPaperId, onSelect, project, cycleStatus, toggleStar],
  );

  // Build body based on mode
  let body: React.ReactNode;

  if (mode === "topic") {
    // Group by tags; papers without tags go into "Untagged"
    const topicMap = new Map<string, PaperRecord[]>();
    for (const p of filtered) {
      const tags = p.tags ?? [];
      if (tags.length === 0) {
        if (!topicMap.has("Untagged")) topicMap.set("Untagged", []);
        topicMap.get("Untagged")!.push(p);
      } else {
        for (const t of tags) {
          if (!topicMap.has(t)) topicMap.set(t, []);
          topicMap.get(t)!.push(p);
        }
      }
    }
    const topics = [...topicMap.keys()].sort();
    body = topics.map((t) => {
      const ps = topicMap.get(t)!;
      return (
        <div key={t}>
          <div className="lb-topicgroup">
            <span className="tg-name">{t}</span>
            <span className="tg-n">{ps.length}</span>
          </div>
          {ps.map((p) => Card(p, { chips: true }))}
        </div>
      );
    });
  } else if (mode === "status") {
    const statuses: Array<"reading" | "unread" | "read"> = ["reading", "unread", "read"];
    body = statuses.map((s) => {
      const ps = filtered.filter((p) => getPaperStatus(p) === s);
      if (ps.length === 0) return null;
      const o = stOpen[s] ?? true;
      const M = STATUS_META[s];
      return (
        <div key={s}>
          <button
            type="button"
            className={`lb-stathead${o ? "" : " collapsed"}`}
            style={{ background: M.soft, color: M.ink }}
            onClick={() => setStOpen((v) => ({ ...v, [s]: !v[s] }))}
          >
            <span className="sg-glyph" style={{ color: M.color }}>
              <StatusGlyph status={s} />
            </span>
            <span className="sg-name">{M.label}</span>
            <span className="sg-n" style={{ color: "#fff", background: M.pill }}>{ps.length}</span>
            <span className="sg-chev">
              <ChevronDown size={13} />
            </span>
          </button>
          {o && ps.map((p) => Card(p))}
        </div>
      );
    });
  } else {
    // Time view — flat date dividers grouped by date
    const dateMap = new Map<string, PaperRecord[]>();
    for (const p of filtered) {
      const date = p.added_at?.slice(0, 10) || "Unknown";
      if (!dateMap.has(date)) dateMap.set(date, []);
      dateMap.get(date)!.push(p);
    }
    const dates = [...dateMap.keys()].sort().reverse();
    body = dates.map((d) => {
      const ps = dateMap.get(d)!;
      // Format: "May 30" style
      const parts = d.split("-");
      const label =
        parts.length === 3
          ? `${MONTH_NAMES[parseInt(parts[1], 10) - 1] || parts[1]} ${parseInt(parts[2], 10)}`
          : d;
      return (
        <div key={d}>
          <div className="lb-divider">
            <span className="lb-dlabel">{label}</span>
            <span className="lb-dline" />
            <span className="lb-dnum">{ps.length}</span>
          </div>
          {ps.map((p) => Card(p))}
        </div>
      );
    });
  }

  return (
    <aside className="lb relative flex-shrink-0" style={{ width: `${width}px`, borderRight: "1px solid var(--lb-line)" }}>
      {/* Header */}
      <div className="lb-head">
        <h2>Papers</h2>
        <span className="lb-count">{filtered.length}</span>
        <button
          type="button"
          className={`lb-iconbtn${starredOnly ? " lb-filter-active" : ""}`}
          title={starredOnly ? "Show all papers" : "Show starred only"}
          onClick={() => setStarredOnly((v) => !v)}
        >
          <Star size={15} fill={starredOnly ? "#e0a800" : "none"} color={starredOnly ? "#e0a800" : "currentColor"} />
        </button>
        <button type="button" className="lb-iconbtn" title="Collapse" onClick={onCollapse}>
          <ChevronLeft size={15} />
        </button>
      </div>

      {/* Group-by switch */}
      <div className="lb-gby">
        <span className="lb-gby-label">Group by</span>
        <div className="lb-gby-opts">
          {(
            [
              ["time", "Time"],
              ["topic", "Topic"],
              ["status", "Status"],
            ] as const
          ).map(([k, l]) => (
            <button key={k} type="button" className={mode === k ? "on" : ""} onClick={() => setMode(k)}>
              {l}
            </button>
          ))}
        </div>
      </div>

      {/* Drag handle */}
      <div
        className="absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/40 active:bg-blue-500/50 z-10"
        onMouseDown={onResizeMouseDown}
      />

      {/* Scroll area */}
      <div className="lb-scroll">
        {isLoading ? <EmptyState label="Loading papers" /> : null}
        {!isLoading && filtered.length === 0 ? (
          <EmptyState label={starredOnly ? "No starred papers" : "No papers in this project"} />
        ) : null}
        {body}
      </div>

      {/* Footer search */}
      <div className="lb-foot">
        <Search size={14} />
        <input
          placeholder="Search papers & folders"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
      </div>
    </aside>
  );
}

function PaperDetail({ project, paper }: { project: string; paper?: PaperRecord; showSidebar?: boolean }) {
  if (!paper) {
    return <EmptyState label="Select a paper" />;
  }

  return (
    <div className="min-w-0 overflow-y-auto p-5">
      <div className="mb-4">
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{paper.id}</div>
        <h2 className="mt-1 max-w-5xl text-2xl font-semibold leading-tight">{paper.title}</h2>
        <p className="mt-2 max-w-4xl text-sm text-slate-600">{paper.authors.join(", ")}</p>
      </div>
      {paper.local_pdf_path ? (
        <PaperImageReader project={project} paper={paper} />
      ) : (
        <EmptyState label="No PDF saved for this paper" />
      )}
    </div>
  );
}

function RightSidebar({
  project,
  paper,
  onCollapse,
  onResizeMouseDown,
  width,
}: {
  project: string;
  paper?: PaperRecord;
  onCollapse: () => void;
  onResizeMouseDown: (e: React.MouseEvent) => void;
  width: number;
}) {
  const [tab, setTab] = useState<"chat" | "notes" | "details">("chat");
  const [activeChatId, setActiveChatId] = useState("");
  const queryClient = useQueryClient();

  const artifactsQuery = useQuery({
    queryKey: ["artifacts", project, paper?.id],
    queryFn: () => listArtifacts(project, paper?.id ?? ""),
    enabled: Boolean(project && paper?.id),
  });

  const chatsQuery = useQuery({
    queryKey: ["chats", project, paper?.id],
    queryFn: () => listChats(project, paper?.id ?? ""),
    enabled: Boolean(project && paper?.id),
  });
  const chats = chatsQuery.data ?? [];

  // Auto-select first chat when list loads
  useEffect(() => {
    if (!activeChatId && chats.length > 0) {
      setActiveChatId(chats[0].chat_id);
    }
  }, [activeChatId, chats]);

  // Reset active chat when paper changes
  useEffect(() => {
    setActiveChatId("");
  }, [paper?.id]);

  const handleCreateChat = useCallback(async () => {
    if (!project || !paper?.id) return;
    const chat = await createChat(project, paper.id);
    setActiveChatId(chat.chat_id);
    void queryClient.invalidateQueries({ queryKey: ["chats", project, paper.id] });
  }, [project, paper?.id, queryClient]);

  const handleDeleteChat = useCallback(async (chatId: string) => {
    if (!project || !paper?.id) return;
    await deleteChat(project, paper.id, chatId);
    if (activeChatId === chatId) setActiveChatId("");
    void queryClient.invalidateQueries({ queryKey: ["chats", project, paper.id] });
  }, [project, paper?.id, activeChatId, queryClient]);

  return (
    <aside className="relative flex-shrink-0 border-l border-slate-200 bg-white" style={{ width: `${width}px` }}>
      {/* Drag handle for resizing */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/40 active:bg-blue-500/50 z-10"
        onMouseDown={onResizeMouseDown}
      />

      {/* Tab bar */}
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
          paper ? (
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
                  <ChatPanel
                    project={project}
                    paperId={paper.id}
                    chatId={activeChatId}
                  />
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
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">Select a paper</div>
          )
        ) : tab === "notes" ? (
          paper ? (
            <NoteEditor project={project} paperId={paper.id} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-slate-400">Select a paper</div>
          )
        ) : (
          <div className="h-full overflow-y-auto">
            {paper ? (
              <>
                <div className="border-b border-slate-200 p-4">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Tags</h4>
                  <TagEditor project={project} paper={paper} />
                </div>
                <div className="border-b border-slate-200 p-4">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Metadata</h4>
                  <dl className="space-y-2 text-xs">
                    <MetadataRow label="Authors" value={paper.authors.join(", ")} />
                    <MetadataRow label="Submitted" value={paper.submitted_date || "-"} />
                    <MetadataRow label="Source" value={paper.source} />
                    <MetadataRow label="arXiv" value={paper.arxiv_id} />
                    <MetadataRow label="Added" value={formatVpsTimestamp(paper.added_at)} />
                  </dl>
                </div>
                <div className="border-b border-slate-200 p-4">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Abstract</h4>
                  <p className="text-sm leading-6 text-slate-700">{paper.abstract || "No abstract saved."}</p>
                </div>
                <div className="p-4">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                    <FolderOpen size={15} />
                    <span>Artifacts</span>
                  </div>
                  <div className="space-y-2">
                    {(artifactsQuery.data ?? []).map((artifact) => (
                      <div key={artifact.path} className="flex items-center gap-2 rounded-md border border-slate-200 p-2 text-sm">
                        <FileText size={15} />
                        <span className="min-w-0 truncate">{artifact.relative_path}</span>
                      </div>
                    ))}
                    {!artifactsQuery.isLoading && (artifactsQuery.data ?? []).length === 0 ? (
                      <p className="text-sm text-slate-500">No artifacts yet.</p>
                    ) : null}
                  </div>
                </div>
              </>
            ) : null}
          </div>
        )}
      </div>
    </aside>
  );
}

/** Format an ISO 8601 timestamp as "YYYY-MM-DD HH:MM" preserving the original
 *  (VPS) time — no browser timezone conversion. */
function formatVpsTimestamp(iso: string): string {
  if (!iso) return "-";
  // ISO format: "2026-05-30T14:30:00+00:00" — extract date and time parts directly
  const match = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!match) return iso;
  return `${match[1]} ${match[2]}`;
}

function TagEditor({ project, paper }: { project: string; paper: PaperRecord }) {
  const [input, setInput] = useState("");
  const queryClient = useQueryClient();
  const tags = paper.tags ?? [];

  const optimisticSetTags = useCallback((newTags: string[]) => {
    queryClient.setQueryData<PaperRecord[]>(["papers", project], (old) =>
      old?.map((p) => (p.id === paper.id ? { ...p, tags: newTags } : p))
    );
  }, [project, paper.id, queryClient]);

  const addTag = useCallback(async (tag: string) => {
    const trimmed = tag.trim().toLowerCase();
    if (!trimmed || tags.includes(trimmed)) return;
    const newTags = [...tags, trimmed];
    optimisticSetTags(newTags);
    try {
      await updatePaperTags(project, paper.id, newTags);
    } catch {
      void queryClient.invalidateQueries({ queryKey: ["papers", project] });
    }
  }, [project, paper.id, tags, queryClient, optimisticSetTags]);

  const removeTag = useCallback(async (tag: string) => {
    const newTags = tags.filter((t) => t !== tag);
    optimisticSetTags(newTags);
    try {
      await updatePaperTags(project, paper.id, newTags);
    } catch {
      void queryClient.invalidateQueries({ queryKey: ["papers", project] });
    }
  }, [project, paper.id, tags, queryClient, optimisticSetTags]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && input.trim()) {
      e.preventDefault();
      void addTag(input);
      setInput("");
    }
  };

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {tags.map((t) => (
          <span key={t} className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-600">
            {t}
            <button
              type="button"
              className="hover:text-blue-800"
              onClick={() => void removeTag(t)}
            >
              <X size={10} />
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          className="h-7 flex-1 rounded border border-slate-300 px-2 text-xs"
          placeholder="Add tag..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded border border-slate-300 hover:bg-slate-100"
          onClick={() => { void addTag(input); setInput(""); }}
        >
          <Plus size={12} />
        </button>
      </div>
    </div>
  );
}

function NoteEditor({ project, paperId }: { project: string; paperId: string }) {
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentRef = useRef("");
  const dirtyRef = useRef(false);

  // Load note content
  const noteQuery = useQuery({
    queryKey: ["note", project, paperId],
    queryFn: () => getNote(project, paperId),
    enabled: Boolean(project && paperId),
  });

  // Sync loaded content
  useEffect(() => {
    if (noteQuery.data !== undefined) {
      setContent(noteQuery.data.content);
      contentRef.current = noteQuery.data.content;
      setDirty(false);
      dirtyRef.current = false;
    }
  }, [noteQuery.data]);

  // Reset when paper changes
  useEffect(() => {
    setMode("edit");
    setDirty(false);
    dirtyRef.current = false;
  }, [paperId]);

  const doSave = useCallback(async (text: string) => {
    setSaving(true);
    try {
      await saveNote(project, paperId, text);
      if (contentRef.current === text) {
        setDirty(false);
        dirtyRef.current = false;
      }
    } finally {
      setSaving(false);
    }
  }, [project, paperId]);

  // Auto-save after 1s of inactivity
  const handleChange = useCallback((value: string) => {
    setContent(value);
    contentRef.current = value;
    setDirty(true);
    dirtyRef.current = true;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      void doSave(value);
    }, 1000);
  }, [doSave]);

  // Flush the latest note when switching papers or unmounting before the timer fires.
  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      if (dirtyRef.current) {
        void saveNote(project, paperId, contentRef.current);
      }
    };
  }, [project, paperId]);

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5">
        <div className="flex items-center gap-1">
          <button
            type="button"
            className={`rounded px-2 py-1 text-xs font-medium ${mode === "edit" ? "bg-slate-200 text-slate-800" : "text-slate-500 hover:bg-slate-100"}`}
            onClick={() => setMode("edit")}
          >
            <Edit3 size={12} className="inline mr-1" />
            Edit
          </button>
          <button
            type="button"
            className={`rounded px-2 py-1 text-xs font-medium ${mode === "preview" ? "bg-slate-200 text-slate-800" : "text-slate-500 hover:bg-slate-100"}`}
            onClick={() => setMode("preview")}
          >
            <Eye size={12} className="inline mr-1" />
            Preview
          </button>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <span className="text-[10px] text-amber-500">unsaved</span>}
          {saving && <span className="text-[10px] text-slate-400">saving...</span>}
          {!dirty && !saving && content && <span className="text-[10px] text-green-500">saved</span>}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {mode === "edit" ? (
          <textarea
            className="h-full w-full resize-none border-0 bg-white p-4 text-sm leading-relaxed text-slate-800 focus:outline-none font-mono"
            value={content}
            onChange={(e) => handleChange(e.target.value)}
            placeholder="Write your notes here... (Markdown supported)"
            spellCheck={false}
          />
        ) : (
          <div className="h-full overflow-y-auto p-4">
            {content ? (
              <div className="prose prose-sm prose-slate max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                  {content}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm text-slate-400 italic">No notes yet.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[70px_minmax(0,1fr)] gap-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="truncate text-slate-800">{value || "-"}</dd>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-sm text-slate-500">
      {label}
    </div>
  );
}
