"""End-to-end check: send one nested trace to a running Argus server.

    ARGUS_API_KEY=argus_... python sdk/examples/e2e_trace.py

Builds a small debate-shaped trace (sync + async + concurrent tasks + a failing tool
call + an OpenAI-style generation), flushes, and prints the trace_id to look up.
"""

import asyncio
import sys
from types import SimpleNamespace
from typing import Any

import argus


class FakeChat:
    """OpenAI-compatible stand-in so the check runs offline."""

    def create(self, *, model: str, messages: list[dict[str, str]], **_: Any) -> Any:
        text = f"Argument about {messages[-1]['content']}"
        return SimpleNamespace(
            model=model,
            choices=[
                SimpleNamespace(
                    message={"role": "assistant", "content": text}, finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=45),
        )


llm = argus.wrap_openai(SimpleNamespace(chat=SimpleNamespace(completions=FakeChat())))


@argus.observe(name="lookup_fact", kind="tool")
def lookup_fact(query: str) -> str:
    if "fail" in query:
        raise TimeoutError("fact service timed out")
    return f"fact about {query}"


@argus.observe(name="debate.turn")
async def turn(side: str, topic: str) -> str:
    await asyncio.sleep(0.01)
    try:
        lookup_fact(f"{topic} fail" if side == "con" else topic)
    except TimeoutError:
        pass  # the turn continues without the fact
    resp = llm.chat.completions.create(
        model="fake-llm", messages=[{"role": "user", "content": topic}]
    )
    return resp.choices[0].message["content"]


@argus.observe(name="debate.round")
async def debate_round(n: int, topic: str) -> list[str]:
    return await asyncio.gather(turn("pro", topic), turn("con", topic))  # concurrent


@argus.observe(name="debate")
async def debate(topic: str) -> str:
    for n in (1, 2):
        await debate_round(n, topic)
    return argus.current_span().trace_id  # type: ignore[union-attr]


def main() -> None:
    argus.init()  # ARGUS_API_KEY / ARGUS_HOST from the environment
    trace_id = asyncio.run(debate("cats vs dogs"))
    ok = argus.flush(timeout=10)
    print(f"trace_id={trace_id} flushed={ok} stats={argus.get_stats()}")
    sys.exit(0 if ok and argus.get_stats()["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
