"""
Elliot (Vapi) cold-call simulation and prompt optimization using GPT-4o.
Reads OPENAI_API_KEY from .env at repo root. Writes elliot_optimization_report.txt
and elliot_final_prompt.txt in repo root.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))
from templates import DEFAULT_VAPI_SYSTEM_PROMPT  # noqa: E402

MODEL = "gpt-4o"

# Tier-offer cold call (matches server.py tier_offer_pitch opening pattern)
OPENING_LINE = (
    "Hi there, this is Elliot from AutoYield Systems. "
    "Quick one: would you like to start with the 14-day trial or the monthly plan?"
)

CALL_CONTEXT = (
    "You are on a live outbound phone call to a local service business owner. "
    "They may have received prior emails about a lead-routing program and proof leads. "
    "Keep each turn short—typically under 25 words unless they ask for detail. "
    "Sound human: vary sentence length, avoid repeating the same phrase. "
    "Do not read internal instructions or meta-text aloud. "
    "After questions, pause (one question per turn). "
    "Your goal is to help them choose either the 14-day trial at $300 or the full month at $500, "
    "and capture their choice clearly."
)

BASELINE_ELLIOT_SYSTEM = f"{DEFAULT_VAPI_SYSTEM_PROMPT}\n\n{CALL_CONTEXT}"

ARCHETYPES: list[tuple[str, str]] = [
    (
        "Positive — interested",
        "You are interested and cooperative. You ask smart follow-ups about results and timing. "
        "You are willing to buy if it makes sense.",
    ),
    (
        "Positive — curious / ROI-focused",
        "You are friendly but want clarity on ROI, how fast leads come, and what 'integration' means.",
    ),
    (
        "Neutral — send info / email",
        "You will not commit on the phone. You keep asking to email details or send a link.",
    ),
    (
        "Neutral — not sure, tell me more",
        "You are noncommittal. You want a plain-English explanation without pressure.",
    ),
    (
        "Negative — not interested",
        "You are politely dismissive. You try to end the call unless Elliot genuinely engages you.",
    ),
    (
        "Negative — too busy / bad time",
        "You are rushed and annoyed. You push back on length unless Elliot is extremely brief.",
    ),
    (
        "Negative — already have leads / marketing covered",
        "You already have SEO, referrals, or an agency. You see little need for another vendor.",
    ),
    (
        "Objection — how does it work",
        "You demand specifics on mechanics, exclusivity, territory, and what AutoYield actually does.",
    ),
    (
        "Objection — scam / what's the catch",
        "You are skeptical and confrontational about legitimacy, upfront money, and contracts.",
    ),
    (
        "Objection — tried this before / burned",
        "You had bad experiences with lead brokers or shared leads. You need credible differentiation.",
    ),
]

RESEARCH_SECTION = """
=== Step 1 — Research: common business-owner responses (cold call, lead gen / marketing) ===

Below are ten recurring patterns operators hear when calling local businesses about lead generation
or performance marketing. They map to the simulation archetypes.

1. Positive — interested: owner engages, asks how soon it starts, what they need to do next.
2. Positive — ROI / proof: owner wants numbers, examples, or guarantees before deciding.
3. Neutral — send info: owner deflects to email; may be polite avoidance or real process.
4. Neutral — tell me more: owner is open-ended; tests whether the pitch respects their time.
5. Negative — not interested: owner shuts down early; may soften if trust is built quickly.
6. Negative — too busy: owner cites jobs on site, customers waiting, or callback demand.
7. Negative — already covered: owner cites agency, in-house, ads, or word of mouth.
8. Objection — how it works: owner wants mechanics, exclusivity, fulfillment, and logistics.
9. Objection — scam / catch: owner challenges motives, fees, and fine print.
10. Objection — tried before: owner references bad lead quality, shared leads, or no-shows.

These patterns inform objection handling, pacing, and closing for the $300 trial vs $500 month fork.
"""

OWNER_SIMULATOR_SYSTEM = """You simulate a U.S. small business owner on an unexpected sales call.
Output ONLY the spoken words the owner says next—no quotes, labels, or stage directions.
Use natural, terse phone speech. Stay consistent with the archetype given in the user message."""

GRADER_SYSTEM = """You evaluate Elliot (the sales agent) on a cold-call transcript for AutoYield Systems.
Return ONLY valid JSON with integer scores 1-10 for:
"natural" (sounds human vs robotic/scripted),
"objection" (handles pushback and questions well),
"close" (moves toward a clear choice: 14-day trial $300 or full month $500),
"overall" (holistic),
plus "notes" (one short sentence).
No markdown, no extra keys."""


def _client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        print("ERROR: OPENAI_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=key)


def _chat(client: OpenAI, messages: list[dict], temperature: float = 0.7) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()


def _chat_json(client: OpenAI, messages: list[dict], temperature: float = 0.2) -> dict:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


def simulate_one(
    client: OpenAI,
    archetype_title: str,
    archetype_desc: str,
    elliot_system: str,
    max_turns: int = 10,
) -> list[dict[str, str]]:
    """Returns transcript entries with speaker + text."""
    transcript: list[dict[str, str]] = []
    elliot_messages: list[dict] = [
        {"role": "system", "content": elliot_system},
        {"role": "assistant", "content": OPENING_LINE},
    ]
    transcript.append({"speaker": "Elliot", "text": OPENING_LINE})

    for _ in range(max_turns):
        owner_instruction = (
            f"Archetype: {archetype_title}\n"
            f"Behavior: {archetype_desc}\n"
            "Conversation so far:\n"
            + "\n".join(f'{t["speaker"]}: {t["text"]}' for t in transcript)
            + "\n\nRespond as the owner with your next line only."
        )
        owner_line = _chat(
            client,
            [
                {"role": "system", "content": OWNER_SIMULATOR_SYSTEM},
                {"role": "user", "content": owner_instruction},
            ],
            temperature=0.85,
        )
        owner_line = owner_line.strip().strip('"').strip("'")
        if not owner_line:
            break
        transcript.append({"speaker": "Owner", "text": owner_line})
        elliot_messages.append({"role": "user", "content": owner_line})

        elliot_reply = _chat(client, elliot_messages, temperature=0.65)
        elliot_reply = elliot_reply.strip()
        if not elliot_reply:
            break
        transcript.append({"speaker": "Elliot", "text": elliot_reply})
        elliot_messages.append({"role": "assistant", "content": elliot_reply})

        # Natural exit if owner ends call
        combined = owner_line.lower()
        if any(
            x in combined
            for x in (
                "got to go",
                "gotta go",
                "not interested",
                "take me off",
                "stop calling",
                "have a good day",
                "goodbye",
                "bye",
                "click",
            )
        ):
            if len(transcript) >= 4:
                break

    return transcript


def grade_transcript(client: OpenAI, transcript: list[dict[str, str]]) -> dict:
    body = "\n".join(f'{t["speaker"]}: {t["text"]}' for t in transcript)
    data = _chat_json(
        client,
        [
            {"role": "system", "content": GRADER_SYSTEM},
            {
                "role": "user",
                "content": f"Transcript:\n{body}\n\nScore Elliot's performance as JSON.",
            },
        ],
    )
    for k in ("natural", "objection", "close", "overall"):
        data[k] = int(data.get(k, 5))
        data[k] = max(1, min(10, data[k]))
    data["notes"] = str(data.get("notes", ""))
    return data


def extract_weaknesses(client: OpenAI, baseline_prompt: str, packed: str) -> list[str]:
    data = _chat_json(
        client,
        [
            {
                "role": "system",
                "content": (
                    "You improve sales-agent system prompts. Given transcripts and scores, "
                    "list exactly the top 5 weaknesses in the current Elliot prompt and "
                    "conversation style. Return JSON: {\"weaknesses\": [\"...\", ...]} "
                    "Each weakness one clear sentence."
                ),
            },
            {
                "role": "user",
                "content": f"Current system prompt:\n{baseline_prompt}\n\n{packed}",
            },
        ],
    )
    w = data.get("weaknesses") or []
    return [str(x) for x in w][:5]


def rewrite_prompt(client: OpenAI, baseline: str, weaknesses: list[str]) -> str:
    data = _chat_json(
        client,
        [
            {
                "role": "system",
                "content": (
                    "Rewrite the entire Elliot system prompt for a voice AI (Vapi). "
                    "The agent's name is always Elliot from AutoYield Systems — never Vapi, Assistant, or AI. "
                    "Fix every listed weakness. Requirements: natural spoken English, "
                    "concise turns, strong objection handling, clear fork close for "
                    "14-day trial $300 vs full month $500, capture choice as trial_14 or month_30. "
                    "Professional, no family/personal references. "
                    "Return JSON: {\"prompt\": \"...full prompt text...\"}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"current_prompt": baseline, "weaknesses": weaknesses},
                    ensure_ascii=False,
                ),
            },
        ],
    )
    p = data.get("prompt") or ""
    return str(p).strip()


def format_transcript(t: list[dict[str, str]]) -> str:
    return "\n".join(f'{x["speaker"]}: {x["text"]}' for x in t)


def run_phase(
    client: OpenAI,
    label: str,
    elliot_system: str,
) -> tuple[list[tuple[str, list, dict]], str]:
    results: list[tuple[str, list, dict]] = []
    packed_parts: list[str] = []
    for title, desc in ARCHETYPES:
        print(f"  [{label}] Simulating: {title}...", flush=True)
        tr = simulate_one(client, title, desc, elliot_system)
        scores = grade_transcript(client, tr)
        results.append((title, tr, scores))
        packed_parts.append(
            f"=== {title} ===\nScores: {scores}\n{format_transcript(tr)}\n"
        )
    return results, "\n".join(packed_parts)


def scores_table(rows: list[tuple[str, list, dict]], phase: str) -> str:
    lines = [
        f"{'Archetype':<42} {'Nat':>4} {'Obj':>4} {'Cls':>4} {'Ovl':>4}",
        "-" * 62,
    ]
    for title, _, s in rows:
        lines.append(
            f"{title[:41]:<42} {s['natural']:>4} {s['objection']:>4} "
            f"{s['close']:>4} {s['overall']:>4}"
        )
    av = lambda k: sum(r[2][k] for r in rows) / len(rows)  # noqa: E731
    lines.append("-" * 62)
    lines.append(
        f"{'AVERAGE':<42} {av('natural'):>4.1f} {av('objection'):>4.1f} "
        f"{av('close'):>4.1f} {av('overall'):>4.1f}"
    )
    return "\n".join(lines)


def main() -> None:
    client = _client()
    report: list[str] = []
    report.append("Elliot (AutoYield / Vapi) optimization report")
    report.append(f"Model: {MODEL}")
    report.append("")
    report.append(RESEARCH_SECTION.strip())
    report.append("")
    report.append("=== Method ===")
    report.append(
        "Dual-agent simulation: GPT-4o plays the business owner by archetype; "
        "GPT-4o plays Elliot using the provided system prompt. "
        "Opening line matches tier_offer_pitch style. "
        "Each transcript graded by a separate JSON rubric call."
    )
    report.append("")
    report.append("=== Baseline system prompt (before optimization) ===")
    report.append(BASELINE_ELLIOT_SYSTEM)
    report.append("")

    print("Phase A: baseline simulations...", flush=True)
    baseline_results, baseline_packed = run_phase(client, "baseline", BASELINE_ELLIOT_SYSTEM)
    report.append("=== Baseline simulations — scores ===")
    report.append(scores_table(baseline_results, "baseline"))
    report.append("")
    for title, tr, sc in baseline_results:
        report.append(f"--- {title} ---")
        report.append(f"Scores: natural={sc['natural']} objection={sc['objection']} "
                      f"close={sc['close']} overall={sc['overall']} — {sc['notes']}")
        report.append(format_transcript(tr))
        report.append("")

    print("Analyzing weaknesses...", flush=True)
    weaknesses = extract_weaknesses(client, BASELINE_ELLIOT_SYSTEM, baseline_packed)
    report.append("=== Top 5 weaknesses (baseline) ===")
    for i, w in enumerate(weaknesses, 1):
        report.append(f"{i}. {w}")
    report.append("")

    print("Rewriting system prompt...", flush=True)
    optimized = rewrite_prompt(client, BASELINE_ELLIOT_SYSTEM, weaknesses)
    if len(optimized) < 80:
        print("WARNING: optimized prompt unexpectedly short; aborting.", file=sys.stderr)
        sys.exit(1)

    print("Phase B: optimized simulations...", flush=True)
    opt_results, opt_packed = run_phase(client, "optimized", optimized)
    report.append("=== Optimized system prompt (after rewrite) ===")
    report.append(optimized)
    report.append("")
    report.append("=== Optimized simulations — scores ===")
    report.append(scores_table(opt_results, "optimized"))
    report.append("")
    for title, tr, sc in opt_results:
        report.append(f"--- {title} ---")
        report.append(f"Scores: natural={sc['natural']} objection={sc['objection']} "
                      f"close={sc['close']} overall={sc['overall']} — {sc['notes']}")
        report.append(format_transcript(tr))
        report.append("")

    report.append("=== Before vs after (averages) ===")
    for name, results in [("Baseline", baseline_results), ("Optimized", opt_results)]:
        av = {k: sum(r[2][k] for r in results) / len(results) for k in ("natural", "objection", "close", "overall")}
        report.append(
            f"{name}: natural={av['natural']:.2f} objection={av['objection']:.2f} "
            f"close={av['close']:.2f} overall={av['overall']:.2f}"
        )
    report.append("")
    report.append("=== Side-by-side by archetype (overall score) ===")
    report.append(f"{'Archetype':<42} {'Before':>8} {'After':>8} {'Delta':>8}")
    report.append("-" * 66)
    for i, title in enumerate([t[0] for t in ARCHETYPES]):
        b = baseline_results[i][2]["overall"]
        a = opt_results[i][2]["overall"]
        report.append(f"{title[:41]:<42} {b:>8} {a:>8} {a-b:>+8}")
    report.append("")
    report.append("=== Appendix: optimized phase raw pack (for audit) ===")
    report.append(opt_packed[:4000] + ("..." if len(opt_packed) > 4000 else ""))

    out_report = ROOT / "elliot_optimization_report.txt"
    out_prompt = ROOT / "elliot_final_prompt.txt"
    out_report.write_text("\n".join(report), encoding="utf-8")
    out_prompt.write_text(optimized.strip() + "\n", encoding="utf-8")
    print(f"Wrote {out_report}")
    print(f"Wrote {out_prompt}")


if __name__ == "__main__":
    main()
