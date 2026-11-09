"use client";

// Waterfall timeline of one trace + detail panel for the selected span.

import { useMemo, useState } from "react";
import { buildTree } from "@/lib/tree";
import { formatCost, formatDuration, formatTokens } from "@/lib/format";
import type { Json, Span } from "@/lib/types";
import { KindBadge, StatusBadge } from "@/components/badges";

const KINDS = ["span", "generation", "tool"] as const;

export function TraceView({ spans }: { spans: Span[] }) {
  const rows = useMemo(() => buildTree(spans), [spans]);
  const [selectedId, setSelectedId] = useState<string>(
    () => (spans.find((s) => s.status === "error") ?? rows[0]?.span)?.span_id ?? "",
  );
  const selected = spans.find((s) => s.span_id === selectedId) ?? rows[0]?.span;

  const t0 = Math.min(...spans.map((s) => Date.parse(s.start_time)));
  const t1 = Math.max(...spans.map((s) => Date.parse(s.end_time)));
  const total = Math.max(t1 - t0, 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <section className="overflow-hidden rounded-xl border border-border bg-surface-1">
        {/* legend: kinds are identity colours, always also written in each row */}
        <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-2.5">
          <h2 className="text-sm font-medium">Timeline</h2>
          <div className="flex gap-4">
            {KINDS.map((k) => (
              <KindBadge key={k} kind={k} />
            ))}
          </div>
        </div>

        <div className="overflow-x-auto">
          <div className="min-w-[720px]">
            {/* axis */}
            <div className="grid grid-cols-[minmax(220px,38%)_72px_1fr] border-b border-border text-[11px] text-ink-3">
              <div className="px-4 py-1.5">Span</div>
              <div className="py-1.5 pr-3 text-right">Duration</div>
              <div className="relative mr-4 h-7">
                {ticks.map((f) => (
                  <span
                    key={f}
                    className="absolute top-1.5 -translate-x-1/2 whitespace-nowrap tabular-nums first:translate-x-0 last:-translate-x-full"
                    style={{ left: `${f * 100}%` }}
                  >
                    {formatDuration(f * total)}
                  </span>
                ))}
              </div>
            </div>

            <ul role="listbox" aria-label="Spans">
              {rows.map(({ span: s, depth, orphan }) => {
                const left = ((Date.parse(s.start_time) - t0) / total) * 100;
                const width = (s.duration_ms / total) * 100;
                const isSelected = s.span_id === selected?.span_id;
                const error = s.status === "error";
                return (
                  <li
                    key={s.span_id}
                    role="option"
                    aria-selected={isSelected}
                    tabIndex={0}
                    onClick={() => setSelectedId(s.span_id)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setSelectedId(s.span_id)}
                    className={`grid cursor-pointer grid-cols-[minmax(220px,38%)_72px_1fr] items-center text-sm outline-none ${
                      isSelected ? "bg-surface-2" : "hover:bg-surface-2/60 focus-visible:bg-surface-2/60"
                    }`}
                  >
                    <div className="flex min-w-0 items-center gap-1.5 py-1.5 pr-2" style={{ paddingLeft: 16 + depth * 16 }}>
                      <span aria-hidden className="size-2 shrink-0 rounded-sm" style={{ background: `var(--kind-${s.kind})` }} />
                      <span className={`truncate ${error ? "text-critical" : ""}`}>{s.name}</span>
                      {error && (
                        <span className="shrink-0 text-xs text-critical" title={s.error_message ?? "error"}>
                          ✕ error
                        </span>
                      )}
                      {orphan && <span className="shrink-0 text-xs text-ink-3">parent missing</span>}
                    </div>
                    <div className="pr-3 text-right text-xs text-ink-2 tabular-nums">{formatDuration(s.duration_ms)}</div>
                    <div className="relative mr-4 h-7">
                      {ticks.slice(1, -1).map((f) => (
                        <span key={f} aria-hidden className="absolute inset-y-0 w-px bg-border" style={{ left: `${f * 100}%` }} />
                      ))}
                      <span
                        className="absolute top-1/2 h-3 -translate-y-1/2 rounded-[3px]"
                        title={`${s.name}: starts +${formatDuration(Date.parse(s.start_time) - t0)}, takes ${formatDuration(s.duration_ms)}`}
                        style={{
                          left: `${left}%`,
                          width: `max(${width}%, 2px)`,
                          background: `var(--kind-${s.kind})`,
                          // errors: a ring in the status colour, plus the ✕ label in the name column
                          boxShadow: error ? "0 0 0 2px var(--surface-1), 0 0 0 3.5px var(--status-critical)" : undefined,
                        }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </section>

      {selected && <SpanDetail span={selected} offsetMs={Date.parse(selected.start_time) - t0} />}
    </div>
  );
}

function SpanDetail({ span: s, offsetMs }: { span: Span; offsetMs: number }) {
  return (
    <aside className="self-start rounded-xl border border-border bg-surface-1 xl:sticky xl:top-20">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="truncate font-medium">{s.name}</h2>
          <StatusBadge status={s.status} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
          <KindBadge kind={s.kind} />
          <span className="font-mono text-[11px] text-ink-3">{s.span_id}</span>
        </div>
      </div>

      <dl className="grid grid-cols-3 gap-3 border-b border-border px-4 py-3 text-sm">
        <Item label="Duration">{formatDuration(s.duration_ms)}</Item>
        <Item label="Starts at">+{formatDuration(Math.round(offsetMs))}</Item>
        <Item label="Cost">{formatCost(s.cost_usd)}</Item>
        {s.model && <Item label="Model">{s.model}</Item>}
        {(s.input_tokens !== null || s.output_tokens !== null) && (
          <>
            <Item label="Input tokens">{formatTokens(s.input_tokens)}</Item>
            <Item label="Output tokens">{formatTokens(s.output_tokens)}</Item>
          </>
        )}
      </dl>

      {s.error_message && (
        <div className="mx-4 mt-3 rounded-md bg-critical-bg px-3 py-2 text-sm text-critical">
          <p className="font-medium">
            <span aria-hidden>✕ </span>Error
          </p>
          <p className="mt-0.5 font-mono text-xs break-words">{s.error_message}</p>
        </div>
      )}

      <div className="space-y-4 px-4 py-3">
        <Payload title="Input" value={s.input} />
        <Payload title="Output" value={s.output} />
        {s.metadata !== null && <Payload title="Metadata" value={s.metadata} />}
      </div>
    </aside>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="truncate font-medium tabular-nums">{children}</dd>
    </div>
  );
}

type Msg = { role: string; content: string };

function isMessages(v: Json): v is Msg[] {
  return Array.isArray(v) && v.length > 0 && v.every((m) => typeof m === "object" && m !== null && "role" in m && "content" in m);
}

function Payload({ title, value }: { title: string; value: Json }) {
  if (value === null) {
    return (
      <div>
        <h3 className="text-xs font-medium text-ink-3">{title}</h3>
        <p className="mt-1 text-sm text-ink-3">–</p>
      </div>
    );
  }
  const obj = typeof value === "object" && !Array.isArray(value) ? value : null;

  // SDK truncation marker: show that it was cut, and the preview.
  if (obj && obj._argus_truncated === true) {
    return (
      <div>
        <h3 className="text-xs font-medium text-ink-3">
          {title} <span className="ml-1 rounded bg-surface-2 px-1.5 py-0.5">truncated · {formatTokens(Number(obj.original_bytes))} bytes</span>
        </h3>
        <Pre>{String(obj.preview)}</Pre>
      </div>
    );
  }

  // LLM calls (wrap_openai): {messages: [...], params: {...}} → chat view.
  if (obj && isMessages(obj.messages)) {
    const params = obj.params as Json;
    return (
      <div>
        <h3 className="text-xs font-medium text-ink-3">{title}</h3>
        <ol className="mt-1.5 space-y-2">
          {obj.messages.map((m, i) => (
            <li key={i} className="rounded-md bg-surface-2/70 px-3 py-2">
              <p className="text-[11px] font-medium tracking-wide text-ink-3 uppercase">{m.role}</p>
              <p className="mt-0.5 text-sm whitespace-pre-wrap">{m.content}</p>
            </li>
          ))}
        </ol>
        {params && typeof params === "object" && Object.keys(params).length > 0 && <Pre>{JSON.stringify(params, null, 2)}</Pre>}
      </div>
    );
  }

  if (obj && typeof obj.content === "string" && typeof obj.role === "string") {
    return (
      <div>
        <h3 className="text-xs font-medium text-ink-3">{title}</h3>
        <p className="mt-1.5 rounded-md bg-surface-2/70 px-3 py-2 text-sm whitespace-pre-wrap">{obj.content}</p>
      </div>
    );
  }

  return (
    <div>
      <h3 className="text-xs font-medium text-ink-3">{title}</h3>
      <Pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</Pre>
    </div>
  );
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="mt-1.5 max-h-80 overflow-auto rounded-md bg-surface-2/70 px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
      {children}
    </pre>
  );
}
