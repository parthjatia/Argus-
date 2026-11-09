from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest

from arena.agents import InvalidVerdict, JudgeFailed, parse_verdict
from arena.debate import run_debate
from arena.faults import Faults, LLMTimeout
from arena.llm import make_client
from argus import client as argus_client
from traffic import run_traffic


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def enqueue(self, event: dict[str, Any], size: int = 0) -> None:
        self.events.append(event)

    def named(self, name: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["name"] == name]

    def one(self, name: str) -> dict[str, Any]:
        (e,) = self.named(name)
        return e

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {e["span_id"]: e for e in self.events}


class ScriptedRandom:
    """Stands in for random.Random: returns scripted values, then `default`."""

    def __init__(self, values: list[float], default: float = 0.99) -> None:
        self.values, self.default = list(values), default

    def random(self) -> float:
        return self.values.pop(0) if self.values else self.default


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[Collector]:
    c = Collector()
    monkeypatch.setattr(argus_client, "_exporter", c)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    yield c


def debate(faults: Faults | None = None, **kwargs: Any):
    faults = faults or Faults()
    return run_debate("Cats are better than dogs", faults=faults,
                      client=make_client(faults, fake_latency_s=0), **kwargs)  # fmt: skip


def test_debate_produces_one_complete_trace(spans: Collector) -> None:
    result = debate()

    counts = Counter(e["name"] for e in spans.events)
    assert counts == {
        "debate": 1, "debate.round": 3, "debate.turn": 6, "lookup_fact": 6,
        "openai.chat.completions": 7, "judge": 1, "judge.attempt": 1,
    }  # fmt: skip
    assert {e["trace_id"] for e in spans.events} == {result.trace_id}

    by_id = spans.by_id()
    parent = {e["name"]: set() for e in spans.events}
    for e in spans.events:
        if e["parent_span_id"]:
            parent[e["name"]].add(by_id[e["parent_span_id"]]["name"])
    assert parent["debate.round"] == {"debate"}
    assert parent["debate.turn"] == {"debate.round"}
    assert parent["lookup_fact"] == {"debate.turn"}
    assert parent["openai.chat.completions"] == {"debate.turn", "judge.attempt"}
    assert parent["judge.attempt"] == {"judge"}
    assert spans.one("debate")["parent_span_id"] is None

    gens = spans.named("openai.chat.completions")
    assert all(g["kind"] == "generation" and g["input_tokens"] > 0 for g in gens)
    assert {g["model"] for g in gens} == {"fake-small", "fake-large"}
    assert all(t["kind"] == "tool" for t in spans.named("lookup_fact"))
    assert result.verdict.winner in ("pro", "con", "tie")
    assert all(e["status"] == "ok" for e in spans.events)


def test_fake_provider_is_deterministic(spans: Collector) -> None:
    a, b = debate(), debate()
    assert [t.text for t in a.transcript] == [t.text for t in b.transcript]
    assert a.verdict == b.verdict


def test_bad_json_is_retried_once_and_both_attempts_are_traced(spans: Collector) -> None:
    # Only judge calls draw for bad_json; 0.1 → corrupt the first answer, then fine.
    result = debate(Faults(bad_json_rate=0.5, rng=ScriptedRandom([0.1, 0.1])))

    attempts = sorted(spans.named("judge.attempt"), key=lambda e: e["metadata"]["attempt"])
    assert [a["status"] for a in attempts] == ["error", "ok"]
    assert attempts[0]["error_message"].startswith("InvalidVerdict")
    assert spans.one("judge")["status"] == "ok"
    assert result.verdict is not None


def test_bad_json_twice_fails_the_debate(spans: Collector) -> None:
    with pytest.raises(JudgeFailed):
        debate(Faults(bad_json_rate=1.0))
    assert [a["status"] for a in spans.named("judge.attempt")] == ["error", "error"]
    assert spans.one("judge")["status"] == "error"
    assert spans.one("debate")["status"] == "error"


def test_llm_timeout_is_retried_and_visible(spans: Collector) -> None:
    # First LLM call times out, everything after succeeds (tool calls draw first: 0.99).
    rng = ScriptedRandom([0.99, 0.1])  # tool ok, first LLM call → timeout
    result = debate(Faults(timeout_rate=0.5, rng=rng))

    first_turn_gens = [g for g in spans.named("openai.chat.completions")
                       if g["status"] == "error"]  # fmt: skip
    assert len(first_turn_gens) == 1
    assert first_turn_gens[0]["error_message"] == "LLMTimeout: injected: LLM request timed out"
    assert len(spans.named("openai.chat.completions")) == 8  # 7 + one retry
    assert result.failed_turns == 0


def test_turn_fails_but_debate_continues_when_retry_also_times_out(spans: Collector) -> None:
    rng = ScriptedRandom([0.99, 0.1, 0.1])  # tool ok, both attempts of turn 1 time out
    result = debate(Faults(timeout_rate=0.5, rng=rng))

    turns = spans.named("debate.turn")
    assert [t["status"] for t in turns].count("error") == 1
    assert result.failed_turns == 1
    assert result.transcript[0].text is None
    assert spans.one("debate")["status"] == "ok"


def test_everything_timing_out_fails_the_debate(spans: Collector) -> None:
    with pytest.raises(LLMTimeout):
        debate(Faults(timeout_rate=1.0))
    assert spans.one("debate")["status"] == "error"
    assert all(t["status"] == "error" for t in spans.named("lookup_fact"))


def test_tool_timeout_does_not_fail_the_turn(spans: Collector) -> None:
    rng = ScriptedRandom([0.1], default=0.99)  # first lookup_fact times out
    debate(Faults(timeout_rate=0.5, rng=rng))
    tools = spans.named("lookup_fact")
    assert [t["status"] for t in tools].count("error") == 1
    failed_turn = next(t for t in spans.named("debate.turn")
                       if (t["metadata"] or {}).get("fact_lookup") == "failed")  # fmt: skip
    assert failed_turn["status"] == "ok"


def test_slow_injection_adds_latency(spans: Collector) -> None:
    from datetime import datetime

    debate(Faults(slow_rate=1.0, slow_seconds=0.02), rounds=1)
    for g in spans.named("openai.chat.completions"):
        took = datetime.fromisoformat(g["end_time"]) - datetime.fromisoformat(g["start_time"])
        assert took.total_seconds() >= 0.02


def test_faults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARENA_TIMEOUT_RATE", "0.1")
    monkeypatch.setenv("ARENA_BAD_JSON_RATE", "0.2")
    monkeypatch.setenv("ARENA_SLOW_RATE", "0.3")
    f = Faults.from_env()
    assert (f.timeout_rate, f.bad_json_rate, f.slow_rate) == (0.1, 0.2, 0.3)


@pytest.mark.parametrize(
    "content",
    ['{"winner": "pro"}', "Sure! {}", '{"winner": "maybe", "scores": {"pro": 1, "con": 2}, '
     '"reasoning": "x"}', '{"winner": "pro", "scores": {"pro": 11, "con": 2}, "reasoning": "x"}'],
)  # fmt: skip
def test_parse_verdict_rejects_invalid(content: str) -> None:
    with pytest.raises(InvalidVerdict):
        parse_verdict(content)


def test_parse_verdict_accepts_code_fences() -> None:
    v = parse_verdict('```json\n{"winner": "tie", "scores": {"pro": 5, "con": 5}, '
                      '"reasoning": "even"}\n```')  # fmt: skip
    assert v.winner == "tie"


def test_traffic_runs_many_debates_as_separate_traces(
    spans: Collector, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LATENCY_MS", "0")
    outcomes = run_traffic(4, concurrency=4)
    assert outcomes == {"ok": 4}
    roots = spans.named("debate")
    assert len(roots) == 4
    assert len({r["trace_id"] for r in roots}) == 4
    assert all(r["parent_span_id"] is None for r in roots)  # threads don't share context


def test_every_span_carries_debate_attributes(spans: Collector) -> None:
    run_debate("Cats vs dogs", "fake-small", "fake-large", "fake-large", rounds=2,
               faults=Faults(), client=make_client(Faults(), fake_latency_s=0))  # fmt: skip
    for e in spans.events:
        m = e["metadata"]
        assert m["topic"] == "Cats vs dogs" and m["rounds"] == 2, e["name"]
        assert (m["pro_model"], m["con_model"]) == ("fake-small", "fake-large")
    for e in spans.events:
        if e["name"] in ("debate.turn", "lookup_fact"):
            assert e["metadata"]["role"] in ("pro", "con") and e["metadata"]["round"] in (1, 2)
        if e["name"] in ("judge", "judge.attempt"):
            assert e["metadata"]["role"] == "judge"
    gens = [e for e in spans.events if e["kind"] == "generation"]
    assert {g["metadata"]["role"] for g in gens} == {"pro", "con", "judge"}
    root = next(e for e in spans.events if e["parent_span_id"] is None)
    assert root["metadata"]["winner"] in ("pro", "con", "tie")
    assert isinstance(root["metadata"]["score_pro"], int)
