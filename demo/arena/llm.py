"""LLM provider switch: LLM_PROVIDER=fake | ollama | openai.

Every provider exposes the OpenAI client interface (`client.chat.completions.create`),
so `argus.wrap_openai` traces all of them identically. Faults are injected in a layer
*inside* the wrapper, so injected timeouts and slowness show up in the generation span.
"""

import hashlib
import os
import random
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import argus
from arena.faults import Faults, LLMTimeout

DEFAULT_MODELS = {
    "fake": {"pro": "fake-small", "con": "fake-large", "judge": "fake-large"},
    "ollama": {"pro": "llama3.1", "con": "llama3.1", "judge": "llama3.1"},
    "openai": {"pro": "gpt-4o-mini", "con": "gpt-4o-mini", "judge": "gpt-4o-mini"},
}


def provider() -> str:
    return os.environ.get("LLM_PROVIDER", "fake")


def default_model(role: str) -> str:
    return os.environ.get(f"{role.upper()}_MODEL") or DEFAULT_MODELS[provider()][role]


# --- fake provider: OpenAI-shaped responses, deterministic, offline --------------------------


@dataclass
class FakeMessage:
    content: str
    role: str = "assistant"

    def model_dump(self, mode: str = "python") -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class FakeChoice:
    message: FakeMessage
    finish_reason: str = "stop"


@dataclass
class FakeUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class FakeResponse:
    model: str
    choices: list[FakeChoice]
    usage: FakeUsage
    id: str = field(default="fake")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # ~4 characters per token for English


OPENERS = [
    "Let me be direct.", "Consider the evidence.", "My opponent misses the point.",
    "History is on our side.", "Look at the numbers.", "Think about everyday life.",
]  # fmt: skip
POINTS = [
    "it leads to better outcomes for most people",
    "the long-term costs are routinely underestimated",
    "the strongest studies point the same way",
    "it scales in a way the alternative never has",
    "the people most affected consistently prefer it",
    "the risks are real but manageable with simple rules",
]
CLOSERS = [
    "That is why this side should win.", "The burden of proof has not been met.",
    "On balance, the case is clear.", "No serious objection survives this.",
]  # fmt: skip


def _fake_argument(prompt: str, rng: random.Random) -> str:
    topic = m.group(1) if (m := re.search(r"Topic: (.+)", prompt)) else "this motion"
    stance = "against" if "AGAINST" in prompt else "for"
    fact = m.group(1) if (m := re.search(r"Fact: (.+)", prompt)) else None
    points = rng.sample(POINTS, 2)
    text = (f"{rng.choice(OPENERS)} I argue {stance} '{topic}' because {points[0]}, "
            f"and because {points[1]}.")  # fmt: skip
    if fact:
        text += f" Remember: {fact}."
    return f"{text} {rng.choice(CLOSERS)}"


def _fake_verdict(rng: random.Random) -> str:
    pro, con = rng.randint(4, 9), rng.randint(4, 9)
    winner = "pro" if pro > con else "con" if con > pro else "tie"
    reasoning = rng.choice(
        ["The winning side used more concrete evidence.",
         "Both were persuasive; one rebutted more directly.",
         "The stronger side engaged with the opponent's best points."]
    )  # fmt: skip
    return f'{{"winner": "{winner}", "scores": {{"pro": {pro}, "con": {con}}}, "reasoning": "{reasoning}"}}'  # noqa: E501


class FakeCompletions:
    def __init__(self, latency_s: float = 0.04) -> None:
        self.latency_s = latency_s

    def create(self, *, model: str, messages: list[dict[str, str]], **kwargs: Any) -> FakeResponse:
        prompt = "\n".join(m["content"] for m in messages)
        # Same input → same output: seed the RNG from a hash of the request.
        seed = int.from_bytes(hashlib.sha256(f"{model}\n{prompt}".encode()).digest()[:8], "big")
        rng = random.Random(seed)
        if self.latency_s:
            size = 2.0 if "large" in model else 1.0  # bigger models are slower
            time.sleep(self.latency_s * size * rng.uniform(0.5, 1.5))
        wants_json = (kwargs.get("response_format") or {}).get("type") == "json_object"
        content = _fake_verdict(rng) if wants_json else _fake_argument(prompt, rng)
        return FakeResponse(
            model=model,
            choices=[FakeChoice(FakeMessage(content))],
            usage=FakeUsage(estimate_tokens(prompt) + 4 * len(messages), estimate_tokens(content)),
        )


# --- fault injection layer -------------------------------------------------------------------


class FaultyCompletions:
    def __init__(self, inner: Any, faults: Faults) -> None:
        self.inner = inner
        self.faults = faults

    def create(self, **kwargs: Any) -> Any:
        if self.faults.slow():
            time.sleep(self.faults.slow_seconds)
        if self.faults.timeout():
            raise LLMTimeout("injected: LLM request timed out")
        resp = self.inner.create(**kwargs)
        wants_json = (kwargs.get("response_format") or {}).get("type") == "json_object"
        if wants_json and self.faults.bad_json():
            msg = resp.choices[0].message
            # Typical real-world failures: chatty preamble, or output cut off mid-object.
            msg.content = (f"Sure! Here is my verdict: {msg.content}"
                           if self.faults.rng.random() < 0.5 else msg.content[: len(msg.content) // 2])  # fmt: skip  # noqa: E501
        return resp


def make_client(faults: Faults | None = None, *, fake_latency_s: float | None = None) -> Any:
    """An OpenAI-compatible client for LLM_PROVIDER, with faults injected and tracing on."""
    name = provider()
    if name == "fake":
        latency = fake_latency_s
        if latency is None:
            latency = float(os.environ.get("FAKE_LATENCY_MS", "40")) / 1000
        base: Any = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(latency)))
    elif name in ("ollama", "openai"):
        from openai import OpenAI  # imported lazily: not needed for the fake provider

        if name == "ollama":
            url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            base = OpenAI(base_url=url, api_key="ollama", timeout=120)
        else:  # OPENAI_API_KEY from the environment; OPENAI_BASE_URL for other vendors
            base = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, timeout=60)
    else:
        raise ValueError(f"LLM_PROVIDER must be fake, ollama or openai, not {name!r}")

    faulty = FaultyCompletions(base.chat.completions, faults or Faults.from_env())
    return argus.wrap_openai(SimpleNamespace(chat=SimpleNamespace(completions=faulty)))


def chat(client: Any, model: str, messages: list[dict[str, str]], *, retries: int = 1,
         **kwargs: Any) -> str:  # fmt: skip
    """One LLM call, retried on timeout. Every attempt is its own generation span."""
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
            return resp.choices[0].message.content or ""
        except LLMTimeout:
            if attempt == retries:
                raise
    raise AssertionError("unreachable")
