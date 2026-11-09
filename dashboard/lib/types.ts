// Shapes returned by the Argus read API (server/app/api/traces.py, metrics.py).

export interface TraceSummary {
  trace_id: string;
  name: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: "ok" | "error";
  has_root: boolean;
  span_count: number;
  error_count: number;
  models: string[];
  total_cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  root_metadata: Json;
}

export interface TraceList {
  traces: TraceSummary[];
  next_cursor: string | null;
}

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: "span" | "generation" | "tool";
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: "ok" | "error";
  error_message: string | null;
  model: string | null;
  input: Json;
  output: Json;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  metadata: Json;
}

export interface TraceDetail {
  trace_id: string;
  spans: Span[];
}

export interface CostPoint {
  day: string;
  model: string;
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
  calls: number;
}

export interface LatencyRow {
  model: string;
  calls: number;
  p50_ms: number;
  p95_ms: number;
}

export interface ErrorPoint {
  bucket: string;
  traces: number;
  trace_errors: number;
  error_rate: number | null;
  spans: number;
  span_errors: number;
}

// --- control-room endpoints -------------------------------------------------------------

export interface LiveTrace {
  trace_id: string;
  start_time: string;
  last_received_at: string;
  span_count: number;
  error_count: number;
  models: string[];
  total_cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  attributes: { [key: string]: Json };
  latest_span: {
    name: string;
    kind: string;
    status: string;
    model: string | null;
    end_time: string;
    metadata: Json;
  };
}

export interface RecentSpan {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  kind: "span" | "generation" | "tool";
  status: "ok" | "error";
  error_message: string | null;
  model: string | null;
  start_time: string;
  end_time: string;
  duration_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  metadata: Json;
}

export interface Summary {
  start: string;
  end: string;
  traces: number;
  trace_errors: number;
  error_rate: number | null;
  spans: number;
  span_errors: number;
  generations: number;
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
  p50_ms: number | null;
  p95_ms: number | null;
  spans_last_5m: number;
  running: number;
}

export interface SeriesPoint {
  bucket: string;
  traces: number;
  trace_errors: number;
  error_rate: number | null;
  spans: number;
  span_errors: number;
  generations: number;
  cost_usd: number | null;
}

export interface ModelCostPoint {
  bucket: string;
  model: string;
  calls: number;
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
}

export interface ModelStats {
  model: string;
  calls: number;
  errors: number;
  error_rate: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  avg_cost_usd: number | null;
}

export interface BreakdownRow {
  value: string | null;
  calls: number;
  errors: number;
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
}

export interface Dependency {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface SystemStatus {
  checked_at: string;
  postgres: Dependency;
  redis: Dependency;
  stream: {
    length: number;
    backlog: number | null;
    pending: number;
    dead_letters: number;
    consumers: { name: string; pending: number; idle_ms: number }[];
    oldest_pending_ms: number | null;
  } | null;
  rates: { minute: string; ingested: number; written: number }[];
}
