import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDebate, currentSpeaker, liveDebate } from "./debate.ts";
import type { Json, LiveTrace, Span } from "./types.ts";

let clock = 0;
function sp(id: string, name: string, parent: string | null, extra: Partial<Span> = {}): Span {
  const start = new Date(Date.UTC(2026, 0, 1) + clock++ * 100).toISOString();
  return {
    span_id: id, parent_span_id: parent, name, kind: "span", start_time: start,
    end_time: new Date(Date.parse(start) + 50).toISOString(), duration_ms: 50, status: "ok",
    error_message: null, model: null, input: null, output: null, input_tokens: null,
    output_tokens: null, cost_usd: null, metadata: null, ...extra,
  };
}

const gen = (id: string, parent: string, text: string, extra: Partial<Span> = {}) =>
  sp(id, "openai.chat.completions", parent, {
    kind: "generation", model: "fake-small", output: { role: "assistant", content: text },
    input_tokens: 10, output_tokens: 5, cost_usd: 0.001, ...extra,
  });

function fullDebate(): Span[] {
  clock = 0;
  const m = (x: object): Json => ({ topic: "Tea vs coffee", rounds: 1, pro_model: "fake-small",
                                      con_model: "fake-large", judge_model: "fake-large", ...x });
  return [
    sp("root", "debate", null, { input: { topic: "Tea vs coffee", rounds: 1 },
      output: { winner: "con", scores: { pro: 6, con: 8 }, reasoning: "Better evidence." },
      metadata: m({ winner: "con" }) }),
    sp("r1", "debate.round", "root", { metadata: m({ round: 1 }) }),
    sp("tp", "debate.turn", "r1", { input: { side: "pro", round: 1, model: "fake-small" },
      output: "Tea is calmer.", metadata: m({ round: 1, role: "pro" }) }),
    sp("fp", "lookup_fact", "tp", { kind: "tool", output: "a fact", metadata: m({ round: 1, role: "pro" }) }),
    gen("gp1", "tp", "", { status: "error", error_message: "LLMTimeout: injected", output: null,
      input_tokens: null, output_tokens: null, cost_usd: null, metadata: m({ round: 1, role: "pro" }) }),
    gen("gp2", "tp", "Tea is calmer.", { metadata: m({ round: 1, role: "pro" }) }),
    sp("tc", "debate.turn", "r1", { input: { side: "con", round: 1, model: "fake-large" },
      output: "Coffee works.", metadata: m({ round: 1, role: "con" }) }),
    sp("fc", "lookup_fact", "tc", { kind: "tool", status: "error", error_message: "ToolTimeout" }),
    gen("gc", "tc", "Coffee works.", { model: "fake-large", metadata: m({ round: 1, role: "con" }) }),
    sp("judge", "judge", "root", { metadata: m({ role: "judge" }) }),
    sp("ja1", "judge.attempt", "judge", { status: "error", error_message: "InvalidVerdict: json",
      metadata: m({ role: "judge", attempt: 1 }) }),
    gen("jg1", "ja1", "Sure! {", { model: "fake-large" }),
    sp("ja2", "judge.attempt", "judge", { metadata: m({ role: "judge", attempt: 2 }) }),
    gen("jg2", "ja2", '{"winner":"con"}', { model: "fake-large" }),
  ];
}

test("buildDebate: rounds, turns, facts, retries, verdict", () => {
  const d = buildDebate("t1", fullDebate());
  assert.equal(d.topic, "Tea vs coffee");
  assert.equal(d.status, "ok");
  assert.deepEqual(d.models, { pro: "fake-small", con: "fake-large", judge: "fake-large" });
  assert.equal(d.rounds.length, 1);
  const { pro, con } = d.rounds[0];
  assert.equal(pro?.text, "Tea is calmer.");
  assert.equal(pro?.attempts.length, 2); // timeout + retry
  assert.equal(pro?.attempts[0].ok, false);
  assert.equal(pro?.fact?.text, "a fact");
  assert.equal(con?.fact?.span.status, "error"); // the con side's lookup failed
  assert.equal(d.judge.attempts.length, 2);
  assert.equal(d.judge.attempts[0].ok, false);
  assert.equal(d.judge.attempts[0].raw, "Sure! {");
  assert.deepEqual(d.judge.verdict, { winner: "con", scorePro: 6, scoreCon: 8, reasoning: "Better evidence." });
  assert.equal(d.retries, 2); // one LLM retry + one judge retry
  assert.equal(d.errors.length, 3);
  assert.equal(d.totalCostUsd?.toFixed(3), "0.004"); // the timed-out call has no cost
});

test("buildDebate: a running debate (no root yet, turn still open) uses span metadata", () => {
  const spans = fullDebate().filter((s) => ["fp", "gp2"].includes(s.span_id));
  const d = buildDebate("t1", spans);
  assert.equal(d.status, "running");
  assert.equal(d.topic, "Tea vs coffee");
  assert.equal(d.rounds[0].pro?.span, null); // turn span not received yet
  assert.equal(d.rounds[0].pro?.text, "Tea is calmer."); // from the finished LLM call
  assert.equal(d.judge.verdict, null);
});

test("currentSpeaker follows turn order", () => {
  assert.equal(currentSpeaker("lookup_fact", "pro", 1, 3), "pro");
  assert.equal(currentSpeaker("debate.turn", "pro", 1, 3), "con");
  assert.equal(currentSpeaker("debate.turn", "con", 1, 3), "pro");
  assert.equal(currentSpeaker("debate.turn", "con", 3, 3), "judge");
  assert.equal(currentSpeaker("judge.attempt", "judge", 3, 3), "judge");
  assert.equal(currentSpeaker("debate.round", null, 1, 3), "pro");
  assert.equal(currentSpeaker("debate.round", null, 3, 3), "judge");
  assert.equal(currentSpeaker("something", null, null, null), null);
});

test("liveDebate summarises a live trace", () => {
  const t: LiveTrace = {
    trace_id: "t", start_time: "2026-01-01T00:00:00Z", last_received_at: "2026-01-01T00:00:05Z",
    span_count: 7, error_count: 0, models: ["fake-small"], total_cost_usd: 0.01, input_tokens: 1,
    output_tokens: 1,
    attributes: { topic: "X", rounds: 3, round: 2, role: "pro", pro_model: "a", con_model: "b", judge_model: "c" },
    latest_span: { name: "debate.turn", kind: "span", status: "ok", model: null,
      end_time: "2026-01-01T00:00:05Z", metadata: { round: 2, role: "pro" } },
  };
  const d = liveDebate(t);
  assert.equal(d.topic, "X");
  assert.equal(d.round, 2);
  assert.equal(d.speaker, "con");
  assert.equal(d.phase, "debating");
  assert.equal(d.latestStep, "pro · debate.turn");
  assert.deepEqual(d.models, { pro: "a", con: "b", judge: "c" });
});
