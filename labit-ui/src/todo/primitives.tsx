// primitives.tsx — small shared UI atoms (Checkbox, PriorityToggle, LabelChips).
import React from "react";
import type { Priority } from "./types";
import { IconCheck } from "./icons";

const LABEL_HUE: Record<string, number> = {
  experiment: 300, paper: 255, "paper-ui": 192, bug: 18, writing: 70, infra: 235,
};
export function labelStyle(name: string): React.CSSProperties {
  const h = LABEL_HUE[name] ?? 250;
  return {
    color: `oklch(0.5 0.12 ${h})`,
    background: `oklch(0.96 0.035 ${h})`,
    borderColor: `oklch(0.9 0.05 ${h})`,
  };
}

export function Checkbox({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
      className={[
        "shrink-0 grid place-items-center w-[19px] h-[19px] rounded-md border-[1.6px] transition-all duration-150",
        checked
          ? "bg-[var(--accent)] border-[var(--accent)]"
          : "bg-[var(--surface)] border-[var(--border-strong)] hover:border-[var(--accent)]",
      ].join(" ")}
    >
      <span className={["grid place-items-center text-[var(--accent-text)] transition-all duration-150",
        checked ? "opacity-100 scale-100" : "opacity-0 scale-50"].join(" ")}>
        <IconCheck size={13} stroke={3} />
      </span>
    </button>
  );
}

const PRI_CYCLE: Record<Priority, Priority> = { low: "normal", normal: "high", high: "low" };
const PRI_MARK: Record<Priority, string> = {
  high: "bg-[var(--pri-high)]",
  normal: "bg-[var(--pri-normal)]",
  low: "bg-transparent shadow-[inset_0_0_0_1.6px_var(--faint)]",
};

export function PriorityToggle({ level, onChange, dimmed }: { level: Priority; onChange: (p: Priority) => void; dimmed?: boolean }) {
  return (
    <button
      type="button"
      title={`Priority: ${level} · click to change`}
      onClick={(e) => { e.stopPropagation(); onChange(PRI_CYCLE[level]); }}
      className="group shrink-0 grid place-items-center w-[18px] h-6 p-0 bg-transparent border-0"
    >
      <span className={["w-[9px] h-[9px] rounded-full transition-transform duration-150 group-hover:scale-125",
        PRI_MARK[level], dimmed ? "opacity-40" : ""].join(" ")} />
    </button>
  );
}

export function LabelChips({ labels }: { labels: string[] }) {
  if (!labels?.length) return null;
  return (
    <span className="shrink-0 inline-flex flex-wrap gap-1">
      {labels.map((l) => (
        <span key={l} style={labelStyle(l)}
          className="font-[var(--font-mono)] text-[10.5px] font-medium px-[7px] py-px rounded-md border whitespace-nowrap">
          {l}
        </span>
      ))}
    </span>
  );
}
