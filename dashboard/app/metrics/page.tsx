import { Suspense } from "react";
import { api } from "@/lib/api";
import { OTHER, modelColors, seriesName } from "@/lib/colors";
import { formatCost, formatDuration, formatTokens } from "@/lib/format";
import { bucketLabel, bucketStarts, parseRange, rangeWindow, type Window } from "@/lib/range";
import { ErrorStepLine, HBars, Legend, TimeBars, type TimeRow } from "@/components/charts";
import { Empty, PageHeader, Panel, PanelError, PanelSkeleton, Td, Th, Tile, TileSkeleton } from "@/components/ui";

export const dynamic = "force-dynamic";

type SP = Promise<Record<string, string | undefined>>;

export default async function MetricsPage({ searchParams }: { searchParams: SP }) {
  const w = rangeWindow(parseRange((await searchParams).range));
  return (
    <>
      <PageHeader title="Metrics">
        <span>
          LLM cost, latency, tokens and errors in the last <span className="num text-ink">{w.range}</span>. Buckets are
          UTC-aligned.
        </span>
      </PageHeader>
      <Suspense fallback={<MetricsSkeleton />}>
        <MetricsBody w={w} />
      </Suspense>
    </>
  );
}

function MetricsSkeleton() {
  return (
    <div className="grid gap-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        {["Cost", "LLM calls", "Tokens", "p95 latency", "LLM error rate"].map((l) => (
          <TileSkeleton key={l} label={l} />
        ))}
      </div>
      <PanelSkeleton title="Cost over time, by model" rows={8} />
      <div className="grid gap-2 lg:grid-cols-2">
        <PanelSkeleton title="Latency per model" />
        <PanelSkeleton title="Error rate" />
      </div>
    </div>
  );
}

// "Cost per day" for 7 days; shorter ranges use their own bucket so the chart isn't one bar.
function costBucket(w: Window): number {
  return w.range === "7d" ? 86_400 : w.bucketS;
}

async function MetricsBody({ w }: { w: Window }) {
  // Each panel fails on its own: one rejected request shows an error in that panel only.
  const [cost, models, series, roles, allModels] = await Promise.allSettled([
    api.costByModel(w, costBucket(w)),
    api.models(w),
    api.timeseries(w),
    api.breakdown(w, "role"),
    api.models({ ...w, start: new Date(Date.now() - 90 * 86_400_000).toISOString() }), // stable colours
  ]);

  const known = allModels.status === "fulfilled" ? allModels.value.map((m) => m.model) : [];
  const colors = modelColors([
    ...known,
    ...(models.status === "fulfilled" ? models.value.map((m) => m.model) : []),
    ...(cost.status === "fulfilled" ? cost.value.map((c) => c.model) : []),
  ]);
  const color = (m: string) => colors.get(seriesName(m, colors)) ?? "var(--series-other)";

  return (
    <div className="grid gap-2">
      {models.status === "fulfilled" ? <Tiles models={models.value} /> : <Panel title="Totals"><PanelError error={models.reason} /></Panel>}

      <Panel
        title={`Cost per ${bucketLabel(costBucket(w))}, by model`}
        right={cost.status === "fulfilled" ? <CostLegend models={cost.value.map((c) => c.model)} colors={colors} /> : null}
      >
        <div className="p-2">
          {cost.status === "rejected" ? (
            <PanelError error={cost.reason} />
          ) : cost.value.length === 0 ? (
            <Empty>No LLM calls in this range.</Empty>
          ) : (
            <CostChart rows={cost.value} w={w} colors={colors} />
          )}
        </div>
      </Panel>

      <div className="grid gap-2 lg:grid-cols-3">
        <Panel title="Cost per debate role" right={<span>metadata.role</span>}>
          <div className="p-2.5">
            {roles.status === "rejected" ? (
              <PanelError error={roles.reason} />
            ) : roles.value.length === 0 ? (
              <Empty>No LLM calls in this range.</Empty>
            ) : (
              <HBars
                format="cost"
                rows={roles.value.map((r) => ({
                  key: r.value ?? "none",
                  label: r.value ?? "(no role: older traces)",

                  segments: [{ value: r.cost_usd ?? 0, color: r.value === "judge" ? "var(--series-2)" : "var(--accent)", label: "cost" }],
                  note: `${formatCost(r.cost_usd)} · ${r.calls}`,
                }))}
              />
            )}
          </div>
        </Panel>

        <Panel title="Latency per model" right={<span>bar p50 · light to p95</span>}>
          <div className="p-2.5">
            {models.status === "rejected" ? (
              <PanelError error={models.reason} />
            ) : models.value.length === 0 ? (
              <Empty>No LLM calls in this range.</Empty>
            ) : (
              <LatencyBars models={models.value} color={color} />
            )}
          </div>
        </Panel>

        <Panel title="Tokens per model" right={<Legend items={[{ label: "input", color: "var(--accent)" }, { label: "output", color: "var(--series-2)" }]} />}>
          <div className="p-2.5">
            {models.status === "rejected" ? (
              <PanelError error={models.reason} />
            ) : models.value.length === 0 ? (
              <Empty>No LLM calls in this range.</Empty>
            ) : (
              <HBars
                format="tokens"
                rows={models.value.map((m) => ({
                  key: m.model,
                  label: m.model,
                  segments: [
                    { value: m.input_tokens, color: "var(--accent)", label: "input" },
                    { value: m.output_tokens, color: "var(--series-2)", label: "output" },
                  ],
                }))}
              />
            )}
          </div>
        </Panel>
      </div>

      <Panel title={`Trace error rate per ${w.bucketLabel}`} right={<span>failed root spans / traces · gaps = no traces</span>}>
        <div className="p-2">
          {series.status === "rejected" ? (
            <PanelError error={series.reason} />
          ) : !series.value.some((r) => r.traces) ? (
            <Empty>No finished traces in this range.</Empty>
          ) : (
            <ErrorStepLine
              bucketS={w.bucketS}
              rows={series.value.map((r) => ({
                bucket: r.bucket,
                rate: r.error_rate === null ? null : r.error_rate * 100,
                traces: r.traces,
                errors: r.trace_errors,
              }))}
            />
          )}
        </div>
      </Panel>

      <Panel title="Model comparison">
        {models.status === "rejected" ? (
          <PanelError error={models.reason} />
        ) : models.value.length === 0 ? (
          <Empty>No LLM calls in this range.</Empty>
        ) : (
          <ModelTable models={models.value} color={color} />
        )}
      </Panel>
    </div>
  );
}

type Models = Awaited<ReturnType<typeof api.models>>;

function Tiles({ models }: { models: Models }) {
  const calls = models.reduce((a, m) => a + m.calls, 0);
  const errors = models.reduce((a, m) => a + m.errors, 0);
  const cost = models.reduce<number | null>((a, m) => (m.cost_usd === null ? a : (a ?? 0) + m.cost_usd), null);
  const tokens = models.reduce((a, m) => a + m.input_tokens + m.output_tokens, 0);
  const p95 = Math.max(0, ...models.map((m) => m.p95_ms));
  const unpriced = models.filter((m) => m.cost_usd === null).map((m) => m.model);
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
      <Tile label="Cost" value={formatCost(cost)} hint={unpriced.length ? `no price: ${unpriced.join(", ")}` : "all models priced"} />
      <Tile label="LLM calls" value={calls.toLocaleString("en")} hint={`${models.length} model(s)`} />
      <Tile label="Tokens" value={formatTokens(tokens)} />
      <Tile label="p95 latency" value={calls ? formatDuration(p95) : "–"} tone={p95 >= 2000 ? "warning" : "normal"} hint="slowest model" />
      <Tile
        label="LLM error rate"
        value={calls ? `${((errors / calls) * 100).toFixed(1)}%` : "–"}
        tone={errors ? "critical" : "normal"}
        hint={`${errors} failed calls`}
      />
    </div>
  );
}

function CostLegend({ models, colors }: { models: string[]; colors: Map<string, string> }) {
  const names = [...new Set(models.map((m) => seriesName(m, colors)))].sort((a, b) => (a === OTHER ? 1 : b === OTHER ? -1 : a.localeCompare(b)));
  return <Legend items={names.map((n) => ({ label: n, color: colors.get(n)! }))} />;
}

function CostChart({ rows, w, colors }: { rows: Awaited<ReturnType<typeof api.costByModel>>; w: Window; colors: Map<string, string> }) {
  const bucketS = costBucket(w);
  const names = [...new Set(rows.map((c) => seriesName(c.model, colors)))].sort((a, b) =>
    a === OTHER ? 1 : b === OTHER ? -1 : a.localeCompare(b),
  );
  const byBucket = new Map<number, TimeRow>();
  for (const c of rows) {
    const key = Date.parse(c.bucket);
    const row = byBucket.get(key) ?? { bucket: c.bucket };
    const name = seriesName(c.model, colors);
    row[name] = (Number(row[name]) || 0) + (c.cost_usd ?? 0);
    byBucket.set(key, row);
  }
  // Every bucket of the range gets a slot, so bars sit on a real time axis.
  const filled = bucketStarts({ ...w, bucketS }).map((b) => {
    const row: TimeRow = { ...(byBucket.get(Date.parse(b)) ?? {}), bucket: b };
    for (const n of names) row[n] ??= 0;
    return row;
  });
  return (
    <TimeBars
      rows={filled}
      bucketS={bucketS}
      series={names.map((n) => ({ key: n, label: n, color: colors.get(n)! }))}
      format="cost"
      height={200}
    />
  );
}

function LatencyBars({ models, color }: { models: Models; color: (m: string) => string }) {
  const max = Math.max(1, ...models.map((m) => m.p95_ms));
  return (
    <ul className="space-y-2">
      {models.map((m) => (
        <li key={m.model} className="text-xs">
          <p className="mb-0.5 flex justify-between font-mono text-[11px]">
            <span className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block size-2" style={{ background: color(m.model) }} />
              {m.model}
            </span>
            <span className={`num ${m.p95_ms >= 2000 ? "text-warning" : "text-ink-2"}`}>
              p50 {formatDuration(m.p50_ms)} · p95 {formatDuration(m.p95_ms)}
            </span>
          </p>
          <span className="relative block h-3 bg-surface-2" title={`${m.model}: p50 ${formatDuration(m.p50_ms)}, p95 ${formatDuration(m.p95_ms)}`}>
            <span className="absolute inset-y-0 left-0 opacity-35" style={{ width: `${(m.p95_ms / max) * 100}%`, background: color(m.model) }} />
            <span className="absolute inset-y-0 left-0" style={{ width: `${(m.p50_ms / max) * 100}%`, background: color(m.model) }} />
          </span>
        </li>
      ))}
    </ul>
  );
}

function ModelTable({ models, color }: { models: Models; color: (m: string) => string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] text-xs">
        <thead>
          <tr>
            <Th>Model</Th>
            <Th num>Calls</Th>
            <Th num>Errors</Th>
            <Th num>Error rate</Th>
            <Th num>Avg latency</Th>
            <Th num>p50</Th>
            <Th num>p95</Th>
            <Th num>Input tok</Th>
            <Th num>Output tok</Th>
            <Th num>Total cost</Th>
            <Th num>Avg cost / call</Th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.model} className="border-b border-border last:border-0 hover:bg-surface-2">
              <Td>
                <span className="flex items-center gap-1.5 font-mono text-[11px]">
                  <span aria-hidden className="inline-block size-2" style={{ background: color(m.model) }} />
                  {m.model}
                </span>
              </Td>
              <Td num>{m.calls.toLocaleString("en")}</Td>
              <Td num className={m.errors ? "text-critical" : ""}>{m.errors}</Td>
              <Td num className={m.errors ? "text-critical" : ""}>{(m.error_rate * 100).toFixed(1)}%</Td>
              <Td num>{formatDuration(m.avg_ms)}</Td>
              <Td num>{formatDuration(m.p50_ms)}</Td>
              <Td num className={m.p95_ms >= 2000 ? "text-warning" : ""}>{formatDuration(m.p95_ms)}</Td>
              <Td num>{formatTokens(m.input_tokens)}</Td>
              <Td num>{formatTokens(m.output_tokens)}</Td>
              <Td num>{formatCost(m.cost_usd)}</Td>
              <Td num>{formatCost(m.avg_cost_usd)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
