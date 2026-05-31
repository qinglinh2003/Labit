import type { ChatMode } from "../api/chat";

const MODES: { value: ChatMode; label: string }[] = [
  { value: "single", label: "Single" },
  { value: "parallel", label: "Parallel" },
  { value: "round_robin", label: "Round Robin" },
];

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
    <div className="flex items-center gap-2 border-b border-slate-200 px-3 py-2">
      <div className="flex rounded-md border border-slate-300 text-xs">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            disabled={disabled}
            className={`px-2.5 py-1 ${
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
        className="ml-auto rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium hover:bg-slate-100 disabled:opacity-50"
        onClick={onSwap}
        title="Switch first agent"
      >
        First: {firstAgent === "claude" ? "Claude" : "Codex"}
      </button>
    </div>
  );
}
