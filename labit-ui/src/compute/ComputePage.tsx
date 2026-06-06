import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Monitor, Pencil, Plus, Terminal, Trash2, X, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import {
  listComputeProfiles,
  saveComputeProfile,
  deleteComputeProfile,
  testComputeProfile,
  type ComputeProfile,
  type SaveProfileRequest,
} from "../api/compute";

type TestStatus = "idle" | "testing" | "success" | "failed";

interface ProfileTestState {
  status: TestStatus;
  message: string;
}

export default function ComputePage({ project }: { project: string }) {
  const queryClient = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editingProfile, setEditingProfile] = useState<ComputeProfile | null>(null);
  const [testStates, setTestStates] = useState<Record<string, ProfileTestState>>({});
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const profilesQuery = useQuery({
    queryKey: ["compute", project],
    queryFn: () => listComputeProfiles(project),
    enabled: Boolean(project),
  });
  const profiles = profilesQuery.data ?? [];

  const handleTest = useCallback(async (name: string) => {
    setTestStates((s) => ({ ...s, [name]: { status: "testing", message: "" } }));
    try {
      const result = await testComputeProfile(project, name);
      setTestStates((s) => ({
        ...s,
        [name]: { status: result.success ? "success" : "failed", message: result.message },
      }));
    } catch (err: any) {
      setTestStates((s) => ({ ...s, [name]: { status: "failed", message: err.message } }));
    }
  }, [project]);

  const handleDelete = useCallback(async (name: string) => {
    await deleteComputeProfile(project, name);
    setDeleteConfirm(null);
    void queryClient.invalidateQueries({ queryKey: ["compute", project] });
  }, [project, queryClient]);

  const handleSave = useCallback(async (name: string, body: SaveProfileRequest) => {
    await saveComputeProfile(project, name, body);
    setShowModal(false);
    setEditingProfile(null);
    void queryClient.invalidateQueries({ queryKey: ["compute", project] });
  }, [project, queryClient]);

  const openAdd = () => { setEditingProfile(null); setShowModal(true); };
  const openEdit = (p: ComputeProfile) => { setEditingProfile(p); setShowModal(true); };

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--bg,#eef3fb)]">
      <div className="mx-auto max-w-3xl px-6 py-8">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="grid place-items-center w-9 h-9 rounded-lg bg-[var(--ink,#0e72ed)] text-white">
              <Monitor size={18} />
            </span>
            <div>
              <h1 className="text-lg font-semibold">Compute Profiles</h1>
              <p className="text-xs text-slate-500">SSH environments for {project}</p>
            </div>
          </div>
          <button
            onClick={openAdd}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--ink,#0e72ed)] px-3 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            <Plus size={14} />
            Add Profile
          </button>
        </div>

        {/* Profile cards */}
        {profilesQuery.isLoading ? (
          <div className="flex items-center justify-center py-20 text-sm text-slate-400">
            <Loader2 size={16} className="animate-spin mr-2" /> Loading...
          </div>
        ) : profiles.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-12 text-center">
            <Monitor size={32} className="mx-auto mb-3 text-slate-300" />
            <p className="text-sm text-slate-500 mb-4">No compute profiles configured.</p>
            <button
              onClick={openAdd}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--ink,#0e72ed)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              <Plus size={14} />
              Add your first profile
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {profiles.map((p) => {
              const ts = testStates[p.name] ?? { status: "idle" as TestStatus, message: "" };
              return (
                <div key={p.name} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                  {/* Top row: name + actions */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2.5">
                      <span className="grid place-items-center w-8 h-8 rounded-lg bg-slate-100 text-slate-600">
                        <Terminal size={16} />
                      </span>
                      <h3 className="text-base font-semibold">{p.name}</h3>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => openEdit(p)}
                        className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
                      >
                        <Pencil size={12} /> Edit
                      </button>
                      {deleteConfirm === p.name ? (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => void handleDelete(p.name)}
                            className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-red-600 bg-red-50 hover:bg-red-100"
                          >
                            <Check size={12} /> Confirm
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(null)}
                            className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-500 hover:bg-slate-100"
                          >
                            <X size={12} />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setDeleteConfirm(p.name)}
                          className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-500 hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 size={12} /> Delete
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Details grid */}
                  <div className="grid grid-cols-[80px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm mb-4">
                    <span className="text-slate-400 text-xs font-medium">SSH</span>
                    <code className="text-xs font-mono text-slate-700 bg-slate-50 rounded px-1.5 py-0.5 inline-block">
                      {p.ssh_display}
                    </code>
                    {p.workdir && (
                      <>
                        <span className="text-slate-400 text-xs font-medium">Workdir</span>
                        <code className="text-xs font-mono text-slate-600">{p.workdir}</code>
                      </>
                    )}
                    {p.identity_file && (
                      <>
                        <span className="text-slate-400 text-xs font-medium">Key</span>
                        <code className="text-xs font-mono text-slate-600">{p.identity_file}</code>
                      </>
                    )}
                    {p.notes && (
                      <>
                        <span className="text-slate-400 text-xs font-medium">Notes</span>
                        <span className="text-xs text-slate-600">{p.notes}</span>
                      </>
                    )}
                  </div>

                  {/* Test row */}
                  <div className="flex items-center gap-3 border-t border-slate-100 pt-3">
                    <button
                      onClick={() => void handleTest(p.name)}
                      disabled={ts.status === "testing"}
                      className="flex items-center gap-1.5 rounded-md border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      {ts.status === "testing" ? (
                        <><Loader2 size={12} className="animate-spin" /> Testing...</>
                      ) : (
                        <>Test SSH</>
                      )}
                    </button>
                    {ts.status === "success" && (
                      <span className="flex items-center gap-1 text-xs text-green-600">
                        <CheckCircle2 size={13} /> {ts.message}
                      </span>
                    )}
                    {ts.status === "failed" && (
                      <span className="flex items-center gap-1 text-xs text-red-500 max-w-md truncate" title={ts.message}>
                        <AlertCircle size={13} /> {ts.message}
                      </span>
                    )}
                    {ts.status === "idle" && (
                      <span className="text-xs text-slate-400">Not tested</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Add/Edit Modal */}
      {showModal && (
        <ProfileModal
          profile={editingProfile}
          onSave={handleSave}
          onClose={() => { setShowModal(false); setEditingProfile(null); }}
        />
      )}
    </div>
  );
}

function ProfileModal({
  profile,
  onSave,
  onClose,
}: {
  profile: ComputeProfile | null;
  onSave: (name: string, body: SaveProfileRequest) => Promise<void>;
  onClose: () => void;
}) {
  const isEdit = profile !== null;
  const [name, setName] = useState(profile?.name ?? "");
  const [user, setUser] = useState(profile?.user ?? "");
  const [host, setHost] = useState(profile?.host ?? "");
  const [port, setPort] = useState(String(profile?.port ?? 22));
  const [identityFile, setIdentityFile] = useState(profile?.identity_file ?? "");
  const [workdir, setWorkdir] = useState(profile?.workdir ?? "");
  const [notes, setNotes] = useState(profile?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !user.trim() || !host.trim()) {
      setError("Name, user, and host are required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave(name.trim(), {
        user: user.trim(),
        host: host.trim(),
        port: parseInt(port, 10) || 22,
        identity_file: identityFile.trim() || null,
        workdir: workdir.trim(),
        notes: notes.trim(),
      });
    } catch (err: any) {
      setError(err.message);
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <form
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => void handleSubmit(e)}
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-base font-semibold">{isEdit ? "Edit Profile" : "Add Compute Profile"}</h2>
          <button type="button" onClick={onClose} className="rounded p-1 hover:bg-slate-100">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-3">
          <Field label="Name" value={name} onChange={setName} placeholder="e.g. lab-gpu" disabled={isEdit} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="User" value={user} onChange={setUser} placeholder="root" />
            <Field label="Host" value={host} onChange={setHost} placeholder="10.0.0.5" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Port" value={port} onChange={setPort} placeholder="22" type="number" />
            <Field label="Identity File" value={identityFile} onChange={setIdentityFile} placeholder="~/.ssh/id_rsa" />
          </div>
          <Field label="Working Directory" value={workdir} onChange={setWorkdir} placeholder="/home/user/projects" />
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Notes</label>
            <textarea
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="4x A100, 512GB RAM"
            />
          </div>
        </div>

        {error && (
          <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{error}</div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-[var(--ink,#0e72ed)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving..." : isEdit ? "Update" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  disabled?: boolean;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600 mb-1">{label}</label>
      <input
        type={type}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-slate-100 disabled:text-slate-500"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
    </div>
  );
}
