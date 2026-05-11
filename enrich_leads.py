from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import or_

from models import Lead, get_session, init_db

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10
DELAY_SECONDS = 2

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

JUNK_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "sentry.io",
        "google.com",
        "facebook.com",
        "apple.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "gmail.com",
    }
)

# Local parts we never want (newsletter / placeholder / role noise).
_BLOCKED_LOCAL_PARTS = frozenset(
    {"user", "example", "admin", "noreply", "support", "mailer-daemon", "postmaster"}
)

EXTRA_PATHS = ("contact", "contact-us", "about", "about-us")


def _normalize_base_url(website: str) -> str:
    u = (website or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u.rstrip("/")


def _email_is_dirty(addr: str | None) -> bool:
    """
    True = discard / scrub from DB. Stricter than initial scrape pass.
    """
    if not (addr or "").strip() or "@" not in addr:
        return True
    raw = addr.strip()
    lower = raw.lower()
    local, _, domain = lower.partition("@")
    if not local or not domain:
        return True
    # Image-like / asset junk that regex sometimes captures as "emails"
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        if ext in lower:
            return True
    # Hard substring blocks (provider / placeholder)
    for bad in (
        "@sentry.io",
        "sentry-next",
        "@wix.com",
        "wixpress.com",
        "@godaddy.com",
        "filler@godaddy",
        "@domain.com",
        "user@domain",
        "info@domain",
        "your@email",
    ):
        if bad in lower:
            return True
    if domain in JUNK_EMAIL_DOMAINS or domain.endswith(".example.com"):
        return True
    if local in _BLOCKED_LOCAL_PARTS:
        return True
    if local == "info" and domain == "domain.com":
        return True
    return False


def _extract_emails_from_html(html: str) -> list[str]:
    found = EMAIL_PATTERN.findall(html or "")
    # BeautifulSoup: mailto: links
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        for a in soup.select('a[href^="mailto:"]'):
            href = (a.get("href") or "").replace("mailto:", "").split("?")[0].strip()
            if href and "@" in href:
                found.append(href)
    except Exception:
        pass
    seen: set[str] = set()
    out: list[str] = []
    for e in found:
        e = e.strip().strip(".,);\"'<>")
        if not e or e.lower() in seen:
            continue
        seen.add(e.lower())
        out.append(e)
    return out


def _first_valid_email(candidates: list[str]) -> str | None:
    for e in candidates:
        if not _email_is_dirty(e):
            return e
    return None


def scrub_dirty_emails_from_db() -> int:
    """Set leads.email to NULL when current value fails quality checks."""
    cleared = 0
    with get_session() as db:
        for lead in db.query(Lead).filter(Lead.email.isnot(None), Lead.email != "").all():
            if _email_is_dirty(lead.email):
                lead.email = None
                db.add(lead)
                cleared += 1
        db.commit()
    return cleared


def _fetch_html(session: requests.Session, url: str) -> str | None:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            return None
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            # Still try regex on body for edge cases
            pass
        return r.text or ""
    except Exception:
        return None


def _urls_to_try(base: str) -> list[str]:
    urls = [base]
    for path in EXTRA_PATHS:
        urls.append(urljoin(base + "/", path))
    return urls


def enrich_one(session: requests.Session, lead: Lead) -> str | None:
    base = _normalize_base_url(lead.website or "")
    if not base:
        return None
    for url in _urls_to_try(base):
        html = _fetch_html(session, url)
        if not html:
            time.sleep(DELAY_SECONDS)
            continue
        emails = _extract_emails_from_html(html)
        picked = _first_valid_email(emails)
        time.sleep(DELAY_SECONDS)
        if picked:
            return picked
    return None


def main() -> int:
    init_db()
    scrubbed = scrub_dirty_emails_from_db()
    print(f"[enrich_leads] scrubbed_dirty_emails_cleared={scrubbed}")

    http = requests.Session()
    http.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    with get_session() as db:
        leads = (
            db.query(Lead)
            .filter(
                Lead.website.isnot(None),
                Lead.website != "",
                or_(Lead.email.is_(None), Lead.email == ""),
            )
            .order_by(Lead.id.asc())
            .all()
        )

    total = len(leads)
    found_n = 0
    not_found_n = 0

    print(f"[enrich_leads] Leads to process: {total}")

    for i, lead in enumerate(leads, start=1):
        base = _normalize_base_url(lead.website or "")
        host = urlparse(base).netloc or base
        try:
            email = enrich_one(http, lead)
        except Exception as exc:
            email = None
            print(f"[enrich_leads] [{i}/{total}] ERROR {lead.business_name[:50]} — {exc}")

        if email:
            # enrich_one only returns addresses passing _first_valid_email / _email_is_dirty
            found_n += 1
            with get_session() as db:
                row = db.query(Lead).filter(Lead.id == lead.id).first()
                if row:
                    row.email = email
                    db.add(row)
                    db.commit()
            print(f"[enrich_leads] [{i}/{total}] FOUND {lead.business_name[:60]} | {host} -> {email}")
        else:
            not_found_n += 1
            print(f"[enrich_leads] [{i}/{total}] NOT FOUND {lead.business_name[:60]} | {host}")

    print(
        f"[enrich_leads] Done. processed={total} emails_found={found_n} emails_not_found={not_found_n}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
