import Link from "next/link";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { liveDebate } from "@/lib/debate";
import { formatCost, formatDuration, formatRelative, formatTokens } from "@/lib/format";
import { parseRange, withRange, type RangeKey } from "@/lib/range";
import type { Json } from "@/lib/types";
import { Id } from "@/components/copy";
import { RefreshRate } from "@/components/refresh";
import { Empty, ModelTag, PageHeader, Panel, PanelError, PanelSkeleton, RoleTag, StatusBadge, Td, Th } from "@/components/ui";

export const dynamic = "force-dynamic";

type SP = Promise<Record<string, string | undefined>>;

export default async function LivePage({ searchParams }: { searchParams: SP }) {
  const range = parseRange((await searchParams).range);
  return (
    <>
      <RefreshRate ms={2000} />
      <PageHeader title="Live">
        <span>
          In progress = a trace with spans received in the last 60 s and no root span yet (the SDK sends a span when it
          ends). Refreshes every 2 s.
        </span>
      </PageHeader>
      <div className="grid gap-2">
        <Suspense fallback={<PanelSkeleton title="In progress" rows={5} />}>
          <InProgress range={range} />
        </Suspense>
        <Suspense fallback={<PanelSkeleton title="Recently finished" rows={8} />}>
          <RecentlyFinished range={range} />
        </Suspense>
      </div>
    </>
  );
}

async function InProgress({ range }: { range: RangeKey }) {
  let live;
  try {
    live = await api.live();
  } catch (error) {
    return (
      <Panel title="In progress">
        <PanelError error={error} />
      </Panel>
    );
  }
  const now = Date.now();
  return (
    <Panel title="In progress" right={<span className="num">{live.length} running</span>}>
      {live.length === 0 ? (
        <Empty>
          No debate is running. Start one with <code className="mx-1 font-mono">make demo</code> or several
          with <code className="mx-1 font-mono">make traffic N=4</code> (real models: a debate takes minutes).
        </Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-xs">
            <thead>
              <tr>
                <Th>Topic</Th>
                <Th>Pro</Th>
                <Th>Con</Th>
                <Th>Judge</Th>
                <Th num>Round</Th>
                <Th>Speaking</Th>
                <Th num>Elapsed</Th>
                <Th num>Spans</Th>
                <Th num>Tokens</Th>
                <Th num>Cost</Th>
                <Th>Latest step</Th>
                <Th>Trace</Th>
              </tr>
            </thead>
            <tbody>
              {live.map((t) => {
                const d = liveDebate(t);
                return (
                  <tr key={t.trace_id} className="border-b border-border last:border-0 hover:bg-surface-2">
                    <Td className="max-w-80 truncate">
                      <span aria-hidden className="mr-2 inline-block size-2 bg-accent align-middle" />
                      <Link href={withRange(`/debates/${t.trace_id}`, range)} className="font-medium hover:text-accent">
                        {d.topic ?? "(no topic metadata)"}
                      </Link>
                    </Td>
                    <Td>
                      <ModelTag model={d.models.pro} />
                    </Td>
                    <Td>
                      <ModelTag model={d.models.con} />
                    </Td>
                    <Td>
                      <ModelTag model={d.models.judge} />
                    </Td>
                    <Td num>{d.phase === "judging" ? "judging" : d.round !== null ? `${d.round}/${d.rounds ?? "?"}` : "–"}</Td>
                    <Td>
                      <RoleTag role={d.speaker} />
                    </Td>
                    <Td num>{formatDuration(now - Date.parse(t.start_time))}</Td>
                    <Td num>
                      {t.span_count}
                      {t.error_count > 0 && <span className="ml-1 text-critical">✕{t.error_count}</span>}
                    </Td>
                    <Td num>{formatTokens((t.input_tokens ?? 0) + (t.output_tokens ?? 0))}</Td>
                    <Td num>{formatCost(t.total_cost_usd)}</Td>
                    <Td className="max-w-72 truncate">
                      <span className={`font-mono text-[11px] ${d.latestStatus === "error" ? "text-critical" : "text-ink-2"}`}>
                        {d.latestStatus === "error" ? "✕ " : ""}
                        {d.latestStep}
                      </span>
                      <span className="num ml-2 text-ink-3">{formatRelative(d.latestAt, now)}</span>
                    </Td>
                    <Td>
                      <Id value={t.trace_id} short />
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function meta(m: Json): Record<string, Json> {
  return m && typeof m === "object" && !Array.isArray(m) ? (m as Record<string, Json>) : {};
}

async function RecentlyFinished({ range }: { range: RangeKey }) {
  let list;
  try {
    list = await api.listTraces({ name: "debate", limit: 15 });
  } catch (error) {
    return (
      <Panel title="Recently finished">
        <PanelError error={error} />
      </Panel>
    );
  }
  const now = Date.now();
  return (
    <Panel
      title="Recently finished"
      right={
        <Link href={withRange("/debates", range)} className="hover:text-ink">
          all debates →
        </Link>
      }
    >
      {list.traces.length === 0 ? (
        <Empty>No finished debates yet.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-xs">
            <thead>
              <tr>
                <Th>Topic</Th>
                <Th>Status</Th>
                <Th>Winner</Th>
                <Th num>Score pro–con</Th>
                <Th num>Duration</Th>
                <Th num>Spans</Th>
                <Th num>Cost</Th>
                <Th num>Ended</Th>
                <Th>Trace</Th>
              </tr>
            </thead>
            <tbody>
              {list.traces.map((t) => {
                const m = meta(t.root_metadata);
                const winner = typeof m.winner === "string" ? m.winner : null;
                return (
                  <tr key={t.trace_id} className="border-b border-border last:border-0 hover:bg-surface-2">
                    <Td className="max-w-96 truncate">
                      <Link href={withRange(`/debates/${t.trace_id}`, range)} className="hover:text-accent">
                        {typeof m.topic === "string" ? m.topic : t.name}
                      </Link>
                    </Td>
                    <Td>
                      <StatusBadge status={t.status} />
                    </Td>
                    <Td>{winner ? <RoleTag role={winner} /> : <span className="text-ink-3">no verdict</span>}</Td>
                    <Td num>
                      {typeof m.score_pro === "number" ? `${m.score_pro}–${m.score_con}` : "–"}
                    </Td>
                    <Td num>{formatDuration(t.duration_ms)}</Td>
                    <Td num>
                      {t.span_count}
                      {t.error_count > 0 && <span className="ml-1 text-critical">✕{t.error_count}</span>}
                    </Td>
                    <Td num>{formatCost(t.total_cost_usd)}</Td>
                    <Td num className="text-ink-3">
                      {formatRelative(t.end_time, now)}
                    </Td>
                    <Td>
                      <Id value={t.trace_id} short />
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
