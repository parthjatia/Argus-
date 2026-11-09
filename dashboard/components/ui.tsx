// Shared UI pieces for server components (ErrorState needs the server-side API client).

import { ApiError } from "@/lib/api";

export { KindBadge, StatusBadge } from "@/components/badges";

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-border bg-surface-1 ${className}`}>{children}</section>
  );
}

export function PageTitle({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-ink-2">{subtitle}</p>}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  const hint =
    error instanceof ApiError && error.status === 401
      ? "The dashboard's ARGUS_API_KEY was rejected. Run `make seed` and put the key in .env."
      : error instanceof ApiError && error.status === 0
        ? "Is the server running (`docker compose up -d`) and ARGUS_API_KEY set in .env?"
        : null;
  return (
    <Card className="p-6">
      <p className="flex items-center gap-2 font-medium text-critical">
        <span aria-hidden>✕</span> Couldn&apos;t load data
      </p>
      <p className="mt-2 font-mono text-sm text-ink-2">{message}</p>
      {hint && <p className="mt-3 text-sm text-ink-2">{hint}</p>}
    </Card>
  );
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-ink-3 uppercase">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-ink-3">{hint}</p>}
    </Card>
  );
}
