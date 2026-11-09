// The top bar's time range (?range=15m|1h|24h|7d) → an absolute window + chart bucket size.
// Pure functions, unit-tested in range.test.ts.

export const RANGE_KEYS = ["15m", "1h", "24h", "7d"] as const;
export type RangeKey = (typeof RANGE_KEYS)[number];
export const DEFAULT_RANGE: RangeKey = "1h";

const RANGE_S: Record<RangeKey, number> = { "15m": 900, "1h": 3600, "24h": 86_400, "7d": 604_800 };
// ~24-30 bars per chart. 7d uses one bar per day so "cost per day" reads directly.
const BUCKET_S: Record<RangeKey, number> = { "15m": 30, "1h": 120, "24h": 3600, "7d": 86_400 };

export interface Window {
  range: RangeKey;
  start: string; // ISO
  end: string; // ISO
  seconds: number;
  bucketS: number;
  bucketLabel: string; // "30 s", "2 min", "hour", "day"
}

export function parseRange(value: string | string[] | undefined): RangeKey {
  const v = Array.isArray(value) ? value[0] : value;
  return (RANGE_KEYS as readonly string[]).includes(v ?? "") ? (v as RangeKey) : DEFAULT_RANGE;
}

export function bucketLabel(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${seconds / 60} min`;
  if (seconds === 3600) return "hour";
  if (seconds === 86_400) return "day";
  return `${seconds / 3600} h`;
}

export function rangeWindow(range: RangeKey, now: number = Date.now()): Window {
  const seconds = RANGE_S[range];
  const bucketS = BUCKET_S[range];
  return {
    range,
    start: new Date(now - seconds * 1000).toISOString(),
    end: new Date(now).toISOString(),
    seconds,
    bucketS,
    bucketLabel: bucketLabel(bucketS),
  };
}

/** Epoch-aligned bucket starts covering the window (same alignment as the API's date_bin). */
export function bucketStarts(w: Window): string[] {
  const step = w.bucketS * 1000;
  const first = Math.floor(Date.parse(w.start) / step) * step;
  const end = Date.parse(w.end);
  const out: string[] = [];
  for (let t = first; t <= end; t += step) out.push(new Date(t).toISOString());
  return out;
}

/** Keep the current range (and only it) when linking to another page. */
export function withRange(href: string, range: RangeKey): string {
  if (range === DEFAULT_RANGE) return href;
  return href + (href.includes("?") ? "&" : "?") + `range=${range}`;
}
