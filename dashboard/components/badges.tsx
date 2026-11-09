// Small pieces usable from server and client components (no server-only imports).

/** Status never relies on colour alone: symbol + label + colour. */
export function StatusBadge({ status }: { status: "ok" | "error" | "running" | string }) {
  const tone =
    status === "error"
      ? "border-critical text-critical bg-critical-soft"
      : status === "running"
        ? "border-accent text-accent bg-accent-soft"
        : "border-border text-ink-2 bg-surface-1";
  const symbol = status === "error" ? "✕" : status === "running" ? "▶" : "✓";
  return (
    <span className={`inline-flex h-5 items-center gap-1 border px-1.5 font-mono text-[11px] ${tone}`}>
      <span aria-hidden>{symbol}</span>
      {status}
    </span>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-ink-2">
      <span aria-hidden className="inline-block size-2" style={{ background: `var(--kind-${kind})` }} />
      {kind}
    </span>
  );
}

export function RoleTag({ role }: { role: string | null | undefined }) {
  if (!role) return <span className="text-ink-3">–</span>;
  return (
    <span
      className={`inline-flex h-5 items-center border px-1.5 font-mono text-[11px] uppercase ${
        role === "judge" ? "border-ink-2 text-ink" : "border-border text-ink-2"
      }`}
    >
      {role}
    </span>
  );
}

export function ModelTag({ model }: { model: string | null | undefined }) {
  if (!model) return <span className="text-ink-3">–</span>;
  return <span className="inline-flex h-5 items-center bg-surface-2 px-1.5 font-mono text-[11px] text-ink-2">{model}</span>;
}

/** Amber is reserved for "slow" and warnings. */
export const SLOW_MS = 2000;

export function Duration({ ms, slowMs = SLOW_MS, text }: { ms: number | null; slowMs?: number; text: string }) {
  const slow = ms !== null && ms >= slowMs;
  return (
    <span className={`num ${slow ? "text-warning" : ""}`} title={slow ? `slow (≥ ${slowMs / 1000} s)` : undefined}>
      {text}
      {slow && <span className="ml-1 text-[10px] uppercase">slow</span>}
    </span>
  );
}
