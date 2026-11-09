// Badges usable from both server and client components (no server-only imports).

/** Status never relies on colour alone: icon + label + colour. */
export function StatusBadge({ status }: { status: "ok" | "error" | string }) {
  const error = status === "error";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
        error ? "bg-critical-bg text-critical" : "bg-surface-2 text-ink-2"
      }`}
    >
      <span aria-hidden className={error ? "text-critical" : "text-good"}>
        {error ? "✕" : "✓"}
      </span>
      {error ? "error" : "ok"}
    </span>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      <span aria-hidden className="size-2 rounded-sm" style={{ background: `var(--kind-${kind})` }} />
      {kind}
    </span>
  );
}
