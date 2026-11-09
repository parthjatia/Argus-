"""Run one debate as one trace.

    python -m arena.debate --topic "Remote work is better than office work" [--rounds 3]

Pro and con run on Ollama (llama3.2:3b), the judge on Groq (openai/gpt-oss-120b); see
arena/llm.py for the per-role env vars. Every span carries metadata `topic`, `rounds`,
`pro_model`, `con_model`, `judge_model`;
spans inside a round add `round`, a turn's spans add `role` (pro | con), the judge's `role`
= judge. The root adds `winner`, `score_pro`, `score_con` and `failed_turns` when it ends.

Trace shape:
    debate
    ├── debate.round (×rounds)
    │   └── debate.turn (pro, then con)
    │       ├── lookup_fact            (tool)
    │       └── openai.chat.completions (generation; one per attempt: 429 / timeout retries)
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
from arena.faults import Faults, LLMError
from arena.llm import Role, RoleConfig, make_clients
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
    clients: dict[Role, tuple[RoleConfig, Any]] | None = None,
) -> DebateResult:
    """Raises if the judge fails (LLM error after retries, or invalid JSON twice).

    `clients` maps each role to (config, traced client); default: built from the env.
    """
    faults = faults or Faults.from_env()
    clients = clients or make_clients(faults)
    tool = FactTool(faults)
    pro_model = pro_model or clients["pro"][0].model
    con_model = con_model or clients["con"][0].model
    judge_model = judge_model or clients["judge"][0].model

    debaters = [Debater("pro", pro_model, clients["pro"][1], tool),
                Debater("con", con_model, clients["con"][1], tool)]  # fmt: skip
    judge = Judge(judge_model, clients["judge"][1])
    models = {"pro": pro_model, "con": con_model, "judge": judge_model}
    endpoints = {role: cfg.base_url for role, (cfg, _) in clients.items()}  # never the keys

    # Attributes go into the metadata of every span created inside (also LLM calls), so
    # the dashboard can show topic, round and speaker while the root span is still open.
    with (
        argus.attributes(topic=topic, rounds=rounds, pro_model=pro_model,
                         con_model=con_model, judge_model=judge_model),
        argus.span("debate", input={"topic": topic, "rounds": rounds, "models": models},
                   metadata={"endpoints": endpoints}) as root,
    ):  # fmt: skip
        transcript: list[Turn] = []
        failed = 0
        for n in range(1, rounds + 1):
            with argus.attributes(round=n), argus.span("debate.round", metadata={"round": n}):
                for debater in debaters:
                    try:
                        with argus.attributes(role=debater.side):
                            text: str | None = debater.take_turn(topic, n, transcript)
                    except LLMError:  # timeout, 429 or provider down, after retries
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
    clients = make_clients()
    for role, (cfg, _) in clients.items():
        print(f"{role:>5}: {cfg.model} @ {cfg.base_url}")  # never the API key
    print(f"Topic: {topic}\n(CPU-only Ollama: a debate can take several minutes)\n", flush=True)
    try:
        result = run_debate(topic, rounds=args.rounds, clients=clients)
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
