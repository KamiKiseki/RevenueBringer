from __future__ import annotations

import os
import time
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

import requests

from models import (
    FollowUpJob,
    Lead,
    LeadStatus,
    MessageEvent,
    MessageStatus,
    SuppressionEntry,
    get_session,
    init_db,
)
from templates import build_ceo_outreach_templates

INSTANTLY_API_URL = "https://api.instantly.ai/api/v2/leads"


def push_lead_to_instantly(lead: Lead) -> tuple[bool, str]:
    api_key = os.getenv("INSTANTLY_API_KEY", "")
    campaign_id = os.getenv("INSTANTLY_CAMPAIGN_ID", "")
    if not api_key or not campaign_id:
        return False, "missing_instantly_config"

    payload = {
        "campaign_id": campaign_id,
        "email": (lead.email or "").strip(),
        "first_name": (lead.owner_name or lead.business_name).split(" ")[0],
        "last_name": "",
        "phone": lead.phone or "",
        "company_name": lead.business_name,
        "website": lead.website or "",
        "custom_variables": {"niche": lead.niche, "lead_id": str(lead.id)},
    }
    if not payload["email"]:
        return False, "missing_email_required_for_instantly"
    # Instantly auth varies across accounts/endpoints. Try common header formats.
    header_options = [
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        {"Authorization": api_key, "Content-Type": "application/json"},
        {"X-Api-Key": api_key, "Content-Type": "application/json"},
        {"x-api-key": api_key, "Content-Type": "application/json"},
    ]
    errors: list[str] = []
    for headers in header_options:
        response = requests.post(INSTANTLY_API_URL, headers=headers, json=payload, timeout=20)
        if response.status_code < 300:
            return True, "sent"
        body = (response.text or "").strip()
        errors.append(f"{response.status_code}:{body[:120]}")
    detail = " | ".join(errors)[:240]
    if not detail:
        detail = "instantly_push_failed"
    return False, detail


def _ensure_correlation(lead: Lead) -> str:
    from uuid import uuid4

    if not lead.correlation_id:
        lead.correlation_id = uuid4().hex
    return lead.correlation_id


def is_suppressed(lead: Lead) -> bool:
    with get_session() as session:
        q = session.query(SuppressionEntry).filter(SuppressionEntry.active == True)  # noqa: E712
        if lead.correlation_id:
            if q.filter(SuppressionEntry.correlation_id == lead.correlation_id).first():
                return True
        if lead.email:
            if q.filter(SuppressionEntry.email == lead.email).first():
                return True
        if lead.phone:
            if q.filter(SuppressionEntry.phone == lead.phone).first():
                return True
    return False


def _smtp_send(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = os.getenv("SMTP_FROM_EMAIL", username).strip()

    if not (host and username and password and from_email and to_email):
        return False, "smtp_not_configured"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP(host=host, port=port, timeout=25) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True, "sent"
    except Exception as exc:
        return False, str(exc)


def _log_message(
    lead: Lead,
    channel: str,
    status: MessageStatus,
    subject: str | None = None,
    body: str | None = None,
    error: str | None = None,
) -> None:
    with get_session() as session:
        evt = MessageEvent(
            correlation_id=_ensure_correlation(lead),
            lead_id=lead.id,
            channel=channel,
            direction="outbound",
            status=status,
            subject=subject,
            body=body,
            error=error,
        )
        session.add(evt)
        session.commit()


def send_outreach_email(lead: Lead, simulate: bool = False) -> tuple[bool, str]:
    _ensure_correlation(lead)
    tmpl = build_ceo_outreach_templates(
        lead_name=lead.owner_name or "",
        business_name=lead.business_name,
        correlation_id=lead.correlation_id,
    )
    if simulate:
        _log_message(lead, "email", MessageStatus.SENT, tmpl.email_subject, tmpl.email_body)
        return True, "simulated"

    to_email = (lead.email or "").strip()
    if not to_email:
        _log_message(
            lead,
            "email",
            MessageStatus.FAILED,
            tmpl.email_subject,
            tmpl.email_body,
            "missing_email",
        )
        return False, "missing_email"

    ok, info = _smtp_send(to_email, tmpl.email_subject, tmpl.email_body)
    if ok:
        _log_message(lead, "email", MessageStatus.SENT, tmpl.email_subject, tmpl.email_body)
    else:
        _log_message(lead, "email", MessageStatus.FAILED, tmpl.email_subject, tmpl.email_body, info)
    return ok, info


def _enqueue_followup(lead: Lead, delay_days: int = 2) -> None:
    with get_session() as session:
        exists = (
            session.query(FollowUpJob)
            .filter(
                FollowUpJob.lead_id == lead.id,
                FollowUpJob.status == "pending",
                FollowUpJob.step == 1,
            )
            .first()
        )
        if exists:
            return
        job = FollowUpJob(
            lead_id=lead.id,
            correlation_id=_ensure_correlation(lead),
            step=1,
            status="pending",
            due_at=datetime.now(timezone.utc) + timedelta(days=delay_days),
        )
        session.add(job)
        session.commit()


def process_queued_leads(simulate: bool = False) -> int:
    daily_cap = int(os.getenv("OUTREACH_DAILY_CAP", "50") or "50")
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_count = 0
    transport = (os.getenv("OUTREACH_TRANSPORT", "instantly").strip().lower() or "instantly")
    smtp_allowed = transport in {"smtp", "hybrid"}
    instantly_allowed = transport in {"instantly", "hybrid"}
    with get_session() as session:
        already_sent_today = (
            session.query(MessageEvent)
            .filter(
                MessageEvent.direction == "outbound",
                MessageEvent.status == MessageStatus.SENT,
                MessageEvent.created_at >= day_start,
            )
            .count()
        )
        remaining_today = max(0, daily_cap - int(already_sent_today))
        if remaining_today <= 0:
            return 0
        queued = session.query(Lead).filter(Lead.status == LeadStatus.QUEUED).all()
        for lead in queued:
            if sent_count >= remaining_today:
                break
            if is_suppressed(lead):
                continue
            ok_instantly = False
            instantly_info = "disabled"
            if instantly_allowed:
                try:
                    ok_instantly, instantly_info = push_lead_to_instantly(lead)
                except Exception as exc:
                    ok_instantly = False
                    instantly_info = str(exc)
                _log_message(
                    lead,
                    "instantly",
                    MessageStatus.SENT if ok_instantly else MessageStatus.FAILED,
                    "Instantly lead push",
                    None,
                    None if ok_instantly else instantly_info,
                )

            ok_email = False
            if smtp_allowed:
                ok_email, _ = send_outreach_email(lead, simulate=simulate)

            ok = ok_instantly or ok_email or simulate
            if ok:
                lead.status = LeadStatus.EMAILED
                session.add(lead)
                sent_count += 1
                _enqueue_followup(lead)
        session.commit()
    return sent_count


def run_loop(interval_seconds: int = 3600, simulate: bool = False) -> None:
    init_db()
    print(f"[OUTREACH] Running. Interval={interval_seconds}s")
    while True:
        try:
            sent = process_queued_leads(simulate=simulate)
            print(f"[OUTREACH] Sent {sent} queued leads to Instantly.")
        except Exception as exc:
            print(f"[OUTREACH] Error: {exc}")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_loop()
