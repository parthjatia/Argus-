import Link from "next/link";
import { api } from "@/lib/api";
import { OTHER, modelColors, seriesName } from "@/lib/colors";
import { formatCost, formatDuration, formatTokens } from "@/lib/format";
import { Card, ErrorState, PageTitle, Stat } from "@/components/ui";
import { CostChart, ErrorRateChart, Legend, type CostRow, type ErrorRow } from "@/components/charts";

export const dynamic = "force-dynamic";

const DAY_OPTIONS = [1, 7, 14, 30];

export default async function MetricsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const days = DAY_OPTIONS.includes(Number(sp.days)) ? Number(sp.days) : 7;
  const bucket = days <= 7 ? "hour" : "day";

  let data;
  try {
    const [cost, latency, errors, allCost] = await Promise.all([
      api.cost(days),
      api.latency(days),
      api.errors(days, bucket),
      api.cost(90), // stable model → colour mapping, independent of the selected range
    ]);
    data = { cost, latency, errors, allModels: allCost.map((c) => c.model) };
  } catch (error) {
    return (
      <>
        <PageTitle title="Metrics" />
        <ErrorState error={error} />
      </>
    );
  }

  const colors = modelColors([...data.allModels, ...data.cost.map((c) => c.model), ...data.latency.map((l) => l.model)]);

  // cost: pivot [{day, model, cost}] → [{day, modelA: $, modelB: $}]
  const seriesNames = [...new Set(data.cost.map((c) => seriesName(c.model, colors)))].sort((a, b) =>
    a === OTHER ? 1 : b === OTHER ? -1 : a.localeCompare(b),
  );
  const byDay = new Map<string, CostRow>();
  for (const c of data.cost) {
    const key = c.day.slice(0, 10); // YYYY-MM-DD (API buckets are UTC midnights)
    const row = byDay.get(key) ?? { day: c.day };
    const name = seriesName(c.model, colors);
    row[name] = (Number(row[name]) || 0) + (c.cost_usd ?? 0);
    byDay.set(key, row);
  }
  // Every day of the range gets a slot (zero if no calls), so bars sit on a real time axis.
  const today = Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), new Date().getUTCDate());
  const costRows: CostRow[] = Array.from({ length: days }, (_, i) => {
    const day = new Date(today - (days - 1 - i) * 86_400_000).toISOString();
    const row: CostRow = { day, ...(byDay.get(day.slice(0, 10)) ?? {}) };
    row.day = day;
    for (const n of seriesNames) row[n] ??= 0;
    return row;
  });
  const series = seriesNames.map((name) => ({ name, color: colors.get(name)! }));

  const errorRows: ErrorRow[] = data.errors.map((e) => ({
    bucket: e.bucket,
    rate: e.error_rate === null ? null : e.error_rate * 100,
    traces: e.traces,
    errors: e.trace_errors,
  }));

  const totalCost = data.cost.reduce((a, c) => a + (c.cost_usd ?? 0), 0);
  const unpriced = data.cost.filter((c) => c.cost_usd === null).reduce((a, c) => a + c.calls, 0);
  const calls = data.cost.reduce((a, c) => a + c.calls, 0);
  const tokens = data.cost.reduce((a, c) => a + c.input_tokens + c.output_tokens, 0);
  const traces = data.errors.reduce((a, e) => a + e.traces, 0);
  const failed = data.errors.reduce((a, e) => a + e.trace_errors, 0);
  const maxP95 = Math.max(1, ...data.latency.map((l) => l.p95_ms));

  return (
    <>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <PageTitle title="Metrics" subtitle="Cost and latency of LLM calls, and how often traces fail. Times in UTC." />
        <nav aria-label="Time range" className="flex rounded-md border border-border bg-surface-1 p-0.5 text-sm">
          {DAY_OPTIONS.map((d) => (
            <Link
              key={d}
              href={`/metrics?days=${d}`}
              aria-current={d === days ? "page" : undefined}
              className={`rounded px-3 py-1 ${d === days ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink"}`}
            >
              {d === 1 ? "24h" : `${d}d`}
            </Link>
          ))}
        </nav>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total cost" value={formatCost(totalCost)} hint={unpriced ? `${unpriced} call(s) without a price` : undefined} />
        <Stat label="LLM calls" value={calls.toLocaleString("en")} hint={`${formatTokens(tokens)} tokens`} />
        <Stat label="Traces" value={traces.toLocaleString("en")} />
        <Stat
          label="Trace error rate"
          value={traces ? `${((failed / traces) * 100).toFixed(1)}%` : "–"}
          hint={traces ? `${failed} failed` : undefined}
        />
      </div>

      <Card className="mb-4 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-medium">Cost per day, by model</h2>
          {series.length > 1 && <Legend items={series.map((s) => ({ label: s.name, color: s.color }))} />}
        </div>
        {data.cost.length ? <CostChart rows={costRows} series={series} /> : <Empty />}
        <DataTable
          head={["Day", "Model", "Calls", "Tokens", "Cost"]}
          rows={data.cost.map((c) => [
            c.day.slice(0, 10),
            c.model,
            String(c.calls),
            formatTokens(c.input_tokens + c.output_tokens),
            formatCost(c.cost_usd),
          ])}
        />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <h2 className="text-sm font-medium">LLM latency per model</h2>
          <p className="mt-0.5 mb-3 text-xs text-ink-3">
            Bar: median (p50), lighter extension: up to p95. One scale for all rows.
          </p>
          {data.latency.length ? (
            <ul className="space-y-3">
              {data.latency.map((l) => {
                const color = colors.get(l.model) ?? "var(--series-other)";
                return (
                  <li key={l.model} className="grid grid-cols-[minmax(0,9rem)_1fr] items-center gap-3 text-sm">
                    <div className="min-w-0">
                      <p className="flex items-center gap-1.5 truncate font-mono text-xs">
                        <span aria-hidden className="size-2.5 shrink-0 rounded-sm" style={{ background: color }} />
                        {l.model}
                      </p>
                      <p className="text-xs text-ink-3">{l.calls} calls</p>
                    </div>
                    <div>
                      <div
                        className="relative h-3 rounded-[4px] bg-surface-2"
                        title={`${l.model}: p50 ${formatDuration(l.p50_ms)}, p95 ${formatDuration(l.p95_ms)}`}
                      >
                        <span
                          className="absolute inset-y-0 left-0 rounded-[4px] opacity-35"
                          style={{ width: `${(l.p95_ms / maxP95) * 100}%`, background: color }}
                        />
                        <span
                          className="absolute inset-y-0 left-0 rounded-[4px]"
                          style={{ width: `${(l.p50_ms / maxP95) * 100}%`, background: color }}
                        />
                      </div>
                      <p className="mt-1 flex justify-between text-xs text-ink-2 tabular-nums">
                        <span>p50 {formatDuration(l.p50_ms)}</span>
                        <span>p95 {formatDuration(l.p95_ms)}</span>
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <Empty />
          )}
        </Card>

        <Card className="p-4">
          <h2 className="text-sm font-medium">Trace error rate</h2>
          <p className="mt-0.5 mb-3 text-xs text-ink-3">
            Share of traces whose root span failed, per {bucket}. Gaps = no traces.
          </p>
          {traces ? <ErrorRateChart rows={errorRows} bucket={bucket} /> : <Empty />}
          <DataTable
            head={[bucket === "hour" ? "Hour" : "Day", "Traces", "Failed", "Error rate"]}
            rows={data.errors
              .filter((e) => e.traces > 0)
              .map((e) => [
                e.bucket.slice(0, bucket === "hour" ? 13 : 10).replace("T", " ") + (bucket === "hour" ? ":00" : ""),
                String(e.traces),
                String(e.trace_errors),
                e.error_rate === null ? "–" : `${(e.error_rate * 100).toFixed(1)}%`,
              ])}
          />
        </Card>
      </div>
    </>
  );
}

function Empty() {
  return (
    <p className="grid h-40 place-items-center text-sm text-ink-3">
      No data in this range yet. Try <code className="mx-1 font-mono">make traffic N=50</code>.
    </p>
  );
}

function DataTable({ head, rows }: { head: string[]; rows: string[][] }) {
  if (!rows.length) return null;
  return (
    <details className="mt-3 text-xs">
      <summary className="cursor-pointer text-ink-3 hover:text-ink-2">Show data table</summary>
      <div className="mt-2 max-h-64 overflow-auto">
        <table className="w-full tabular-nums">
          <thead className="text-left text-ink-3">
            <tr>
              {head.map((h, i) => (
                <th key={h} className={`py-1 pr-3 font-medium ${i > 1 ? "text-right" : ""}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="text-ink-2">
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-border">
                {r.map((c, j) => (
                  <td key={j} className={`py-1 pr-3 ${j > 1 ? "text-right" : ""}`}>
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
