"use client";

// Recharts, styled for the control room: bars and step lines only (no curves, no areas,
// no gradients, no animation), square markers, 1px recessive grid, one y-axis per chart,
// tooltip on hover. Colours are CSS variables, so light/dark switch in globals.css.

import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatCost, formatTokens } from "@/lib/format";

// Server components can't pass functions to client components, so formats are named.
export type Format = "cost" | "count" | "tokens";
const FORMATS: Record<Format, (v: number) => string> = {
  cost: (v) => (v === 0 ? "$0" : formatCost(v)),
  count: (v) => (v >= 10_000 ? `${(v / 1000).toFixed(0)}k` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v))),
  tokens: (v) => formatTokens(Math.round(v)),
};

const axis = {
  stroke: "var(--text-muted)",
  fontSize: 10,
  fontFamily: "var(--font-mono)",
  tickLine: false,
  axisLine: { stroke: "var(--border)" },
} as const;

export function timeLabel(iso: string, bucketS: number): string {
  const d = new Date(iso);
  if (bucketS >= 86_400) return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const hm = d.toISOString().slice(11, 16);
  return bucketS < 60 ? d.toISOString().slice(11, 19) : hm;
}

function rangeLabel(iso: string, bucketS: number): string {
  const end = new Date(Date.parse(iso) + bucketS * 1000).toISOString();
  return bucketS >= 86_400 ? timeLabel(iso, bucketS) : `${iso.slice(0, 10)} ${timeLabel(iso, bucketS)}–${timeLabel(end, bucketS)}`;
}

function TooltipBox({ title, rows }: { title: string; rows: { color?: string; label: string; value: string }[] }) {
  return (
    <div className="border border-border-strong bg-surface-1 px-2 py-1.5 text-[11px]">
      <p className="num mb-1 text-ink">{title}</p>
      {rows.map((r) => (
        <p key={r.label} className="flex items-center justify-between gap-4 text-ink-2">
          <span className="flex items-center gap-1.5">
            {r.color && <span aria-hidden className="inline-block size-2" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="num text-ink">{r.value}</span>
        </p>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-ink-2">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block size-2" style={{ background: i.color }} />
          <span className="font-mono">{i.label}</span>
        </li>
      ))}
    </ul>
  );
}

export interface TimeRow {
  bucket: string;
  [series: string]: number | string | null;
}

interface Series {
  key: string;
  label: string;
  color: string;
}

/** Bars over time: one series, stacked series, or side-by-side groups. */
export function TimeBars({
  rows,
  series,
  bucketS,
  format: formatName,
  height = 160,
  stacked = true,
  total = true,
}: {
  rows: TimeRow[];
  series: Series[];
  bucketS: number;
  format: Format;
  height?: number;
  stacked?: boolean;
  total?: boolean;
}) {
  const format = FORMATS[formatName];
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 6, right: 6, bottom: 0, left: 0 }} barCategoryGap={1} barGap={1}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="bucket" tickFormatter={(v: string) => timeLabel(v, bucketS)} minTickGap={28} {...axis} />
          <YAxis tickFormatter={(v: number) => format(v)} width={52} allowDecimals={formatName !== "count"} {...axis} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            isAnimationActive={false}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TooltipBox
                  title={rangeLabel(String(label), bucketS)}
                  rows={[
                    ...[...payload].reverse().map((p) => ({
                      color: String(p.color),
                      label: String(p.name),
                      value: format(Number(p.value ?? 0)),
                    })),
                    ...(total && series.length > 1
                      ? [{ label: "total", value: format(payload.reduce((a, p) => a + Number(p.value ?? 0), 0)) }]
                      : []),
                  ]}
                />
              ) : null
            }
          />
          {series.map((s) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stackId={stacked ? "a" : undefined}
              fill={s.color}
              // 1px surface gap between stacked segments
              stroke="var(--surface-1)"
              strokeWidth={stacked && series.length > 1 ? 1 : 0}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export interface RateRow {
  bucket: string;
  rate: number | null; // percent; null = no traces in the bucket
  traces: number;
  errors: number;
}

/** Error rate as a step line (the value holds for the whole bucket). Gaps = no traces. */
export function ErrorStepLine({ rows, bucketS, height = 160 }: { rows: RateRow[]; bucketS: number; height?: number }) {
  const max = Math.max(0, ...rows.map((r) => r.rate ?? 0));
  const top = Math.max(10, Math.ceil(max / 10) * 10);
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="bucket" tickFormatter={(v: string) => timeLabel(v, bucketS)} minTickGap={28} {...axis} />
          <YAxis
            domain={[0, top]}
            ticks={[0, top / 4, top / 2, (3 * top) / 4, top]}
            tickFormatter={(v: number) => `${v}%`}
            width={40}
            {...axis}
          />
          <Tooltip
            cursor={{ stroke: "var(--border-strong)" }}
            isAnimationActive={false}
            content={({ active, payload }) => {
              const r = active && payload?.length ? (payload[0].payload as RateRow) : null;
              return r ? (
                <TooltipBox
                  title={rangeLabel(r.bucket, bucketS)}
                  rows={[
                    { label: "error rate", value: r.rate === null ? "no traces" : `${r.rate.toFixed(1)}%` },
                    { label: "failed traces", value: `${r.errors} / ${r.traces}` },
                  ]}
                />
              ) : null;
            }}
          />
          <Line
            dataKey="rate"
            type="stepAfter"
            stroke="var(--critical)"
            strokeWidth={1.5}
            connectNulls={false}
            isAnimationActive={false}
            // A bucket between two empty ones has no line segment: draw a square marker.
            dot={(p: { cx?: number; cy?: number; index?: number }) => {
              const i = p.index ?? 0;
              const isolated = rows[i]?.rate !== null && rows[i - 1]?.rate == null && rows[i + 1]?.rate == null;
              return isolated && p.cx !== undefined && p.cy !== undefined ? (
                <rect key={i} x={p.cx - 3} y={p.cy - 3} width={6} height={6} fill="var(--critical)" />
              ) : (
                <g key={i} />
              );
            }}
            activeDot={(p: { cx?: number; cy?: number }) =>
              p.cx !== undefined && p.cy !== undefined ? (
                <rect x={p.cx - 3} y={p.cy - 3} width={6} height={6} fill="var(--critical)" stroke="var(--surface-1)" />
              ) : (
                <g />
              )
            }
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal HTML bars (one scale for all rows): breakdowns, tokens, latency. */
export function HBars({
  rows,
  format: formatName,
}: {
  rows: { label: React.ReactNode; key: string; segments: { value: number; color: string; label: string }[]; note?: string }[];
  format: Format;
}) {
  const format = FORMATS[formatName];
  const max = Math.max(1e-12, ...rows.map((r) => r.segments.reduce((a, s) => a + s.value, 0)));
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => {
        const total = r.segments.reduce((a, s) => a + s.value, 0);
        return (
          <li key={r.key} className="grid grid-cols-[7.5rem_1fr_7.5rem] items-center gap-2 text-xs">
            <span className="truncate font-mono text-[11px]">{r.label}</span>
            <span
              className="flex h-3 bg-surface-2"
              title={r.segments.map((s) => `${s.label}: ${format(s.value)}`).join(" · ")}
            >
              {r.segments.map((s) => (
                <span
                  key={s.label}
                  className="h-full border-r border-surface-1 last:border-r-0"
                  style={{ width: `${(s.value / max) * 100}%`, background: s.color }}
                />
              ))}
            </span>
            <span className="num text-right text-[11px] text-ink-2">{r.note ?? format(total)}</span>
          </li>
        );
      })}
    </ul>
  );
}
