import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { buildDebate, type TurnView } from "@/lib/debate";
import { formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";
import { parseRange, withRange, type RangeKey } from "@/lib/range";
import type { Span, TraceDetail } from "@/lib/types";
import { Id } from "@/components/copy";
import { Duration, Empty, ErrorState, ModelTag, PageHeader, Panel, RoleTag, StatusBadge, Tile } from "@/components/ui";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | undefined>> };

export default async function DebatePage({ params, searchParams }: Props) {
  const { id } = await params;
  const range = parseRange((await searchParams).range);
  let trace: TraceDetail | null;
  try {
    trace = await api.getTrace(id);
  } catch (error) {
    return <ErrorState error={error} />;
  }
  if (!trace) notFound();
  const d = buildDebate(trace.trace_id, trace.spans);
  const spanLink = (s: Span) => withRange(`/traces/${d.traceId}?span=${s.span_id}`, range);
  const llmCalls = trace.spans.filter((s) => s.kind === "generation").length;

  return (
    <>
      <div className="mb-1 flex items-center gap-2 text-xs text-ink-3">
        <Link href={withRange("/debates", range)} className="hover:text-ink">
          ← Debates
        </Link>
        <span>/</span>
        <Id value={d.traceId} />
        <Link href={withRange(`/traces/${d.traceId}`, range)} className="btn ml-auto">
          Open full trace →
        </Link>
      </div>
      <PageHeader title={d.topic ?? "Debate"}>
        <StatusBadge status={d.status} />
        {d.judge.verdict && (
          <span className="flex items-center gap-1.5">
            winner <RoleTag role={d.judge.verdict.winner} />
          </span>
        )}
        {d.root && <span className="num text-ink-3">{formatTime(d.root.start_time)}</span>}
      </PageHeader>

      <div className="mb-2 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Tile label="Duration" value={formatDuration(d.durationMs)} />
        <Tile label="Rounds" value={`${d.rounds.length}/${d.roundsPlanned ?? "?"}`} />
        <Tile label="LLM calls" value={llmCalls} />
        <Tile label="Retries" value={d.retries} tone={d.retries ? "warning" : "normal"} hint="timeouts + judge retries" />
        <Tile label="Errors" value={d.errors.length} tone={d.errors.length ? "critical" : "normal"} hint="failed spans" />
        <Tile label="Tokens" value={formatTokens(d.totalTokens)} />
        <Tile label="Cost" value={formatCost(d.totalCostUsd)} />
        <Tile label="Models" value={<span className="text-xs">{[d.models.pro, d.models.con].filter(Boolean).join(" vs ") || "–"}</span>} hint={`judge ${d.models.judge ?? "–"}`} />
      </div>

      <div className="grid gap-2 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="grid content-start gap-2">
          {/* column headers */}
          <div className="grid grid-cols-[3.5rem_1fr_1fr] gap-2 text-[11px] tracking-wider text-ink-3 uppercase">
            <span />
            <span className="flex items-center gap-2 px-1">
              Pro <ModelTag model={d.models.pro} />
            </span>
            <span className="flex items-center gap-2 px-1">
              Con <ModelTag model={d.models.con} />
            </span>
          </div>
          {d.rounds.length === 0 && (
            <Panel>
              <Empty>No turns recorded yet.</Empty>
            </Panel>
          )}
          {d.rounds.map((r) => (
            <div key={r.n} className="grid grid-cols-[3.5rem_1fr_1fr] gap-2">
              <div className="num border border-border bg-surface-1 p-2 text-center text-xs text-ink-2">
                R{r.n}
              </div>
              <TurnCard turn={r.pro} role="pro" link={spanLink} />
              <TurnCard turn={r.con} role="con" link={spanLink} />
            </div>
          ))}
        </div>

        <div className="grid content-start gap-2">
          <Panel title="Judge" right={<ModelTag model={d.models.judge} />}>
            <div className="space-y-3 p-2.5 text-xs">
              {d.judge.verdict ? (
                <Scores pro={d.judge.verdict.scorePro} con={d.judge.verdict.scoreCon} winner={d.judge.verdict.winner} />
              ) : (
                <p className={d.status === "running" ? "text-ink-3" : "font-medium text-critical"}>
                  {d.status === "running" ? "Waiting for the verdict…" : `✕ No verdict: ${d.judge.error ?? d.root?.error_message ?? "judge failed"}`}
                </p>
              )}
              {d.judge.verdict?.reasoning && <p className="leading-relaxed text-ink-2">{d.judge.verdict.reasoning}</p>}
              <ol className="space-y-1.5">
                {d.judge.attempts.map((a) => (
                  <li key={a.span.span_id} className={`border px-2 py-1.5 ${a.ok ? "border-border" : "border-critical bg-critical-soft"}`}>
                    <p className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[11px]">
                        attempt {a.n} {a.ok ? "✓ valid verdict" : "✕ invalid"}
                        {a.n > 1 && <span className="ml-2 text-warning">retry</span>}
                      </span>
                      <Link href={link(a.span)} className="text-[11px] text-ink-3 hover:text-ink">
                        span →
                      </Link>
                    </p>
                    {a.error && <p className="mt-1 font-mono text-[11px] break-words text-critical">{a.error}</p>}
                    {!a.ok && a.raw && (
                      <pre className="mt-1 max-h-28 overflow-auto bg-surface-1 p-1.5 font-mono text-[11px] whitespace-pre-wrap text-ink-2">{a.raw}</pre>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          </Panel>

          <Panel title="Errors and retries" right={<span className="num">{d.errors.length}</span>}>
            {d.errors.length === 0 ? (
              <Empty className="min-h-12">No failed spans.</Empty>
            ) : (
              <ul className="divide-y divide-border text-xs">
                {d.errors.map((e) => {
                  const m = (e.metadata ?? {}) as Record<string, unknown>;
                  return (
                    <li key={e.span_id} className="px-2.5 py-1.5">
                      <p className="flex items-center gap-2">
                        <span className="text-critical" aria-hidden>
                          ✕
                        </span>
                        <Link href={link(e)} className="font-mono text-[11px] hover:text-accent">
                          {e.name}
                        </Link>
                        <RoleTag role={typeof m.role === "string" ? m.role : null} />
                        {typeof m.round === "number" && <span className="num text-ink-3">R{m.round}</span>}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] break-words text-ink-2">{e.error_message}</p>
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  );

  function link(s: Span) {
    return spanLink(s);
  }
}

function Scores({ pro, con, winner }: { pro: number | null; con: number | null; winner: string }) {
  const rows = [
    { side: "pro", v: pro },
    { side: "con", v: con },
  ];
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.side} className="grid grid-cols-[2.5rem_1fr_2rem] items-center gap-2">
          <RoleTag role={r.side} />
          <span className="flex h-3 bg-surface-2">
            <span
              className="h-full"
              style={{ width: `${((r.v ?? 0) / 10) * 100}%`, background: r.side === winner ? "var(--accent)" : "var(--series-3)" }}
            />
          </span>
          <span className="num text-right">{r.v ?? "–"}/10</span>
        </li>
      ))}
      {winner === "tie" && <li className="text-ink-3">Tie.</li>}
    </ul>
  );
}

function TurnCard({ turn, role, link }: { turn: TurnView | null; role: "pro" | "con"; link: (s: Span) => string }) {
  if (!turn) {
    return (
      <div className="border border-dashed border-border bg-surface-1 p-2 text-xs text-ink-3">
        {role} hasn&apos;t spoken yet
      </div>
    );
  }
  const failed = Boolean(turn.error) || (turn.span === null && turn.attempts.length > 0 && turn.attempts.every((a) => !a.ok));
  return (
    <article className={`min-w-0 border bg-surface-1 ${failed ? "border-critical" : "border-border"}`}>
      <header className="flex h-7 items-center gap-2 border-b border-border px-2 text-[11px] text-ink-3">
        <RoleTag role={role} />
        {turn.span ? (
          <span className="num">{formatDuration(turn.durationMs)}</span>
        ) : (
          <span className="text-accent">▶ in progress</span>
        )}
        <span className="num">{formatTokens(turn.tokens)} tok</span>
        <span className="num">{formatCost(turn.costUsd)}</span>
        {turn.span && (
          <Link href={link(turn.span)} className="ml-auto hover:text-ink">
            span →
          </Link>
        )}
      </header>

      <div className="space-y-1.5 p-2 text-xs">
        {turn.fact && (
          <p className={`border-l-2 pl-2 font-mono text-[11px] ${turn.fact.span.status === "error" ? "border-critical text-critical" : "border-ink-3 text-ink-2"}`}>
            <Link href={link(turn.fact.span)} className="hover:underline">
              lookup_fact
            </Link>
            {": "}
            {turn.fact.span.status === "error" ? `✕ ${turn.fact.span.error_message}` : turn.fact.text}
            {turn.fact.span.duration_ms >= 2000 && <span className="ml-1 text-warning">slow</span>}
          </p>
        )}
        {turn.attempts.length > 1 || turn.attempts.some((a) => !a.ok) ? (
          <ol className="space-y-0.5">
            {turn.attempts.map((a, i) => (
              <li key={a.span.span_id} className={`font-mono text-[11px] ${a.ok ? "text-ink-2" : "text-critical"}`}>
                <Link href={link(a.span)} className="hover:underline">
                  LLM call {i + 1}
                </Link>{" "}
                {a.ok ? `✓ ${formatDuration(a.span.duration_ms)}` : `✕ ${a.error}`}
                {i > 0 && <span className="ml-1 text-warning">retry</span>}
              </li>
            ))}
          </ol>
        ) : null}
        {turn.text ? (
          <p className="leading-relaxed whitespace-pre-wrap">{turn.text}</p>
        ) : failed ? (
          <p className="font-medium text-critical">✕ Turn failed{turn.error ? `: ${turn.error}` : ""}</p>
        ) : (
          <p className="text-ink-3">…</p>
        )}
      </div>
    </article>
  );
}
