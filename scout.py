from __future__ import annotations

from dataclasses import dataclass

from models import Lead, LeadStatus, get_session, init_db
from scraper import clean_business_name, extract_street_name, fetch_business_leads


@dataclass
class ScrapedLead:
    business_name: str
    niche: str = "HVAC"
    location: str = "United States"
    street_address: str | None = None
    street_name: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    owner_name: str | None = None

def fetch_hvac_leads(niche: str = "HVAC", location: str = "United States", limit: int = 20) -> list[ScrapedLead]:
    raw = fetch_business_leads(niche=niche, location=location, limit=limit)
    out: list[ScrapedLead] = []
    for item in raw:
        out.append(
            ScrapedLead(
                business_name=clean_business_name(item.business_name),
                niche=item.niche,
                location=item.location,
                street_address=item.street_address,
                street_name=item.street_name or extract_street_name(item.street_address),
                website=item.website_url,
                phone=item.phone,
                email=item.email,
                owner_name=item.owner_name,
            )
        )
    return out


def upsert_scraped_leads(leads: list[ScrapedLead]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    with get_session() as session:
        for lead in leads:
            clean_name = clean_business_name(lead.business_name)
            exists = session.query(Lead).filter(Lead.business_name == lead.business_name).first()
            if not exists:
                exists = session.query(Lead).filter(Lead.business_name == clean_name).first()
            if exists:
                skipped += 1
                continue
            session.add(
                Lead(
                    business_name=clean_name,
                    status=LeadStatus.QUEUED,
                    email=lead.email,
                    website=lead.website,
                    phone=lead.phone,
                    owner_name=lead.owner_name,
                    niche=lead.niche,
                    location=lead.location,
                    street_address=lead.street_address,
                    street_name=lead.street_name,
                )
            )
            inserted += 1
        session.commit()
    return inserted, skipped


def run(niche: str = "HVAC", location: str = "United States", limit: int = 20) -> None:
    init_db()
    scraped = fetch_hvac_leads(niche=niche, location=location, limit=limit)
    inserted, skipped = upsert_scraped_leads(scraped)
    print(
        f"[SCOUT] Niche={niche} Location={location} Scraped={len(scraped)} "
        f"Inserted={inserted} SkippedExisting={skipped}"
    )


if __name__ == "__main__":
    run(niche="HVAC", location="United States", limit=20)
