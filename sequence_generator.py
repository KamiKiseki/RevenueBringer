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
OFFER_LINE = "14-day trial for $300, then $500/month if you want to keep going"
SIGNATURE_LINE = "Stevie from AutoYield Systems"
MODEL = "gpt-4o"

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
    "hvac": "seasonal demand swings and jobs slipping through the cracks when you're on a job",
    "roofing": "storm chasers and low-quality leads that waste your crews' time",
    "dental": "empty chair time and new patients not finding you before your competitors",
    "law": "needing a steadier flow of consult-ready cases, not just website traffic",
    "real estate": "standing out in a crowded market and getting real listing conversations",
    "plumber": "emergency calls you miss when you're under a sink and slow follow-up on quotes",
    "auto body": "low-margin jobs and long cycle times from leads that never convert",
    "med spa": "competition for high-intent clients and no-shows on consults",
    "gym": "membership churn and getting serious trial signups, not just tire-kickers",
    "insurance": "explaining value fast because prospects compare you on price alone",
    "default": "inconsistent lead flow and spending time on tire-kickers instead of buyers",
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


def _apply_signature_to_sequence(data: dict) -> dict:
    """Replace placeholder signatures; normalize to Stevie / AutoYield."""
    out = dict(data)
    for k in (
        "email_1_body",
        "email_2_body",
        "email_3_body",
    ):
        if k in out and isinstance(out[k], str):
            out[k] = out[k].replace("[Your Name]", SIGNATURE_LINE)
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
) -> dict:
    system = (
        "You write short, human cold email sequences for a local lead-generation service (AutoYield Systems). "
        "Rules: never pushy, conversational, plain text feel, no fake statistics—if you use a result, keep it "
        "plausible and generic (e.g. 'teams like yours often want more consistency'). "
        f"Every email must mention the offer once in natural language: {OFFER_LINE}. "
        f"Include the checkout link exactly once across the whole 3-email sequence: {CHECKOUT_URL} "
        "(use it in the most natural email, usually email 2 or 3). "
        f"Sign every email with this exact line only—never placeholders or alternatives: {SIGNATURE_LINE}. "
        "Output strictly valid JSON with keys: "
        "email_1_subject, email_1_body, email_2_subject, email_2_body, email_3_subject, email_3_body. "
        "Email 1 = day 1 cold intro, short, ends with one simple question, references niche pain. "
        "Email 2 = day 3 follow-up, references first email, one credibility line, CTA to short chat. "
        "Email 3 = day 7 final, low pressure, easy out, last nudge for trial. "
        "Do not use ALL CAPS or multiple exclamation marks."
    )
    greet_hint = (
        'Use exactly "Hi there" as the opening salutation.'
        if first_name.strip().lower() == "there"
        else f'Open with a natural "Hi {first_name}" salutation (use that exact first name token).'
    )
    user = (
        f"Business name: {business_name}\n"
        f"Owner/first name for greeting line only: {first_name}\n"
        f"{greet_hint}\n"
        f"Niche: {niche}\n"
        f"City: {city}\n"
        f"Pain point to weave in: {pain}\n"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
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
            data = _generate_sequence(
                client,
                business_name=lead.business_name,
                niche=lead.niche,
                city=city,
                pain=pain,
                first_name=fn,
            )
            data = _apply_signature_to_sequence(data)
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
