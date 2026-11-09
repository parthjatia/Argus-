"""A fake fact-lookup tool, so traces contain realistic tool spans (and tool failures)."""

import hashlib
import time

import argus
from arena.faults import Faults, ToolTimeout

FACTS = [
    "a 2019 survey of 12,000 people found a 61% preference",
    "costs fell by roughly 40% over the last decade",
    "three independent meta-analyses reached the same conclusion",
    "adoption doubled in five years in every region studied",
    "the effect disappears once income is controlled for",
    "early adopters reported higher satisfaction after one year",
]


class FactTool:
    def __init__(self, faults: Faults) -> None:
        self.faults = faults

    @argus.observe(name="lookup_fact", kind="tool")
    def __call__(self, query: str) -> str:
        if self.faults.slow():
            time.sleep(self.faults.slow_seconds)
        if self.faults.timeout():
            raise ToolTimeout("injected: fact service timed out")
        time.sleep(0.005)  # a real lookup takes a little time
        index = int(hashlib.sha256(query.encode()).hexdigest(), 16) % len(FACTS)
        return FACTS[index]
