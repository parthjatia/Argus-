// k6 load test for POST /v1/ingest.  Run with `make load` (k6 runs in Docker).
//
// Each iteration sends one SDK-shaped batch (BATCH spans, default 100 = the SDK's batch
// size) and checks for 202. VUS virtual users send back-to-back for DURATION.
//   events/second = spans accepted / test duration
//   latency       = http_req_duration (p50 / p95 / p99), time to 202 (after XADD)
//
// Env: HOST (default http://server:8000), ARGUS_API_KEY (required), VUS, DURATION, BATCH.

import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const HOST = __ENV.HOST || "http://server:8000";
const KEY = __ENV.ARGUS_API_KEY;
const BATCH = Number(__ENV.BATCH || 100);

const spansAccepted = new Counter("spans_accepted");

export const options = {
  scenarios: {
    ingest: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 20),
      duration: __ENV.DURATION || "30s",
    },
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
  thresholds: { checks: ["rate>0.99"] }, // the run fails if more than 1% of batches aren't 202
};

// UUIDv4 from Math.random: fine for test data (k6 has no built-in uuid without a remote import).
function uuid4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// One trace per batch: a root span and children; every third span is an LLM generation
// with ~500 bytes of prompt/output, like the Debate Arena produces.
function makeBatch() {
  const traceId = uuid4();
  const rootId = uuid4();
  const now = Date.now();
  const batch = [];
  for (let i = 0; i < BATCH; i++) {
    const gen = i % 3 === 1;
    const start = new Date(now + i * 10);
    batch.push({
      span_id: i === 0 ? rootId : uuid4(),
      trace_id: traceId,
      parent_span_id: i === 0 ? null : rootId,
      name: i === 0 ? "load.root" : gen ? "llm.chat" : "load.step",
      kind: gen ? "generation" : "span",
      start_time: start.toISOString(),
      end_time: new Date(start.getTime() + 8).toISOString(),
      status: "ok",
      model: gen ? "fake-small" : null,
      input: gen ? { messages: [{ role: "user", content: "a".repeat(250) }] } : {},
      output: gen ? { text: "b".repeat(250) } : {},
      input_tokens: gen ? 120 : null,
      output_tokens: gen ? 80 : null,
      metadata: {},
    });
  }
  return JSON.stringify({ batch });
}

export function setup() {
  if (!KEY) throw new Error("set ARGUS_API_KEY (make load creates one)");
}

export default function () {
  const res = http.post(`${HOST}/v1/ingest`, makeBatch(), {
    headers: { "Content-Type": "application/json", "X-API-Key": KEY },
  });
  if (check(res, { "202 accepted": (r) => r.status === 202 })) spansAccepted.add(BATCH);
}
