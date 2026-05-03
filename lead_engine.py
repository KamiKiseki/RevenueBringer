from __future__ import annotations

from dataclasses import asdict, dataclass

from scraper import get_last_fetch_meta, get_random_target
from scout import ScrapedLead, fetch_hvac_leads, upsert_scraped_leads


@dataclass
class LeadEngineResult:
    generated: int
    inserted: int
    skipped_duplicates: int
    niche: str
    location: str
    source: str = "unknown"
    zero_result_reason: str = ""


def generate_leads(niche: str | None, amount: int, location: str | None = None) -> LeadEngineResult:
    """
    Real lead engine entry point.
    - pulls from Google maps when API key is present
    - falls back to generated realistic samples for safe testing
    - deduplicates via DB upsert
    """
    amount = max(1, min(1000, int(amount)))
    if not (niche and location):
        target = get_random_target()
        niche = target["niche"]
        location = target["location"]
    scraped: list[ScrapedLead] = fetch_hvac_leads(niche=niche, location=location, limit=amount)
    inserted, skipped = upsert_scraped_leads(scraped)
    fetch_meta = get_last_fetch_meta()
    reason = ""
    if len(scraped) == 0:
        reason = fetch_meta.get("reason") or "no_scraped_results"
        extra = (fetch_meta.get("detail") or "").strip()
        if extra:
            reason = f"{reason} | {extra[:400]}"
    elif inserted == 0 and skipped > 0:
        reason = "all_scraped_results_were_duplicates"
    elif inserted == 0:
        reason = "scraped_results_not_inserted_unknown_reason"
    return LeadEngineResult(
        generated=len(scraped),
        inserted=inserted,
        skipped_duplicates=skipped,
        niche=niche,
        location=location,
        source=(fetch_meta.get("source") or "unknown"),
        zero_result_reason=reason,
    )


def generate_leads_dict(niche: str | None, amount: int, location: str | None = None) -> dict:
    return asdict(generate_leads(niche=niche, amount=amount, location=location))

