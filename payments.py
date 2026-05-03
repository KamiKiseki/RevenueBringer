from __future__ import annotations

from models import Agreement, AgreementStatus, Lead, LeadStatus, get_session


def mark_paid_by_correlation(correlation_id: str, tx_id: str = "manual") -> bool:
    with get_session() as db:
        agreement = db.query(Agreement).filter(Agreement.correlation_id == correlation_id).first()
        lead = db.query(Lead).filter(Lead.correlation_id == correlation_id).first()
        changed = False
        if agreement:
            agreement.signing_status = AgreementStatus.PAID
            agreement.stripe_transaction_id = tx_id
            db.add(agreement)
            changed = True
        if lead:
            lead.status = LeadStatus.ACTIVE_CLIENT
            db.add(lead)
            changed = True
        db.commit()
        return changed

