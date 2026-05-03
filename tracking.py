from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from models import Agreement, AgreementStatus, DailyReport, Lead, LeadStatus, MessageEvent, MessageStatus, get_session


def compute_metrics() -> dict[str, int]:
    with get_session() as session:
        total_leads = session.query(Lead).count()
        contacted = session.query(Lead).filter(Lead.status != LeadStatus.QUEUED).count()
        replies = session.query(MessageEvent).filter(MessageEvent.status == MessageStatus.REPLIED).count()
        conversions = (
            session.query(Lead)
            .filter(Lead.status.in_([LeadStatus.PAID, LeadStatus.ACTIVE_CLIENT]))
            .count()
        )
        revenue_cents = (
            session.query(Agreement)
            .filter(
                Agreement.signing_status == AgreementStatus.PAID,
                Agreement.stripe_transaction_id.isnot(None),
            )
            .with_entities(Agreement.stripe_plan_amount_cents)
            .all()
        )
        total_revenue = sum(int(x[0] or 0) for x in revenue_cents)
    return {
        "total_leads": int(total_leads),
        "contacted": int(contacted),
        "replies": int(replies),
        "conversions": int(conversions),
        "revenue_cents": int(total_revenue),
    }


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


def build_daily_report_text(metrics: dict[str, int] | None = None) -> tuple[str, str]:
    m = metrics or compute_metrics()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"AutoYield Daily Report - {date_str}"
    body = (
        f"Daily report ({date_str})\n\n"
        f"Total leads: {m['total_leads']}\n"
        f"Contacted: {m['contacted']}\n"
        f"Replies: {m['replies']}\n"
        f"Conversions: {m['conversions']}\n"
        f"Revenue: ${m['revenue_cents'] / 100:.2f}\n"
    )
    return subject, body


def create_and_send_daily_report(send_email: bool = True) -> dict:
    metrics = compute_metrics()
    subject, body = build_daily_report_text(metrics)
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = os.getenv("REPORT_EMAIL_TO", "").strip()
    status = "generated"

    if send_email and target:
        ok, info = _smtp_send(target, subject, body)
        status = "sent" if ok else f"failed:{info}"

    with get_session() as session:
        row = DailyReport(
            report_date=report_date,
            subject=subject,
            body=body,
            email_to=target or None,
            status=status,
        )
        session.add(row)
        session.commit()
        rid = row.id

    return {"report_id": rid, "status": status, "metrics": metrics, "subject": subject}

