"""Argus tracing SDK.

import argus
argus.init(api_key="argus_...", host="http://localhost:8000")

@argus.observe()
def answer(question: str) -> str:
    with argus.span("lookup", kind="tool") as s:
        s.set_output(...)
    ...

client = argus.wrap_openai(OpenAI(...))
"""

from argus.client import flush, get_stats, init, shutdown
from argus.context import current_span
from argus.decorators import observe
from argus.integrations.openai import wrap_openai
from argus.spans import Span, span

__version__ = "0.1.0"

__all__ = [
    "Span",
    "current_span",
    "flush",
    "get_stats",
    "init",
    "observe",
    "shutdown",
    "span",
    "wrap_openai",
]
