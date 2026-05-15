from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import exists, not_

from models import (
    EmailSequence,
    Lead,
    LeadStatus,
    MessageEvent,
    MessageStatus,
    get_session,
    init_db,
)

load_dotenv()

CHECKOUT_URL = "https://autoyieldsystems.com/checkout"
# Plain-language billing hint for emails 2–3 only (avoid spam-trigger words like "trial", "AI", "automate").
OFFER_HINT = "Two weeks at $300, then $500/month if you stay on."
SIGNATURE_LINE = "Stevie, AutoYield Systems"
SIGNATURE_BLOCK = "\n\n--\nStevie, AutoYield Systems"
MODEL = "gpt-4o"

# Subjects are fixed for consistency and inbox friendliness.
FORCED_SUBJECTS: dict[str, str] = {
    "email_1_subject": "quick question",
    "email_2_subject": "following up",
    "email_3_subject": "last note",
}

# Case-insensitive phrases we never want in sequence bodies (before signature).
BANNED_BODY_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"lead\s+generation", re.I),
    re.compile(r"\bAI\b", re.I),
    re.compile(r"\btrial\b", re.I),
    re.compile(r"\bcustomers?\b", re.I),
    re.compile(r"\bsystem\b", re.I),
    re.compile(r"automate|automation", re.I),
    re.compile(r"\bCRM\b", re.I),
    re.compile(r"https?://", re.I),
)

# Tokens not used alone for "Hi …" when deriving from business name (after LLC/Inc/Co stripped).
_SKIP_GREETING_TOKEN = frozenset(
    {
        "llc",
        "l.l.c.",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "pllc",
        "pc",
        "p.c.",
        "llp",
        "l.p.",
        "ltd",
        "limited",
        "the",
        "a",
        "an",
        "&",
        "and",
        "of",
        "for",
        "at",
        # Often first word of business title, not a person's name
        "luxury",
        "premier",
        "ambient",
        "texas",
        "two",
        "metal",
        "storm",
        "bison",
        "dominion",
        "caliber",
        "office",
        "team",
        "services",
        "roofing",
        "dental",
        "hvac",
        "law",
        "real",
        "estate",
        "auto",
        "body",
        "med",
        "spa",
        "gym",
        "insurance",
        "san",
        "antonio",
    }
)

_LEGAL_SUFFIX_END = re.compile(
    r"(?i)[,\s]*\b(?:llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|co\.?|company|pllc|p\.c\.|pc|llp|l\.p\.|ltd\.?|limited)\b\.?\s*$"
)

# Niche -> one concrete local-business pain (for personalization).
NICHE_PAIN: dict[str, str] = {
    "hvac": "busy stretches where the phone rings while you are already on a job",
    "roofing": "sorting serious repair asks from time-wasters after bad weather",
    "dental": "open chair time and people choosing another office down the street",
    "law": "wanting a steadier flow of consult-ready cases, not just website traffic",
    "real estate": "standing out in a crowded market and getting real listing conversations",
    "plumber": "emergency calls you miss when you are under a sink and slow follow-up on quotes",
    "auto body": "low-margin jobs and long back-and-forth that never books",
    "med spa": "no-shows on consults and price-shoppers who ghost after one visit",
    "gym": "membership churn and foot traffic that does not stick",
    "insurance": "explaining value fast when people compare you on price alone",
    "default": "inconsistent inbound calls and time spent on people who never book",
}


def _niche_key(niche: str) -> str:
    n = (niche or "").strip().lower()
    for key in NICHE_PAIN:
        if key != "default" and key in n:
            return key
    if "med" in n and "spa" in n:
        return "med spa"
    if "auto" in n and "body" in n:
        return "auto body"
    if "real" in n and "estate" in n:
        return "real estate"
    if "law" in n or "attorney" in n or "legal" in n:
        return "law"
    return "default"


def _pain_for(niche: str) -> str:
    k = _niche_key(niche)
    if k in NICHE_PAIN and k != "default":
        return NICHE_PAIN[k]
    return NICHE_PAIN["default"]


def _normalize_greeting_token(raw: str) -> str:
    t = raw.strip().strip(',.;:"')
    if not t:
        return ""
    return t


def _looks_like_abbrev_or_allcaps(token: str) -> bool:
    """True when a greeting token is not a plausible person name (e.g. LRES, PROS)."""
    letters = re.sub(r"[^A-Za-z]", "", token or "")
    if len(letters) < 2:
        return True
    if letters.isupper():
        return True
    vowels = set("aeiouyAEIOUY")
    if len(letters) <= 5 and not any(c in vowels for c in letters):
        return True
    return False


def _greeting_first_name(raw: str | None) -> str:
    token = _normalize_greeting_token(raw or "")
    if not token or _looks_like_abbrev_or_allcaps(token):
        return "there"
    return token


def _owner_first_name(owner_name: str) -> str | None:
    o = (owner_name or "").strip()
    if not o:
        return None
    tokens = o.split()
    honorifics = {"dr", "mr", "mrs", "ms", "miss", "prof"}
    first = tokens[0].lower().rstrip(".")
    start = 1 if first in honorifics and len(tokens) > 1 else 0
    pick = _normalize_greeting_token(tokens[start])
    if not pick or _looks_like_abbrev_or_allcaps(pick):
        return None
    return pick


def _tokens_from_business_name(business_name: str) -> list[str]:
    s = (business_name or "").strip()
    if not s:
        return []
    # Prefer the segment after "//" (often the trade / shorter brand name).
    if "//" in s:
        parts = [p.strip() for p in s.split("//") if p.strip()]
        if parts:
            s = parts[-1]
    while True:
        ns = _LEGAL_SUFFIX_END.sub("", s).strip().rstrip(",")
        if ns == s:
            break
        s = ns
    # Split on whitespace and common separators
    bits = re.split(r"[\s|/,\-–—]+", s)
    out: list[str] = []
    for b in bits:
        t = _normalize_greeting_token(b)
        if t:
            out.append(t)
    return out


def _first_name(lead: Lead) -> str:
    owner = _owner_first_name(lead.owner_name or "")
    if owner:
        return _greeting_first_name(owner)

    tokens = _tokens_from_business_name(lead.business_name or "")
    for t in tokens:
        key = re.sub(r"[^\w]", "", t).lower()
        if len(key) < 2:
            continue
        if key in _SKIP_GREETING_TOKEN:
            continue
        return _greeting_first_name(t)
    return "there"


def _strip_urls(text: str) -> str:
    text = re.sub(r"https?://[^\s]+", "", text, flags=re.I)
    text = re.sub(r"\bmailto:\S+", "", text, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _truncate_max_sentences(text: str, max_sentences: int) -> str:
    t = (text or "").strip()
    if not t:
        return t
    parts = re.split(r"(?<=[.!?])\s+", t)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= max_sentences:
        return t
    return " ".join(parts[:max_sentences]).strip()


def _strip_model_signature(body: str) -> str:
    b = (body or "").strip()
    for marker in (
        "\n\n--\n",
        "\n\nThanks,\nStevie",
        "\n\nBest,\nStevie",
        "Stevie from AutoYield",
        SIGNATURE_LINE,
    ):
        i = b.rfind(marker)
        if i != -1:
            b = b[:i].rstrip()
    return b


def _deliverability_violations(body: str) -> list[str]:
    probe = (body or "").replace(CHECKOUT_URL, "")
    bad: list[str] = []
    for rx in BANNED_BODY_RES:
        if rx.search(probe):
            bad.append(rx.pattern)
    return bad


def _finalize_sequence(data: dict) -> dict:
    """Plain text, forced subjects, no links in email 1, checkout only at foot of 2 and 3, fixed sign-off."""
    out = dict(data)
    for k, subj in FORCED_SUBJECTS.items():
        out[k] = subj

    b1 = _strip_model_signature(str(out.get("email_1_body", "")))
    b1 = _strip_urls(b1)
    b1 = _truncate_max_sentences(b1, 5)
    out["email_1_body"] = (b1 + SIGNATURE_BLOCK).strip()

    b2 = _strip_model_signature(str(out.get("email_2_body", "")))
    b2 = _strip_urls(b2)
    b2 = _truncate_max_sentences(b2, 5)
    if CHECKOUT_URL not in b2:
        b2 = f"{b2.rstrip()}\n\n{CHECKOUT_URL}".strip()
    out["email_2_body"] = (b2 + SIGNATURE_BLOCK).strip()

    b3 = _strip_model_signature(str(out.get("email_3_body", "")))
    b3 = _strip_urls(b3)
    b3 = _truncate_max_sentences(b3, 3)
    if CHECKOUT_URL not in b3:
        b3 = f"{b3.rstrip()}\n\n{CHECKOUT_URL}".strip()
    out["email_3_body"] = (b3 + SIGNATURE_BLOCK).strip()

    for k in ("email_1_body", "email_2_body", "email_3_body"):
        out[k] = out[k].replace("[Your Name]", "").replace("  ", " ").strip()

    return out


def _city(lead: Lead) -> str:
    return (lead.location or "").strip() or "San Antonio"


def _leads_to_process(session, *, regenerate: bool):
    """
    Leads with email, not yet emailed in our pipeline (status queued),
    and no successful outbound email logged. Optional: skip if sequence exists.
    """
    sent_outbound = exists().where(
        MessageEvent.lead_id == Lead.id,
        MessageEvent.direction == "outbound",
        MessageEvent.channel == "email",
        MessageEvent.status == MessageStatus.SENT,
    )
    q = (
        session.query(Lead)
        .filter(
            Lead.email.isnot(None),
            Lead.email != "",
            Lead.status == LeadStatus.QUEUED,
            not_(sent_outbound),
        )
    )
    if not regenerate:
        have_seq = exists().where(EmailSequence.lead_id == Lead.id)
        q = q.filter(not_(have_seq))
    return q.all()


def _parse_sequence_json(text: str) -> dict:
    t = (text or "").strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        t = m.group(0)
    return json.loads(t)


def _generate_sequence(
    client: OpenAI,
    *,
    business_name: str,
    niche: str,
    city: str,
    pain: str,
    first_name: str,
    temperature: float = 0.55,
) -> dict:
    system = (
        "You write three plain-text cold emails for a small local services company (AutoYield Systems). "
        "Deliverability rules (strict): "
        "1) Plain text only — no HTML tags, no markdown, no bullet lists with dashes that look like ads. "
        "2) Never use these words or phrases anywhere: lead generation, AI, trial, customers, system, automate, automation. "
        "3) Do not use the checkout URL in email 1. Do not put any http(s) or mailto links in email 1. "
        "4) Email 1: at most 5 short sentences, one simple question at the end, mention their city or trade lightly, "
        "weave in the pain in one clause — no pitch, no pricing, no product name hype. "
        "5) Email 2: at most 5 short sentences, gentle follow-up to email 1, you may mention pricing once in plain words: "
        f"{OFFER_HINT} Put that pricing sentence above the closing; do not paste URLs in the body (host will add the link). "
        "6) Email 3: at most 3 short sentences total in the body before any link line, soft close, no pressure. "
        "7) Do not sign the email in the body — no 'Best,' no name lines (added later). "
        "8) Subject lines will be overwritten; still output the JSON keys with short lowercase subjects. "
        "9) Sound like one person (Stevie) wrote it — conversational, lowercase subject style ok in JSON only. "
        "Output strictly valid JSON with keys: "
        "email_1_subject, email_1_body, email_2_subject, email_2_body, email_3_subject, email_3_body."
    )
    greet_hint = (
        'Use exactly "Hi there" as the opening salutation.'
        if first_name.strip().lower() == "there"
        else f'Open with a natural "Hi {first_name}" salutation (use that exact first name token).'
    )
    user = (
        f"Business name: {business_name}\n"
        f"Greeting first name token: {first_name}\n"
        f"{greet_hint}\n"
        f"Niche: {niche}\n"
        f"City: {city}\n"
        f"Lightweight pain to reference once: {pain}\n"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    content = (resp.choices[0].message.content or "").strip()
    data = _parse_sequence_json(content)
    required = [
        "email_1_subject",
        "email_1_body",
        "email_2_subject",
        "email_2_body",
        "email_3_subject",
        "email_3_body",
    ]
    for k in required:
        if k not in data or not str(data[k]).strip():
            raise ValueError(f"missing or empty field: {k}")
    data = _finalize_sequence(data)
    for k in ("email_1_body", "email_2_body", "email_3_body"):
        body_only = data[k].split("--")[0] if "--" in data[k] else data[k]
        hits = _deliverability_violations(body_only)
        if hits:
            raise ValueError(f"deliverability_check_failed:{k}:{hits[:3]}")
    return data


def _save_sequence(session, lead_id: int, data: dict) -> None:
    row = session.query(EmailSequence).filter(EmailSequence.lead_id == lead_id).first()
    if row:
        row.email_1_subject = data["email_1_subject"][:500]
        row.email_1_body = data["email_1_body"]
        row.email_2_subject = data["email_2_subject"][:500]
        row.email_2_body = data["email_2_body"]
        row.email_3_subject = data["email_3_subject"][:500]
        row.email_3_body = data["email_3_body"]
        row.created_at = datetime.now(timezone.utc)
    else:
        session.add(
            EmailSequence(
                lead_id=lead_id,
                email_1_subject=data["email_1_subject"][:500],
                email_1_body=data["email_1_body"],
                email_2_subject=data["email_2_subject"][:500],
                email_2_body=data["email_2_body"],
                email_3_subject=data["email_3_subject"][:500],
                email_3_body=data["email_3_body"],
            )
        )


def _export_csv(path: str) -> int:
    rows_written = 0
    with get_session() as session:
        pairs = (
            session.query(Lead, EmailSequence)
            .join(EmailSequence, EmailSequence.lead_id == Lead.id)
            .order_by(EmailSequence.id.asc())
            .all()
        )
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "email",
                    "first_name",
                    "sequence_1_subject",
                    "sequence_1_body",
                    "sequence_2_subject",
                    "sequence_2_body",
                    "sequence_3_subject",
                    "sequence_3_body",
                ]
            )
            for lead, seq in pairs:
                fn = _first_name(lead)
                w.writerow(
                    [
                        lead.email or "",
                        fn,
                        seq.email_1_subject,
                        seq.email_1_body,
                        seq.email_2_subject,
                        seq.email_2_body,
                        seq.email_3_subject,
                        seq.email_3_body,
                    ]
                )
                rows_written += 1
    return rows_written


def main() -> int:
    regenerate = "--regenerate" in sys.argv or os.getenv("SEQUENCE_REGENERATE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("[sequence_generator] ERROR: OPENAI_API_KEY is not set in environment.")
        return 1

    init_db()
    client = OpenAI(api_key=api_key)

    with get_session() as session:
        leads = _leads_to_process(session, regenerate=regenerate)

    total = len(leads)
    saved = 0
    failed = 0

    print(f"[sequence_generator] Leads to process: {total} (regenerate={regenerate})")

    for i, lead in enumerate(leads, start=1):
        pain = _pain_for(lead.niche)
        city = _city(lead)
        fn = _first_name(lead)
        try:
            try:
                data = _generate_sequence(
                    client,
                    business_name=lead.business_name,
                    niche=lead.niche,
                    city=city,
                    pain=pain,
                    first_name=fn,
                    temperature=0.55,
                )
            except ValueError as exc:
                if "missing or empty" in str(exc):
                    raise
                data = _generate_sequence(
                    client,
                    business_name=lead.business_name,
                    niche=lead.niche,
                    city=city,
                    pain=pain,
                    first_name=fn,
                    temperature=0.35,
                )
            with get_session() as session:
                _save_sequence(session, lead.id, data)
                session.commit()
            saved += 1
            print(f"[sequence_generator] [{i}/{total}] OK lead_id={lead.id} {lead.business_name[:40]}")
        except Exception as exc:
            failed += 1
            print(f"[sequence_generator] [{i}/{total}] FAIL lead_id={lead.id}: {exc}")

    export_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sequences_export.csv")
    exported = _export_csv(export_path)

    print(
        f"[sequence_generator] Done. generated_ok={saved} failed={failed} "
        f"csv_rows_written={exported} file={export_path}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
