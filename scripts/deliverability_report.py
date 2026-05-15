"""
Print DNS status for autoyieldagency.com, optionally send a plain-text probe to mail-tester.

Set MAILTESTER_TO in .env to your unique address from https://www.mail-tester.com/
Then run: python scripts/deliverability_report.py --send-mailtester

Score JSON is only available for paid mail-tester accounts; free tier: check the site after send.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass


def _dns_txt(name: str) -> list[str]:
    r = requests.get(
        "https://dns.google/resolve",
        params={"name": name, "type": "TXT"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    out: list[str] = []
    for ans in data.get("Answer") or []:
        if ans.get("type") == 16:
            t = (ans.get("data") or "").strip('"')
            out.append(t)
    return out


def _has_spf(txts: list[str]) -> bool:
    return any(t.lower().startswith("v=spf1") for t in txts)


def _has_dmarc(txts: list[str]) -> bool:
    return any(t.lower().startswith("v=dmarc1") for t in txts)


def _has_dkim(txts: list[str]) -> bool:
    return any("v=dkim1" in t.lower() for t in txts)


def _send_mailtester_probe(to_addr: str) -> tuple[bool, str]:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = os.getenv("SMTP_FROM_EMAIL", username).strip()
    if not (host and username and password and from_email and to_addr):
        return False, "smtp_or_recipient_incomplete"

    body = (
        "Hi — quick plain-text check from AutoYield.\n\n"
        "This message is only for mailbox diagnostics.\n\n"
        "Thanks,\n"
        "Stevie, AutoYield Systems\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "quick question"
    msg["From"] = from_email
    msg["To"] = to_addr

    try:
        use_ssl = port == 465
        if use_ssl:
            with smtplib.SMTP_SSL(host=host, port=port, timeout=25) as s:
                s.login(username, password)
                s.sendmail(from_email, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(host=host, port=port, timeout=25) as s:
                s.starttls()
                s.login(username, password)
                s.sendmail(from_email, [to_addr], msg.as_string())
        return True, "sent"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-mailtester", action="store_true", help="Send probe to MAILTESTER_TO")
    args = ap.parse_args()

    domain = "autoyieldagency.com"
    root_txt = _dns_txt(domain)
    dmarc_txt = _dns_txt(f"_dmarc.{domain}")
    dkim_txt = _dns_txt(f"default._domainkey.{domain}")

    spf_ok = _has_spf(root_txt)
    dmarc_ok = _has_dmarc(dmarc_txt)
    dkim_ok = _has_dkim(dkim_txt)

    report = {
        "domain": domain,
        "spf": {"present": spf_ok, "records": [t for t in root_txt if t.lower().startswith("v=spf1")][:3]},
        "dmarc": {"present": dmarc_ok, "records": dmarc_txt[:3]},
        "dkim_default_selector": {"present": dkim_ok, "records_count": len(dkim_txt)},
        "mailforge": "No MAILFORGE_API_KEY (or similar) in repo — cannot call Mailforge. Add credentials in .env and use their dashboard/API to confirm inboxes for this domain.",
        "mail_tester_score": "Not fetched (free tier has no JSON score without paid API key). Set MAILTESTER_TO and send, then open the mail-tester result URL in your browser.",
    }

    if args.send_mailtester:
        mt = os.getenv("MAILTESTER_TO", "").strip()
        ok, info = _send_mailtester_probe(mt) if mt else (False, "MAILTESTER_TO unset")
        report["mail_tester_send"] = {"ok": ok, "detail": info}

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
