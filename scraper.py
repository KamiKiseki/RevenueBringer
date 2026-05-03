from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
import random
from urllib.parse import quote

import requests

from models import log_system_event

_LAST_FETCH_META: dict[str, str] = {
    "source": "none",
    "reason": "not_run",
    "detail": "",
}

# Substrings that usually indicate blocking / anti-bot (Maps or upstream).
_BLOCK_SUBSTRINGS = (
    "captcha",
    "blocked",
    "block",
    "proxy",
    "unusual traffic",
    "automated queries",
    "consent",
    "access denied",
    "403",
    "429",
    "forbidden",
    "rate limit",
)


def _set_fetch_meta(*, source: str, reason: str, detail: str = "") -> None:
    global _LAST_FETCH_META
    _LAST_FETCH_META = {
        "source": (source or "unknown")[:64],
        "reason": (reason or "unknown")[:128],
        "detail": (detail or "")[:1000],
    }


def get_last_fetch_meta() -> dict[str, str]:
    return dict(_LAST_FETCH_META)


def _normalize_actor_id(actor_id: str) -> str:
    """Apify accepts owner~name; env often uses owner/name."""
    aid = (actor_id or "").strip()
    if not aid:
        return aid
    if "~" in aid:
        return aid
    if "/" in aid:
        owner, _, name = aid.partition("/")
        return f"{owner}~{name}" if name else aid
    return aid


def _apify_path_actor(actor_id: str) -> str:
    """Quote actor id for URL path; keep ~ unencoded (Apify owner~name style)."""
    norm = _normalize_actor_id(actor_id)
    return quote(norm, safe="~")


def _fetch_apify_last_run(token: str, actor_id: str) -> dict[str, Any] | None:
    path = _apify_path_actor(actor_id)
    url = f"https://api.apify.com/v2/acts/{path}/runs/last"
    try:
        resp = requests.get(url, params={"token": token}, timeout=45)
        if resp.status_code >= 300:
            return None
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            inner = data.get("data")
            return inner if isinstance(inner, dict) else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _diagnose_empty_dataset(token: str, actor_id: str, niche: str, location: str) -> None:
    """
    After run-sync returns zero items, inspect the actor's last run to classify the failure.
    Emits explicit SystemLog lines for Command Center visibility.
    """
    run = _fetch_apify_last_run(token, actor_id)
    if not run:
        detail = (
            f"Empty Apify dataset for niche={niche!r} location={location!r}; "
            "could not load last run status (API error or auth)."
        )
        _set_fetch_meta(source="apify", reason="empty_dataset_run_status_unknown", detail=detail)
        log_system_event(
            source="scraper",
            action="apify_empty_result",
            detail=f"[WARN] {detail}",
            level="warn",
        )
        return

    status = (run.get("status") or "").upper()
    raw_msg = str(run.get("statusMessage") or run.get("message") or "")
    msg_l = raw_msg.lower()

    blob = f"{status} {msg_l}"
    if any(s in blob for s in _BLOCK_SUBSTRINGS):
        human = "[ERROR] Google blocked the scraper; rotation needed."
        _set_fetch_meta(
            source="apify",
            reason="proxy_or_block",
            detail=f"{human} status={status} statusMessage={raw_msg[:400]}",
        )
        log_system_event(
            source="scraper",
            action="apify_blocked_or_proxy",
            detail=f"{human} Apify status={status} message={raw_msg[:500]}",
            level="error",
        )
        return

    if status in ("FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"):
        human = f"[ERROR] Actor run failed: status={status} message={raw_msg[:300] or 'n/a'}"
        _set_fetch_meta(
            source="apify",
            reason="actor_failed",
            detail=human,
        )
        log_system_event(
            source="scraper",
            action="apify_actor_failed",
            detail=human,
            level="error",
        )
        return

    if status == "SUCCEEDED":
        human = "[INFO] No businesses found in geo-range."
        _set_fetch_meta(
            source="apify",
            reason="empty_search_geo",
            detail=f"{human} (niche={niche!r} location={location!r})",
        )
        log_system_event(
            source="scraper",
            action="apify_empty_search",
            detail=f"{human} Target {niche} @ {location}",
            level="info",
        )
        return

    human = f"[WARN] Empty dataset with unexpected Apify status={status} message={raw_msg[:300]}"
    _set_fetch_meta(source="apify", reason="empty_dataset_unknown", detail=human)
    log_system_event(
        source="scraper",
        action="apify_empty_unknown",
        detail=human,
        level="warn",
    )


@dataclass
class ScrapedLead:
    business_name: str
    street_address: str | None = None
    street_name: str | None = None
    phone: str | None = None
    email: str | None = None
    website_url: str | None = None
    niche: str = "HVAC"
    location: str = "United States"
    owner_name: str | None = None


def extract_street_name(street_address: str | None) -> str | None:
    if not street_address:
        return None
    addr = street_address.strip()
    # "1234 Market St, Any City, ST 12345" -> "Market St"
    first = addr.split(",")[0].strip()
    first = re.sub(r"^\d+\s*", "", first)
    first = re.sub(r"\s+", " ", first).strip()
    return first or None


def clean_business_name(name: str | None) -> str:
    if not name:
        return "Unknown Business"
    cleaned = re.sub(r"\s+", " ", name).strip()
    # remove suffix noise often added by map entries
    cleaned = re.sub(r"\s+\|\s+.*$", "", cleaned)
    return cleaned[:255] if cleaned else "Unknown Business"


RANDOM_NICHES = [
    "HVAC",
    "Roofing",
    "Solar Installation",
    "Landscaping",
    "Paving",
    "Commercial Plumbing",
    "Concrete Services",
    "Electrical Services",
    "General Contracting",
    "Kitchen Remodeling",
    "Bathroom Remodeling",
    "Foundation Repair",
    "Water Damage Restoration",
    "Tree Services",
    "Pool Installation",
    "Garage Door Services",
    "Pest Control",
    "Window Replacement",
    "Commercial Cleaning",
    "Flooring Installation",
]

RANDOM_CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ",
    "Philadelphia, PA", "San Diego, CA", "Dallas, TX", "San Jose, CA", "Austin, TX",
    "Jacksonville, FL", "Fort Worth, TX", "Columbus, OH", "Charlotte, NC", "Indianapolis, IN",
    "San Francisco, CA", "Seattle, WA", "Denver, CO", "Washington, DC", "Boston, MA",
    "Nashville, TN", "El Paso, TX", "Detroit, MI", "Oklahoma City, OK", "Portland, OR",
    "Las Vegas, NV", "Memphis, TN", "Louisville, KY", "Baltimore, MD", "Milwaukee, WI",
    "Albuquerque, NM", "Tucson, AZ", "Fresno, CA", "Mesa, AZ", "Sacramento, CA",
    "Atlanta, GA", "Kansas City, MO", "Colorado Springs, CO", "Miami, FL", "Raleigh, NC",
    "Omaha, NE", "Long Beach, CA", "Virginia Beach, VA", "Oakland, CA", "Minneapolis, MN",
    "Tulsa, OK", "Arlington, TX", "New Orleans, LA", "Wichita, KS", "Cleveland, OH",
    "Tampa, FL", "Bakersfield, CA", "Aurora, CO", "Honolulu, HI", "Anaheim, CA",
    "Santa Ana, CA", "Riverside, CA", "Corpus Christi, TX", "Lexington, KY", "Stockton, CA",
    "Henderson, NV", "Saint Paul, MN", "St. Louis, MO", "Cincinnati, OH", "Pittsburgh, PA",
    "Greensboro, NC", "Anchorage, AK", "Plano, TX", "Lincoln, NE", "Orlando, FL",
    "Irvine, CA", "Newark, NJ", "Durham, NC", "Chula Vista, CA", "Toledo, OH",
    "Fort Wayne, IN", "St. Petersburg, FL", "Laredo, TX", "Jersey City, NJ", "Chandler, AZ",
    "Madison, WI", "Lubbock, TX", "Scottsdale, AZ", "Reno, NV", "Buffalo, NY",
    "Gilbert, AZ", "Glendale, AZ", "North Las Vegas, NV", "Winston-Salem, NC", "Chesapeake, VA",
    "Norfolk, VA", "Fremont, CA", "Garland, TX", "Irving, TX", "Hialeah, FL",
    "Richmond, VA", "Boise, ID", "Spokane, WA", "Baton Rouge, LA", "Des Moines, IA",
]


def get_random_target(
    *,
    last_location: str | None = None,
    last_niche: str | None = None,
) -> dict[str, str]:
    niche_pool = [n for n in RANDOM_NICHES if n != (last_niche or "").strip()] or RANDOM_NICHES
    city_pool = [c for c in RANDOM_CITIES if c != (last_location or "").strip()] or RANDOM_CITIES
    return {
        "niche": random.choice(niche_pool),
        "location": random.choice(city_pool),
    }


def _sample_leads(niche: str, location: str, limit: int) -> list[ScrapedLead]:
    streets = ["Market St", "Central Ave", "Broadway", "Commerce Blvd", "Main St"]
    out: list[ScrapedLead] = []
    for i in range(1, limit + 1):
        st = streets[(i - 1) % len(streets)]
        out.append(
            ScrapedLead(
                business_name=f"{niche} Prospect {i:02d}",
                street_address=f"{1000 + i} {st}, {location}",
                street_name=st,
                phone=f"210-555-{1000+i}",
                email=f"info{i:02d}@example.com",
                website_url=f"https://prospect-{i:02d}.example.com",
                niche=niche,
                location=location,
            )
        )
    return out


def _apify_run_input(niche: str, location: str, limit: int, postal_codes: list[str] | None) -> dict[str, Any]:
    """
    Keep search strings niche-only; geography is anchored by locationQuery to avoid
    double-location / over-constrained queries.
    """
    niche_clean = (niche or "").strip() or "business"
    search_strings = [niche_clean]
    for extra in (f"best {niche_clean}", f"{niche_clean} services"):
        if extra not in search_strings:
            search_strings.append(extra)
    zips = postal_codes or []
    if zips:
        for z in zips:
            q = f"{niche_clean} {z}".strip()
            if q not in search_strings:
                search_strings.append(q)
    return {
        "searchStringsArray": search_strings,
        "locationQuery": location,
        "maxCrawledPlacesPerSearch": max(1, limit),
        "language": "en",
        "includeWebResults": False,
    }


def fetch_business_leads(
    niche: str = "HVAC",
    location: str = "United States",
    limit: int = 20,
    postal_codes: list[str] | None = None,
) -> list[ScrapedLead]:
    """
    Real source: Apify Google Maps actor.
    Fallback: deterministic sample leads for safe simulation.
    """
    limit = max(1, min(500, int(limit)))
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    actor_id = os.getenv("APIFY_GOOGLE_MAPS_ACTOR_ID", "compass/google-maps-scraper").strip()
    allow_sample = os.getenv("ALLOW_SAMPLE_LEADS", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not token:
        if allow_sample:
            _set_fetch_meta(
                source="sample",
                reason="missing_apify_token_sample_used",
                detail="APIFY_API_TOKEN missing; deterministic sample leads returned.",
            )
            return _sample_leads(niche, location, limit)
        _set_fetch_meta(
            source="apify",
            reason="missing_apify_token",
            detail="APIFY_API_TOKEN missing and ALLOW_SAMPLE_LEADS disabled.",
        )
        return []

    run_input = _apify_run_input(niche=niche, location=location, limit=limit, postal_codes=postal_codes)
    path_actor = _apify_path_actor(actor_id)
    run_url = f"https://api.apify.com/v2/acts/{path_actor}/run-sync-get-dataset-items"
    try:
        resp = requests.post(
            run_url,
            params={"token": token, "format": "json", "clean": "true"},
            json=run_input,
            timeout=120,
        )
        raw = resp.json()
        if resp.status_code >= 300:
            detail = f"Apify HTTP {resp.status_code} for actor={actor_id} body={str(raw)[:300]}"
            if allow_sample:
                _set_fetch_meta(
                    source="sample",
                    reason="apify_http_error_sample_used",
                    detail=detail,
                )
                return _sample_leads(niche, location, limit)
            _set_fetch_meta(source="apify", reason="apify_http_error", detail=detail)
            return []

        if isinstance(raw, dict) and raw.get("error"):
            detail = str(raw.get("error"))[:500]
            if allow_sample:
                _set_fetch_meta(source="sample", reason="apify_error_body_sample_used", detail=detail)
                return _sample_leads(niche, location, limit)
            _set_fetch_meta(source="apify", reason="apify_error_body", detail=detail)
            return []

        items = raw if isinstance(raw, list) else []
    except Exception as exc:
        if allow_sample:
            _set_fetch_meta(
                source="sample",
                reason="apify_exception_sample_used",
                detail=str(exc)[:500],
            )
            return _sample_leads(niche, location, limit)
        _set_fetch_meta(
            source="apify",
            reason="apify_exception",
            detail=str(exc)[:500],
        )
        return []

    if not items:
        _diagnose_empty_dataset(token, actor_id, niche, location)
        if allow_sample:
            _set_fetch_meta(
                source="sample",
                reason="apify_empty_items_sample_used",
                detail=f"No dataset items; queries={run_input.get('searchStringsArray', [])}",
            )
            return _sample_leads(niche, location, limit)
        return []

    out: list[ScrapedLead] = []
    for item in items:
        if len(out) >= limit:
            break
        name = (item.get("title") or item.get("name") or "").strip()
        if not name:
            continue
        street_address = (
            item.get("address")
            or item.get("streetAddress")
            or item.get("fullAddress")
            or ""
        )
        phone = (item.get("phone") or item.get("phoneNumber") or "").strip() or None
        website = (item.get("website") or item.get("websiteUrl") or "").strip() or None
        email = (item.get("email") or "").strip() or None
        out.append(
            ScrapedLead(
                business_name=clean_business_name(name),
                street_address=street_address or None,
                street_name=extract_street_name(street_address),
                phone=phone,
                email=email,
                website_url=website,
                niche=niche,
                location=location,
            )
        )
    if out:
        _set_fetch_meta(
            source="apify",
            reason="ok",
            detail=f"Fetched {len(out)} normalized leads from Apify.",
        )
        return out
    if allow_sample:
        _set_fetch_meta(
            source="sample",
            reason="apify_zero_valid_names_sample_used",
            detail="Apify returned items but none had usable business names.",
        )
        return _sample_leads(niche, location, limit)
    _set_fetch_meta(
        source="apify",
        reason="apify_zero_valid_names",
        detail="Apify returned items but none had usable business names.",
    )
    log_system_event(
        source="scraper",
        action="apify_unusable_rows",
        detail="[WARN] Apify returned dataset rows but none had a usable business title field.",
        level="warn",
    )
    return []


__all__ = [
    "ScrapedLead",
    "fetch_business_leads",
    "extract_street_name",
    "clean_business_name",
    "get_random_target",
    "get_last_fetch_meta",
]
