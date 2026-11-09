import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";
import type { TraceDetail } from "@/lib/types";
import { ErrorState, StatusBadge } from "@/components/ui";
import { TraceView } from "@/components/TraceView";

export const dynamic = "force-dynamic";

export default async function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let trace: TraceDetail | null;
  try {
    trace = await api.getTrace(id);
  } catch (error) {
    return <ErrorState error={error} />;
  }
  if (!trace) notFound();

  const spans = trace.spans;
  const ids = new Set(spans.map((s) => s.span_id));
  const root = spans.find((s) => s.parent_span_id === null);
  const start = Math.min(...spans.map((s) => Date.parse(s.start_time)));
  const end = Math.max(...spans.map((s) => Date.parse(s.end_time)));
  const sum = (f: (s: (typeof spans)[number]) => number | null) =>
    spans.reduce<number | null>((acc, s) => (f(s) === null ? acc : (acc ?? 0) + f(s)!), null);
  const cost = sum((s) => s.cost_usd);
  const tokens = sum((s) => (s.input_tokens ?? 0) + (s.output_tokens ?? 0) || null);
  const errors = spans.filter((s) => s.status === "error").length;
  const orphans = spans.filter((s) => s.parent_span_id && !ids.has(s.parent_span_id)).length;
  const models = [...new Set(spans.map((s) => s.model).filter(Boolean))];

  return (
    <>
      <Link href="/traces" className="text-sm text-ink-2 hover:text-ink">
        ← Traces
      </Link>
      <div className="mt-2 mb-5 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{root?.name ?? spans[0].name}</h1>
        <StatusBadge status={root?.status ?? (errors ? "error" : "ok")} />
        <span className="font-mono text-xs text-ink-3">{trace.trace_id}</span>
      </div>

      <dl className="mb-5 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
        <Meta label="Started">{formatTime(new Date(start).toISOString())}</Meta>
        <Meta label="Duration">{formatDuration(end - start)}</Meta>
        <Meta label="Spans">
          {spans.length}
          {errors > 0 && <span className="ml-1.5 text-critical">✕ {errors} failed</span>}
        </Meta>
        <Meta label="Cost">{formatCost(cost)}</Meta>
        <Meta label="Tokens">{formatTokens(tokens)}</Meta>
        <Meta label="Models">{models.join(", ") || "–"}</Meta>
      </dl>

      {orphans > 0 && (
        <p className="mb-4 rounded-md border border-border bg-surface-1 px-3 py-2 text-sm text-ink-2">
          {orphans} span(s) reference a parent that hasn&apos;t arrived yet (the parent may still be
          running). They&apos;re shown at the top level, marked <em>parent missing</em>.
        </p>
      )}

      <TraceView spans={spans} />
    </>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="mt-0.5 font-medium tabular-nums">{children}</dd>
    </div>
  );
}
