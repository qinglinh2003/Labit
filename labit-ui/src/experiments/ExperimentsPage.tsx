import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Eye,
  File,
  FileText,
  FlaskConical,
  FolderOpen,
  Image,
  Loader2,
  Octagon,
  Play,
  RefreshCw,
  ScrollText,
  Square,
  XCircle,
  BarChart3,
  List,
  Settings2,
  Terminal,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  listExperiments,
  launchExperiment,
  refreshRun,
  getRunLogs,
  getRunEvents,
  getRunMetrics,
  stopRun,
  updateRunStatus,
  syncRun,
  listResults,
  getResultPreview,
  getResultDownloadUrl,
  type ExperimentWithRun,
  type RunInfo,
  type LabItEvent,
  type MetricPoint,
  type ResultEntry,
  type ResultPreview,
  type SyncResult,
} from "../api/experiments";

type Category = "ready" | "running" | "recent";

function categorize(item: ExperimentWithRun): Category {
  const run = item.latest_run;
  if (!run) return "ready";
  if (run.status === "running" || run.status === "syncing") return "running";
  return "recent";
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function duration(start: string, end: string): string {
  if (!start) return "";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `${hrs}h ${rem}m`;
}

const STATUS_STYLES: Record<string, { color: string; bg: string; label: string }> = {
  running: { color: "text-blue-600", bg: "bg-blue-50", label: "Running" },
  syncing: { color: "text-yellow-600", bg: "bg-yellow-50", label: "Syncing" },
  completed: { color: "text-green-600", bg: "bg-green-50", label: "Completed" },
  failed: { color: "text-red-600", bg: "bg-red-50", label: "Failed" },
  stopped: { color: "text-slate-600", bg: "bg-slate-100", label: "Stopped" },
  pending: { color: "text-slate-500", bg: "bg-slate-50", label: "Pending" },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[status] ?? STATUS_STYLES.pending;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${s.color} ${s.bg}`}>
      {status === "running" || status === "syncing" ? (
        <Loader2 size={11} className="animate-spin" />
      ) : status === "completed" ? (
        <CheckCircle2 size={11} />
      ) : status === "failed" ? (
        <XCircle size={11} />
      ) : status === "stopped" ? (
        <Octagon size={11} />
      ) : (
        <Clock size={11} />
      )}
      {s.label}
    </span>
  );
}

type LogTab = "metrics" | "events" | "raw" | "config" | "results";

export default function ExperimentsPage({ project }: { project: string }) {
  const queryClient = useQueryClient();
  const [logModal, setLogModal] = useState<{ experimentId: string; runId: string } | null>(null);
  const [logTab, setLogTab] = useState<LogTab>("metrics");
  const [logContent, setLogContent] = useState("");
  const [logLoading, setLogLoading] = useState(false);
  const [logStream, setLogStream] = useState<"stdout" | "stderr">("stdout");
  const [launching, setLaunching] = useState<string | null>(null);
  const [events, setEvents] = useState<LabItEvent[]>([]);
  const [metricsData, setMetricsData] = useState<Record<string, MetricPoint[]>>({});
  const [copied, setCopied] = useState(false);
  const [resultFiles, setResultFiles] = useState<ResultEntry[]>([]);
  const [resultPreview, setResultPreview] = useState<ResultPreview | null>(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [lastSyncResult, setLastSyncResult] = useState<SyncResult | null>(null);

  const expQuery = useQuery({
    queryKey: ["experiments", project],
    queryFn: () => listExperiments(project),
    enabled: Boolean(project),
    refetchInterval: 10000,
  });
  const items = expQuery.data ?? [];

  const ready = items.filter((i) => categorize(i) === "ready");
  const running = items.filter((i) => categorize(i) === "running");
  const recent = items.filter((i) => categorize(i) === "recent");

  const handleLaunch = useCallback(
    async (experimentId: string) => {
      setLaunching(experimentId);
      try {
        await launchExperiment(project, experimentId);
        void queryClient.invalidateQueries({ queryKey: ["experiments", project] });
      } catch (err: any) {
        alert(`Launch failed: ${err.message}`);
      } finally {
        setLaunching(null);
      }
    },
    [project, queryClient],
  );

  const handleRefresh = useCallback(
    async (experimentId: string, runId: string) => {
      try {
        await refreshRun(project, experimentId, runId);
        void queryClient.invalidateQueries({ queryKey: ["experiments", project] });
      } catch {}
    },
    [project, queryClient],
  );

  const handleStop = useCallback(
    async (experimentId: string, runId: string) => {
      try {
        await stopRun(project, experimentId, runId);
      } catch (err: any) {
        console.error("Stop failed:", err);
      } finally {
        void queryClient.invalidateQueries({ queryKey: ["experiments", project] });
      }
    },
    [project, queryClient],
  );

  const handleMarkComplete = useCallback(
    async (experimentId: string, runId: string) => {
      try {
        await updateRunStatus(project, experimentId, runId, { status: "completed", exit_code: 0 });
        void queryClient.invalidateQueries({ queryKey: ["experiments", project] });
      } catch (err: any) {
        alert(`Mark complete failed: ${err.message}`);
      }
    },
    [project, queryClient],
  );

  const loadModalData = useCallback(
    async (experimentId: string, runId: string) => {
      setLogLoading(true);
      try {
        const [logResp, eventsResp, metricsResp] = await Promise.all([
          getRunLogs(project, experimentId, runId, "stdout"),
          getRunEvents(project, experimentId, runId),
          getRunMetrics(project, experimentId, runId),
        ]);
        setLogContent(logResp.content);
        setEvents(eventsResp.events);
        setMetricsData(metricsResp.metrics);
        setLogStream("stdout");
      } catch (err: any) {
        setLogContent(`Error: ${err.message}`);
        setEvents([]);
        setMetricsData({});
      } finally {
        setLogLoading(false);
      }
    },
    [project],
  );

  const openLog = useCallback(
    async (experimentId: string, runId: string) => {
      setLogModal({ experimentId, runId });
      setLogTab("metrics");
      setResultFiles([]);
      setResultPreview(null);
      await loadModalData(experimentId, runId);
    },
    [loadModalData],
  );

  // Load result files when switching to Results tab
  useEffect(() => {
    if (logTab !== "results" || !logModal) return;
    let cancelled = false;
    (async () => {
      setResultsLoading(true);
      try {
        const files = await listResults(project, logModal.experimentId, logModal.runId);
        if (!cancelled) setResultFiles(files);
      } catch {
        if (!cancelled) setResultFiles([]);
      } finally {
        if (!cancelled) setResultsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [logTab, logModal, project]);

  const refreshLog = useCallback(async () => {
    if (!logModal) return;
    if (logTab === "raw") {
      setLogLoading(true);
      try {
        const resp = await getRunLogs(project, logModal.experimentId, logModal.runId, logStream);
        setLogContent(resp.content);
      } catch (err: any) {
        setLogContent(`Error: ${err.message}`);
      } finally {
        setLogLoading(false);
      }
    } else {
      await loadModalData(logModal.experimentId, logModal.runId);
    }
  }, [project, logModal, logStream, logTab, loadModalData]);

  const handleCopy = useCallback(async () => {
    let text = "";
    if (logTab === "raw") {
      text = logContent;
    } else if (logTab === "metrics") {
      text = Object.entries(metricsData)
        .map(([key, pts]) => `${key}\n${pts.map((p) => `  step=${p.step} value=${p.value}`).join("\n")}`)
        .join("\n\n");
    } else if (logTab === "events") {
      text = events
        .filter((e) => e.type !== "metric")
        .map((e) => JSON.stringify(e))
        .join("\n");
    } else if (logTab === "config") {
      const cfgs = events.filter((e) => e.type === "config");
      const latest = cfgs[cfgs.length - 1];
      text = latest?.data ? JSON.stringify(latest.data, null, 2) : "";
    }
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [logTab, logContent, metricsData, events]);

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--bg,#eef3fb)]">
      <div className="mx-auto max-w-5xl px-6 py-8">
        {/* Header */}
        <div className="mb-6 flex items-center gap-3">
          <span className="grid place-items-center w-9 h-9 rounded-lg bg-[var(--ink,#0e72ed)] text-white">
            <FlaskConical size={18} />
          </span>
          <div>
            <h1 className="text-lg font-semibold">Experiments</h1>
            <p className="text-xs text-slate-500">
              {items.length} experiment{items.length !== 1 ? "s" : ""} in {project}
            </p>
          </div>
        </div>

        {expQuery.isLoading ? (
          <div className="flex items-center justify-center py-20 text-sm text-slate-400">
            <Loader2 size={16} className="animate-spin mr-2" /> Loading...
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-12 text-center">
            <FlaskConical size={32} className="mx-auto mb-3 text-slate-300" />
            <p className="text-sm text-slate-500 mb-2">No experiments found.</p>
            <p className="text-xs text-slate-400">
              Create experiments under <code className="bg-slate-100 px-1 rounded">code/experiments/&lt;id&gt;/</code> with
              a <code className="bg-slate-100 px-1 rounded">manifest.yaml</code> and <code className="bg-slate-100 px-1 rounded">run.sh</code>.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Ready to Run */}
            <Section title="Ready to Run" count={ready.length} color="slate">
              {ready.map((item) => (
                <ExperimentCard
                  key={item.experiment.experiment_id}
                  item={item}
                  onLaunch={handleLaunch}
                  launching={launching}
                />
              ))}
            </Section>

            {/* Running */}
            <Section title="Running" count={running.length} color="blue">
              {running.map((item) => (
                <ExperimentCard
                  key={item.experiment.experiment_id}
                  item={item}
                  onRefresh={handleRefresh}
                  onStop={handleStop}
                  onLog={openLog}
                />
              ))}
            </Section>

            {/* Recent */}
            <Section title="Recent" count={recent.length} color="green">
              {recent.map((item) => (
                <ExperimentCard
                  key={item.experiment.experiment_id}
                  item={item}
                  onLaunch={handleLaunch}
                  onLog={openLog}
                  onMarkComplete={handleMarkComplete}
                  launching={launching}
                />
              ))}
            </Section>
          </div>
        )}
      </div>

      {/* Log Modal — multi-tab */}
      {logModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setLogModal(null)}>
          <div
            className="w-full max-w-4xl max-h-[85vh] flex flex-col rounded-xl bg-slate-900 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
              <div className="flex items-center gap-2">
                <ScrollText size={14} className="text-slate-400" />
                <span className="text-sm font-medium text-slate-200">
                  {logModal.experimentId} / {logModal.runId}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void handleCopy()}
                  className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
                >
                  {copied ? (
                    <><CheckCircle2 size={12} className="text-green-400" /> Copied</>
                  ) : (
                    <><Copy size={12} /> Copy</>
                  )}
                </button>
                <button
                  onClick={refreshLog}
                  className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
                >
                  <RefreshCw size={12} className={logLoading ? "animate-spin" : ""} />
                  Refresh
                </button>
                <button
                  onClick={() => setLogModal(null)}
                  className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-700 hover:text-slate-200"
                >
                  Close
                </button>
              </div>
            </div>

            {/* Tabs */}
            <div className="flex items-center gap-1 px-4 pt-2 border-b border-slate-700">
              {([
                { key: "metrics" as LogTab, label: "Metrics", icon: BarChart3 },
                { key: "events" as LogTab, label: "Events", icon: List },
                { key: "config" as LogTab, label: "Config", icon: Settings2 },
                { key: "raw" as LogTab, label: "Raw Log", icon: Terminal },
                { key: "results" as LogTab, label: "Results", icon: FolderOpen },
              ]).map(({ key, label, icon: Icon }) => (
                <button
                  key={key}
                  onClick={() => setLogTab(key)}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                    logTab === key
                      ? "border-blue-400 text-blue-400"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Icon size={12} />
                  {label}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-auto p-4">
              {logLoading ? (
                <div className="flex items-center justify-center py-12 text-sm text-slate-400">
                  <Loader2 size={16} className="animate-spin mr-2" /> Loading...
                </div>
              ) : logTab === "metrics" ? (
                <MetricsPanel metrics={metricsData} />
              ) : logTab === "events" ? (
                <EventsPanel events={events} />
              ) : logTab === "config" ? (
                <ConfigPanel events={events} />
              ) : logTab === "results" ? (
                <ResultsPanel
                  project={project}
                  experimentId={logModal.experimentId}
                  runId={logModal.runId}
                  files={resultFiles}
                  preview={resultPreview}
                  loading={resultsLoading}
                  syncing={syncing}
                  lastSyncResult={lastSyncResult}
                  onLoadFiles={async () => {
                    setResultsLoading(true);
                    try {
                      const files = await listResults(project, logModal.experimentId, logModal.runId);
                      setResultFiles(files);
                    } catch {
                      setResultFiles([]);
                    } finally {
                      setResultsLoading(false);
                    }
                  }}
                  onPreview={async (path) => {
                    setResultsLoading(true);
                    try {
                      const p = await getResultPreview(project, logModal.experimentId, logModal.runId, path);
                      setResultPreview(p);
                    } catch (err: any) {
                      setResultPreview(null);
                      alert(`Preview failed: ${err.message}`);
                    } finally {
                      setResultsLoading(false);
                    }
                  }}
                  onClosePreview={() => setResultPreview(null)}
                  onSync={async () => {
                    setSyncing(true);
                    try {
                      const sr = await syncRun(project, logModal.experimentId, logModal.runId, "results");
                      setLastSyncResult(sr);
                      const files = await listResults(project, logModal.experimentId, logModal.runId);
                      setResultFiles(files);
                    } catch (err: any) {
                      alert(`Sync failed: ${err.message}`);
                    } finally {
                      setSyncing(false);
                    }
                  }}
                />
              ) : (
                <RawLogPanel
                  content={logContent}
                  stream={logStream}
                  onStreamChange={async (s) => {
                    setLogStream(s);
                    setLogLoading(true);
                    try {
                      const resp = await getRunLogs(
                        project, logModal.experimentId, logModal.runId, s,
                      );
                      setLogContent(resp.content);
                    } catch (err: any) {
                      setLogContent(`Error: ${err.message}`);
                    } finally {
                      setLogLoading(false);
                    }
                  }}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  count,
  color,
  children,
}: {
  title: string;
  count: number;
  color: string;
  children: React.ReactNode;
}) {
  const colorMap: Record<string, string> = {
    slate: "bg-slate-500",
    blue: "bg-blue-500",
    green: "bg-green-500",
  };
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        <span className={`grid place-items-center h-5 min-w-[20px] rounded-full px-1.5 text-[10px] font-bold text-white ${colorMap[color] ?? "bg-slate-500"}`}>
          {count}
        </span>
      </div>
      <div className="space-y-2">
        {count === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white/60 p-6 text-center text-xs text-slate-400">
            None
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

function ExperimentCard({
  item,
  onLaunch,
  onRefresh,
  onStop,
  onLog,
  onMarkComplete,
  launching,
}: {
  item: ExperimentWithRun;
  onLaunch?: (id: string) => Promise<void>;
  onRefresh?: (expId: string, runId: string) => Promise<void>;
  onStop?: (expId: string, runId: string) => Promise<void>;
  onLog?: (expId: string, runId: string, stream?: "stdout" | "stderr") => Promise<void>;
  onMarkComplete?: (expId: string, runId: string) => Promise<void>;
  launching?: string | null;
}) {
  const exp = item.experiment;
  const run = item.latest_run;
  const isLaunching = launching === exp.experiment_id;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      {/* Title row */}
      <div className="flex items-start justify-between mb-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-800 truncate">{exp.name}</h3>
          <p className="text-[11px] text-slate-400 font-mono truncate">{exp.experiment_id}</p>
        </div>
        {run && <StatusBadge status={run.status} />}
      </div>

      {/* Description */}
      {exp.description && (
        <p className="text-xs text-slate-500 mb-2 line-clamp-2">{exp.description}</p>
      )}

      {/* Meta */}
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400 mb-3">
        {exp.profile && <span className="bg-slate-100 rounded px-1.5 py-0.5 font-medium">{exp.profile}</span>}
        {exp.tags.map((t) => (
          <span key={t} className="bg-blue-50 text-blue-600 rounded px-1.5 py-0.5">{t}</span>
        ))}
      </div>

      {/* Run info */}
      {run && (
        <div className="text-[11px] text-slate-500 space-y-0.5 mb-3">
          {run.pid != null && <div>PID: {run.pid}</div>}
          {run.local_commit && (
            <div>
              Commit: <code className="font-mono">{run.local_commit}</code>
              {run.dirty && <span className="text-amber-500 ml-1">(dirty)</span>}
            </div>
          )}
          {run.started_at && (
            <div>
              Started {timeAgo(run.started_at)}
              {run.finished_at && ` \u00b7 ${duration(run.started_at, run.finished_at)}`}
            </div>
          )}
          {run.exit_code != null && <div>Exit code: {run.exit_code}</div>}
          {run.error && (
            <div className="text-red-500 flex items-start gap-1">
              <AlertCircle size={11} className="mt-0.5 flex-shrink-0" />
              <span className="line-clamp-2">{run.error}</span>
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2.5">
        {onLaunch && (!run || !["running", "syncing"].includes(run.status)) && (
          <button
            onClick={() => void onLaunch(exp.experiment_id)}
            disabled={isLaunching}
            className="flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {isLaunching ? (
              <><Loader2 size={11} className="animate-spin" /> Launching...</>
            ) : (
              <><Play size={11} /> Run</>
            )}
          </button>
        )}
        {onRefresh && run && run.status === "running" && (
          <button
            onClick={() => void onRefresh(exp.experiment_id, run.run_id)}
            className="flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw size={11} /> Refresh
          </button>
        )}
        {onLog && run && (
          <button
            onClick={() => void onLog(exp.experiment_id, run.run_id)}
            className="flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <ScrollText size={11} /> Log
          </button>
        )}
        {onMarkComplete && run && run.status === "failed" && (
          <button
            onClick={() => void onMarkComplete(exp.experiment_id, run.run_id)}
            className="flex items-center gap-1 rounded-md border border-green-200 px-2.5 py-1.5 text-xs font-medium text-green-600 hover:bg-green-50"
          >
            <CheckCircle2 size={11} /> Mark Complete
          </button>
        )}
        {onStop && run && run.status === "running" && (
          <button
            onClick={() => void onStop(exp.experiment_id, run.run_id)}
            className="flex items-center gap-1 rounded-md border border-red-200 px-2.5 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
          >
            <Square size={11} /> Stop
          </button>
        )}
      </div>
    </div>
  );
}

/* ── Log tab panels ──────────────────────────────────────────────── */

const CHART_COLORS = [
  "#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6",
  "#ec4899", "#06b6d4", "#f97316",
];

function MetricsPanel({ metrics }: { metrics: Record<string, MetricPoint[]> }) {
  const keys = Object.keys(metrics);
  if (keys.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-slate-400 text-sm">
        <BarChart3 size={24} className="mb-2 text-slate-500" />
        No metrics logged yet.
        <p className="text-xs mt-1 text-slate-500">
          Use <code className="bg-slate-800 px-1 rounded">log_metric(step, {"{"}&quot;loss&quot;: ...{"}"})</code> in your training script.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {keys.map((key, idx) => {
        const data = metrics[key];
        return (
          <div key={key} className="rounded-lg border border-slate-700 bg-slate-800/50 p-4">
            <h4 className="text-xs font-semibold text-slate-300 mb-3">{key}</h4>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis
                  dataKey="step"
                  tick={{ fontSize: 10, fill: "#94a3b8" }}
                  stroke="#475569"
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "#94a3b8" }}
                  stroke="#475569"
                  width={60}
                  tickFormatter={(v: number) =>
                    Math.abs(v) < 0.001 || Math.abs(v) >= 10000
                      ? v.toExponential(1)
                      : v.toPrecision(4)
                  }
                />
                <Tooltip
                  contentStyle={{
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "#e2e8f0",
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={CHART_COLORS[idx % CHART_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                  animationDuration={300}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}

const EVENT_ICONS: Record<string, { icon: typeof CheckCircle2; color: string }> = {
  config: { icon: Settings2, color: "text-blue-400" },
  status: { icon: Clock, color: "text-yellow-400" },
  metric: { icon: BarChart3, color: "text-green-400" },
  eval: { icon: CheckCircle2, color: "text-purple-400" },
  artifact: { icon: ScrollText, color: "text-cyan-400" },
  error: { icon: AlertCircle, color: "text-red-400" },
};

function EventsPanel({ events }: { events: LabItEvent[] }) {
  // Show non-metric events (metrics are shown in the Metrics tab)
  const filtered = events.filter((e) => e.type !== "metric");
  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-slate-400 text-sm">
        <List size={24} className="mb-2 text-slate-500" />
        No events logged yet.
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {filtered.map((ev, i) => {
        const info = EVENT_ICONS[ev.type] ?? { icon: Clock, color: "text-slate-400" };
        const Icon = info.icon;
        let summary = "";
        if (ev.type === "status") {
          summary = `${ev.status ?? ""} \u2014 ${ev.message ?? ""}`;
        } else if (ev.type === "eval") {
          const dataStr = ev.data
            ? Object.entries(ev.data).map(([k, v]) => `${k}=${v}`).join(", ")
            : "";
          summary = `${ev.benchmark ?? ""} \u2014 ${dataStr}`;
          if (ev.split) summary += ` (${ev.split})`;
        } else if (ev.type === "artifact") {
          summary = `${ev.name ?? ""} \u2014 ${ev.path ?? ""}`;
        } else if (ev.type === "config") {
          summary = ev.data
            ? Object.entries(ev.data).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")
            : "";
        } else if (ev.type === "error") {
          summary = `[${ev.severity ?? "error"}] ${ev.message ?? ""}`;
        } else {
          summary = JSON.stringify(ev);
        }
        return (
          <div key={i} className="flex items-start gap-2 rounded-md bg-slate-800/50 px-3 py-2">
            <Icon size={13} className={`mt-0.5 flex-shrink-0 ${info.color}`} />
            <span className="text-xs text-slate-400 font-medium min-w-[52px]">{ev.type}</span>
            <span className="text-xs text-slate-300 break-all">{summary}</span>
            {ev.step != null && (
              <span className="ml-auto text-[10px] text-slate-500 flex-shrink-0">step {ev.step}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ConfigPanel({ events }: { events: LabItEvent[] }) {
  const configEvents = events.filter((e) => e.type === "config");
  if (configEvents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-slate-400 text-sm">
        <Settings2 size={24} className="mb-2 text-slate-500" />
        No config logged.
        <p className="text-xs mt-1 text-slate-500">
          Use <code className="bg-slate-800 px-1 rounded">log_config({"{"}&quot;model&quot;: ...{"}"})</code> at the start of training.
        </p>
      </div>
    );
  }
  const latest = configEvents[configEvents.length - 1];
  const data = latest.data ?? {};
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-4">
      <h4 className="text-xs font-semibold text-slate-300 mb-3">
        Experiment Configuration
        {configEvents.length > 1 && (
          <span className="text-slate-500 font-normal ml-2">
            ({configEvents.length} config events, showing latest)
          </span>
        )}
      </h4>
      <div className="space-y-1">
        {Object.entries(data).map(([key, val]) => (
          <div key={key} className="flex items-baseline gap-2 text-xs">
            <span className="text-slate-400 font-medium min-w-[140px]">{key}</span>
            <span className="text-slate-200 font-mono break-all">
              {typeof val === "object" ? JSON.stringify(val, null, 2) : String(val)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RawLogPanel({
  content,
  stream,
  onStreamChange,
}: {
  content: string;
  stream: "stdout" | "stderr";
  onStreamChange: (s: "stdout" | "stderr") => Promise<void>;
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="mb-2">
        <select
          className="h-7 rounded bg-slate-800 border border-slate-600 px-2 text-xs text-slate-300"
          value={stream}
          onChange={(e) => void onStreamChange(e.target.value as "stdout" | "stderr")}
        >
          <option value="stdout">stdout</option>
          <option value="stderr">stderr</option>
        </select>
      </div>
      <pre className="flex-1 overflow-auto text-xs text-green-400 font-mono whitespace-pre leading-relaxed bg-black/30 rounded-lg p-3">
        {content || "(empty)"}
      </pre>
    </div>
  );
}

/* ── Results panel ─────────────────────────────────────────────────── */

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(path: string) {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return Image;
  if (["json", "jsonl"].includes(ext)) return FileText;
  if (["csv", "tsv"].includes(ext)) return FileText;
  if (["txt", "log", "md", "yaml", "yml", "toml", "py", "sh"].includes(ext)) return FileText;
  return File;
}

function ResultsPanel({
  project,
  experimentId,
  runId,
  files,
  preview,
  loading,
  syncing,
  lastSyncResult,
  onLoadFiles,
  onPreview,
  onClosePreview,
  onSync,
}: {
  project: string;
  experimentId: string;
  runId: string;
  files: ResultEntry[];
  preview: ResultPreview | null;
  loading: boolean;
  syncing: boolean;
  lastSyncResult: SyncResult | null;
  onLoadFiles: () => Promise<void>;
  onPreview: (path: string) => Promise<void>;
  onClosePreview: () => void;
  onSync: () => Promise<void>;
}) {
  const API = import.meta.env.VITE_API_URL ?? "";

  // If showing preview, render preview view
  if (preview) {
    return (
      <div className="flex flex-col h-full">
        {/* Preview header */}
        <div className="flex items-center justify-between mb-3">
          <button
            onClick={onClosePreview}
            className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
          >
            &larr; Back to file list
          </button>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">{preview.path}</span>
            <span className="text-[10px] text-slate-600">{formatSize(preview.size)}</span>
            <a
              href={`${API}${preview.download_url}`}
              download
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              <Download size={12} /> Download
            </a>
          </div>
        </div>

        {/* Preview content */}
        <div className="flex-1 overflow-auto rounded-lg border border-slate-700 bg-slate-800/50">
          {preview.kind === "image" ? (
            <div className="flex items-center justify-center p-4">
              <img
                src={`${API}${preview.download_url}`}
                alt={preview.path}
                className="max-w-full max-h-[60vh] object-contain rounded"
              />
            </div>
          ) : preview.kind === "csv" ? (
            <div className="overflow-auto p-3">
              <CsvPreview content={preview.content ?? ""} />
              {preview.truncated && (
                <p className="text-[10px] text-amber-400 mt-2">File truncated for preview. Download to see full content.</p>
              )}
            </div>
          ) : preview.kind === "json" ? (
            <pre className="p-3 text-xs text-emerald-400 font-mono whitespace-pre overflow-auto">
              {preview.content ?? ""}
              {preview.truncated && (
                <span className="text-amber-400 block mt-2">... truncated</span>
              )}
            </pre>
          ) : preview.kind === "text" ? (
            <pre className="p-3 text-xs text-slate-300 font-mono whitespace-pre overflow-auto">
              {preview.content ?? ""}
              {preview.truncated && (
                <span className="text-amber-400 block mt-2">... truncated</span>
              )}
            </pre>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 text-sm text-slate-400">
              <File size={32} className="mb-3 text-slate-500" />
              <p>This file type cannot be previewed.</p>
              <a
                href={`${API}${preview.download_url}`}
                download
                className="mt-3 flex items-center gap-1 rounded-md border border-slate-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
              >
                <Download size={12} /> Download File
              </a>
            </div>
          )}
        </div>
      </div>
    );
  }

  // File list view
  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-slate-400">
          {files.length} file{files.length !== 1 ? "s" : ""} synced
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void onLoadFiles()}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
          >
            <RefreshCw size={12} /> Refresh
          </button>
          <button
            onClick={() => void onSync()}
            disabled={syncing}
            className="flex items-center gap-1 rounded-md border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-xs font-medium text-blue-400 hover:bg-blue-500/20 disabled:opacity-50"
          >
            {syncing ? (
              <><Loader2 size={12} className="animate-spin" /> Syncing...</>
            ) : (
              <><Download size={12} /> Sync Results</>
            )}
          </button>
        </div>
      </div>

      {/* Artifact fallback banner */}
      {lastSyncResult?.artifact_sources && lastSyncResult.artifact_sources.length > 0 && (
        <div className="mb-3 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-400">
          <p className="font-medium">Results synced from artifact paths (not $LABIT_RESULTS_DIR):</p>
          <ul className="mt-1 space-y-0.5 text-amber-400/80 font-mono">
            {lastSyncResult.artifact_sources.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12 text-sm text-slate-400">
          <Loader2 size={16} className="animate-spin mr-2" /> Loading...
        </div>
      ) : files.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-slate-400 text-sm">
          <FolderOpen size={24} className="mb-2 text-slate-500" />
          <p>No synced results yet.</p>
          <p className="text-xs mt-1 text-slate-500">
            Click "Sync Results" to pull result files from the remote server.
          </p>
        </div>
      ) : (
        <div className="space-y-0.5 overflow-auto">
          {files.map((f) => {
            const Icon = fileIcon(f.path);
            return (
              <button
                key={f.path}
                onClick={() => void onPreview(f.path)}
                className="flex items-center gap-2 w-full text-left rounded-md px-3 py-2 hover:bg-slate-800/60 transition-colors group"
              >
                <Icon size={14} className="text-slate-500 flex-shrink-0" />
                <span className="text-xs text-slate-300 truncate flex-1 font-mono">{f.path}</span>
                <span className="text-[10px] text-slate-600">{formatSize(f.size)}</span>
                <Eye size={12} className="text-slate-600 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CsvPreview({ content }: { content: string }) {
  const lines = content.split("\n").filter(Boolean);
  if (lines.length === 0) return <span className="text-xs text-slate-500">(empty)</span>;

  // Detect separator
  const sep = lines[0].includes("\t") ? "\t" : ",";
  const rows = lines.slice(0, 100).map((line) => line.split(sep));
  const header = rows[0];
  const body = rows.slice(1);

  return (
    <table className="text-xs text-slate-300 w-full border-collapse">
      <thead>
        <tr>
          {header.map((h, i) => (
            <th
              key={i}
              className="text-left px-2 py-1.5 border-b border-slate-700 text-slate-400 font-semibold bg-slate-800/80 sticky top-0"
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, ri) => (
          <tr key={ri} className="hover:bg-slate-800/40">
            {row.map((cell, ci) => (
              <td key={ci} className="px-2 py-1 border-b border-slate-800 font-mono">
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
