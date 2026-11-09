import Link from "next/link";
import { api } from "@/lib/api";
import { formatCost, formatDuration, formatRelative, formatTime, formatTokens } from "@/lib/format";
import { parseRange, rangeWindow, withRange } from "@/lib/range";
import type { TraceList } from "@/lib/types";
import { Id } from "@/components/copy";
import { Duration, Empty, ErrorState, ModelTag, PageHeader, Panel, StatusBadge, Td, Th } from "@/components/ui";

export const dynamic = "force-dynamic"; // always fresh data; reads env at request time

type SearchParams = Record<string, string | undefined>;

const FILTER_KEYS = ["range", "status", "name", "model", "min_cost", "min_duration_ms"] as const;

export default async function TracesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const w = rangeWindow(parseRange(sp.range));

  let data: TraceList;
  try {
    data = await api.listTraces({
      status: sp.status,
      name: sp.name,
      model: sp.model,
      min_cost: sp.min_cost,
      min_duration_ms: sp.min_duration_ms,
      start: w.start,
      cursor: sp.cursor,
      limit: 50,
    });
  } catch (error) {
    return (
      <>
        <PageHeader title="Traces" />
        <ErrorState error={error} />
      </>
    );
  }

  const filters = new URLSearchParams();
  for (const k of FILTER_KEYS) if (sp[k]) filters.set(k, sp[k]!);
  const nextHref = data.next_cursor ? `/traces?${new URLSearchParams([...filters, ["cursor", data.next_cursor]])}` : null;
  const models = [...new Set(data.traces.flatMap((t) => t.models))].sort();
  const now = Date.now();
  const active = FILTER_KEYS.filter((k) => k !== "range" && sp[k]).length;

  return (
    <>
      <PageHeader title="Traces">
        <span>Every traced request (here: every debate) is one trace. Started in the last {w.range}, newest first.</span>
      </PageHeader>

      {/* Plain GET form: filters live in the URL, so views are shareable and work without JS. */}
      <form method="get" className="mb-2 flex flex-wrap items-end gap-2 border border-border bg-surface-1 p-2">
        {sp.range && <input type="hidden" name="range" value={sp.range} />}
        <Field label="Status">
          <select name="status" defaultValue={sp.status ?? ""} className="field">
            <option value="">any</option>
            <option value="ok">ok</option>
            <option value="error">error</option>
          </select>
        </Field>
        <Field label="Name">
          <input name="name" defaultValue={sp.name ?? ""} placeholder="any" className="field w-32" />
        </Field>
        <Field label="Model">
          <input name="model" list="models" defaultValue={sp.model ?? ""} placeholder="any" className="field w-32" />
          <datalist id="models">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Field>
        <Field label="Min cost ($)">
          <input name="min_cost" type="number" step="any" min="0" defaultValue={sp.min_cost ?? ""} className="field w-24" />
        </Field>
        <Field label="Min duration (ms)">
          <input name="min_duration_ms" type="number" min="0" defaultValue={sp.min_duration_ms ?? ""} className="field w-28" />
        </Field>
        <button type="submit" className="btn-primary">
          Apply
        </button>
        <Link href={withRange("/traces", w.range)} className="btn">
          Reset
        </Link>
        {active > 0 && <span className="ml-auto self-center text-[11px] text-ink-3">{active} filter(s) active</span>}
      </form>

      <Panel title="Traces" right={<span className="num">{data.traces.length} on this page</span>}>
        {data.traces.length === 0 ? (
          <Empty>
            No traces match. Widen the time range in the top bar, or generate some with `make traffic N=20`.
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-xs">
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Trace</Th>
                  <Th num>Started</Th>
                  <Th num>Duration</Th>
                  <Th num>Cost</Th>
                  <Th num>Tokens</Th>
                  <Th>Status</Th>
                  <Th>Models</Th>
                  <Th num>Spans</Th>
                </tr>
              </thead>
              <tbody>
                {data.traces.map((t) => (
                  <tr key={t.trace_id} className="border-b border-border last:border-0 hover:bg-surface-2">
                    <Td>
                      <Link href={withRange(`/traces/${t.trace_id}`, w.range)} className="font-medium hover:text-accent">
                        {t.name}
                      </Link>
                      {!t.has_root && <span className="ml-2 text-[11px] text-ink-3">(root pending)</span>}
                    </Td>
                    <Td>
                      <Id value={t.trace_id} short />
                    </Td>
                    <Td num className="text-ink-2">
                      <span title={formatTime(t.start_time)}>{formatRelative(t.start_time, now)}</span>
                    </Td>
                    <Td num>
                      <Duration ms={t.duration_ms} slowMs={10_000} text={formatDuration(t.duration_ms)} />
                    </Td>
                    <Td num>{formatCost(t.total_cost_usd)}</Td>
                    <Td num className="text-ink-2">
                      {formatTokens((t.input_tokens ?? 0) + (t.output_tokens ?? 0) || null)}
                    </Td>
                    <Td>
                      <StatusBadge status={t.status} />
                    </Td>
                    <Td>
                      <span className="flex gap-1">
                        {t.models.map((m) => (
                          <ModelTag key={m} model={m} />
                        ))}
                      </span>
                    </Td>
                    <Td num>
                      {t.span_count}
                      {t.error_count > 0 && (
                        <span className="ml-1 text-critical" title={`${t.error_count} span(s) failed`}>
                          ✕{t.error_count}
                        </span>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="mt-2 flex justify-end gap-2 text-xs">
        {sp.cursor && (
          <Link href={`/traces?${filters}`} className="btn">
            ← first page
          </Link>
        )}
        {nextHref && (
          <Link href={nextHref} className="btn">
            next page →
          </Link>
        )}
      </div>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5 text-[11px] text-ink-3">
      {label}
      {children}
    </label>
  );
}
