import type { ChatMode } from "../api/chat";
import { ClaudeIcon, CodexIcon } from "./AgentIcons";

const MODES: { value: ChatMode; label: string }[] = [
  { value: "single", label: "1" },
  { value: "parallel", label: "2" },
  { value: "round_robin", label: "RR" },
];

const MODE_LABELS: Record<ChatMode, string> = {
  single: "Single agent",
  parallel: "Parallel agents",
  round_robin: "Round Robin",
};

export default function ModeSwapBar({
  mode,
  firstAgent,
  onModeChange,
  onSwap,
  disabled,
}: {
  mode: ChatMode;
  firstAgent: string;
  onModeChange: (mode: ChatMode) => void;
  onSwap: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-1 border-b border-slate-200 px-2 py-1.5">
      <div className="flex shrink-0 overflow-hidden rounded-md border border-slate-300 text-[11px]">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            disabled={disabled}
            title={MODE_LABELS[m.value]}
            aria-label={MODE_LABELS[m.value]}
            className={`h-7 min-w-8 px-2 font-medium ${
              mode === m.value
                ? "bg-slate-800 text-white"
                : "bg-white text-slate-600 hover:bg-slate-100"
            } first:rounded-l-md last:rounded-r-md`}
            onClick={() => onModeChange(m.value)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        disabled={disabled}
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-slate-300 bg-white text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        onClick={onSwap}
        title={`First agent: ${firstAgent === "claude" ? "Claude" : "Codex"}. Click to switch.`}
        aria-label={`First agent: ${firstAgent === "claude" ? "Claude" : "Codex"}. Click to switch.`}
      >
        {firstAgent === "claude" ? (
          <ClaudeIcon size={14} className="text-orange-500" />
        ) : (
          <CodexIcon size={14} className="text-emerald-500" />
        )}
      </button>
    </div>
  );
}
