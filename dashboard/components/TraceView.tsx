"use client";

// Waterfall timeline of one trace + inspector panel for the selected span.
// Click a span to inspect it; ↑/↓ move between spans, Esc closes the inspector.
// The selection is kept in ?span= so a link can open a trace on a given span.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildTree } from "@/lib/tree";
import { formatCost, formatDuration, formatTime, formatTokens } from "@/lib/format";
import type { Json, Span } from "@/lib/types";
import { Duration, KindBadge, RoleTag, SLOW_MS, StatusBadge } from "@/components/badges";
import { CopyButton, Id } from "@/components/copy";

const KINDS = ["span", "generation", "tool"] as const;
const TABS = ["Overview", "Input", "Output", "Metadata", "Error", "Raw JSON"] as const;
type Tab = (typeof TABS)[number];

function setSpanParam(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("span", id);
  else url.searchParams.delete("span");
  window.history.replaceState(window.history.state, "", url);
}

export function TraceView({ spans, initialSpanId }: { spans: Span[]; initialSpanId: string | null }) {
  const rows = useMemo(() => buildTree(spans), [spans]);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialSpanId && spans.some((s) => s.span_id === initialSpanId) ? initialSpanId : null,
  );
  const [tab, setTab] = useState<Tab>("Overview");
  const rowRefs = useRef(new Map<string, HTMLLIElement>());
  const selected = selectedId ? spans.find((s) => s.span_id === selectedId) ?? null : null;

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    setSpanParam(id);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el?.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key === "Escape" && selectedId) {
        select(null);
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      if (!rows.length) return;
      e.preventDefault();
      const i = rows.findIndex((r) => r.span.span_id === selectedId);
      const next =
        i === -1 ? (e.key === "ArrowDown" ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)));
      select(rows[next].span.span_id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selectedId, select]);

  useEffect(() => {
    if (selectedId) rowRefs.current.get(selectedId)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const t0 = Math.min(...spans.map((s) => Date.parse(s.start_time)));
  const t1 = Math.max(...spans.map((s) => Date.parse(s.end_time)));
  const total = Math.max(t1 - t0, 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className={`grid gap-2 ${selected ? "xl:grid-cols-[minmax(0,1fr)_480px]" : ""}`}>
      <section className="min-w-0 border border-border bg-surface-1">
        <header className="flex h-8 items-center justify-between gap-4 border-b border-border px-2.5">
          <h2 className="text-[11px] font-medium tracking-wider text-ink-2 uppercase">Timeline</h2>
          <div className="flex items-center gap-3">
            {KINDS.map((k) => (
              <KindBadge key={k} kind={k} />
            ))}
            <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-ink-2">
              <span aria-hidden className="inline-block size-2 bg-critical" />
              error
            </span>
            <span className="font-mono text-[10px] text-ink-3">click · ↑↓ · esc</span>
          </div>
        </header>

        <div className="overflow-x-auto">
          <div className="min-w-[760px]">
            <div className="grid grid-cols-[minmax(240px,36%)_80px_1fr] border-b border-border text-[10px] text-ink-3">
              <div className="px-2.5 py-1">Span</div>
              <div className="py-1 pr-2 text-right">Duration</div>
              <div className="relative mr-3 h-6">
                {ticks.map((f) => (
                  <span
                    key={f}
                    className="num absolute top-1 -translate-x-1/2 whitespace-nowrap first:translate-x-0 last:-translate-x-full"
                    style={{ left: `${f * 100}%` }}
                  >
                    {formatDuration(f * total)}
                  </span>
                ))}
              </div>
            </div>

            <ul role="listbox" aria-label="Spans" aria-activedescendant={selectedId ?? undefined}>
              {rows.map(({ span: s, depth, orphan }) => {
                const left = ((Date.parse(s.start_time) - t0) / total) * 100;
                const width = (s.duration_ms / total) * 100;
                const isSelected = s.span_id === selectedId;
                const error = s.status === "error";
                const role = metaString(s.metadata, "role");
                return (
                  <li
                    key={s.span_id}
                    id={s.span_id}
                    ref={(el) => {
                      if (el) rowRefs.current.set(s.span_id, el);
                      else rowRefs.current.delete(s.span_id);
                    }}
                    role="option"
                    aria-selected={isSelected}
                    tabIndex={0}
                    onClick={() => select(isSelected ? null : s.span_id)}
                    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), select(s.span_id))}
                    className={`grid h-6 cursor-pointer grid-cols-[minmax(240px,36%)_80px_1fr] items-center border-l-2 text-xs outline-none ${
                      isSelected
                        ? "border-accent bg-accent-soft"
                        : error
                          ? "border-critical hover:bg-surface-2"
                          : "border-transparent hover:bg-surface-2 focus-visible:bg-surface-2"
                    }`}
                  >
                    <div className="flex min-w-0 items-center gap-1.5 pr-2" style={{ paddingLeft: 8 + depth * 14 }}>
                      <span aria-hidden className="inline-block size-2 shrink-0" style={{ background: error ? "var(--critical)" : `var(--kind-${s.kind})` }} />
                      <span className={`truncate ${error ? "text-critical" : ""}`}>{s.name}</span>
                      {role && <span className="font-mono text-[10px] text-ink-3 uppercase">{role}</span>}
                      {error && <span className="shrink-0 font-mono text-[10px] text-critical">✕ error</span>}
                      {orphan && <span className="shrink-0 font-mono text-[10px] text-warning">parent missing</span>}
                    </div>
                    <div className="pr-2 text-right text-[11px]">
                      <Duration ms={s.kind === "span" ? null : s.duration_ms} text={formatDuration(s.duration_ms)} />
                    </div>
                    <div className="relative mr-3 h-6">
                      {ticks.slice(1, -1).map((f) => (
                        <span key={f} aria-hidden className="absolute inset-y-0 w-px bg-border" style={{ left: `${f * 100}%` }} />
                      ))}
                      <span
                        className="absolute top-1/2 h-2.5 -translate-y-1/2"
                        title={`${s.name}: +${formatDuration(Date.parse(s.start_time) - t0)}, ${formatDuration(s.duration_ms)}`}
                        style={{
                          left: `${left}%`,
                          width: `max(${width}%, 2px)`,
                          background: error ? "var(--critical)" : `var(--kind-${s.kind})`,
                          outline:
                            s.kind !== "span" && s.duration_ms >= SLOW_MS && !error ? "1px solid var(--warning)" : undefined,
                          outlineOffset: 1,
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

      {selected && (
        <Inspector
          key={selected.span_id}
          span={selected}
          offsetMs={Date.parse(selected.start_time) - t0}
          tab={tab}
          setTab={setTab}
          onClose={() => select(null)}
          position={`${rows.findIndex((r) => r.span.span_id === selected.span_id) + 1}/${rows.length}`}
        />
      )}
    </div>
  );
}

function metaString(m: Json, key: string): string | null {
  return m && typeof m === "object" && !Array.isArray(m) && typeof m[key] === "string" ? (m[key] as string) : null;
}

function Inspector({
  span: s,
  offsetMs,
  tab,
  setTab,
  onClose,
  position,
}: {
  span: Span;
  offsetMs: number;
  tab: Tab;
  setTab: (t: Tab) => void;
  onClose: () => void;
  position: string;
}) {
  const empty: Record<Tab, boolean> = {
    Overview: false,
    Input: s.input === null,
    Output: s.output === null,
    Metadata: s.metadata === null || (typeof s.metadata === "object" && !Array.isArray(s.metadata) && Object.keys(s.metadata).length === 0),
    Error: !s.error_message,
    "Raw JSON": false,
  };
  const raw = JSON.stringify(s, null, 2);
  return (
    <aside
      aria-label="Span inspector"
      className="flex max-h-[calc(100vh-4.5rem)] min-w-0 flex-col self-start border border-border bg-surface-1 xl:sticky xl:top-14"
    >
      <header className="border-b border-border px-2.5 py-2">
        <div className="flex items-center gap-2">
          <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{s.name}</h2>
          <StatusBadge status={s.status} />
          <span className="num text-[10px] text-ink-3">{position}</span>
          <button type="button" onClick={onClose} className="btn h-6 px-1.5" aria-label="Close inspector (Esc)">
            esc ✕
          </button>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <KindBadge kind={s.kind} />
          <RoleTag role={metaString(s.metadata, "role")} />
          <Id value={s.span_id} />
        </div>
      </header>

      <div role="tablist" aria-label="Span details" className="flex border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`h-7 border-r border-border px-2 text-[11px] last:border-r-0 ${
              tab === t ? "bg-surface-1 font-medium text-ink" : "bg-surface-2 text-ink-2 hover:text-ink"
            } ${t === "Error" && s.error_message ? "text-critical" : ""} ${empty[t] && tab !== t ? "text-ink-3" : ""}`}
            style={tab === t ? { borderBottom: "2px solid var(--accent)", marginBottom: -1 } : undefined}
          >
            {t}
            {t === "Error" && s.error_message && " ✕"}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="min-h-0 flex-1 overflow-auto p-2.5 text-xs">
        {tab === "Overview" && <OverviewTab s={s} offsetMs={offsetMs} />}
        {tab === "Input" && <Payload value={s.input} />}
        {tab === "Output" && <Payload value={s.output} />}
        {tab === "Metadata" && <Payload value={empty.Metadata ? null : s.metadata} />}
        {tab === "Error" &&
          (s.error_message ? (
            <div className="border border-critical bg-critical-soft p-2">
              <p className="font-medium text-critical">✕ {s.error_message.split(":")[0]}</p>
              <pre className="mt-1 font-mono text-[11px] whitespace-pre-wrap break-words text-ink">{s.error_message}</pre>
            </div>
          ) : (
            <p className="text-ink-3">No error: this span finished with status ok.</p>
          ))}
        {tab === "Raw JSON" && (
          <div>
            <div className="mb-1.5 flex justify-end">
              <CopyButton value={raw} label="copy JSON" />
            </div>
            <Pre>{raw}</Pre>
          </div>
        )}
      </div>
    </aside>
  );
}

function OverviewTab({ s, offsetMs }: { s: Span; offsetMs: number }) {
  const rows: [string, React.ReactNode][] = [
    ["Status", <StatusBadge key="s" status={s.status} />],
    ["Duration", <Duration key="d" ms={s.kind === "span" ? null : s.duration_ms} text={formatDuration(s.duration_ms)} />],
    ["Starts at", <span key="o" className="num">+{formatDuration(Math.round(offsetMs))}</span>],
    ["Start", <span key="st" className="num">{formatTime(s.start_time)}</span>],
    ["End", <span key="en" className="num">{formatTime(s.end_time)}</span>],
    ["Model", s.model ? <span className="font-mono">{s.model}</span> : "–"],
    ["Input tokens", <span key="it" className="num">{formatTokens(s.input_tokens)}</span>],
    ["Output tokens", <span key="ot" className="num">{formatTokens(s.output_tokens)}</span>],
    ["Cost", <span key="c" className="num">{formatCost(s.cost_usd)}</span>],
    ["Span ID", <Id key="si" value={s.span_id} />],
    ["Parent ID", s.parent_span_id ? <Id key="pi" value={s.parent_span_id} /> : <span className="text-ink-3">none (root)</span>],
  ];
  return (
    <dl className="grid grid-cols-[7rem_1fr] border-t border-l border-border">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="border-r border-b border-border bg-surface-2 px-2 py-1 text-[11px] text-ink-3">{k}</dt>
          <dd className="min-w-0 truncate border-r border-b border-border px-2 py-1">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

type Msg = { role: string; content: string };

function isMessages(v: Json): v is Msg[] {
  return Array.isArray(v) && v.length > 0 && v.every((m) => typeof m === "object" && m !== null && "role" in m && "content" in m);
}

function Payload({ value }: { value: Json }) {
  if (value === null) return <p className="text-ink-3">Empty.</p>;
  const obj = typeof value === "object" && !Array.isArray(value) ? value : null;

  // SDK truncation marker: say that it was cut, and show the preview.
  if (obj && obj._argus_truncated === true) {
    return (
      <div>
        <p className="mb-1.5 border border-warning bg-warning-soft px-2 py-1 text-[11px]">
          Truncated by the SDK: original {formatTokens(Number(obj.original_bytes))} bytes
        </p>
        <Pre>{String(obj.preview)}</Pre>
      </div>
    );
  }

  // LLM calls (wrap_openai): {messages: [...], params: {...}} → chat view.
  if (obj && isMessages(obj.messages)) {
    const params = obj.params as Json;
    return (
      <div className="space-y-1.5">
        {obj.messages.map((m, i) => (
          <div key={i} className="border border-border">
            <p className="border-b border-border bg-surface-2 px-2 py-0.5 font-mono text-[10px] tracking-wider text-ink-3 uppercase">{m.role}</p>
            <p className="px-2 py-1.5 leading-relaxed whitespace-pre-wrap">{m.content}</p>
          </div>
        ))}
        {params && typeof params === "object" && Object.keys(params).length > 0 && <Pre>{JSON.stringify(params, null, 2)}</Pre>}
      </div>
    );
  }

  if (obj && typeof obj.content === "string" && typeof obj.role === "string") {
    return (
      <div className="border border-border">
        <p className="border-b border-border bg-surface-2 px-2 py-0.5 font-mono text-[10px] tracking-wider text-ink-3 uppercase">{obj.role}</p>
        <p className="px-2 py-1.5 leading-relaxed whitespace-pre-wrap">{obj.content}</p>
      </div>
    );
  }

  if (typeof value === "string") return <p className="border border-border px-2 py-1.5 leading-relaxed whitespace-pre-wrap">{value}</p>;
  return <Pre>{JSON.stringify(value, null, 2)}</Pre>;
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="overflow-auto border border-border bg-surface-2 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
      {children}
    </pre>
  );
}
