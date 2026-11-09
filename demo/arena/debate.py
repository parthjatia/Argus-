"""Run one debate as one trace.

    python -m arena.debate --topic "Remote work is better than office work" [--rounds 3]

Every span carries metadata `topic`, `rounds`, `pro_model`, `con_model`, `judge_model`;
spans inside a round add `round`, a turn's spans add `role` (pro | con), the judge's `role`
= judge. The root adds `winner`, `score_pro`, `score_con` and `failed_turns` when it ends.

Trace shape:
    debate
    ├── debate.round (×rounds)
    │   └── debate.turn (pro, then con)
    │       ├── lookup_fact            (tool)
    │       └── openai.chat.completions (generation; a second one if the first timed out)
    └── judge
        └── judge.attempt (1, or 2 if the first answer was invalid JSON)
            └── openai.chat.completions
"""

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import argus
from arena.agents import Debater, Judge, JudgeVerdict, Turn
from arena.faults import Faults, LLMTimeout
from arena.llm import default_model, make_client, provider
from arena.tools import FactTool

TOPICS_FILE = Path(__file__).resolve().parent.parent / "topics.yaml"


@dataclass
class DebateResult:
    topic: str
    trace_id: str
    transcript: list[Turn]
    verdict: JudgeVerdict
    failed_turns: int


def run_debate(
    topic: str,
    pro_model: str | None = None,
    con_model: str | None = None,
    judge_model: str | None = None,
    rounds: int = 3,
    *,
    faults: Faults | None = None,
    client: Any = None,
) -> DebateResult:
    """Raises if the judge fails (timeout twice, or invalid JSON twice): the trace shows why."""
    faults = faults or Faults.from_env()
    client = client or make_client(faults)
    tool = FactTool(faults)
    pro_model = pro_model or default_model("pro")
    con_model = con_model or default_model("con")
    judge_model = judge_model or default_model("judge")

    debaters = [Debater("pro", pro_model, client, tool), Debater("con", con_model, client, tool)]
    judge = Judge(judge_model, client)
    models = {"pro": pro_model, "con": con_model, "judge": judge_model}

    # Attributes go into the metadata of every span created inside (also LLM calls), so
    # the dashboard can show topic, round and speaker while the root span is still open.
    with (
        argus.attributes(topic=topic, rounds=rounds, pro_model=pro_model,
                         con_model=con_model, judge_model=judge_model),
        argus.span("debate", input={"topic": topic, "rounds": rounds, "models": models},
                   metadata={"provider": provider()}) as root,
    ):  # fmt: skip
        transcript: list[Turn] = []
        failed = 0
        for n in range(1, rounds + 1):
            with argus.attributes(round=n), argus.span("debate.round", metadata={"round": n}):
                for debater in debaters:
                    try:
                        with argus.attributes(role=debater.side):
                            text: str | None = debater.take_turn(topic, n, transcript)
                    except LLMTimeout:
                        text, failed = None, failed + 1  # the turn span shows the error
                    transcript.append(Turn(n, debater.side, text))

        with argus.attributes(role="judge"):
            verdict = judge.decide(topic, transcript)
        root.set_output(verdict.model_dump())
        root.set_metadata(failed_turns=failed, winner=verdict.winner,
                          score_pro=verdict.scores.pro, score_con=verdict.scores.con)  # fmt: skip
        return DebateResult(topic, root.trace_id, transcript, verdict, failed)


def load_topics() -> list[str]:
    import yaml

    return list(yaml.safe_load(TOPICS_FILE.read_text(encoding="utf-8"))["topics"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one AI debate (one Argus trace).")
    parser.add_argument("--topic", default="", help="debate motion (default: random topic)")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    topic = args.topic or random.choice(load_topics())

    argus.init()  # ARGUS_API_KEY / ARGUS_HOST from the environment
    print(f"Topic: {topic}  (provider: {provider()})\n")
    try:
        result = run_debate(topic, rounds=args.rounds)
    except Exception as exc:
        argus.flush(timeout=10)
        print(f"Debate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    for t in result.transcript:
        print(f"[Round {t.round}] {t.side.upper()}: {t.text or '(failed)'}\n")
    v = result.verdict
    print(f"Winner: {v.winner}  (pro {v.scores.pro} / con {v.scores.con})  {v.reasoning}")
    ok = argus.flush(timeout=10)
    print(f"\ntrace_id: {result.trace_id}  exported: {ok}  stats: {argus.get_stats()}")


if __name__ == "__main__":
    main()
