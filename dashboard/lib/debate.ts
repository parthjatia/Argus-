// Turn a debate trace (flat spans) into the Debates view model, and summarise in-progress
// debates for the Live page. Pure functions, unit-tested in debate.test.ts.
//
// Conventions from the Debate Arena (demo/arena/debate.py): spans carry metadata `topic`,
// `rounds`, `pro_model`, `con_model`, `judge_model`; inside a round `round`; a turn's spans
// `role` = pro | con; the judge's spans `role` = judge. Older traces without that metadata
// still work through the tree (turn input {side, round}).

import type { Json, LiveTrace, Span } from "./types.ts";

export type Role = "pro" | "con" | "judge";

type Obj = { [key: string]: Json };

function obj(v: Json | undefined): Obj {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};
}

function str(v: Json | undefined): string | null {
  return typeof v === "string" ? v : null;
}

function num(v: Json | undefined): number | null {
  return typeof v === "number" ? v : null;
}

function byStart(a: Span, b: Span): number {
  return a.start_time < b.start_time ? -1 : a.start_time > b.start_time ? 1 : 0;
}

/** Text of an LLM answer, whatever shape the SDK stored it in. */
export function messageText(output: Json): string | null {
  if (typeof output === "string") return output;
  const o = obj(output);
  return str(o.content) ?? str(o.text);
}

export interface Attempt {
  span: Span; // the generation span (one per LLM call attempt)
  ok: boolean;
  error: string | null;
}

export interface TurnView {
  role: "pro" | "con";
  round: number;
  span: Span | null; // the debate.turn span (null while it is still running)
  text: string | null;
  model: string | null;
  fact: { text: string | null; span: Span } | null;
  attempts: Attempt[];
  error: string | null;
  durationMs: number | null;
  tokens: number;
  costUsd: number | null;
}

export interface JudgeAttempt {
  n: number;
  span: Span;
  ok: boolean;
  error: string | null;
  raw: string | null; // what the model answered (shown for invalid JSON)
}

export interface Verdict {
  winner: string;
  scorePro: number | null;
  scoreCon: number | null;
  reasoning: string | null;
}

export interface DebateView {
  traceId: string;
  topic: string | null;
  status: "ok" | "error" | "running";
  root: Span | null;
  models: { pro: string | null; con: string | null; judge: string | null };
  roundsPlanned: number | null;
  rounds: { n: number; pro: TurnView | null; con: TurnView | null }[];
  judge: { span: Span | null; attempts: JudgeAttempt[]; verdict: Verdict | null; error: string | null };
  errors: Span[];
  retries: number; // extra LLM attempts after a timeout + extra judge attempts
  totalCostUsd: number | null;
  totalTokens: number;
  durationMs: number;
}

function sumCost(spans: Span[]): number | null {
  return spans.reduce<number | null>((a, s) => (s.cost_usd === null ? a : (a ?? 0) + s.cost_usd), null);
}

function sumTokens(spans: Span[]): number {
  return spans.reduce((a, s) => a + (s.input_tokens ?? 0) + (s.output_tokens ?? 0), 0);
}

export function buildDebate(traceId: string, spans: Span[]): DebateView {
  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const childrenOf = (id: string) => spans.filter((s) => s.parent_span_id === id).sort(byStart);
  const meta = (s: Span) => obj(s.metadata);
  const root = spans.find((s) => s.parent_span_id === null) ?? null;
  const rootIn = root ? obj(root.input) : {};
  const anyMeta = (key: string): Json | undefined => {
    for (const s of spans) if (meta(s)[key] !== undefined) return meta(s)[key];
    return undefined;
  };

  // Which turn does a span belong to? Its own role/round metadata, else its parent turn.
  const roleOf = (s: Span): string | null => str(meta(s).role) ?? str(obj(s.input).side);
  const roundOf = (s: Span): number | null => num(meta(s).round) ?? num(obj(s.input).round);

  const turnSpans = spans.filter((s) => s.name === "debate.turn");
  const turnKey = (role: string, round: number) => `${round}:${role}`;
  const turns = new Map<string, TurnView>();
  const ensureTurn = (role: "pro" | "con", round: number): TurnView => {
    const key = turnKey(role, round);
    let t = turns.get(key);
    if (!t) {
      t = { role, round, span: null, text: null, model: null, fact: null, attempts: [], error: null,
            durationMs: null, tokens: 0, costUsd: null };
      turns.set(key, t);
    }
    return t;
  };

  for (const ts of turnSpans) {
    const role = roleOf(ts);
    const round = roundOf(ts);
    if ((role !== "pro" && role !== "con") || round === null) continue;
    const t = ensureTurn(role, round);
    t.span = ts;
    t.text = messageText(ts.output);
    t.error = ts.error_message;
    t.durationMs = ts.duration_ms;
    t.model = str(obj(ts.input).model);
  }

  // Children of turns (or orphans that carry role + round metadata while the turn runs).
  for (const s of spans) {
    if (s.name !== "lookup_fact" && s.kind !== "generation") continue;
    const parent = s.parent_span_id ? byId.get(s.parent_span_id) : undefined;
    if (parent && parent.name !== "debate.turn") continue; // judge generations: below
    const role = parent ? roleOf(parent) : roleOf(s);
    const round = parent ? roundOf(parent) : roundOf(s);
    if ((role !== "pro" && role !== "con") || round === null) continue;
    const t = ensureTurn(role, round);
    if (s.name === "lookup_fact") {
      t.fact = { text: typeof s.output === "string" ? s.output : messageText(s.output), span: s };
    } else {
      t.attempts.push({ span: s, ok: s.status === "ok", error: s.error_message });
      t.model ??= s.model;
    }
  }
  for (const t of turns.values()) {
    t.attempts.sort((a, b) => byStart(a.span, b.span));
    const own = [...t.attempts.map((a) => a.span), ...(t.fact ? [t.fact.span] : [])];
    t.tokens = sumTokens(own);
    t.costUsd = sumCost(own);
    if (t.text === null && !t.error) {
      const okAttempt = t.attempts.find((a) => a.ok);
      t.text = okAttempt ? messageText(okAttempt.span.output) : null;
    }
  }

  const roundNumbers = [...new Set([...turns.values()].map((t) => t.round))].sort((a, b) => a - b);
  const rounds = roundNumbers.map((n) => ({
    n,
    pro: turns.get(turnKey("pro", n)) ?? null,
    con: turns.get(turnKey("con", n)) ?? null,
  }));

  // Judge
  const judgeSpan = spans.find((s) => s.name === "judge") ?? null;
  const attemptSpans = spans.filter((s) => s.name === "judge.attempt").sort(byStart);
  const attempts: JudgeAttempt[] = attemptSpans.map((a, i) => {
    const gens = childrenOf(a.span_id).filter((c) => c.kind === "generation");
    const answered = [...gens].reverse().find((g) => g.status === "ok");
    return {
      n: num(meta(a).attempt) ?? i + 1,
      span: a,
      ok: a.status === "ok",
      error: a.error_message,
      raw: answered ? messageText(answered.output) : null,
    };
  });
  const verdictSource = root && root.output ? obj(root.output) : obj(attemptSpans.find((a) => a.status === "ok")?.output ?? null);
  const scores = obj(verdictSource.scores);
  const winner = str(verdictSource.winner) ?? (root ? str(meta(root).winner) : null);
  const verdict: Verdict | null = winner
    ? {
        winner,
        scorePro: num(scores.pro) ?? (root ? num(meta(root).score_pro) : null),
        scoreCon: num(scores.con) ?? (root ? num(meta(root).score_con) : null),
        reasoning: str(verdictSource.reasoning),
      }
    : null;

  const generations = spans.filter((s) => s.kind === "generation");
  const turnRetries = [...turns.values()].reduce((a, t) => a + Math.max(0, t.attempts.length - 1), 0);
  const start = Math.min(...spans.map((s) => Date.parse(s.start_time)));
  const end = Math.max(...spans.map((s) => Date.parse(s.end_time)));

  return {
    traceId,
    topic: str(rootIn.topic) ?? str(anyMeta("topic")),
    status: root ? root.status : "running",
    root,
    models: {
      pro: str(anyMeta("pro_model")) ?? str(obj(rootIn.models).pro),
      con: str(anyMeta("con_model")) ?? str(obj(rootIn.models).con),
      judge: str(anyMeta("judge_model")) ?? str(obj(rootIn.models).judge),
    },
    roundsPlanned: num(rootIn.rounds) ?? num(anyMeta("rounds")),
    rounds,
    judge: { span: judgeSpan, attempts, verdict, error: judgeSpan?.error_message ?? null },
    errors: spans.filter((s) => s.status === "error").sort(byStart),
    retries: turnRetries + Math.max(0, attempts.length - 1),
    totalCostUsd: sumCost(generations),
    totalTokens: sumTokens(spans),
    durationMs: spans.length ? end - start : 0,
  };
}

// --- live --------------------------------------------------------------------------------

export interface LiveDebate {
  traceId: string;
  topic: string | null;
  models: { pro: string | null; con: string | null; judge: string | null };
  round: number | null;
  rounds: number | null;
  phase: "debating" | "judging";
  speaker: Role | null;
  latestStep: string; // e.g. "con · openai.chat.completions"
  latestStatus: string;
  latestAt: string;
}

/** Who is speaking now. The latest FINISHED span tells us: inside a turn (fact lookup or
 *  LLM call) that side is still speaking; a finished turn hands over to the other side, or
 *  to the judge after the last round's con turn. */
export function currentSpeaker(
  latestName: string,
  latestRole: string | null,
  round: number | null,
  rounds: number | null,
): Role | null {
  if (latestRole === "judge") return "judge";
  // A finished round: the next round starts with pro, or the judge after the last one.
  if (latestName === "debate.round") return round !== null && rounds !== null && round >= rounds ? "judge" : "pro";
  if (latestRole !== "pro" && latestRole !== "con") return null;
  if (latestName !== "debate.turn") return latestRole;
  if (latestRole === "pro") return "con";
  return round !== null && rounds !== null && round >= rounds ? "judge" : "pro";
}

export function liveDebate(t: LiveTrace): LiveDebate {
  const a = t.attributes;
  const latestMeta = obj(t.latest_span.metadata);
  const role = str(latestMeta.role); // the latest span's own role (a round span has none)
  const round = num(latestMeta.round) ?? num(a.round);
  const rounds = num(a.rounds);
  const speaker = currentSpeaker(t.latest_span.name, role, round, rounds);
  // The latest span already finished: if a round span closed, the next round is under way.
  const shownRound =
    t.latest_span.name === "debate.round" && round !== null && rounds !== null && round < rounds ? round + 1 : round;
  return {
    traceId: t.trace_id,
    topic: str(a.topic),
    models: { pro: str(a.pro_model), con: str(a.con_model), judge: str(a.judge_model) },
    round: shownRound,
    rounds,
    phase: speaker === "judge" ? "judging" : "debating",
    speaker,
    latestStep: [role, t.latest_span.name].filter(Boolean).join(" · "),
    latestStatus: t.latest_span.status,
    latestAt: t.latest_span.end_time,
  };
}
