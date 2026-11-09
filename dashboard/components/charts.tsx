"use client";

// Recharts charts for /metrics. Colours are CSS variables (light/dark tokens in globals.css).
// Each chart: one y-axis, recessive grid, hover tooltip, legend for 2+ series,
// and a data table under it (accessibility + exact values).

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCost } from "@/lib/format";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false, axisLine: false } as const;

function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

function hourLabel(iso: string): string {
  const d = new Date(iso);
  return `${dayLabel(iso)} ${String(d.getUTCHours()).padStart(2, "0")}:00`;
}

function TooltipBox({ title, rows }: { title: string; rows: { color?: string; label: string; value: string }[] }) {
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink">{title}</p>
      {rows.map((r) => (
        <p key={r.label} className="flex items-center justify-between gap-4 text-ink-2">
          <span className="flex items-center gap-1.5">
            {r.color && <span aria-hidden className="size-2 rounded-sm" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span className="font-medium text-ink tabular-nums">{r.value}</span>
        </p>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm" style={{ background: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

// --- cost per day, stacked by model -----------------------------------------------------

export interface CostRow {
  day: string;
  [model: string]: number | string;
}

export function CostChart({ rows, series }: { rows: CostRow[]; series: { name: string; color: string }[] }) {
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="day" tickFormatter={dayLabel} {...axis} />
          <YAxis tickFormatter={(v: number) => (v === 0 ? "$0" : `$${Number(v.toPrecision(2))}`)} width={56} {...axis} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TooltipBox
                  title={dayLabel(String(label))}
                  rows={[
                    ...[...payload].reverse().map((p) => ({
                      color: String(p.color),
                      label: String(p.name),
                      value: formatCost(Number(p.value)),
                    })),
                    { label: "Total", value: formatCost(payload.reduce((a, p) => a + Number(p.value), 0)) },
                  ]}
                />
              ) : null
            }
          />
          {series.map((s, i) => (
            <Bar
              key={s.name}
              dataKey={s.name}
              stackId="cost"
              fill={s.color}
              maxBarSize={36}
              // 2px surface gap between stacked segments; round only the top segment
              stroke="var(--surface-1)"
              strokeWidth={2}
              radius={i === series.length - 1 ? [4, 4, 0, 0] : 0}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- trace error rate over time (single series: no legend, the title names it) ----------

export interface ErrorRow {
  bucket: string;
  rate: number | null; // percent
  traces: number;
  errors: number;
}

export function ErrorRateChart({ rows, bucket }: { rows: ErrorRow[]; bucket: "hour" | "day" }) {
  const label = bucket === "hour" ? hourLabel : dayLabel;
  // One tick per day (midnight buckets), so hourly data doesn't repeat day names.
  const ticks = bucket === "hour" ? rows.map((r) => r.bucket).filter((b) => new Date(b).getUTCHours() === 0) : undefined;
  // Fixed, readable y ticks: 0 .. top in quarters (top at least 10%).
  const maxRate = Math.max(0, ...rows.map((r) => r.rate ?? 0));
  const top = Math.max(10, Math.ceil(maxRate / 10) * 10);
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="bucket" tickFormatter={dayLabel} ticks={ticks} minTickGap={24} {...axis} />
          <YAxis
            tickFormatter={(v: number) => `${v}%`}
            width={44}
            domain={[0, top]}
            ticks={[0, top / 4, top / 2, (3 * top) / 4, top]}
            {...axis}
          />
          <Tooltip
            cursor={{ stroke: "var(--text-muted)", strokeDasharray: "3 3" }}
            content={({ active, payload }) => {
              const r = active && payload?.length ? (payload[0].payload as ErrorRow) : null;
              return r ? (
                <TooltipBox
                  title={label(r.bucket)}
                  rows={[
                    { label: "Error rate", value: r.rate === null ? "no traces" : `${r.rate.toFixed(1)}%` },
                    { label: "Failed traces", value: `${r.errors} of ${r.traces}` },
                  ]}
                />
              ) : null;
            }}
          />
          <Line
            dataKey="rate"
            stroke="var(--text-secondary)"
            strokeWidth={2}
            // A bucket with data between two empty buckets has no line segment: draw a dot.
            dot={(p: { cx?: number; cy?: number; index?: number }) => {
              const i = p.index ?? 0;
              const isolated = rows[i]?.rate !== null && rows[i - 1]?.rate == null && rows[i + 1]?.rate == null;
              return isolated && p.cx !== undefined && p.cy !== undefined ? (
                <circle key={i} cx={p.cx} cy={p.cy} r={4} fill="var(--text-secondary)" stroke="var(--surface-1)" strokeWidth={2} />
              ) : (
                <g key={i} />
              );
            }}
            activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
