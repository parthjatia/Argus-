import Link from "next/link";
import { api } from "@/lib/api";
import { formatCost, formatDuration, formatRelative, formatTime, formatTokens, rangeStart } from "@/lib/format";
import type { TraceList } from "@/lib/types";
import { Card, ErrorState, PageTitle, StatusBadge } from "@/components/ui";

export const dynamic = "force-dynamic"; // always fresh data; reads env at request time

type SearchParams = Record<string, string | undefined>;

const FILTER_KEYS = ["range", "status", "model", "min_cost", "min_duration_ms"] as const;

export default async function TracesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const range = sp.range ?? "24h";

  let data: TraceList;
  try {
    data = await api.listTraces({
      status: sp.status,
      model: sp.model,
      min_cost: sp.min_cost,
      min_duration_ms: sp.min_duration_ms,
      start: rangeStart(range),
      cursor: sp.cursor,
      limit: 50,
    });
  } catch (error) {
    return (
      <>
        <PageTitle title="Traces" />
        <ErrorState error={error} />
      </>
    );
  }

  const filters = new URLSearchParams();
  for (const k of FILTER_KEYS) if (sp[k]) filters.set(k, sp[k]!);
  const nextHref = data.next_cursor
    ? `/traces?${new URLSearchParams([...filters, ["cursor", data.next_cursor]])}`
    : null;
  const models = [...new Set(data.traces.flatMap((t) => t.models))].sort();
  const now = Date.now();

  return (
    <>
      <PageTitle title="Traces" subtitle="Every debate (or any traced request) is one trace. Newest first." />

      {/* Plain GET form: filters live in the URL, so views are shareable and work without JS. */}
      <form method="get" className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Time range">
          <select name="range" defaultValue={range} className="field">
            <option value="1h">Last hour</option>
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="all">All time</option>
          </select>
        </Field>
        <Field label="Status">
          <select name="status" defaultValue={sp.status ?? ""} className="field">
            <option value="">Any</option>
            <option value="ok">ok</option>
            <option value="error">error</option>
          </select>
        </Field>
        <Field label="Model">
          <input name="model" list="models" defaultValue={sp.model ?? ""} placeholder="any" className="field w-36" />
          <datalist id="models">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Field>
        <Field label="Min cost ($)">
          <input name="min_cost" type="number" step="any" min="0" defaultValue={sp.min_cost ?? ""} className="field w-28" />
        </Field>
        <Field label="Min duration (ms)">
          <input name="min_duration_ms" type="number" min="0" defaultValue={sp.min_duration_ms ?? ""} className="field w-32" />
        </Field>
        <button type="submit" className="h-9 rounded-md bg-accent px-4 text-sm font-medium text-white hover:opacity-90">
          Apply
        </button>
        <Link href="/traces" className="h-9 px-2 text-sm leading-9 text-ink-2 hover:text-ink">
          Reset
        </Link>
      </form>

      <Card className="overflow-x-auto">
        {data.traces.length === 0 ? (
          <div className="p-10 text-center text-sm text-ink-2">
            <p className="font-medium text-ink">No traces match.</p>
            <p className="mt-1">
              Widen the time range, or generate some with <code className="font-mono">make demo</code> /{" "}
              <code className="font-mono">make traffic N=50</code>.
            </p>
          </div>
        ) : (
          <table className="w-full min-w-[820px] text-sm">
            <thead className="border-b border-border text-left text-xs text-ink-3">
              <tr>
                <Th>Name</Th>
                <Th>Started</Th>
                <Th right>Duration</Th>
                <Th right>Cost</Th>
                <Th right>Tokens</Th>
                <Th>Status</Th>
                <Th>Models</Th>
                <Th right>Spans</Th>
              </tr>
            </thead>
            <tbody>
              {data.traces.map((t) => (
                <tr key={t.trace_id} className="border-b border-border last:border-0 hover:bg-surface-2/60">
                  <td className="px-4 py-2.5">
                    <Link href={`/traces/${t.trace_id}`} className="font-medium text-ink hover:text-accent">
                      {t.name}
                    </Link>
                    <span className="ml-2 font-mono text-xs text-ink-3">{t.trace_id.slice(0, 8)}</span>
                    {!t.has_root && <span className="ml-2 text-xs text-ink-3">(root pending)</span>}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap text-ink-2" title={formatTime(t.start_time)}>
                    {formatRelative(t.start_time, now)}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatDuration(t.duration_ms)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatCost(t.total_cost_usd)}</td>
                  <td className="px-4 py-2.5 text-right text-ink-2 tabular-nums">
                    {formatTokens((t.input_tokens ?? 0) + (t.output_tokens ?? 0) || null)}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={t.status} />
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {t.models.map((m) => (
                        <span key={m} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-ink-2">
                          {m}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-right whitespace-nowrap tabular-nums">
                    {t.span_count}
                    {t.error_count > 0 && (
                      <span className="ml-1.5 text-xs text-critical" title={`${t.error_count} span(s) failed`}>
                        ✕{t.error_count}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="mt-4 flex items-center justify-between text-sm">
        <span className="text-ink-3">{data.traces.length} trace(s) on this page</span>
        <div className="flex gap-4">
          {sp.cursor && (
            <Link href={`/traces?${filters}`} className="text-ink-2 hover:text-ink">
              ← First page
            </Link>
          )}
          {nextHref && (
            <Link href={nextHref} className="font-medium text-accent hover:opacity-80">
              Next page →
            </Link>
          )}
        </div>
      </div>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-2">
      {label}
      {children}
    </label>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={`px-4 py-2.5 font-medium ${right ? "text-right" : ""}`}>{children}</th>;
}
