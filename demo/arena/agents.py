"""Debater and judge agents: prompts, LLM calls, and judge output validation."""

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

import argus
from arena.faults import ToolTimeout
from arena.llm import chat
from arena.tools import FactTool

Side = Literal["pro", "con"]


@dataclass
class Turn:
    round: int
    side: Side
    text: str | None  # None = the turn failed (e.g. LLM timeout)


def _transcript_text(transcript: list[Turn]) -> str:
    lines = [f"[Round {t.round}] {t.side.upper()}: {t.text or '(no answer)'}" for t in transcript]
    return "\n".join(lines) or "(no turns yet)"


# --- debaters ------------------------------------------------------------------------------

DEBATER_SYSTEM = (
    "You are a skilled debater arguing {stance} the motion. Be persuasive and concrete, "
    "rebut your opponent's last point, and answer in at most 4 sentences."
)


class Debater:
    def __init__(self, side: Side, model: str, client: Any, tool: FactTool) -> None:
        self.side, self.model, self.client, self.tool = side, model, client, tool

    @argus.observe(name="debate.turn", capture_input=False)
    def take_turn(self, topic: str, round_no: int, transcript: list[Turn]) -> str:
        span = argus.current_span()
        if span:
            span.set_input({"side": self.side, "round": round_no, "model": self.model})

        fact = None
        try:
            fact = self.tool(f"{topic} ({self.side})")
        except ToolTimeout:
            if span:
                span.set_metadata(fact_lookup="failed")  # argue without the fact

        stance = "FOR" if self.side == "pro" else "AGAINST"
        user = (
            f"Topic: {topic}\nRound {round_no}.\nTranscript so far:\n{_transcript_text(transcript)}"
        )
        if fact:
            user += f"\nFact: {fact}"
        messages = [
            {"role": "system", "content": DEBATER_SYSTEM.format(stance=stance)},
            {"role": "user", "content": user},
        ]
        return chat(self.client, self.model, messages)


# --- judge ---------------------------------------------------------------------------------


class Scores(BaseModel):
    pro: int = Field(ge=0, le=10)
    con: int = Field(ge=0, le=10)


class JudgeVerdict(BaseModel):
    winner: Literal["pro", "con", "tie"]
    scores: Scores
    reasoning: str = Field(min_length=1)


class InvalidVerdict(ValueError):
    pass


class JudgeFailed(RuntimeError):
    """The judge returned invalid JSON on both attempts."""


JUDGE_SYSTEM = (
    "You are an impartial debate judge. Reply with ONLY a JSON object of the form "
    '{"winner": "pro" | "con" | "tie", "scores": {"pro": 0-10, "con": 0-10}, '
    '"reasoning": "<one or two sentences>"}.'
)


def parse_verdict(content: str) -> JudgeVerdict:
    text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        return JudgeVerdict.model_validate_json(text)
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'json'}: {e['msg']}"
                           for e in exc.errors())  # fmt: skip
        raise InvalidVerdict(errors[:500]) from None


class Judge:
    def __init__(self, model: str, client: Any) -> None:
        self.model, self.client = model, client

    @argus.observe(name="judge", capture_input=False)
    def decide(self, topic: str, transcript: list[Turn]) -> JudgeVerdict:
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"Topic: {topic}\n\n{_transcript_text(transcript)}"},
        ]
        last_error: InvalidVerdict | None = None
        for attempt in (1, 2):  # one retry on invalid JSON; both attempts are spans
            try:
                with argus.span("judge.attempt", metadata={"attempt": attempt}) as s:
                    content = chat(self.client, self.model, messages,
                                   response_format={"type": "json_object"})  # fmt: skip
                    verdict = parse_verdict(content)  # raises InvalidVerdict → span = error
                    s.set_output(verdict.model_dump())
                    return verdict
            except InvalidVerdict as exc:
                last_error = exc
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That was not valid: {exc}. "
                                                "Reply with ONLY the JSON object."},
                ]  # fmt: skip
        raise JudgeFailed(f"invalid verdict JSON twice: {last_error}")


def verdict_json(v: JudgeVerdict) -> str:
    return json.dumps(v.model_dump())
