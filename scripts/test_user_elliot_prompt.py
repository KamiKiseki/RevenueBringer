"""One-off eval: run 10 archetype sims against a pasted Elliot system prompt."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import elliot_optimization_run as er  # noqa: E402

_ORIG_CHAT = er._chat
_ORIG_JSON = er._chat_json


def _chat_retry(client, messages, temperature=0.7, retries: int = 8):
    delay = 1.5
    for attempt in range(retries):
        try:
            return _ORIG_CHAT(client, messages, temperature)
        except Exception as exc:
            err = str(exc).lower()
            if attempt < retries - 1 and ("429" in err or "rate" in err):
                time.sleep(delay)
                delay = min(delay * 1.8, 45.0)
                continue
            raise


def _chat_json_retry(client, messages, temperature=0.2, retries: int = 8):
    delay = 1.5
    for attempt in range(retries):
        try:
            return _ORIG_JSON(client, messages, temperature)
        except Exception as exc:
            err = str(exc).lower()
            if attempt < retries - 1 and ("429" in err or "rate" in err):
                time.sleep(delay)
                delay = min(delay * 1.8, 45.0)
                continue
            raise


er._chat = _chat_retry
er._chat_json = _chat_json_retry

USER_OPENING = (
    "Hey, this is Elliot from AutoYield Systems — you got a quick second?"
)

USER_SYSTEM_PROMPT = (ROOT / "elliot_final_prompt.txt").read_text(encoding="utf-8").strip()


def main() -> None:
    client = er._client()
    er.OPENING_LINE = USER_OPENING
    rows = []
    for title, desc in er.ARCHETYPES:
        print(f"  Simulating: {title}...", flush=True)
        tr = er.simulate_one(client, title, desc, USER_SYSTEM_PROMPT)
        sc = er.grade_transcript(client, tr)
        time.sleep(2.0)
        rows.append((title, tr, sc))

    def avg(k: str) -> float:
        return sum(r[2][k] for r in rows) / len(rows)

    print()
    print("=== Scores (1-10) ===")
    print(f"{'Archetype':<44} Nat Obj Cls Ovl")
    print("-" * 62)
    for title, _, s in rows:
        print(
            f"{title[:43]:<44} {s['natural']:3} {s['objection']:3} "
            f"{s['close']:3} {s['overall']:3}"
        )
    print("-" * 62)
    print(
        f"{'AVERAGE':<44} {avg('natural'):5.2f} {avg('objection'):5.2f} "
        f"{avg('close'):5.2f} {avg('overall'):5.2f}"
    )


if __name__ == "__main__":
    main()
