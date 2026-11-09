"""Real LLM clients, one per debate role, all OpenAI-compatible and traced with wrap_openai.

Each role is configured with env vars (see .env.example):

    PRO_BASE_URL / PRO_MODEL / PRO_API_KEY / PRO_TIMEOUT_S        (default: Ollama, llama3.2:3b)
    CON_BASE_URL / CON_MODEL / CON_API_KEY / CON_TIMEOUT_S        (default: Ollama, llama3.2:3b)
    JUDGE_BASE_URL / JUDGE_MODEL / JUDGE_API_KEY / JUDGE_TIMEOUT_S (default: Groq, gpt-oss-120b)

Layers of one client, outside in:

    wrap_openai  → one generation span per create() call (so every retry is its own span)
    FaultyCompletions → ARENA_* failure injection (inside the span, so it looks real)
    openai.OpenAI(max_retries=0) → the real HTTP call; *we* retry, so each attempt is visible

`chat()` turns provider errors into the arena's own error types and retries them:
429 rate limits with Retry-After / exponential backoff, timeouts and 5xx once.
"""

import os
import random
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, NoReturn

import openai

import argus
from arena.faults import Faults, LLMError, LLMRateLimited, LLMTimeout, LLMUnavailable

Role = Literal["pro", "con", "judge"]
ROLES: tuple[Role, ...] = ("pro", "con", "judge")

OLLAMA_URL = "http://host.docker.internal:11434/v1/"
GROQ_URL = "https://api.groq.com/openai/v1"

# Defaults per role: (base_url, model, api_key, timeout seconds). CPU-only Ollama can take
# minutes for one answer when two debates share the CPU, hence the long timeout.
DEFAULTS: dict[Role, tuple[str, str, str, float]] = {
    "pro": (OLLAMA_URL, "llama3.2:3b", "ollama", 300.0),
    "con": (OLLAMA_URL, "llama3.2:3b", "ollama", 300.0),
    "judge": (GROQ_URL, "openai/gpt-oss-120b", "", 60.0),
}

# 429 handling: up to RATE_LIMIT_RETRIES extra attempts; waits capped at MAX_WAIT_S.
RATE_LIMIT_RETRIES = int(os.environ.get("LLM_RATE_LIMIT_RETRIES", "4"))
MAX_WAIT_S = 60.0
_sleep = time.sleep  # replaced in tests


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoleConfig:
    role: Role
    base_url: str
    model: str
    api_key: str
    timeout_s: float

    def __repr__(self) -> str:  # never show the key, even in a traceback or debug print
        return f"RoleConfig({self.role}, {self.base_url}, {self.model}, timeout={self.timeout_s})"


def role_config(role: Role) -> RoleConfig:
    url, model, key, timeout = DEFAULTS[role]
    env = role.upper()
    cfg = RoleConfig(
        role=role,
        base_url=os.environ.get(f"{env}_BASE_URL") or url,
        model=os.environ.get(f"{env}_MODEL") or model,
        api_key=os.environ.get(f"{env}_API_KEY") or key,
        timeout_s=float(os.environ.get(f"{env}_TIMEOUT_S") or timeout),
    )
    if not cfg.api_key:
        raise ConfigError(f"{env}_API_KEY is not set (for Groq: set GROQ_API_KEY in .env)")
    return cfg


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


def wrap(completions: Any, faults: Faults) -> Any:
    """Any object with `.create(**kwargs)` → fault layer → traced OpenAI-shaped client."""
    faulty = FaultyCompletions(completions, faults)
    return argus.wrap_openai(SimpleNamespace(chat=SimpleNamespace(completions=faulty)))


def make_client(cfg: RoleConfig, faults: Faults) -> Any:
    # max_retries=0: the openai package would otherwise retry 429/5xx silently inside one
    # span; our own loop in chat() makes every attempt a separate, visible span.
    real = openai.OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout_s,
                         max_retries=0)  # fmt: skip
    return wrap(real.chat.completions, faults)


def make_clients(faults: Faults | None = None) -> dict[Role, tuple[RoleConfig, Any]]:
    """Config + traced client for every role, built from the environment."""
    faults = faults or Faults.from_env()
    configs = {role: role_config(role) for role in ROLES}
    return {role: (cfg, make_client(cfg, faults)) for role, cfg in configs.items()}


# --- calling with retries --------------------------------------------------------------------


def retry_after_s(exc: openai.APIStatusError) -> float | None:
    """Seconds from the Retry-After header (Groq sends seconds), or None if absent/unusable."""
    value = exc.response.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:  # an HTTP date: rare, fall back to backoff
        return None


def backoff_s(attempt: int) -> float:
    """Exponential backoff with full jitter: uniform(0, min(MAX_WAIT_S, 2 s * 2^attempt))."""
    return random.uniform(0, min(MAX_WAIT_S, 2.0 * 2**attempt))


def _classify(exc: Exception) -> LLMError | None:
    """Map a provider error to the arena's error types; None = not retryable (re-raise)."""
    if isinstance(exc, LLMError):
        return exc  # injected fault
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimited(str(exc)[:300])
    if isinstance(exc, openai.APITimeoutError):  # subclass of APIConnectionError: check first
        return LLMTimeout(str(exc)[:300])
    if isinstance(exc, openai.APIConnectionError | openai.InternalServerError):
        return LLMUnavailable(str(exc)[:300])
    return None


def _give_up(err: LLMError, exc: Exception) -> NoReturn:
    if err is exc:
        raise err
    raise err from exc


def chat(client: Any, model: str, messages: list[dict[str, str]], *, retries: int = 1,
         **kwargs: Any) -> str:  # fmt: skip
    """One LLM call with retries. Every attempt is its own generation span.

    429: up to RATE_LIMIT_RETRIES retries, waiting Retry-After (or backoff), capped at 60 s.
    Timeouts, connection errors and 5xx: `retries` retries (default 1) after a short backoff.
    When retries run out, raises LLMRateLimited / LLMTimeout / LLMUnavailable.
    """
    attempt = transient = limited = 0
    while True:
        attempt += 1
        try:
            with argus.attributes(llm_attempt=attempt):  # → metadata of the generation span
                resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            err = _classify(exc)
            if err is None:
                raise  # 4xx other than 429 (bad request, auth): retrying can't help
            if isinstance(err, LLMRateLimited):
                if limited >= RATE_LIMIT_RETRIES:
                    _give_up(err, exc)
                header = retry_after_s(exc) if isinstance(exc, openai.APIStatusError) else None
                wait = min(MAX_WAIT_S, header if header is not None else backoff_s(limited))
                limited += 1
            else:
                if transient >= retries:
                    _give_up(err, exc)
                wait = backoff_s(transient) / 4  # short: the next attempt has its own timeout
                transient += 1
            _sleep(wait)
