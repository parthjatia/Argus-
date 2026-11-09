from collections import Counter
from datetime import datetime
from typing import Any

import httpx
import openai
import pytest

from arena import llm
from arena.agents import InvalidVerdict, JudgeFailed, parse_verdict
from arena.debate import run_debate
from arena.faults import Faults, LLMRateLimited, LLMTimeout
from traffic import run_traffic


class ScriptedRandom:
    """Stands in for random.Random: returns scripted values, then `default`."""

    def __init__(self, values: list[float], default: float = 0.99) -> None:
        self.values, self.default = list(values), default

    def random(self) -> float:
        return self.values.pop(0) if self.values else self.default


def http_error(cls: type[openai.APIStatusError], status: int,
               headers: dict[str, str] | None = None) -> openai.APIStatusError:  # fmt: skip
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    return cls(f"HTTP {status}", response=httpx.Response(status, headers=headers or {},
                                                         request=request), body=None)  # fmt: skip


def rate_limited(retry_after: str | None = None) -> openai.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return http_error(openai.RateLimitError, 429, headers)  # type: ignore[return-value]


def timed_out() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://llm.test/v1"))


@pytest.fixture
def debate(make_stub_clients, spans, waits):  # noqa: ANN001, ANN201
    def run(faults: Faults | None = None, scripts: dict[str, list[Any]] | None = None,
            **kwargs: Any):  # fmt: skip
        faults = faults or Faults()
        clients = make_stub_clients(faults, **(scripts or {}))
        return run_debate("Cats are better than dogs", faults=faults, clients=clients, **kwargs)

    return run


def gens(spans) -> list[dict[str, Any]]:  # noqa: ANN001
    return spans.named("openai.chat.completions")


# --- trace shape -----------------------------------------------------------------------------


def test_debate_produces_one_complete_trace(debate, spans) -> None:  # noqa: ANN001
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

    assert all(g["kind"] == "generation" and g["input_tokens"] > 0 for g in gens(spans))
    assert {g["model"] for g in gens(spans)} == {"test-pro", "test-con", "test-judge"}
    assert all(g["metadata"]["llm_attempt"] == 1 for g in gens(spans))
    assert all(t["kind"] == "tool" for t in spans.named("lookup_fact"))
    assert result.verdict.winner == "pro"
    assert all(e["status"] == "ok" for e in spans.events)


def test_root_records_endpoints_but_never_api_keys(debate, spans) -> None:  # noqa: ANN001
    debate()
    root = spans.one("debate")
    assert root["metadata"]["endpoints"] == {r: "http://llm.test/v1" for r in llm.ROLES}
    assert all("test-key" not in str(e) for e in spans.events)


# --- judge validation ------------------------------------------------------------------------


def test_bad_json_is_retried_once_and_both_attempts_are_traced(debate, spans) -> None:  # noqa: ANN001
    # Only judge calls draw for bad_json; 0.1 → corrupt the first answer, then fine.
    result = debate(Faults(bad_json_rate=0.5, rng=ScriptedRandom([0.1, 0.1])))

    attempts = sorted(spans.named("judge.attempt"), key=lambda e: e["metadata"]["attempt"])
    assert [a["status"] for a in attempts] == ["error", "ok"]
    assert attempts[0]["error_message"].startswith("InvalidVerdict")
    assert spans.one("judge")["status"] == "ok"
    assert result.verdict is not None


def test_bad_json_twice_fails_the_debate(debate, spans) -> None:  # noqa: ANN001
    with pytest.raises(JudgeFailed):
        debate(Faults(bad_json_rate=1.0))
    assert [a["status"] for a in spans.named("judge.attempt")] == ["error", "error"]
    assert spans.one("judge")["status"] == "error"
    assert spans.one("debate")["status"] == "error"


# --- rate limits (429) -----------------------------------------------------------------------


def test_429_waits_retry_after_and_each_attempt_is_a_span(debate, spans, waits) -> None:  # noqa: ANN001
    result = debate(scripts={"judge": [rate_limited("7"), rate_limited("0.5")]})

    judge_gens = [g for g in gens(spans) if g["metadata"]["role"] == "judge"]
    assert [g["status"] for g in judge_gens] == ["error", "error", "ok"]
    assert [g["metadata"]["llm_attempt"] for g in judge_gens] == [1, 2, 3]
    assert judge_gens[0]["error_message"].startswith("RateLimitError")
    assert waits == [7.0, 0.5]  # exactly what Retry-After said
    assert result.verdict.winner == "pro"
    assert spans.one("debate")["status"] == "ok"


def test_429_without_retry_after_uses_capped_backoff(debate, waits) -> None:  # noqa: ANN001
    debate(scripts={"pro": [rate_limited(), rate_limited(), rate_limited("999")]})
    assert 0 <= waits[0] <= 2 and 0 <= waits[1] <= 4  # full jitter, 2 s * 2^n
    assert waits[2] == llm.MAX_WAIT_S  # a huge Retry-After is capped


def test_429_that_never_ends_fails_only_that_turn(debate, spans, waits) -> None:  # noqa: ANN001
    result = debate(scripts={"pro": [rate_limited("1")] * (llm.RATE_LIMIT_RETRIES + 1)})

    assert result.failed_turns == 1 and result.transcript[0].text is None
    first_turn = spans.named("debate.turn")[0]
    assert first_turn["status"] == "error"
    assert first_turn["error_message"].startswith("LLMRateLimited")
    assert len(waits) == llm.RATE_LIMIT_RETRIES
    assert spans.one("debate")["status"] == "ok"


def test_429_on_the_judge_fails_the_debate(debate) -> None:  # noqa: ANN001
    with pytest.raises(LLMRateLimited):
        debate(scripts={"judge": [rate_limited("0")] * 10})


# --- timeouts and other errors ---------------------------------------------------------------


def test_real_timeout_is_retried_once(debate, spans) -> None:  # noqa: ANN001
    result = debate(scripts={"con": [timed_out()]})
    errors = [g for g in gens(spans) if g["status"] == "error"]
    assert len(errors) == 1 and errors[0]["error_message"].startswith("APITimeoutError")
    assert len(gens(spans)) == 8  # 7 + one retry
    assert result.failed_turns == 0


def test_server_error_twice_fails_the_turn(debate) -> None:  # noqa: ANN001
    boom = http_error(openai.InternalServerError, 500)
    result = debate(scripts={"pro": [boom, boom]})
    assert result.failed_turns == 1


def test_client_errors_are_not_retried(debate, spans, waits) -> None:  # noqa: ANN001
    with pytest.raises(openai.AuthenticationError):  # e.g. a wrong GROQ_API_KEY
        debate(scripts={"judge": [http_error(openai.AuthenticationError, 401)]})
    assert waits == []
    assert len([g for g in gens(spans) if g["metadata"]["role"] == "judge"]) == 1


def test_injected_timeout_is_retried_and_visible(debate, spans) -> None:  # noqa: ANN001
    rng = ScriptedRandom([0.99, 0.1])  # tool ok, first LLM call → timeout
    result = debate(Faults(timeout_rate=0.5, rng=rng))

    errors = [g for g in gens(spans) if g["status"] == "error"]
    assert len(errors) == 1
    assert errors[0]["error_message"] == "LLMTimeout: injected: LLM request timed out"
    assert len(gens(spans)) == 8
    assert result.failed_turns == 0


def test_turn_fails_but_debate_continues_when_retry_also_times_out(debate, spans) -> None:  # noqa: ANN001
    rng = ScriptedRandom([0.99, 0.1, 0.1])  # tool ok, both attempts of turn 1 time out
    result = debate(Faults(timeout_rate=0.5, rng=rng))

    assert [t["status"] for t in spans.named("debate.turn")].count("error") == 1
    assert result.failed_turns == 1
    assert result.transcript[0].text is None
    assert spans.one("debate")["status"] == "ok"


def test_everything_timing_out_fails_the_debate(debate, spans) -> None:  # noqa: ANN001
    with pytest.raises(LLMTimeout):
        debate(Faults(timeout_rate=1.0))
    assert spans.one("debate")["status"] == "error"
    assert all(t["status"] == "error" for t in spans.named("lookup_fact"))


def test_tool_timeout_does_not_fail_the_turn(debate, spans) -> None:  # noqa: ANN001
    debate(Faults(timeout_rate=0.5, rng=ScriptedRandom([0.1])))  # first lookup_fact times out
    assert [t["status"] for t in spans.named("lookup_fact")].count("error") == 1
    failed_turn = next(t for t in spans.named("debate.turn")
                       if (t["metadata"] or {}).get("fact_lookup") == "failed")  # fmt: skip
    assert failed_turn["status"] == "ok"


def test_slow_injection_adds_latency(debate, spans) -> None:  # noqa: ANN001
    debate(Faults(slow_rate=1.0, slow_seconds=0.02), rounds=1)
    for g in gens(spans):
        took = datetime.fromisoformat(g["end_time"]) - datetime.fromisoformat(g["start_time"])
        assert took.total_seconds() >= 0.02


# --- configuration ---------------------------------------------------------------------------


def test_role_defaults_are_ollama_debaters_and_groq_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("PRO", "CON", "JUDGE"):
        for var in ("BASE_URL", "MODEL", "API_KEY", "TIMEOUT_S"):
            monkeypatch.delenv(f"{role}_{var}", raising=False)
    pro = llm.role_config("pro")
    assert (pro.base_url, pro.model, pro.api_key) == (llm.OLLAMA_URL, "llama3.2:3b", "ollama")
    assert pro.timeout_s >= 300  # CPU-only Ollama is slow
    with pytest.raises(llm.ConfigError, match="JUDGE_API_KEY"):
        llm.role_config("judge")

    monkeypatch.setenv("JUDGE_API_KEY", "gsk_secret")
    judge = llm.role_config("judge")
    assert (judge.base_url, judge.model) == (llm.GROQ_URL, "openai/gpt-oss-120b")
    assert "gsk_secret" not in repr(judge)


def test_real_client_has_no_hidden_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = llm.RoleConfig("pro", "http://llm.test/v1", "m", "k", 123.0)
    real = llm.make_client(cfg, Faults()).chat.completions.inner  # inside the fault layer
    assert real._client.max_retries == 0
    assert real._client.timeout == 123.0


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


# --- traffic and attributes ------------------------------------------------------------------


def test_traffic_runs_many_debates_as_separate_traces(make_stub_clients, spans) -> None:  # noqa: ANN001
    outcomes = run_traffic(4, concurrency=2, clients_factory=make_stub_clients)
    assert outcomes == {"ok": 4}
    roots = spans.named("debate")
    assert len(roots) == 4
    assert len({r["trace_id"] for r in roots}) == 4
    assert all(r["parent_span_id"] is None for r in roots)  # threads don't share context


def test_traffic_respects_the_concurrency_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    import traffic

    running = peak = 0
    lock = threading.Lock()

    def stub_debate(topic: str, **kwargs: Any) -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.02)
        with lock:
            running -= 1

    monkeypatch.setattr(traffic, "run_debate", stub_debate)
    assert traffic.run_traffic(8, clients_factory=dict) == {"ok": 8}
    assert peak == traffic.DEFAULT_CONCURRENCY == 2


def test_every_span_carries_debate_attributes(debate, spans) -> None:  # noqa: ANN001
    debate(rounds=2)
    for e in spans.events:
        m = e["metadata"]
        assert m["topic"] == "Cats are better than dogs" and m["rounds"] == 2, e["name"]
        assert (m["pro_model"], m["con_model"]) == ("test-pro", "test-con")
    for e in spans.events:
        if e["name"] in ("debate.turn", "lookup_fact"):
            assert e["metadata"]["role"] in ("pro", "con") and e["metadata"]["round"] in (1, 2)
        if e["name"] in ("judge", "judge.attempt"):
            assert e["metadata"]["role"] == "judge"
    assert {g["metadata"]["role"] for g in gens(spans)} == {"pro", "con", "judge"}
    root = next(e for e in spans.events if e["parent_span_id"] is None)
    assert root["metadata"]["winner"] == "pro"
    assert isinstance(root["metadata"]["score_pro"], int)
