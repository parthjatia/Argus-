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
