import { lazy, Suspense, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { create } from "zustand";
import { BookOpen, FileText, FolderOpen, RefreshCw } from "lucide-react";
import {
  listArtifacts,
  listPapers,
  listProjects,
  PaperRecord
} from "./api/client";

const PaperPdfViewer = lazy(() => import("./components/PaperPdfViewer"));

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

      <section className="grid h-[calc(100vh-3.5rem)] grid-cols-[360px_minmax(0,1fr)]">
        <PaperList
          papers={papers}
          selectedPaperId={selectedPaper?.id ?? ""}
          isLoading={papersQuery.isLoading}
          onSelect={setSelectedPaperId}
        />
        <PaperDetail project={project} paper={selectedPaper} />
      </section>
    </main>
  );
}

function PaperList({
  papers,
  selectedPaperId,
  isLoading,
  onSelect
}: {
  papers: PaperRecord[];
  selectedPaperId: string;
  isLoading: boolean;
  onSelect: (paperId: string) => void;
}) {
  return (
    <aside className="border-r border-slate-200 bg-white">
      <div className="flex h-12 items-center justify-between border-b border-slate-200 px-4">
        <h1 className="text-sm font-semibold">Papers</h1>
        <span className="text-xs text-slate-500">{papers.length}</span>
      </div>
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

function PaperDetail({ project, paper }: { project: string; paper?: PaperRecord }) {
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", project, paper?.id],
    queryFn: () => listArtifacts(project, paper?.id ?? ""),
    enabled: Boolean(project && paper?.id)
  });

  if (!paper) {
    return <EmptyState label="Select a paper" />;
  }

  return (
    <section className="grid min-w-0 grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 overflow-y-auto p-5">
        <div className="mb-4">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{paper.id}</div>
          <h2 className="mt-1 max-w-5xl text-2xl font-semibold leading-tight">{paper.title}</h2>
          <p className="mt-2 max-w-4xl text-sm text-slate-600">{paper.authors.join(", ")}</p>
        </div>
        <div className="mb-5 max-w-5xl rounded-md border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold">Abstract</h3>
          <p className="text-sm leading-6 text-slate-700">{paper.abstract || "No abstract saved."}</p>
        </div>
        {paper.local_pdf_path ? (
          <Suspense fallback={<EmptyState label="Loading PDF viewer" />}>
            <PaperPdfViewer project={project} paper={paper} />
          </Suspense>
        ) : (
          <EmptyState label="No PDF saved for this paper" />
        )}
      </div>
      <aside className="border-l border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4">
          <h3 className="text-sm font-semibold">Metadata</h3>
          <dl className="mt-3 space-y-2 text-xs">
            <MetadataRow label="Source" value={paper.source} />
            <MetadataRow label="arXiv" value={paper.arxiv_id} />
            <MetadataRow label="Added" value={paper.added_at} />
          </dl>
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
      </aside>
    </section>
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
