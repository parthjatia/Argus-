"""Failure injection, configured with env vars (all default to 0 = off):

ARENA_TIMEOUT_RATE   chance an LLM call or lookup_fact() raises a timeout
ARENA_BAD_JSON_RATE  chance a JSON-mode LLM answer (the judge) comes back broken
ARENA_SLOW_RATE      chance an LLM call or lookup_fact() takes ARENA_SLOW_SECONDS longer
ARENA_SEED           optional: makes the random faults reproducible
"""

import os
import random
from dataclasses import dataclass, field


class LLMTimeout(TimeoutError):
    """An LLM request timed out (real or injected)."""


class ToolTimeout(TimeoutError):
    """A tool call timed out (real or injected)."""


@dataclass
class Faults:
    timeout_rate: float = 0.0
    bad_json_rate: float = 0.0
    slow_rate: float = 0.0
    slow_seconds: float = 2.0
    rng: random.Random = field(default_factory=random.Random)

    @classmethod
    def from_env(cls) -> "Faults":
        seed = os.environ.get("ARENA_SEED")
        return cls(
            timeout_rate=float(os.environ.get("ARENA_TIMEOUT_RATE", "0")),
            bad_json_rate=float(os.environ.get("ARENA_BAD_JSON_RATE", "0")),
            slow_rate=float(os.environ.get("ARENA_SLOW_RATE", "0")),
            slow_seconds=float(os.environ.get("ARENA_SLOW_SECONDS", "2")),
            rng=random.Random(int(seed)) if seed else random.Random(),
        )

    def _hit(self, rate: float) -> bool:
        # Only draw a random number when the fault is enabled, so tests can script `rng`.
        return rate > 0 and self.rng.random() < rate

    def slow(self) -> bool:
        return self._hit(self.slow_rate)

    def timeout(self) -> bool:
        return self._hit(self.timeout_rate)

    def bad_json(self) -> bool:
        return self._hit(self.bad_json_rate)
