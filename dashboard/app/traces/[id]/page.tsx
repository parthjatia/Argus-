import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";
import { parseRange, withRange } from "@/lib/range";
import type { TraceDetail } from "@/lib/types";
import { Id } from "@/components/copy";
import { ErrorState, PageHeader, StatusBadge, Tile } from "@/components/ui";
import { TraceView } from "@/components/TraceView";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | undefined>> };

export default async function TracePage({ params, searchParams }: Props) {
  const { id } = await params;
  const sp = await searchParams;
  const range = parseRange(sp.range);
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
  const isDebate = root?.name === "debate" || spans.some((s) => s.name === "debate.turn");

  return (
    <>
      <div className="mb-1 flex items-center gap-2 text-xs text-ink-3">
        <Link href={withRange("/traces", range)} className="hover:text-ink">
          ← Traces
        </Link>
        <span>/</span>
        <Id value={trace.trace_id} />
        {isDebate && (
          <Link href={withRange(`/debates/${trace.trace_id}`, range)} className="btn ml-auto">
            Debate view →
          </Link>
        )}
      </div>
      <PageHeader title={root?.name ?? spans[0].name}>
        <StatusBadge status={root ? root.status : "running"} />
        <span className="num text-ink-3">{formatTime(new Date(start).toISOString())}</span>
      </PageHeader>

      <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <Tile label="Duration" value={formatDuration(end - start)} />
        <Tile label="Spans" value={spans.length} hint={orphans ? `${orphans} with parent missing` : "all parents present"} />
        <Tile label="Failed spans" value={errors} tone={errors ? "critical" : "normal"} />
        <Tile label="Cost" value={formatCost(cost)} />
        <Tile label="Tokens" value={formatTokens(tokens)} />
        <Tile label="Models" value={<span className="text-xs">{models.join(", ") || "–"}</span>} />
      </div>

      {orphans > 0 && (
        <p className="mb-2 border border-warning bg-warning-soft px-2.5 py-1.5 text-xs text-ink-2">
          {orphans} span(s) reference a parent that hasn&apos;t arrived yet (the parent may still be running). They&apos;re
          shown at the top level, marked <em>parent missing</em>.
        </p>
      )}

      <TraceView spans={spans} initialSpanId={sp.span ?? null} />
    </>
  );
}
