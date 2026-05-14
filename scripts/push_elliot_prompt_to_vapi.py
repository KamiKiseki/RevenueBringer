"""
Push elliot_final_prompt.txt to the Vapi assistant as the system message.

Requires .env: VAPI_API_KEY, VAPI_ASSISTANT_ID
Usage: python scripts/push_elliot_prompt_to_vapi.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from templates import ELLIOT_VAPI_IDLE_HOOKS

PROMPT_FILE = ROOT / "elliot_final_prompt.txt"


def main() -> int:
    load_dotenv(ROOT / ".env")
    key = os.getenv("VAPI_API_KEY", "").strip()
    aid = os.getenv("VAPI_ASSISTANT_ID", "").strip()
    if not key or not aid:
        print("ERROR: Set VAPI_API_KEY and VAPI_ASSISTANT_ID in .env", file=sys.stderr)
        return 1
    if not PROMPT_FILE.is_file():
        print(f"ERROR: Missing {PROMPT_FILE}", file=sys.stderr)
        return 1
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    if not prompt:
        print("ERROR: Prompt file is empty", file=sys.stderr)
        return 1

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    base = f"https://api.vapi.ai/assistant/{aid}"

    r = requests.get(base, headers={"Authorization": f"Bearer {key}"}, timeout=45)
    if r.status_code >= 300:
        print(f"GET assistant failed: {r.status_code} {r.text[:400]}", file=sys.stderr)
        return 1

    body = r.json()
    model = dict(body.get("model") or {})
    msgs: list = list(model.get("messages") or [])
    replaced = False
    for i, msg in enumerate(msgs):
        if (msg or {}).get("role") == "system":
            msgs[i] = {"role": "system", "content": prompt}
            replaced = True
            break
    if not replaced:
        msgs.insert(0, {"role": "system", "content": prompt})
    model["messages"] = msgs

    patch = requests.patch(
        base,
        headers=headers,
        json={"model": model, "hooks": ELLIOT_VAPI_IDLE_HOOKS},
        timeout=60,
    )
    if patch.status_code >= 300:
        print(f"PATCH failed: {patch.status_code} {patch.text[:600]}", file=sys.stderr)
        return 1

    print(
        f"OK: Updated Vapi assistant {aid} system prompt from {PROMPT_FILE.name} "
        f"and idle hooks ({len(ELLIOT_VAPI_IDLE_HOOKS)} hook(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
