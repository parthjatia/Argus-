// Layout primitives and the three panel states (loading / empty / error). Server-safe;
// ErrorState imports the server-side API client only for its error type.

import { ApiError } from "@/lib/api";

export { Duration, KindBadge, ModelTag, RoleTag, StatusBadge } from "@/components/badges";

export function PageHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-3 flex min-h-8 flex-wrap items-center gap-x-4 gap-y-1">
      <h1 className="text-base font-semibold tracking-tight">{title}</h1>
      {children && <div className="flex flex-wrap items-center gap-3 text-xs text-ink-2">{children}</div>}
    </div>
  );
}

export function Panel({
  title,
  right,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`flex min-w-0 flex-col border border-border bg-surface-1 ${className}`}>
      {(title || right) && (
        <header className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-border px-2.5">
          <h2 className="truncate text-[11px] font-medium tracking-wider text-ink-2 uppercase">{title}</h2>
          {right && <div className="flex items-center gap-2 text-[11px] text-ink-3">{right}</div>}
        </header>
      )}
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export function Tile({
  label,
  value,
  hint,
  tone = "normal",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "normal" | "critical" | "warning" | "accent";
}) {
  const color =
    tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : tone === "accent" ? "text-accent" : "text-ink";
  return (
    <div className="min-w-0 border border-border bg-surface-1 px-2.5 py-2">
      <p className="truncate text-[11px] tracking-wider text-ink-3 uppercase">{label}</p>
      <p className={`num mt-0.5 truncate text-xl leading-7 font-medium ${color}`}>{value}</p>
      <p className="num truncate text-[11px] text-ink-3">{hint ?? " "}</p>
    </div>
  );
}

export function TileSkeleton({ label }: { label: string }) {
  return (
    <div className="border border-border bg-surface-1 px-2.5 py-2" aria-busy>
      <p className="text-[11px] tracking-wider text-ink-3 uppercase">{label}</p>
      <div className="skeleton mt-1.5 h-5 w-20" />
      <div className="skeleton mt-1.5 h-3 w-28" />
    </div>
  );
}

export function PanelSkeleton({ title, rows = 6, className = "" }: { title: string; rows?: number; className?: string }) {
  return (
    <Panel title={title} right={<span>loading…</span>} className={className}>
      <div className="space-y-2 p-2.5" aria-busy aria-label={`Loading ${title}`}>
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="skeleton h-4" style={{ width: `${92 - ((i * 17) % 40)}%` }} />
        ))}
      </div>
    </Panel>
  );
}

export function Empty({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`grid min-h-24 place-items-center p-4 text-center text-xs text-ink-3 ${className}`}>{children}</div>;
}

function errorText(error: unknown): { message: string; hint: string | null } {
  const message = error instanceof Error ? error.message : String(error);
  const hint =
    error instanceof ApiError && error.status === 401
      ? "The dashboard's ARGUS_API_KEY was rejected. Run `make seed` and put the key in .env."
      : error instanceof ApiError && (error.status === 0 || error.status === 503)
        ? "Is the server running (`docker compose up -d`)? See /system."
        : null;
  return { message, hint };
}

/** Error state inside a panel: the rest of the page keeps working. */
export function PanelError({ error }: { error: unknown }) {
  const { message, hint } = errorText(error);
  return (
    <div className="m-2.5 border border-critical bg-critical-soft px-2.5 py-2 text-xs" role="alert">
      <p className="font-medium text-critical">
        <span aria-hidden>✕ </span>Couldn&apos;t load this panel
      </p>
      <p className="mt-1 font-mono break-words text-ink-2">{message.slice(0, 400)}</p>
      {hint && <p className="mt-1 text-ink-2">{hint}</p>}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  return (
    <Panel title="Error">
      <PanelError error={error} />
    </Panel>
  );
}

/** Right-aligned monospace table cell for numbers. */
export function Td({ children, num = false, className = "" }: { children: React.ReactNode; num?: boolean; className?: string }) {
  return <td className={`h-7 px-2 whitespace-nowrap ${num ? "num text-right" : ""} ${className}`}>{children}</td>;
}

export function Th({ children, num = false, className = "" }: { children?: React.ReactNode; num?: boolean; className?: string }) {
  return (
    <th
      className={`sticky top-0 h-7 border-b border-border bg-surface-1 px-2 text-[11px] font-medium whitespace-nowrap text-ink-3 ${
        num ? "text-right" : "text-left"
      } ${className}`}
    >
      {children}
    </th>
  );
}
