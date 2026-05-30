import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { create } from "zustand";
import { BookOpen, ChevronLeft, ChevronRight, FileText, FolderOpen, RefreshCw } from "lucide-react";
import {
  listArtifacts,
  listPapers,
  listProjects,
  PaperRecord,
  prefetchReaderManifest
} from "./api/client";
import PaperImageReader from "./components/PaperImageReader";

interface UiState {
  project: string;
  selectedPaperId: string;
  setProject: (project: string) => void;
  setSelectedPaperId: (paperId: string) => void;
}

const useUiStore = create<UiState>((set) => ({
  project: "",
  selectedPaperId: "",
  setProject: (project) => set({ project, selectedPaperId: "" }),
  setSelectedPaperId: (paperId) => set({ selectedPaperId: paperId })
}));

export function App() {
  const { project, selectedPaperId, setProject, setSelectedPaperId } = useUiStore();
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const projects = projectsQuery.data?.projects ?? [];

  useEffect(() => {
    if (!project && projects.length > 0) {
      setProject(projectsQuery.data?.active_project ?? projects[0]);
    }
  }, [project, projects, projectsQuery.data?.active_project, setProject]);

  const papersQuery = useQuery({
    queryKey: ["papers", project],
    queryFn: () => listPapers(project),
    enabled: Boolean(project)
  });
  const papers = papersQuery.data ?? [];
  const selectedPaper = papers.find((paper) => paper.id === selectedPaperId) ?? papers[0];

  useEffect(() => {
    if (!selectedPaperId && selectedPaper) {
      setSelectedPaperId(selectedPaper.id);
    }
  }, [selectedPaper, selectedPaperId, setSelectedPaperId]);


  useEffect(() => {
    if (project && selectedPaper?.local_pdf_path) {
      prefetchReaderManifest(project, selectedPaper.id);
    }
  }, [project, selectedPaper?.id, selectedPaper?.local_pdf_path]);

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <header className="border-b border-slate-200 bg-white">
        <div className="flex h-14 items-center justify-between px-5">
          <div className="flex items-center gap-2 font-semibold">
            <BookOpen size={18} />
            <span>LABIT</span>
          </div>
          <div className="flex items-center gap-2">
            <select
              className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm"
              value={project}
              onChange={(event) => setProject(event.target.value)}
            >
              {projects.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <button
              className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm"
              onClick={() => {
                void projectsQuery.refetch();
                void papersQuery.refetch();
              }}
              type="button"
            >
              <RefreshCw size={15} />
              Refresh
            </button>
          </div>
        </div>
      </header>

      <ContentArea
        papers={papers}
        project={project}
        selectedPaper={selectedPaper}
        isLoading={papersQuery.isLoading}
        onSelect={setSelectedPaperId}
      />
    </main>
  );
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
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);

  const leftW = leftOpen ? "360px" : "16px";
  const rightW = rightOpen ? "320px" : "16px";

  return (
    <section
      className="h-[calc(100vh-3.5rem)]"
      style={{ display: "grid", gridTemplateColumns: `${leftW} minmax(0,1fr) ${rightW}` }}
    >
      {leftOpen ? (
        <PaperList
          papers={papers}
          project={project}
          selectedPaperId={selectedPaper?.id ?? ""}
          isLoading={isLoading}
          onSelect={onSelect}
          onCollapse={() => setLeftOpen(false)}
        />
      ) : (
        <div className="relative border-r border-slate-200 bg-white">
          <button
            onClick={() => setLeftOpen(true)}
            className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-slate-300 bg-white shadow-sm hover:bg-slate-100"
            title="Show papers"
            type="button"
          >
            <ChevronRight size={18} />
          </button>
        </div>
      )}
      <PaperDetail project={project} paper={selectedPaper} showSidebar={false} />
      {rightOpen ? (
        <RightSidebar project={project} paper={selectedPaper} onCollapse={() => setRightOpen(false)} />
      ) : (
        <div className="relative border-l border-slate-200 bg-white">
          <button
            onClick={() => setRightOpen(true)}
            className="absolute left-0 top-1/2 -translate-y-1/2 -translate-x-1/2 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-slate-300 bg-white shadow-sm hover:bg-slate-100"
            title="Show details"
            type="button"
          >
            <ChevronLeft size={18} />
          </button>
        </div>
      )}
    </section>
  );
}

function PaperList({
  papers,
  project,
  selectedPaperId,
  isLoading,
  onSelect,
  onCollapse,
}: {
  papers: PaperRecord[];
  project: string;
  selectedPaperId: string;
  isLoading: boolean;
  onSelect: (paperId: string) => void;
  onCollapse: () => void;
}) {
  const warmPaper = (paper: PaperRecord) => {
    if (!project || !paper.local_pdf_path) {
      return;
    }
    prefetchReaderManifest(project, paper.id);
  };

  return (
    <aside className="relative border-r border-slate-200 bg-white">
      <div className="flex h-12 items-center justify-between border-b border-slate-200 px-4">
        <h1 className="text-sm font-semibold">Papers</h1>
        <span className="text-xs text-slate-500">{papers.length}</span>
      </div>
      <button
        onClick={onCollapse}
        className="absolute right-0 top-1/2 z-10 flex h-8 w-8 translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-slate-300 bg-white shadow-sm hover:bg-slate-100"
        title="Collapse papers"
        type="button"
      >
        <ChevronLeft size={18} />
      </button>
      <div className="h-[calc(100vh-6.5rem)] overflow-y-auto">
        {isLoading ? <EmptyState label="Loading papers" /> : null}
        {!isLoading && papers.length === 0 ? <EmptyState label="No papers in this project" /> : null}
        {papers.map((paper) => (
          <button
            key={paper.id}
            className={`block w-full border-b border-slate-100 px-4 py-3 text-left ${
              selectedPaperId === paper.id ? "bg-slate-100" : "bg-white hover:bg-slate-50"
            }`}
            onClick={() => onSelect(paper.id)}
            onFocus={() => warmPaper(paper)}
            onMouseEnter={() => warmPaper(paper)}
            type="button"
          >
            <div className="line-clamp-2 text-sm font-medium leading-5">{paper.title}</div>
            <div className="mt-1 truncate text-xs text-slate-500">{paper.authors.join(", ")}</div>
            <div className="mt-2 text-xs text-slate-400">{paper.id}</div>
          </button>
        ))}
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
}: {
  project: string;
  paper?: PaperRecord;
  onCollapse: () => void;
}) {
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", project, paper?.id],
    queryFn: () => listArtifacts(project, paper?.id ?? ""),
    enabled: Boolean(project && paper?.id),
  });

  return (
    <aside className="relative border-l border-slate-200 bg-white">
      <button
        onClick={onCollapse}
        className="absolute left-0 top-1/2 z-10 flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-slate-300 bg-white shadow-sm hover:bg-slate-100"
        title="Collapse details"
        type="button"
      >
        <ChevronRight size={18} />
      </button>
      <div className="h-full overflow-y-auto">
      <div className="flex h-12 items-center justify-between border-b border-slate-200 px-4">
        <h3 className="text-sm font-semibold">Details</h3>
      </div>
      {paper ? (
        <>
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
