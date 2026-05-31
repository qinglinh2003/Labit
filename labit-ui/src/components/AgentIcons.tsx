/** SVG icons for Claude and Codex agents */

export function ClaudeIcon({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
    >
      {/* Claude "sparkle" / anthropic icon - simplified */}
      <path
        d="M16.98 3.38L12 13.13 7.02 3.38a.5.5 0 0 0-.9 0L1.14 13.32a.5.5 0 0 0 .04.5l5.3 8.14a.5.5 0 0 0 .42.22h10.2a.5.5 0 0 0 .42-.22l5.3-8.14a.5.5 0 0 0 .04-.5L17.88 3.38a.5.5 0 0 0-.9 0z"
        fill="currentColor"
      />
    </svg>
  );
}

export function CodexIcon({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
    >
      {/* OpenAI hexagon-style icon - simplified */}
      <path
        d="M12 2L3 7v10l9 5 9-5V7l-9-5zm0 2.18L18.36 7.5 12 10.82 5.64 7.5 12 4.18zM5 9.06l6 3.32v6.44l-6-3.32V9.06zm8 9.76v-6.44l6-3.32v6.44l-6 3.32z"
        fill="currentColor"
      />
    </svg>
  );
}
