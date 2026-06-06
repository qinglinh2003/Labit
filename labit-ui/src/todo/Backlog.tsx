// Backlog.tsx — left panel: collection pool with search / labels / priority / drag.
import React from "react";
import type { Todo, Priority } from "./types";
import { Checkbox, PriorityToggle, LabelChips } from "./primitives";
import { IconSearch, IconX, IconPlus, IconArrowRight, IconTrash, IconLink, IconDrag, IconArchive } from "./icons";

const PRI_RANK: Record<Priority, number> = { high: 0, normal: 1, low: 2 };

export const DRAG_MIME = "text/labit-todo";

function Row({ item, linked, highlight, onToggleDone, onDelete, onCyclePri, onSendToToday, onHover }: {
  item: Todo; linked: boolean; highlight: boolean;
  onToggleDone: (id: string) => void; onDelete: (id: string) => void;
  onCyclePri: (id: string, p: Priority) => void; onSendToToday: (item: Todo) => void;
  onHover: (id: string | null) => void;
}) {
  const done = item.status === "done";
  return (
    <div
      draggable={!done}
      onDragStart={(e) => { e.dataTransfer.setData(DRAG_MIME, item.id); e.dataTransfer.effectAllowed = "copy"; (e.currentTarget as HTMLElement).style.opacity = "0.4"; }}
      onDragEnd={(e) => { (e.currentTarget as HTMLElement).style.opacity = ""; }}
      onMouseEnter={() => onHover(item.id)}
      onMouseLeave={() => onHover(null)}
      className={[
        "group relative flex items-center gap-[9px] rounded-[var(--radius-sm)] transition-all duration-150",
        "py-[var(--row-py)] px-[var(--row-px)]",
        highlight ? "bg-[var(--accent-weak)] shadow-[inset_0_0_0_1px_var(--accent)]" : "hover:bg-[var(--surface-2)]",
      ].join(" ")}
    >
      {linked && <span className="absolute left-0 top-[18%] bottom-[18%] w-[2.5px] rounded-sm bg-[var(--accent)] opacity-55" />}
      <span className="shrink-0 grid place-items-center w-3.5 -ml-1 text-[var(--faint)] cursor-grab opacity-0 group-hover:opacity-60 transition-opacity"><IconDrag size={16} /></span>
      <PriorityToggle level={item.priority} onChange={(p) => onCyclePri(item.id, p)} dimmed={done} />
      <Checkbox checked={done} onChange={() => onToggleDone(item.id)} />
      <span className="flex-1 min-w-0 flex items-center gap-x-2.5 gap-y-1">
        <span className={`flex-1 min-w-0 text-[length:var(--fs-item)] [text-wrap:pretty] ${done ? "line-through decoration-[var(--faint)] text-[var(--muted)]" : ""}`}>{item.title}</span>
        <LabelChips labels={item.labels} />
      </span>
      {linked && <span title="On today's list" className="shrink-0 grid place-items-center w-5 h-5 rounded-md text-[var(--accent)] bg-[var(--accent-weak)]"><IconLink size={12} stroke={2.2} /></span>}
      <span className="shrink-0 flex items-center gap-px opacity-0 group-hover:opacity-100 transition-opacity">
        {!done && (
          <button title="Add to Today" onClick={() => onSendToToday(item)} className="grid place-items-center w-7 h-7 rounded-md text-[var(--muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]"><IconArrowRight size={15} stroke={2} /></button>
        )}
        <button title="Delete" onClick={() => onDelete(item.id)} className="grid place-items-center w-7 h-7 rounded-md text-[var(--muted)] hover:bg-[oklch(0.95_0.04_22)] hover:text-[var(--pri-high)]"><IconTrash size={15} stroke={1.9} /></button>
      </span>
    </div>
  );
}

export function Backlog(props: {
  items: Todo[];
  search: string; onSearch: (s: string) => void;
  draftPri: Priority; setDraftPri: (p: Priority) => void;
  onAdd: (title: string, p: Priority) => void;
  linkedIds: Set<string>; highlightId: string | null; onHover: (id: string | null) => void;
  onToggleDone: (id: string) => void; onDelete: (id: string) => void;
  onCyclePri: (id: string, p: Priority) => void; onSendToToday: (item: Todo) => void;
}) {
  const { items, search, onSearch,
    draftPri, setDraftPri, onAdd, linkedIds, highlightId, onHover,
    onToggleDone, onDelete, onCyclePri, onSendToToday } = props;
  const [draft, setDraft] = React.useState("");
  const openCount = items.filter((i) => i.status === "open").length;

  const view = items
    .filter((i) => {
      if (search && !i.title.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    })
    .sort((a, b) => {
      const ad = a.status === "done" ? 1 : 0, bd = b.status === "done" ? 1 : 0;
      if (ad !== bd) return ad - bd;
      const pr = PRI_RANK[a.priority] - PRI_RANK[b.priority];
      if (pr !== 0) return pr;
      return a.created_at < b.created_at ? 1 : -1;
    });

  const submit = () => { const v = draft.trim(); if (!v) return; onAdd(v, draftPri); setDraft(""); };
  const inputCls = "flex-1 min-w-0 py-2.5 px-3 bg-[var(--surface-2)] border border-[var(--border)] rounded-[var(--radius-sm)] text-sm outline-none transition-shadow placeholder:text-[var(--faint)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-weak)] focus:bg-[var(--surface)]";

  return (
    <section className="flex flex-col min-h-0 bg-[var(--surface)] border border-[var(--border)] rounded-[var(--radius)] shadow-[var(--shadow)] overflow-hidden">
      <div className="flex-none flex items-center justify-between pt-[15px] px-4 pb-[13px]">
        <div className="flex items-center gap-[9px]">
          <h2 className="m-0 font-[var(--font-head)] font-[var(--head-weight)] text-[18px] tracking-[var(--head-tracking)] [text-transform:var(--head-transform)]">Backlog</h2>
          <span className="font-[var(--font-mono)] text-[11.5px] text-[var(--muted)] bg-[var(--surface-3)] px-2 py-0.5 rounded-full">{openCount}</span>
        </div>
      </div>

      <div className="flex-none flex flex-col gap-[9px] px-4 pb-[13px] border-b border-[var(--border)]">
        <div className="relative flex items-center">
          <IconSearch size={15} stroke={1.9} className="absolute left-[11px] text-[var(--faint)] pointer-events-none" />
          <input value={search} onChange={(e) => onSearch(e.target.value)} placeholder="Search backlog…"
            className="w-full py-[9px] pl-[34px] pr-[30px] bg-[var(--surface-2)] border border-[var(--border)] rounded-[var(--radius-sm)] text-[13.5px] outline-none placeholder:text-[var(--faint)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-weak)]" />
          {search && <button onClick={() => onSearch("")} className="absolute right-2 grid place-items-center w-5 h-5 rounded text-[var(--faint)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]"><IconX size={13} stroke={2.2} /></button>}
        </div>
      </div>

      <div className="flex-none flex items-center gap-2 py-3 px-4 border-b border-[var(--border)]">
        <PriorityToggle level={draftPri} onChange={setDraftPri} />
        <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} placeholder="Add to backlog…  (Enter)" className={inputCls} />
        <button onClick={submit} title="Add" className="shrink-0 grid place-items-center w-10 h-10 rounded-[var(--radius-sm)] bg-[var(--accent)] text-[var(--accent-text)] hover:brightness-105 active:scale-95 transition"><IconPlus size={17} stroke={2.2} /></button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-[var(--list-gap)]">
        {view.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 py-12 px-6 text-[var(--faint)] text-center">
            <IconArchive size={26} stroke={1.5} />
            <p className="m-0 text-[13.5px]">{search ? "Nothing matches your search" : "Backlog is clear"}</p>
          </div>
        ) : view.map((it) => (
          <Row key={it.id} item={it} linked={linkedIds.has(it.id)} highlight={highlightId === it.id}
            onToggleDone={onToggleDone} onDelete={onDelete} onCyclePri={onCyclePri} onSendToToday={onSendToToday} onHover={onHover} />
        ))}
      </div>
    </section>
  );
}
