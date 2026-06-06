// TodoPage.tsx — composes the Backlog + Daily board for the Todos module.
import React from "react";
import type { Priority } from "./types";
import { Backlog } from "./Backlog";
import { Daily } from "./Daily";
import { useTodos, dateLabel } from "./useTodos";
import "./tokens.css";

export default function TodoPage({ project }: { project: string }) {
  const [toast, setToast] = React.useState<string | null>(null);
  const [search, setSearch] = React.useState("");
  const [draftPri, setDraftPri] = React.useState<Priority>("normal");
  const [highlightId, setHighlightId] = React.useState<string | null>(null);

  const showToast = (msg: string) => { setToast(msg); window.setTimeout(() => setToast((m) => (m === msg ? null : m)), 1800); };
  const todo = useTodos(project, showToast);

  const today = new Date().toISOString().slice(0, 10);

  return (
    <div data-theme="zoom" data-density="comfortable"
      className="h-full flex flex-col bg-[var(--bg)] text-[var(--text)] font-[var(--font-sans)] text-[14.5px] leading-[1.45] antialiased">
      <main className="flex-1 min-h-0 grid grid-cols-[1fr_1.04fr] gap-4 p-4 max-w-[1520px] w-full mx-auto">
        <Backlog
          items={todo.backlog} search={search} onSearch={setSearch}
          draftPri={draftPri} setDraftPri={setDraftPri} onAdd={todo.addBacklog}
          linkedIds={todo.linkedIds} highlightId={highlightId} onHover={setHighlightId}
          onToggleDone={todo.toggleBacklogDone} onDelete={todo.deleteBacklog} onCyclePri={todo.cyclePri} onSendToToday={todo.addFromBacklog}
        />
        <Daily
          dateLabel={dateLabel(todo.date)} dateISO={todo.date} isToday={todo.date === today}
          onPrev={todo.prevDay} onNext={todo.nextDay}
          items={todo.dayItems} onAdd={todo.addDaily} onToggle={todo.toggleDaily} onDelete={todo.deleteDaily} onCyclePri={todo.cycleDailyPri}
          onCarryOver={todo.carryOver} carryCount={todo.carryCount} onDropBacklog={todo.dropBacklog}
          highlightId={highlightId} onHover={setHighlightId}
        />
      </main>

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] py-2 px-4 rounded-[10px] bg-[var(--ink)] text-[var(--ink-text)] text-[13px] font-medium shadow-[0_8px_28px_-8px_rgba(0,0,0,.4)]">{toast}</div>
      )}
    </div>
  );
}
