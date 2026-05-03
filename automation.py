from __future__ import annotations

import os
import random
import smtplib
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText

from lead_engine import generate_leads
from scraper import get_random_target
from followup import process_due_followups
from models import (
    Agreement,
    AgreementStatus,
    AutomationRun,
    Lead,
    LeadStatus,
    get_session,
    get_setting,
    init_db,
    log_system_event,
    set_setting,
)
from outreach import process_queued_leads
from tracking import compute_metrics, create_and_send_daily_report


def _smtp_alert(subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = os.getenv("SMTP_FROM_EMAIL", username).strip()
    to_email = os.getenv("REPORT_EMAIL_TO", "").strip()
    if not (host and username and password and from_email and to_email):
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    with smtplib.SMTP(host=host, port=port, timeout=25) as server:
        server.starttls()
        server.login(username, password)
        server.sendmail(from_email, [to_email], msg.as_string())


def _simulate_conversions(max_count: int = 2) -> int:
    converted = 0
    with get_session() as session:
        candidates = (
            session.query(Lead)
            .filter(Lead.status == LeadStatus.EMAILED)
            .order_by(Lead.updated_at.asc())
            .limit(20)
            .all()
        )
        random.shuffle(candidates)
        for lead in candidates:
            if converted >= max_count:
                break
            if random.random() > 0.25:
                continue
            lead.status = LeadStatus.ACTIVE_CLIENT
            ag = (
                session.query(Agreement)
                .filter(Agreement.correlation_id == lead.correlation_id)
                .first()
            )
            if ag is None:
                ag = Agreement(
                    client_name=lead.owner_name or lead.business_name,
                    client_email=lead.email,
                    business_name=lead.business_name,
                    correlation_id=lead.correlation_id,
                    lead_id=lead.id,
                    signing_status=AgreementStatus.PAID,
                    offer_kind="trial_14",
                    stripe_plan_amount_cents=30000,
                )
                session.add(ag)
            else:
                ag.signing_status = AgreementStatus.PAID
                if not ag.stripe_plan_amount_cents:
                    ag.stripe_plan_amount_cents = 30000
            session.add(lead)
            converted += 1
        session.commit()
    return converted


def run_cycle(
    *,
    niche: str | None,
    location: str | None,
    daily_target: int,
    simulate: bool = False,
    send_report: bool = False,
) -> dict:
    started = datetime.now(timezone.utc)
    run_id = None
    try:
        chosen_niche = niche
        chosen_location = location
        if not chosen_niche or not chosen_location:
            target = get_random_target(
                last_location=get_setting("last_location", ""),
                last_niche=get_setting("last_niche", ""),
            )
            chosen_niche = target["niche"]
            chosen_location = target["location"]
            set_setting("last_niche", chosen_niche)
            set_setting("last_location", chosen_location)

        log_system_event(
            source="automation",
            action="cycle_started",
            detail=(
                f"Run started mode={'simulate' if simulate else 'live'} "
                f"target={chosen_niche} @ {chosen_location} daily_target={daily_target}"
            ),
            level="info",
        )

        with get_session() as session:
            row = AutomationRun(started_at=started, mode="simulate" if simulate else "live")
            session.add(row)
            session.commit()
            run_id = row.id

        # Step 1: lead generation
        lead_result = generate_leads(niche=chosen_niche, amount=daily_target, location=chosen_location)
        log_system_event(
            source="lead_engine",
            action="ingestion_completed",
            detail=(
                f"Target {lead_result.niche} @ {lead_result.location} | "
                f"generated={lead_result.generated} inserted={lead_result.inserted} "
                f"skipped_duplicates={lead_result.skipped_duplicates} "
                f"source={lead_result.source} reason={lead_result.zero_result_reason or 'none'}"
            ),
            level="info",
        )
        if lead_result.generated == 0 or lead_result.inserted == 0:
            log_system_event(
                source="lead_engine",
                action="ingestion_zero_result_reason",
                detail=(
                    f"Target {lead_result.niche} @ {lead_result.location} | "
                    f"generated={lead_result.generated} inserted={lead_result.inserted} "
                    f"source={lead_result.source} reason={lead_result.zero_result_reason or 'none'}"
                ),
                level="warn",
            )

        # Step 2: outreach
        outreach_sent = process_queued_leads(simulate=simulate)
        log_system_event(
            source="outreach",
            action="dispatch_completed",
            detail=f"Outreach dispatched count={outreach_sent}",
            level="info",
        )

        # Step 3: follow-ups
        followups_sent = process_due_followups(simulate=simulate)

        # Step 4: optional simulation of conversion flow
        simulated_conversions = _simulate_conversions() if simulate else 0

        metrics = compute_metrics()
        if send_report:
            create_and_send_daily_report(send_email=True)

        with get_session() as session:
            row = session.get(AutomationRun, run_id)
            if row:
                row.ended_at = datetime.now(timezone.utc)
                row.status = "ok"
                row.scraped_count = int(lead_result.generated)
                row.inserted_count = int(lead_result.inserted)
                # Legacy column: historically used as “rows committed” after dedupe.
                row.leads_generated = int(lead_result.inserted)
                row.outreach_sent = int(outreach_sent)
                row.followups_sent = int(followups_sent)
                row.conversions = int(simulated_conversions)
                row.revenue_cents = int(metrics.get("revenue_cents") or 0)
                session.add(row)
                session.commit()

        log_system_event(
            source="automation",
            action="cycle_completed",
            detail=(
                f"Run {run_id} complete | inserted={lead_result.inserted} "
                f"outreach_sent={outreach_sent} followups_sent={followups_sent} "
                f"revenue_cents={int(metrics.get('revenue_cents') or 0)}"
            ),
            level="info",
        )

        return {
            "ok": True,
            "run_id": run_id,
            "lead_engine": {
                "generated": lead_result.generated,
                "inserted": lead_result.inserted,
                "skipped_duplicates": lead_result.skipped_duplicates,
                "niche": lead_result.niche,
                "location": lead_result.location,
                "source": lead_result.source,
                "zero_result_reason": lead_result.zero_result_reason,
            },
            "outreach_sent": outreach_sent,
            "followups_sent": followups_sent,
            "simulated_conversions": simulated_conversions,
            "metrics": metrics,
        }
    except Exception as exc:
        with get_session() as session:
            if run_id:
                row = session.get(AutomationRun, run_id)
                if row:
                    row.ended_at = datetime.now(timezone.utc)
                    row.status = "failed"
                    row.error_count = int(row.error_count or 0) + 1
                    row.notes = (row.notes or "") + f"\n{exc}"
                    session.add(row)
                    session.commit()
        try:
            _smtp_alert("AutoYield automation error", str(exc))
        except Exception:
            pass
        log_system_event(
            source="automation",
            action="cycle_failed",
            detail=str(exc),
            level="error",
        )
        return {"ok": False, "error": str(exc), "run_id": run_id}


def start_automation() -> None:
    set_setting("automation_enabled", "1")


def stop_automation() -> None:
    set_setting("automation_enabled", "0")


def automation_status() -> dict:
    return {
        "enabled": get_setting("automation_enabled", "0") == "1",
        "niche": get_setting("last_niche", ""),
        "location": get_setting("last_location", ""),
        "daily_target": int(get_setting("last_lead_count", "50")),
        "simulate": get_setting("simulate_mode", "1") == "1",
    }


def run_loop(interval_seconds: int = 900) -> None:
    init_db()
    print(f"[AUTOMATION] loop started, interval={interval_seconds}s")
    while True:
        enabled = get_setting("automation_enabled", "0") == "1"
        if not enabled:
            time.sleep(3)
            continue
        niche = get_setting("last_niche", "").strip() or None
        location = get_setting("last_location", "").strip() or None
        daily_target = int(get_setting("last_lead_count", "50"))
        simulate = get_setting("simulate_mode", "1") == "1"
        send_report = get_setting("daily_report_enabled", "1") == "1"
        res = run_cycle(
            niche=niche,
            location=location,
            daily_target=daily_target,
            simulate=simulate,
            send_report=send_report,
        )
        print("[AUTOMATION] cycle:", res)
        time.sleep(max(30, interval_seconds))


if __name__ == "__main__":
    run_loop()

