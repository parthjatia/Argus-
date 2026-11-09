import Link from "next/link";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { liveDebate } from "@/lib/debate";
import { formatCost, formatDuration, formatTokens } from "@/lib/format";
import { parseRange, rangeWindow, withRange, type Window } from "@/lib/range";
import type { Json } from "@/lib/types";
import { Id } from "@/components/copy";
import { TimeBars } from "@/components/charts";
import {
  Duration,
  Empty,
  KindBadge,
  PageHeader,
  Panel,
  PanelError,
  PanelSkeleton,
  RoleTag,
  StatusBadge,
  Td,
  Th,
  Tile,
  TileSkeleton,
} from "@/components/ui";

export const dynamic = "force-dynamic";

type SP = Promise<Record<string, string | undefined>>;

export default async function OverviewPage({ searchParams }: { searchParams: SP }) {
  const w = rangeWindow(parseRange((await searchParams).range));
  return (
    <>
      <PageHeader title="Overview">
        <span>
          Range <span className="num text-ink">{w.range}</span>, bars per {w.bucketLabel}
        </span>
      </PageHeader>
      <Suspense fallback={<TilesSkeleton />}>
        <SummaryTiles w={w} />
      </Suspense>

      <div className="mt-2 grid gap-2 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Suspense fallback={<PanelSkeleton title="Live feed · newest spans" rows={14} />}>
          <LiveFeed range={w.range} />
        </Suspense>
        <div className="grid content-start gap-2">
          <Suspense fallback={<PanelSkeleton title="Running now" rows={3} />}>
            <RunningNow range={w.range} />
          </Suspense>
          <Suspense fallback={<PanelSkeleton title="Cost" rows={5} />}>
            <SeriesCharts w={w} />
          </Suspense>
        </div>
      </div>
    </>
  );
}

const TILE_LABELS = ["Traces in range", "Debates running", "Spans / min", "Error rate", "Total cost", "p95 LLM latency"];

function TilesSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
      {TILE_LABELS.map((l) => (
        <TileSkeleton key={l} label={l} />
      ))}
    </div>
  );
}

async function SummaryTiles({ w }: { w: Window }) {
  let s;
  try {
    s = await api.summary(w);
  } catch (error) {
    return (
      <Panel title="Summary">
        <PanelError error={error} />
      </Panel>
    );
  }
  const rate = s.error_rate;
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
      <Tile label={TILE_LABELS[0]} value={s.traces.toLocaleString("en")} hint={`${s.spans.toLocaleString("en")} spans`} />
      <Tile
        label={TILE_LABELS[1]}
        value={s.running}
        tone={s.running ? "accent" : "normal"}
        hint={<Link href={withRange("/live", w.range)} className="hover:text-ink">open live view →</Link>}
      />
      <Tile label={TILE_LABELS[2]} value={(s.spans_last_5m / 5).toFixed(1)} hint="avg of last 5 min" />
      <Tile
        label={TILE_LABELS[3]}
        value={rate === null ? "–" : `${(rate * 100).toFixed(1)}%`}
        tone={rate ? "critical" : "normal"}
        hint={`${s.trace_errors} failed · ${s.span_errors} span errors`}
      />
      <Tile label={TILE_LABELS[4]} value={formatCost(s.cost_usd)} hint={`${formatTokens(s.input_tokens + s.output_tokens)} tokens`} />
      <Tile
        label={TILE_LABELS[5]}
        value={formatDuration(s.p95_ms)}
        tone={s.p95_ms !== null && s.p95_ms >= 2000 ? "warning" : "normal"}
        hint={`p50 ${formatDuration(s.p50_ms)} · ${s.generations} calls`}
      />
    </div>
  );
}

function metaStr(m: Json, key: string): string | null {
  return m && typeof m === "object" && !Array.isArray(m) && typeof m[key] === "string" ? (m[key] as string) : null;
}

async function LiveFeed({ range }: { range: Window["range"] }) {
  let spans;
  try {
    spans = await api.recentSpans(40);
  } catch (error) {
    return (
      <Panel title="Live feed · newest spans">
        <PanelError error={error} />
      </Panel>
    );
  }
  return (
    <Panel title="Live feed · newest spans" right={<span className="num">{spans.length} shown</span>}>
      {spans.length === 0 ? (
        <Empty>No spans in the last 6 hours. Run `make traffic N=20`.</Empty>
      ) : (
        <div className="max-h-[640px] overflow-auto">
          <table className="w-full text-xs">
            <thead>
              <tr>
                <Th>Ended</Th>
                <Th>Kind</Th>
                <Th>Span</Th>
                <Th>Role</Th>
                <Th>Status</Th>
                <Th num>Duration</Th>
                <Th num>Tokens</Th>
                <Th num>Cost</Th>
                <Th>Trace</Th>
              </tr>
            </thead>
            <tbody>
              {spans.map((s) => (
                <tr key={s.span_id} className={`border-b border-border last:border-0 hover:bg-surface-2 ${s.status === "error" ? "bg-critical-soft" : ""}`}>
                  <Td className="num text-ink-3">{s.end_time.slice(11, 19)}</Td>
                  <Td>
                    <KindBadge kind={s.kind} />
                  </Td>
                  <Td className="max-w-64 truncate">
                    <Link href={withRange(`/traces/${s.trace_id}?span=${s.span_id}`, range)} className="hover:text-accent">
                      {s.name}
                    </Link>
                    {s.error_message && (
                      <span className="ml-2 font-mono text-[11px] text-critical" title={s.error_message}>
                        {s.error_message.slice(0, 40)}
                      </span>
                    )}
                  </Td>
                  <Td>
                    <RoleTag role={metaStr(s.metadata, "role")} />
                  </Td>
                  <Td>
                    <StatusBadge status={s.status} />
                  </Td>
                  <Td num>
                    <Duration ms={s.kind === "span" ? null : s.duration_ms} text={formatDuration(s.duration_ms)} />
                  </Td>
                  <Td num className="text-ink-2">
                    {formatTokens(s.input_tokens === null && s.output_tokens === null ? null : (s.input_tokens ?? 0) + (s.output_tokens ?? 0))}
                  </Td>
                  <Td num className="text-ink-2">
                    {formatCost(s.cost_usd)}
                  </Td>
                  <Td>
                    <Id value={s.trace_id} short />
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

async function RunningNow({ range }: { range: Window["range"] }) {
  let live;
  try {
    live = await api.live();
  } catch (error) {
    return (
      <Panel title="Running now">
        <PanelError error={error} />
      </Panel>
    );
  }
  return (
    <Panel title="Running now" right={<Link href={withRange("/live", range)} className="hover:text-ink">live view →</Link>}>
      {live.length === 0 ? (
        <Empty className="min-h-16">Nothing running. In-progress traces appear here within ~1 s.</Empty>
      ) : (
        <ul className="divide-y divide-border">
          {live.slice(0, 6).map((t) => {
            const d = liveDebate(t);
            return (
              <li key={t.trace_id} className="flex items-center gap-2 px-2.5 py-1.5 text-xs">
                <span aria-hidden className="inline-block size-2 shrink-0 bg-accent" />
                <Link href={withRange(`/debates/${t.trace_id}`, range)} className="min-w-0 flex-1 truncate hover:text-accent">
                  {d.topic ?? t.latest_span.name}
                </Link>
                <span className="num text-ink-3">
                  {d.round !== null ? `R${d.round}/${d.rounds ?? "?"}` : ""}
                </span>
                <RoleTag role={d.speaker} />
                <span className="num w-14 text-right text-ink-2">{formatDuration(Date.now() - Date.parse(t.start_time))}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

async function SeriesCharts({ w }: { w: Window }) {
  let rows;
  try {
    rows = await api.timeseries(w);
  } catch (error) {
    return (
      <Panel title="Cost and errors over time">
        <PanelError error={error} />
      </Panel>
    );
  }
  const any = rows.some((r) => r.spans > 0);
  const costRows = rows.map((r) => ({ bucket: r.bucket, cost: r.cost_usd ?? 0 }));
  const errRows = rows.map((r) => ({ bucket: r.bucket, failed: r.trace_errors, spanErrors: r.span_errors - r.trace_errors }));
  const totalCost = rows.reduce((a, r) => a + (r.cost_usd ?? 0), 0);
  const failed = rows.reduce((a, r) => a + r.trace_errors, 0);
  return (
    <>
      <Panel title={`Cost per ${w.bucketLabel}`} right={<span className="num">{formatCost(totalCost)} total</span>}>
        <div className="p-2">
          {any ? (
            <TimeBars rows={costRows} series={[{ key: "cost", label: "cost", color: "var(--accent)" }]} bucketS={w.bucketS}
                      format="cost" height={130} />
          ) : (
            <Empty>No spans in this range.</Empty>
          )}
        </div>
      </Panel>
      <Panel title={`Errors per ${w.bucketLabel}`} right={<span className="num">{failed} failed traces</span>}>
        <div className="p-2">
          {any ? (
            <TimeBars
              rows={errRows}
              series={[
                { key: "failed", label: "failed traces", color: "var(--critical)" },
                { key: "spanErrors", label: "other failed spans", color: "var(--critical-muted)" },
              ]}
              bucketS={w.bucketS}
              format="count"
              height={130}
            />
          ) : (
            <Empty>No spans in this range.</Empty>
          )}
        </div>
      </Panel>
    </>
  );
}
