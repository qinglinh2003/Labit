// Daily.tsx — right panel: per-day execution list with progress, carry-over, drop zone.
import React from "react";
import type { DailyItem, Priority } from "./types";
import { Checkbox, PriorityToggle } from "./primitives";
import { IconChevronLeft, IconChevronRight, IconPlus, IconCarry, IconLink, IconX, IconArrowRight } from "./icons";
import { DRAG_MIME } from "./Backlog";

function Row({ item, highlight, onToggle, onDelete, onCyclePri, onHover }: {
  item: DailyItem; highlight: boolean;
  onToggle: (id: string) => void; onDelete: (id: string) => void;
  onCyclePri: (id: string, p: Priority) => void; onHover: (id: string | null) => void;
}) {
  const done = item.status === "done";
  return (
    <div
      onMouseEnter={() => onHover(item.todo_id || null)}
      onMouseLeave={() => onHover(null)}
      className={[
        "group relative flex items-center gap-[9px] rounded-[var(--radius-sm)] transition-all duration-150 py-[var(--row-py)] px-[var(--row-px)]",
        highlight ? "bg-[var(--accent-weak)] shadow-[inset_0_0_0_1px_var(--accent)]" : "hover:bg-[var(--surface-2)]",
      ].join(" ")}
    >
      <PriorityToggle level={item.priority} onChange={(p) => onCyclePri(item.id, p)} dimmed={done} />
      <Checkbox checked={done} onChange={() => onToggle(item.id)} />
      <span className="flex-1 min-w-0 flex items-center">
        <span className={`flex-1 min-w-0 text-[length:var(--fs-item)] [text-wrap:pretty] ${done ? "line-through decoration-[var(--faint)] text-[var(--muted)]" : ""}`}>{item.title}</span>
      </span>
      {item.todo_id
        ? <span title="Linked to backlog · completing syncs both" className="shrink-0 grid place-items-center w-5 h-5 rounded-md text-[var(--accent-text)] bg-[var(--accent)]"><IconLink size={12} stroke={2.2} /></span>
        : <span title="Ad-hoc task" className="shrink-0 w-5 text-center text-[var(--faint)] font-bold">·</span>}
      <span className="shrink-0 flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
        <button title="Remove from today" onClick={() => onDelete(item.id)} className="grid place-items-center w-7 h-7 rounded-md text-[var(--muted)] hover:bg-[oklch(0.95_0.04_22)] hover:text-[var(--pri-high)]"><IconX size={15} stroke={2.1} /></button>
      </span>
    </div>
  );
}

export function Daily(props: {
  dateLabel: string; dateISO: string; isToday: boolean;
  onPrev: () => void; onNext: () => void;
  items: DailyItem[];
  onAdd: (title: string) => void; onToggle: (id: string) => void; onDelete: (id: string) => void;
  onCyclePri: (id: string, p: Priority) => void;
  onCarryOver: () => void; carryCount: number; onDropBacklog: (id: string) => void;
  highlightId: string | null; onHover: (id: string | null) => void;
}) {
  const { dateLabel, dateISO, isToday, onPrev, onNext, items, onAdd, onToggle, onDelete,
    onCyclePri, onCarryOver, carryCount, onDropBacklog, highlightId, onHover } = props;
  const [draft, setDraft] = React.useState("");
  const [over, setOver] = React.useState(false);
  const done = items.filter((i) => i.status === "done").length;
  const total = items.length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const submit = () => { const v = draft.trim(); if (!v) return; onAdd(v); setDraft(""); };

  return (
    <section className="flex flex-col min-h-0 bg-[var(--surface)] border border-[var(--border)] rounded-[var(--radius)] shadow-[var(--shadow)] overflow-hidden">
      <div className="flex-none flex items-center justify-between pt-[13px] px-4 pb-[11px]">
        <button onClick={onPrev} title="Previous day" className="grid place-items-center w-[34px] h-[34px] rounded-[9px] border border-[var(--border)] bg-[var(--surface-2)] text-[var(--muted)] hover:border-[var(--border-strong)] hover:text-[var(--text)] transition-colors"><IconChevronLeft size={18} stroke={2} /></button>
        <div className="text-center leading-[1.15]">
          <span className="block font-[var(--font-head)] font-[var(--head-weight)] text-[18px] tracking-[var(--head-tracking)] [text-transform:var(--head-transform)]">{isToday ? "Today" : dateLabel}</span>
          <span className="font-[var(--font-mono)] text-[11.5px] text-[var(--faint)] tracking-[0.02em] whitespace-nowrap">{dateISO}</span>
        </div>
        <button onClick={onNext} title="Next day" className="grid place-items-center w-[34px] h-[34px] rounded-[9px] border border-[var(--border)] bg-[var(--surface-2)] text-[var(--muted)] hover:border-[var(--border-strong)] hover:text-[var(--text)] transition-colors"><IconChevronRight size={18} stroke={2} /></button>
      </div>

      <div className="flex-none flex items-center gap-3 pt-1 px-4 pb-3.5 border-b border-[var(--border)]">
        <div className="flex-1 h-1.5 rounded-full bg-[var(--surface-3)] overflow-hidden">
          <div className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-300" style={{ width: `${pct}%` }} />
        </div>
        <span className="font-[var(--font-mono)] text-[11.5px] text-[var(--muted)] whitespace-nowrap">{done}/{total} done{total ? ` · ${pct}%` : ""}</span>
        <button onClick={onCarryOver} disabled={!carryCount} title={carryCount ? `Carry over ${carryCount} unfinished from yesterday` : "Nothing to carry over"}
          className={`flex items-center gap-1.5 py-1.5 px-[11px] rounded-lg border text-[12.5px] font-medium whitespace-nowrap transition-all ${carryCount ? "border-[var(--border)] bg-[var(--surface-2)] hover:border-[var(--accent)] hover:text-[var(--accent-strong)] hover:bg-[var(--accent-weak)]" : "border-[var(--border)] bg-[var(--surface-2)] text-[var(--faint)] opacity-65 cursor-not-allowed"}`}>
          <IconCarry size={15} stroke={1.9} /><span>Carry over{carryCount ? ` (${carryCount})` : ""}</span>
        </button>
      </div>

      <div className="flex-none flex items-center gap-2 py-3 px-4 border-b border-[var(--border)]">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} placeholder="Quick add for this day…  (Enter)"
          className="flex-1 min-w-0 py-2.5 px-3 bg-[var(--surface-2)] border border-[var(--border)] rounded-[var(--radius-sm)] text-sm outline-none placeholder:text-[var(--faint)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-weak)] focus:bg-[var(--surface)]" />
        <button onClick={submit} title="Add" className="shrink-0 grid place-items-center w-10 h-10 rounded-[var(--radius-sm)] bg-[var(--accent)] text-[var(--accent-text)] hover:brightness-105 active:scale-95 transition"><IconPlus size={17} stroke={2.2} /></button>
      </div>

      <div
        onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_MIME)) { e.preventDefault(); setOver(true); } }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false); }}
        onDrop={(e) => { e.preventDefault(); setOver(false); const id = e.dataTransfer.getData(DRAG_MIME); if (id) onDropBacklog(id); }}
        className={`flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-[var(--list-gap)] ${over ? "bg-[var(--accent-weak)] shadow-[inset_0_0_0_2px_var(--accent)] rounded-[var(--radius-sm)]" : ""}`}
      >
        {items.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 py-12 px-6 text-[var(--faint)] text-center border-[1.5px] border-dashed border-[var(--border-strong)] rounded-[var(--radius)] m-1.5">
            <IconArrowRight size={26} stroke={1.4} />
            <p className="m-0 text-[13.5px]">Drag a backlog item here, or quick-add above</p>
          </div>
        ) : items.map((it) => (
          <Row key={it.id} item={it} highlight={!!highlightId && it.todo_id === highlightId}
            onToggle={onToggle} onDelete={onDelete} onCyclePri={onCyclePri} onHover={onHover} />
        ))}
        {over && items.length > 0 && (
          <div className="m-1.5 p-3 rounded-[var(--radius-sm)] border-[1.5px] border-dashed border-[var(--accent)] text-[var(--accent-strong)] text-center text-[12.5px] font-medium">Drop to add to {isToday ? "today" : dateLabel}</div>
        )}
      </div>
    </section>
  );
}
