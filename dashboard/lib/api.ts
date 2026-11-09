// Server-side client for the Argus read API.
// Only imported by server components: the API key comes from the dashboard's environment
// (ARGUS_API_KEY) and is never sent to the browser.

import type {
  BreakdownRow,
  CostPoint,
  ErrorPoint,
  LatencyRow,
  LiveTrace,
  ModelCostPoint,
  ModelStats,
  RecentSpan,
  SeriesPoint,
  Summary,
  SystemStatus,
  TraceDetail,
  TraceList,
} from "./types.ts";
import type { Window } from "./range.ts";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Params = Record<string, string | number | undefined | null>;

async function get<T>(path: string, params: Params = {}): Promise<T> {
  const base = process.env.ARGUS_API_URL ?? "http://localhost:8000";
  const key = process.env.ARGUS_API_KEY;
  if (!key) throw new ApiError(0, "ARGUS_API_KEY is not set for the dashboard (see .env)");

  const url = new URL(path, base);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, { headers: { "X-API-Key": key }, cache: "no-store" });
  } catch (err) {
    throw new ApiError(0, `cannot reach the Argus API at ${base}: ${String(err)}`);
  }
  if (!res.ok) throw new ApiError(res.status, `${res.status}: ${await res.text()}`);
  return (await res.json()) as T;
}

export const api = {
  listTraces: (params: Params) => get<TraceList>("/v1/traces", params),
  getTrace: async (id: string): Promise<TraceDetail | null> => {
    try {
      return await get<TraceDetail>(`/v1/traces/${encodeURIComponent(id)}`);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 422)) return null;
      throw err;
    }
  },
  cost: (days: number) => get<CostPoint[]>("/v1/metrics/cost", { days }),
  latency: (days: number) => get<LatencyRow[]>("/v1/metrics/latency", { days }),
  errors: (days: number, bucket: "hour" | "day") =>
    get<ErrorPoint[]>("/v1/metrics/errors", { days, bucket }),

  // control room
  live: () => get<LiveTrace[]>("/v1/traces/live", { window_s: 60 }),
  recentSpans: (limit = 40) => get<RecentSpan[]>("/v1/spans/recent", { limit }),
  summary: (w: Window) => get<Summary>("/v1/metrics/summary", { start: w.start, end: w.end }),
  timeseries: (w: Window, bucketS = w.bucketS) =>
    get<SeriesPoint[]>("/v1/metrics/timeseries", { start: w.start, end: w.end, bucket_s: bucketS }),
  costByModel: (w: Window, bucketS = w.bucketS) =>
    get<ModelCostPoint[]>("/v1/metrics/cost_by_model", { start: w.start, end: w.end, bucket_s: bucketS }),
  models: (w: Window) => get<ModelStats[]>("/v1/metrics/models", { start: w.start, end: w.end }),
  breakdown: (w: Window, attribute: string) =>
    get<BreakdownRow[]>("/v1/metrics/breakdown", { start: w.start, end: w.end, attribute }),
  system: (minutes = 30) => get<SystemStatus>("/v1/system", { minutes }),
};
