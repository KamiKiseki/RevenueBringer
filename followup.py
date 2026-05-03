from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import FollowUpJob, Lead, LeadStatus, MessageStatus, get_session
from outreach import _log_message, _smtp_send, is_suppressed

MAX_FOLLOWUP_STEPS = 3


def _followup_subject(lead: Lead, step: int) -> str:
    return f"Quick follow-up #{step} for {lead.business_name}"


def _followup_body(lead: Lead, step: int) -> str:
    return (
        f"Hi {lead.owner_name or 'there'},\n\n"
        f"This is a quick follow-up from the Operations Team at AutoYield Systems regarding {lead.business_name}. "
        "If you want us to activate your lead flow, reply YES.\n\n"
        f"Follow-up step: {step}\n"
        f"Ref: {lead.correlation_id}\n"
    )


def process_due_followups(simulate: bool = False) -> int:
    now = datetime.now(timezone.utc)
    sent = 0
    with get_session() as session:
        jobs = (
            session.query(FollowUpJob)
            .filter(FollowUpJob.status == "pending", FollowUpJob.due_at <= now)
            .all()
        )
        for job in jobs:
            lead = session.get(Lead, job.lead_id)
            if lead is None:
                job.status = "cancelled"
                session.add(job)
                continue
            if lead.status in (LeadStatus.PAID, LeadStatus.ACTIVE_CLIENT):
                job.status = "cancelled"
                session.add(job)
                continue
            if is_suppressed(lead):
                job.status = "cancelled"
                session.add(job)
                continue

            subject = _followup_subject(lead, job.step)
            body = _followup_body(lead, job.step)

            if simulate:
                ok, info = True, "simulated"
            else:
                if not lead.email:
                    ok, info = False, "missing_email"
                else:
                    ok, info = _smtp_send(lead.email, subject, body)

            if ok:
                _log_message(lead, "email", MessageStatus.SENT, subject, body)
                job.status = "sent"
                job.sent_at = now
                sent += 1
                if job.step < MAX_FOLLOWUP_STEPS:
                    session.add(
                        FollowUpJob(
                            lead_id=lead.id,
                            correlation_id=lead.correlation_id,
                            step=job.step + 1,
                            status="pending",
                            due_at=now + timedelta(days=2),
                        )
                    )
            else:
                _log_message(lead, "email", MessageStatus.FAILED, subject, body, info)
                job.last_error = info
                # retry tomorrow
                job.due_at = now + timedelta(days=1)

            session.add(job)
        session.commit()
    return sent

