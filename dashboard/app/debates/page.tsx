import Link from "next/link";
import { api } from "@/lib/api";
import { formatCost, formatDuration, formatRelative, formatTime } from "@/lib/format";
import { parseRange, rangeWindow, withRange } from "@/lib/range";
import type { Json, TraceList } from "@/lib/types";
import { Id } from "@/components/copy";
import { Empty, ErrorState, ModelTag, PageHeader, Panel, RoleTag, StatusBadge, Td, Th } from "@/components/ui";

export const dynamic = "force-dynamic";

type SP = Promise<Record<string, string | undefined>>;

function meta(m: Json): Record<string, Json> {
  return m && typeof m === "object" && !Array.isArray(m) ? (m as Record<string, Json>) : {};
}
const s = (v: Json | undefined) => (typeof v === "string" ? v : null);

export default async function DebatesPage({ searchParams }: { searchParams: SP }) {
  const sp = await searchParams;
  const w = rangeWindow(parseRange(sp.range));
  const status = sp.status === "ok" || sp.status === "error" ? sp.status : undefined;

  let data: TraceList;
  try {
    data = await api.listTraces({ name: "debate", status, start: w.start, cursor: sp.cursor, limit: 50 });
  } catch (error) {
    return (
      <>
        <PageHeader title="Debates" />
        <ErrorState error={error} />
      </>
    );
  }

  const base = new URLSearchParams();
  if (sp.range) base.set("range", sp.range);
  if (status) base.set("status", status);
  const href = (extra: Record<string, string | null>) => {
    const p = new URLSearchParams(base);
    for (const [k, v] of Object.entries(extra)) if (v === null) p.delete(k); else p.set(k, v);
    const q = p.toString();
    return q ? `/debates?${q}` : "/debates";
  };
  const now = Date.now();

  return (
    <>
      <PageHeader title="Debates">
        <span>One debate = one trace. Finished debates in the last {w.range}, newest first.</span>
        <span className="flex border border-border">
          {[["all", null], ["ok", "ok"], ["error", "error"]].map(([label, v]) => (
            <Link
              key={label}
              href={href({ status: v, cursor: null })}
              aria-current={(status ?? null) === v ? "true" : undefined}
              className={`num h-6 border-r border-border px-2 leading-6 last:border-r-0 ${
                (status ?? null) === v ? "bg-ink text-surface-1" : "text-ink-2 hover:bg-surface-2"
              }`}
            >
              {label}
            </Link>
          ))}
        </span>
      </PageHeader>

      <Panel title="Debates" right={<span className="num">{data.traces.length} on this page</span>}>
        {data.traces.length === 0 ? (
          <Empty>No debates in this range. Run `make traffic N=20` or widen the range.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1150px] text-xs">
              <thead>
                <tr>
                  <Th>Topic</Th>
                  <Th num>Started</Th>
                  <Th>Pro</Th>
                  <Th>Con</Th>
                  <Th>Judge</Th>
                  <Th>Winner</Th>
                  <Th num>Score</Th>
                  <Th num>Failed turns</Th>
                  <Th num>Spans</Th>
                  <Th num>Duration</Th>
                  <Th num>Cost</Th>
                  <Th>Status</Th>
                  <Th>Trace</Th>
                </tr>
              </thead>
              <tbody>
                {data.traces.map((t) => {
                  const m = meta(t.root_metadata);
                  return (
                    <tr key={t.trace_id} className="border-b border-border last:border-0 hover:bg-surface-2">
                      <Td className="max-w-96 truncate">
                        <Link href={withRange(`/debates/${t.trace_id}`, w.range)} className="font-medium hover:text-accent">
                          {s(m.topic) ?? "(no topic metadata)"}
                        </Link>
                      </Td>
                      <Td num className="text-ink-3">
                        <span title={formatTime(t.start_time)}>{formatRelative(t.start_time, now)}</span>
                      </Td>
                      <Td>
                        <ModelTag model={s(m.pro_model)} />
                      </Td>
                      <Td>
                        <ModelTag model={s(m.con_model)} />
                      </Td>
                      <Td>
                        <ModelTag model={s(m.judge_model)} />
                      </Td>
                      <Td>{s(m.winner) ? <RoleTag role={s(m.winner)} /> : <span className="text-critical">✕ none</span>}</Td>
                      <Td num>{typeof m.score_pro === "number" ? `${m.score_pro}–${m.score_con}` : "–"}</Td>
                      <Td num className={typeof m.failed_turns === "number" && m.failed_turns > 0 ? "text-critical" : ""}>
                        {typeof m.failed_turns === "number" ? m.failed_turns : "–"}
                      </Td>
                      <Td num>
                        {t.span_count}
                        {t.error_count > 0 && <span className="ml-1 text-critical">✕{t.error_count}</span>}
                      </Td>
                      <Td num>{formatDuration(t.duration_ms)}</Td>
                      <Td num>{formatCost(t.total_cost_usd)}</Td>
                      <Td>
                        <StatusBadge status={t.status} />
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

      <div className="mt-2 flex justify-end gap-2 text-xs">
        {sp.cursor && (
          <Link href={href({ cursor: null })} className="btn">
            ← first page
          </Link>
        )}
        {data.next_cursor && (
          <Link href={href({ cursor: data.next_cursor })} className="btn">
            next page →
          </Link>
        )}
      </div>
    </>
  );
}
