// Formatting helpers (pure functions, unit-tested in format.test.ts).

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "–";
  if (ms === 0) return "0 ms";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "–"; // unknown (no price), not zero
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function formatTime(iso: string): string {
  // Fixed UTC format so server and browser render identical text.
  return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export function formatRelative(iso: string, now: number = Date.now()): string {
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86_400)} d ago`;
}

export const RANGES = {
  "1h": 3600_000,
  "24h": 86_400_000,
  "7d": 7 * 86_400_000,
  "30d": 30 * 86_400_000,
  all: null,
} as const;
export type RangeKey = keyof typeof RANGES;

export function rangeStart(range: string, now: number = Date.now()): string | undefined {
  const ms = (RANGES as Record<string, number | null>)[range];
  return ms ? new Date(now - ms).toISOString() : undefined;
}
