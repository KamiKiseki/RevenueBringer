from __future__ import annotations

import os
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import requests
import stripe
from flask import Flask, jsonify, redirect, request, send_file
from sqlalchemy.exc import OperationalError

from sqlalchemy.orm import Session

from models import (
    Agreement,
    AgreementStatus,
    AutomationRun,
    DailyReport,
    Lead,
    LeadStatus,
    MessageEvent,
    MessageStatus,
    SuppressionEntry,
    SystemLog,
    get_session,
    get_setting,
    init_db,
    log_system_event,
    set_setting,
)
from automation import (
    automation_status,
    run_cycle,
    start_automation,
    stop_automation,
)
from scraper import get_random_target
from tracking import compute_metrics, create_and_send_daily_report
from runtime_config import flask_debug_enabled
from templates import (
    DEFAULT_VAPI_SYSTEM_PROMPT,
    build_ceo_outreach_templates,
    build_proof_templates,
    build_service_agreement_text,
    build_tier_offer_templates,
)

_app_root = os.path.dirname(os.path.abspath(__file__))
_flask_stub = os.path.join(_app_root, "_flask_stub")
app = Flask(
    __name__,
    template_folder=os.path.join(_flask_stub, "templates"),
    static_folder=os.path.join(_flask_stub, "static"),
    static_url_path="/assets",
)

# Support both naming styles.
stripe.api_key = os.getenv("STRIPE_API_KEY", "") or os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# Public storefront — customers never see the private Command Center URL.
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://autoyieldsystems.com/success")
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://autoyieldsystems.com/pricing")
STRIPE_PRICE_TRIAL_300 = os.getenv("STRIPE_PRICE_TRIAL_300") or os.getenv("PRICE_TRIAL_300", "")
STRIPE_PRICE_MONTH_500 = os.getenv("STRIPE_PRICE_MONTH_500") or os.getenv("PRICE_MONTH_500", "")
FREE_HOOK_TARGET = int(os.getenv("FREE_HOOK_LEAD_TARGET", "2"))
VALUE_FIRST_FUNNEL = os.getenv("VALUE_FIRST_FUNNEL", "true").strip().lower() in {"1", "true", "yes", "on"}
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_CALL_WEBHOOK_URL = os.getenv("VAPI_CALL_WEBHOOK_URL", "")
VOICE_NOTIFICATION_MODE = os.getenv("VOICE_NOTIFICATION_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
PANDADOC_API_KEY = os.getenv("PANDADOC_API_KEY", "")
PANDADOC_TEMPLATE_ID = os.getenv("PANDADOC_TEMPLATE_ID", "VrZWq6WpiDVFMkh328wsb9")
PANDADOC_SENDER_EMAIL = os.getenv("PANDADOC_SENDER_EMAIL", "")
PANDADOC_API_URL = "https://api.pandadoc.com/public/v1/documents"


def deliver_paid_lead_package(lead: Lead) -> None:
    """Final automation placeholder after payment confirmation."""
    print(f"[DELIVERY] Delivering lead package to {lead.business_name} ({lead.email})")


def _ensure_lead_correlation(lead: Lead) -> str:
    if not lead.correlation_id:
        lead.correlation_id = uuid4().hex
    return lead.correlation_id


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _append_lead_log(lead: Lead, event_name: str, detail: str) -> None:
    cid = _ensure_lead_correlation(lead)
    line = f"[{_stamp()}] [{event_name}] correlation_id={cid} {detail}".strip()
    prev = (lead.notes or "").strip()
    lead.notes = f"{prev}\n{line}".strip() if prev else line
    print(f"[AUDIT] {line}")


def _append_agreement_log(agreement: Agreement, event_name: str, detail: str) -> None:
    cid = (agreement.correlation_id or "").strip()
    line = f"[{_stamp()}] [{event_name}] correlation_id={cid} {detail}".strip()
    prev = (agreement.audit_notes or "").strip()
    agreement.audit_notes = f"{prev}\n{line}".strip() if prev else line
    print(f"[AUDIT] {line}")


def _record_error(source: str, action: str, exc: Exception, correlation_id: str | None = None) -> None:
    log_system_event(
        source=source,
        action=action,
        detail=str(exc),
        level="error",
        correlation_id=correlation_id,
    )


def _send_ceo_outreach(lead: Lead) -> dict[str, str]:
    templates = build_ceo_outreach_templates(
        lead_name=lead.owner_name or "",
        business_name=lead.business_name,
        correlation_id=_ensure_lead_correlation(lead),
        street_name=getattr(lead, "street_name", None),
    )
    # Replace with real provider APIs when keys are available.
    _append_lead_log(lead, "TEXT", f"SMS drafted from Operations Team to {lead.phone or 'unknown phone'}")
    _append_lead_log(lead, "EMAIL", f"Email drafted to {lead.email or 'unknown email'}; subject='{templates.email_subject}'")
    if VOICE_NOTIFICATION_MODE:
        voice_prompt = (
            "This is Elliot from the Operations Team at AutoYield Systems. "
            f"We are currently managing service distribution in {lead.location or 'your market'}. "
            f"We have two high-intent leads on {lead.street_name or 'your service corridor'} ready for dispatch to your firm. "
            "Check your email for dispatch details."
        )
        _trigger_vapi_call(lead, voice_prompt, purpose="lead_notification")
    lead.status = LeadStatus.EMAILED
    return {
        "sms": templates.sms,
        "email_subject": templates.email_subject,
        "email_body": templates.email_body,
    }


def _normalize_offer_choice(raw: object | None) -> str | None:
    """Map inbound webhook payloads to canonical offer_kind: trial_14 | month_30."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace("$", "").replace(",", "")
    if s in {"trial_14", "trial", "14", "300", "trial300", "14_day", "14-day"}:
        return "trial_14"
    if s in {"month_30", "month", "30", "500", "month500", "full", "full_month"}:
        return "month_30"
    return None


def _offer_term_label(offer_kind: str) -> tuple[str, int, str]:
    """Human Term token, cents amount, Stripe-oriented label."""
    if offer_kind == "month_30":
        return "Full Month ($500)", 50000, "AutoYield Systems · Full Month Lead Flow ($500)"
    return "14-Day Trial ($300)", 30000, "AutoYield Systems · 14-Day Trial Lead Flow ($300)"


def _upsert_agreement_for_lead(db: Session, lead: Lead, offer_kind: str) -> Agreement:
    cid = _ensure_lead_correlation(lead)
    agreement = db.query(Agreement).filter(Agreement.correlation_id == cid).first()
    if agreement is None:
        agreement = Agreement(
            client_name=lead.owner_name or lead.business_name,
            client_email=lead.email,
            business_name=lead.business_name,
            correlation_id=cid,
            lead_id=lead.id,
            signing_status=AgreementStatus.DRAFT,
            offer_kind=offer_kind,
        )
        db.add(agreement)
        db.flush()
    else:
        agreement.offer_kind = offer_kind
        agreement.client_email = agreement.client_email or lead.email
        agreement.business_name = agreement.business_name or lead.business_name
        agreement.client_name = agreement.client_name or (lead.owner_name or lead.business_name)
        agreement.lead_id = agreement.lead_id or lead.id
    return agreement


def _trigger_vapi_call(lead: Lead, system_prompt: str, purpose: str = "general") -> None:
    _append_lead_log(lead, "CALL", f"Vapi escalation ({purpose})")
    if not (VAPI_API_KEY and VAPI_CALL_WEBHOOK_URL):
        return
    try:
        requests.post(
            VAPI_CALL_WEBHOOK_URL,
            json={
                "correlation_id": lead.correlation_id,
                "name": lead.owner_name or lead.business_name,
                "phone": lead.phone,
                "system_prompt": system_prompt,
                "purpose": purpose,
            },
            headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
            timeout=20,
        )
    except Exception as exc:
        _append_lead_log(lead, "CALL", f"Vapi trigger error ({purpose}): {exc}")
        _record_error("vapi", f"trigger_{purpose}", exc, correlation_id=lead.correlation_id)


def _trigger_tier_offer_vapi(lead: Lead) -> None:
    """After free proof leads, Elliot calls with the $300 vs $500 fork question."""
    offer = build_tier_offer_templates(
        lead_name=lead.owner_name or "",
        business_name=lead.business_name,
        correlation_id=_ensure_lead_correlation(lead),
    )
    _append_lead_log(lead, "TEXT", f"Tier-offer SMS drafted: {offer.sms[:120]}...")
    _append_lead_log(lead, "EMAIL", f"Tier-offer email drafted; subject='{offer.email_subject}'")
    pitch_prompt = (
        f"{DEFAULT_VAPI_SYSTEM_PROMPT} Use this script verbatim for the offer: {offer.call_script} "
        "When the owner decides, POST to /webhooks/offer_selection JSON with correlation_id and "
        "choice trial_14 or month_30 (aliases: trial/month, 300/500)."
    )
    _trigger_vapi_call(lead, pitch_prompt, purpose="tier_offer_pitch")


def _maybe_trigger_offer_after_hook(lead: Lead) -> None:
    if lead.leads_sent < FREE_HOOK_TARGET or lead.tier_offer_triggered:
        return
    lead.tier_offer_triggered = True
    _append_lead_log(
        lead,
        "AUTOMATION",
        f"Proof threshold met (leads_sent={lead.leads_sent} >= {FREE_HOOK_TARGET}); tier offer triggered",
    )
    _trigger_tier_offer_vapi(lead)


def _send_pandadoc_agreement(db: Session, lead: Lead, agreement: Agreement) -> Agreement:
    """Create/update PandaDoc from template; stamps Term + correlation_id for court-ready lineage."""
    correlation_id = _ensure_lead_correlation(lead)
    offer_kind = (agreement.offer_kind or "trial_14").strip().lower()
    term_text, amount_cents, _product_title = _offer_term_label(offer_kind)
    agreement.offer_kind = offer_kind
    agreement.stripe_plan_amount_cents = amount_cents
    billing_term = "month"
    if offer_kind == "trial_14":
        billing_term = "14-day trial"
    payment_method = "Stripe / PayPal"
    payment_link = agreement.stripe_checkout_url or STRIPE_SUCCESS_URL
    agreement_body = build_service_agreement_text(
        agreement_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        provider_name="Operations Team",
        provider_business_name="AutoYield Systems",
        provider_email=PANDADOC_SENDER_EMAIL or "operations@autoyieldsystems.com",
        client_business_name=lead.business_name or "Client Business",
        client_contact_name=lead.owner_name or lead.business_name or "Client Contact",
        client_email=lead.email or "client@example.com",
        client_phone=lead.phone or "N/A",
        amount_usd=int(amount_cents / 100),
        billing_term=billing_term,
        payment_method=payment_method,
        payment_link=payment_link,
    )
    lead.service_agreement_text = agreement_body

    if not (PANDADOC_API_KEY and PANDADOC_TEMPLATE_ID and lead.email):
        _append_lead_log(lead, "SIGN", "Agreement staged in draft mode (PandaDoc credentials missing)")
        db.add(agreement)
        return agreement

    payload = {
        "name": f"AutoYield Systems Service Agreement - {lead.business_name}",
        "template_uuid": PANDADOC_TEMPLATE_ID,
        "recipients": [
            {
                "email": lead.email,
                "first_name": (lead.owner_name or lead.business_name).split(" ")[0],
                "last_name": "",
                "role": "Client",
            }
        ],
        "tokens": [
            {"name": "client_name", "value": lead.owner_name or lead.business_name},
            {"name": "business_name", "value": lead.business_name},
            {"name": "correlation_id", "value": correlation_id},
            {"name": "Term", "value": term_text},
            {"name": "term", "value": term_text},
            {"name": "offer_kind", "value": offer_kind},
            {"name": "service_agreement_text", "value": agreement_body},
            {"name": "agreement_body", "value": agreement_body},
            {"name": "client_email", "value": lead.email or ""},
            {"name": "client_phone", "value": lead.phone or ""},
            {"name": "provider_name", "value": "Operations Team"},
            {"name": "provider_business_name", "value": "AutoYield Systems"},
            {"name": "provider_email", "value": PANDADOC_SENDER_EMAIL or "operations@autoyieldsystems.com"},
            {"name": "amount_usd", "value": str(int(amount_cents / 100))},
        ],
        "metadata": {"correlation_id": correlation_id, "lead_id": str(lead.id), "offer_kind": offer_kind},
        "parse_form_fields": True,
    }
    if PANDADOC_SENDER_EMAIL:
        payload["sender"] = {"email": PANDADOC_SENDER_EMAIL}

    headers = {"Authorization": f"API-Key {PANDADOC_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(PANDADOC_API_URL, headers=headers, json=payload, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"PandaDoc error {resp.status_code}: {resp.text[:250]}")
    data = resp.json()
    agreement.pandadoc_id = data.get("id")
    agreement.signing_status = AgreementStatus.SENT
    db.add(agreement)
    _append_lead_log(lead, "SIGN", f"PandaDoc sent; pandadoc_id={agreement.pandadoc_id or 'unknown'}; term={term_text}")
    return agreement


def create_automated_checkout(agreement_row: Agreement) -> str:
    """Stripe Checkout — uses PRICE_TRIAL_300 / PRICE_MONTH_500 when set, else inline price_data."""
    if not stripe.api_key:
        raise RuntimeError("Missing STRIPE_SECRET_KEY.")
    if not agreement_row.client_email:
        raise RuntimeError("Agreement client_email is required for Checkout.")
    cid = (agreement_row.correlation_id or "").strip()
    if not cid:
        raise RuntimeError("Agreement correlation_id is required for Stripe metadata / client_reference_id.")

    offer_kind = (agreement_row.offer_kind or "trial_14").strip().lower()
    term_text, amount_cents, product_title = _offer_term_label(offer_kind)
    agreement_row.stripe_plan_amount_cents = amount_cents

    price_id = ""
    if offer_kind == "month_30":
        price_id = (STRIPE_PRICE_MONTH_500 or "").strip()
    else:
        price_id = (STRIPE_PRICE_TRIAL_300 or "").strip()

    if price_id:
        line_items = [{"price": price_id, "quantity": 1}]
    else:
        line_items = [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": product_title},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ]

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=line_items,
        mode="payment",
        customer_email=agreement_row.client_email,
        client_reference_id=cid,
        metadata={
            "correlation_id": cid,
            "lead_id": str(agreement_row.lead_id or ""),
            "business_name": agreement_row.business_name or agreement_row.client_name or "",
            "client_name": agreement_row.client_name,
            "agreement_id": str(agreement_row.id),
            "vapi_call_id": agreement_row.vapi_call_id or "",
            "offer_kind": offer_kind,
            "term_label": term_text,
            "stripe_price_id": price_id or "",
        },
        success_url=STRIPE_SUCCESS_URL,
        cancel_url=STRIPE_CANCEL_URL,
    )
    agreement_row.stripe_checkout_session_id = str(session.get("id") or "")
    agreement_row.stripe_checkout_url = str(session.get("url") or "")
    return agreement_row.stripe_checkout_url


@app.post("/automation/intake")
def automation_intake():
    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get("name") or payload.get("owner_name") or "").strip()
    business_name = (payload.get("business_name") or payload.get("company") or name or "").strip()
    email = (payload.get("email") or "").strip() or None
    phone = (payload.get("phone") or "").strip() or None
    niche = (payload.get("niche") or "HVAC").strip() or "HVAC"
    location = (payload.get("location") or "").strip() or None
    no_response = bool(payload.get("no_response"))
    if not business_name:
        return jsonify({"ok": False, "error": "missing business_name/name"}), 400

    with get_session() as db:
        lead = db.query(Lead).filter(Lead.business_name == business_name).first()
        if lead is None:
            lead = Lead(
                business_name=business_name,
                owner_name=name or None,
                email=email,
                phone=phone,
                niche=niche,
                location=location,
            )
        else:
            lead.owner_name = lead.owner_name or (name or None)
            lead.email = lead.email or email
            lead.phone = lead.phone or phone
            lead.location = lead.location or location
            lead.niche = lead.niche or niche
        _ensure_lead_correlation(lead)
        outreach_preview = _send_ceo_outreach(lead)
        if no_response:
            _trigger_vapi_call(lead, DEFAULT_VAPI_SYSTEM_PROMPT, "intake_follow_up")
        db.add(lead)
        db.commit()
        return jsonify(
            {
                "ok": True,
                "lead_id": lead.id,
                "correlation_id": lead.correlation_id,
                "outreach": outreach_preview,
                "vapi_triggered": no_response,
            }
        )


@app.post("/automation/hook-deliver")
def hook_deliver_free_leads():
    """Proof phase (Hook): increment leads_sent + proof SMS/email drafts; may trigger tier-offer Vapi."""
    payload = request.get_json(force=True, silent=True) or {}
    cid = (payload.get("correlation_id") or "").strip()
    biz = (payload.get("business_name") or "").strip()
    try:
        count = max(1, min(10, int(payload.get("count") or 1)))
    except ValueError:
        count = 1
    if not cid and not biz:
        return jsonify({"ok": False, "error": "correlation_id or business_name required"}), 400

    with get_session() as db:
        lead = None
        if cid:
            lead = db.query(Lead).filter(Lead.correlation_id == cid).first()
        if lead is None and biz:
            lead = db.query(Lead).filter(Lead.business_name == biz).first()
        if lead is None:
            return jsonify({"ok": False, "error": "lead not found"}), 404

        _ensure_lead_correlation(lead)
        lead.leads_sent = int(lead.leads_sent or 0) + count
        proof = build_proof_templates(
            lead.owner_name or "",
            lead.business_name,
            lead.correlation_id,
        )
        _append_lead_log(lead, "TEXT", f"Proof SMS drafted ({count}x): {proof.sms[:160]}...")
        _append_lead_log(lead, "EMAIL", f"Proof email drafted; subject='{proof.email_subject}'")
        _maybe_trigger_offer_after_hook(lead)
        db.add(lead)
        db.commit()
        return jsonify(
            {
                "ok": True,
                "correlation_id": lead.correlation_id,
                "leads_sent": lead.leads_sent,
                "tier_offer_triggered": lead.tier_offer_triggered,
                "proof_preview": {"sms": proof.sms, "email_subject": proof.email_subject},
            }
        )


@app.post("/webhooks/offer_selection")
def offer_selection_webhook():
    """Pivot / Close: client chose trial vs month — PandaDoc Term + Stripe branch keyed by correlation_id."""
    payload = request.get_json(force=True, silent=True) or {}
    cid = (payload.get("correlation_id") or payload.get("client_reference_id") or "").strip()
    biz = (payload.get("business_name") or "").strip()
    choice = _normalize_offer_choice(
        payload.get("choice") or payload.get("offer") or payload.get("selection") or payload.get("tier")
    )
    if not choice:
        return jsonify({"ok": False, "error": "missing or invalid choice (use trial_14 or month_30)"}), 400

    with get_session() as db:
        lead = None
        if cid:
            lead = db.query(Lead).filter(Lead.correlation_id == cid).first()
        if lead is None and biz:
            lead = db.query(Lead).filter(Lead.business_name == biz).first()
        if lead is None:
            return jsonify({"ok": False, "error": "lead not found"}), 404

        _ensure_lead_correlation(lead)
        _append_lead_log(lead, "OFFER", f"Offer selection recorded: {choice}")
        agreement = _upsert_agreement_for_lead(db, lead, choice)
        try:
            agreement = _send_pandadoc_agreement(db, lead, agreement)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        db.commit()
        return jsonify(
            {
                "ok": True,
                "correlation_id": lead.correlation_id,
                "offer_kind": choice,
                "pandadoc_id": agreement.pandadoc_id,
                "signing_status": agreement.signing_status.value if hasattr(agreement.signing_status, "value") else str(agreement.signing_status),
            }
        )


@app.post("/webhooks/vapi")
def vapi_webhook():
    event = request.get_json(force=True, silent=True) or {}
    lead_payload = event.get("lead") or event.get("lead_data") or {}
    call_payload = event.get("call") or {}
    business_name = (lead_payload.get("business_name") or lead_payload.get("name") or "").strip()
    if not business_name:
        return jsonify({"ok": False, "error": "missing business_name"}), 400

    call_status = str(call_payload.get("status") or event.get("call_status") or "").lower()
    call_id = call_payload.get("id") or event.get("call_id")
    interested_flag = str(event.get("disposition") or event.get("outcome") or "").lower() in {
        "interested",
        "qualified",
        "booked",
    }
    interested = interested_flag or call_status == "interested"
    transcript_url = call_payload.get("transcript_url") or event.get("transcript_url")

    with get_session() as db:
        lead = db.query(Lead).filter(Lead.business_name == business_name).first()
        if lead is None:
            lead = Lead(
                business_name=business_name,
                owner_name=lead_payload.get("owner_name"),
                email=lead_payload.get("email"),
                phone=lead_payload.get("phone"),
                website=lead_payload.get("website"),
                niche=lead_payload.get("niche") or "HVAC",
                location=lead_payload.get("location"),
            )
        lead.call_status = call_status or "completed"
        if transcript_url:
            lead.transcript_url = transcript_url
        _ensure_lead_correlation(lead)
        _append_lead_log(lead, "CALL", f"Vapi webhook received; status={lead.call_status}")
        db.add(lead)
        db.commit()
        db.refresh(lead)

        if interested:
            if VALUE_FIRST_FUNNEL:
                _append_lead_log(
                    lead,
                    "FUNNEL",
                    "Interested signal recorded — agreement + Stripe gated until /webhooks/offer_selection",
                )
                db.add(lead)
                db.commit()
                return jsonify(
                    {
                        "ok": True,
                        "interested": True,
                        "awaiting_offer_selection": True,
                        "correlation_id": lead.correlation_id,
                    }
                )

            agreement = _upsert_agreement_for_lead(db, lead, "trial_14")
            if call_id and not agreement.vapi_call_id:
                agreement.vapi_call_id = str(call_id)
            agreement = _send_pandadoc_agreement(db, lead, agreement)
            db.commit()
            return jsonify({"ok": True, "interested": True, "correlation_id": lead.correlation_id})

    return jsonify({"ok": True, "interested": False})


@app.post("/webhooks/pandadoc")
def pandadoc_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    event_type = str(payload.get("event_type") or payload.get("event") or "").lower()
    data = payload.get("data") or {}
    pandadoc_id = data.get("id") or data.get("document_id")
    status = str(data.get("status") or "").lower()
    signed_pdf_url = data.get("pdf") or data.get("document_url")

    with get_session() as db:
        agreement = None
        if pandadoc_id:
            agreement = db.query(Agreement).filter(Agreement.pandadoc_id == pandadoc_id).first()
        if agreement is None:
            return jsonify({"ok": True, "ignored": True})

        if "complete" in event_type or status in {"document.completed", "completed"}:
            agreement.signing_status = AgreementStatus.SIGNED
            if signed_pdf_url:
                agreement.signed_pdf_url = signed_pdf_url
            _append_agreement_log(agreement, "SIGN", "Agreement signed via PandaDoc webhook")
            try:
                create_automated_checkout(agreement)
                _append_agreement_log(agreement, "PAY", "Stripe Checkout session created")
            except Exception as exc:
                print(f"[STRIPE] Failed to create checkout for {agreement.correlation_id}: {exc}")
                _record_error("stripe", "create_checkout_after_pandadoc", exc, correlation_id=agreement.correlation_id)
            db.add(agreement)
            db.commit()

    return jsonify({"ok": True})


@app.post("/agreements/<int:agreement_id>/create-checkout")
def create_checkout_for_agreement(agreement_id: int):
    with get_session() as db:
        agreement = db.query(Agreement).filter(Agreement.id == agreement_id).first()
        if agreement is None:
            return jsonify({"ok": False, "error": "agreement not found"}), 404
        try:
            checkout_url = create_automated_checkout(agreement)
        except Exception as exc:
            _record_error("stripe", "create_checkout_for_agreement", exc, correlation_id=agreement.correlation_id)
            return jsonify({"ok": False, "error": str(exc)}), 400
        db.add(agreement)
        db.commit()
        return jsonify(
            {
                "ok": True,
                "agreement_id": agreement.id,
                "correlation_id": agreement.correlation_id,
                "checkout_url": checkout_url,
                "checkout_session_id": agreement.stripe_checkout_session_id,
            }
        )


@app.post("/webhooks/stripe")
def stripe_webhook():
    payload = request.data
    signature = request.headers.get("Stripe-Signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
        else:
            event = request.get_json(force=True)
    except Exception as exc:
        _record_error("stripe", "webhook_parse", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400

    set_setting("stripe_last_event_type", str(event.get("type") or "unknown"))
    set_setting("stripe_last_event_at", datetime.now(timezone.utc).isoformat())

    if event.get("type") == "checkout.session.completed":
        session_obj = event["data"]["object"]
        metadata = session_obj.get("metadata", {})
        customer_email = (
            session_obj.get("customer_details", {}).get("email")
            or metadata.get("email")
        )
        correlation_id = metadata.get("correlation_id") or session_obj.get("client_reference_id")
        amount_total = int(session_obj.get("amount_total") or 0)
        stripe_tx_id = session_obj.get("payment_intent") or session_obj.get("id")
        print(
            f"[STRIPE] Payment received. email={customer_email} amount={amount_total} "
            f"correlation_id={correlation_id}"
        )

        with get_session() as db:
            agreement = None
            if correlation_id:
                agreement = db.query(Agreement).filter(Agreement.correlation_id == correlation_id).first()
            if agreement is None and customer_email:
                agreement = db.query(Agreement).filter(Agreement.client_email == customer_email).first()

            if agreement:
                agreement.signing_status = AgreementStatus.PAID
                agreement.stripe_transaction_id = str(stripe_tx_id)
                _append_agreement_log(agreement, "PAY", f"Stripe payment confirmed; tx={stripe_tx_id}")
                db.add(agreement)

            lead = None
            if correlation_id:
                lead = db.query(Lead).filter(Lead.correlation_id == correlation_id).first()
            if lead is None and customer_email:
                lead = db.query(Lead).filter(Lead.email == customer_email).first()
            if lead:
                lead.status = LeadStatus.ACTIVE_CLIENT
                _append_lead_log(
                    lead,
                    "PAY",
                    f"Payment confirmed amount_total_cents={amount_total}; tx={stripe_tx_id}",
                )
                db.add(lead)
                deliver_paid_lead_package(lead)
            db.commit()

    elif event.get("type") == "checkout.session.expired":
        session_obj = event["data"]["object"]
        metadata = session_obj.get("metadata", {})
        correlation_id = metadata.get("correlation_id") or session_obj.get("client_reference_id")
        print(f"[AUDIT] [{_stamp()}] [PAY] checkout.session.expired correlation_id={correlation_id}")

    return jsonify({"ok": True})


@app.post("/compliance/optout")
def compliance_optout():
    payload = request.get_json(force=True, silent=True) or {}
    cid = (payload.get("correlation_id") or "").strip() or None
    email = (payload.get("email") or "").strip() or None
    phone = (payload.get("phone") or "").strip() or None
    reason = (payload.get("reason") or "opt_out").strip()
    if not (cid or email or phone):
        return jsonify({"ok": False, "error": "provide correlation_id or email or phone"}), 400
    with get_session() as db:
        entry = SuppressionEntry(
            correlation_id=cid,
            email=email,
            phone=phone,
            reason=reason,
            source="api",
            active=True,
        )
        db.add(entry)
        # Soft-mark lead records as suppressed through notes.
        q = db.query(Lead)
        if cid:
            q = q.filter(Lead.correlation_id == cid)
        elif email:
            q = q.filter(Lead.email == email)
        elif phone:
            q = q.filter(Lead.phone == phone)
        lead = q.first()
        if lead:
            _append_lead_log(lead, "COMPLIANCE", f"Suppressed due to {reason}")
            db.add(lead)
        db.commit()
        return jsonify({"ok": True, "suppressed": True})


@app.post("/webhooks/reply")
def reply_webhook():
    """
    Generic inbound webhook for email/SMS provider callbacks.
    Use this to mark replies and auto-stop follow-up pressure.
    """
    payload = request.get_json(force=True, silent=True) or {}
    cid = (payload.get("correlation_id") or "").strip()
    provider_message_id = (payload.get("provider_message_id") or "").strip() or None
    body = (payload.get("body") or "").strip()
    email = (payload.get("email") or "").strip() or None
    phone = (payload.get("phone") or "").strip() or None
    lower = body.lower()
    is_optout = any(x in lower for x in ["stop", "unsubscribe", "opt out", "do not contact"])

    with get_session() as db:
        lead = None
        if cid:
            lead = db.query(Lead).filter(Lead.correlation_id == cid).first()
        if lead is None and email:
            lead = db.query(Lead).filter(Lead.email == email).first()
        if lead is None and phone:
            lead = db.query(Lead).filter(Lead.phone == phone).first()
        if lead is None:
            return jsonify({"ok": False, "error": "lead not found"}), 404

        evt = MessageEvent(
            correlation_id=lead.correlation_id,
            lead_id=lead.id,
            channel="email" if email else "sms",
            direction="inbound",
            status=MessageStatus.REPLIED,
            body=body[:5000],
            provider_message_id=provider_message_id,
        )
        db.add(evt)
        _append_lead_log(lead, "REPLY", "Inbound reply captured")
        db.add(lead)

        if is_optout:
            db.add(
                SuppressionEntry(
                    correlation_id=lead.correlation_id,
                    email=lead.email,
                    phone=lead.phone,
                    reason="opt_out_keyword",
                    source="inbound_reply",
                    active=True,
                )
            )
            _append_lead_log(lead, "COMPLIANCE", "Auto-suppressed from STOP/UNSUBSCRIBE keyword")
            db.add(lead)

        db.commit()
        return jsonify({"ok": True, "optout": is_optout})


@app.post("/automation/start")
def automation_start():
    payload = request.get_json(force=True, silent=True) or {}
    if payload.get("niche"):
        set_setting("last_niche", str(payload.get("niche")))
    if payload.get("location"):
        set_setting("last_location", str(payload.get("location")))
    if payload.get("daily_target"):
        set_setting("last_lead_count", str(int(payload.get("daily_target"))))
    if payload.get("simulate") is not None:
        set_setting("simulate_mode", "1" if bool(payload.get("simulate")) else "0")
    start_automation()
    return jsonify({"ok": True, **automation_status()})


@app.post("/automation/stop")
def automation_stop():
    stop_automation()
    return jsonify({"ok": True, **automation_status()})


@app.get("/automation/status")
def automation_status_api():
    return jsonify({"ok": True, **automation_status()})


@app.post("/automation/next-target")
def automation_next_target():
    """
    Manually rotate to a new random city+niche target.
    """
    try:
        target = get_random_target(
            last_location=get_setting("last_location", ""),
            last_niche=get_setting("last_niche", ""),
        )
        set_setting("last_niche", target["niche"])
        set_setting("last_location", target["location"])
        return jsonify({"ok": True, "target": target})
    except OperationalError:
        # keep endpoint available even if remote DB is temporarily unavailable
        target = get_random_target(last_location="", last_niche="")
        log_system_event(
            source="automation",
            action="next_target_db_fallback",
            detail="DB unavailable while rotating target; returned ephemeral target only.",
            level="warn",
        )
        return jsonify({"ok": True, "target": target, "db_warning": "fallback_target_only"})


@app.post("/automation/run-once")
def automation_run_once():
    payload = request.get_json(force=True, silent=True) or {}
    niche = (payload.get("niche") or get_setting("last_niche", "")).strip() or None
    location = (payload.get("location") or get_setting("last_location", "")).strip() or None
    daily_target = int(payload.get("daily_target") or get_setting("last_lead_count", "50"))
    simulate = bool(payload.get("simulate", True))
    send_report = bool(payload.get("send_report", True))
    result = run_cycle(
        niche=niche,
        location=location,
        daily_target=daily_target,
        simulate=simulate,
        send_report=send_report,
    )
    return jsonify(result), (200 if result.get("ok") else 500)


@app.post("/reports/daily/send")
def send_daily_report():
    result = create_and_send_daily_report(send_email=True)
    return jsonify({"ok": True, **result})


@app.get("/tracking/metrics")
def tracking_metrics():
    return jsonify({"ok": True, **compute_metrics()})


@app.get("/integrations/stripe/status")
def stripe_integration_status():
    return jsonify(
        {
            "ok": True,
            "webhook_endpoint": "/webhooks/stripe",
            "listening_for": ["checkout.session.completed"],
            "api_key_set": bool(stripe.api_key),
            "webhook_secret_set": bool(STRIPE_WEBHOOK_SECRET),
            "last_event_type": get_setting("stripe_last_event_type", ""),
            "last_event_at": get_setting("stripe_last_event_at", ""),
        }
    )


@app.get("/automation/errors")
def automation_errors():
    """
    Recent actionable errors from automation runs and failed message events.
    """
    try:
        with get_session() as db:
            run_errors = (
                db.query(AutomationRun)
                .filter(AutomationRun.status == "failed")
                .order_by(AutomationRun.id.desc())
                .limit(10)
                .all()
            )
            msg_errors = (
                db.query(MessageEvent)
                .filter(MessageEvent.status == MessageStatus.FAILED)
                .order_by(MessageEvent.id.desc())
                .limit(10)
                .all()
            )
            runs = [
                {
                    "id": r.id,
                    "started_at": str(r.started_at),
                    "ended_at": str(r.ended_at or ""),
                    "status": r.status,
                    "notes": (r.notes or "")[:240],
                }
                for r in run_errors
            ]
            messages = [
                {
                    "id": m.id,
                    "at": str(m.created_at),
                    "channel": m.channel,
                    "error": (m.error or "send_failed")[:240],
                    "lead_id": m.lead_id,
                    "correlation_id": m.correlation_id,
                }
                for m in msg_errors
            ]
        return jsonify({"ok": True, "automation_runs": runs, "message_failures": messages})
    except OperationalError:
        log_system_event(
            source="automation",
            action="errors_feed_db_unavailable",
            detail="Operational DB unavailable while loading errors feed.",
            level="warn",
        )
        return jsonify({"ok": True, "automation_runs": [], "message_failures": [], "db_warning": "unavailable"})


@app.get("/system/logs")
def system_logs():
    try:
        with get_session() as db:
            rows = db.query(SystemLog).order_by(SystemLog.id.desc()).limit(20).all()
            return jsonify(
                {
                    "ok": True,
                    "rows": [
                        {
                            "id": row.id,
                            "at": str(row.created_at),
                            "level": row.level,
                            "source": row.source,
                            "action": row.action,
                            "detail": row.detail,
                            "correlation_id": row.correlation_id or "",
                        }
                        for row in rows
                    ],
                }
            )
    except OperationalError:
        return jsonify({"ok": True, "rows": [], "db_warning": "unavailable"})


@app.post("/automation/retry-last-failed")
def retry_last_failed():
    """
    One-click retry path for last failed automation run using current settings.
    """
    try:
        niche = get_setting("last_niche", "").strip() or None
        location = get_setting("last_location", "").strip() or None
        daily_target = int(get_setting("last_lead_count", "50"))
        simulate = get_setting("simulate_mode", "1") == "1"
    except OperationalError:
        log_system_event(
            source="automation",
            action="retry_last_failed_db_fallback",
            detail="DB unavailable; retry executed with fallback defaults.",
            level="warn",
        )
        niche = None
        location = None
        daily_target = 10
        simulate = True
    result = run_cycle(
        niche=niche,
        location=location,
        daily_target=daily_target,
        simulate=simulate,
        send_report=False,
    )
    return jsonify(result), (200 if result.get("ok") else 500)


@app.get("/automation/today-summary")
def automation_today_summary():
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cap = int(os.getenv("OUTREACH_DAILY_CAP", "50") or "50")
    try:
        with get_session() as db:
            sent_today = (
                db.query(MessageEvent)
                .filter(
                    MessageEvent.direction == "outbound",
                    MessageEvent.status == MessageStatus.SENT,
                    MessageEvent.created_at >= day_start,
                )
                .count()
            )
    except OperationalError:
        log_system_event(
            source="automation",
            action="today_summary_db_unavailable",
            detail="DB unavailable while computing daily sent count.",
            level="warn",
        )
        sent_today = 0
    return jsonify(
        {
            "ok": True,
            "date_utc": now.strftime("%Y-%m-%d"),
            "daily_cap": cap,
            "sent_today": int(sent_today),
            "remaining_today": max(0, cap - int(sent_today)),
            "current_target": {
                "niche": get_setting("last_niche", ""),
                "location": get_setting("last_location", ""),
            },
        }
    )


@app.get("/reports/daily/latest")
def latest_daily_report():
    with get_session() as db:
        row = db.query(DailyReport).order_by(DailyReport.created_at.desc()).first()
        # return quick financial snapshot from agreements + lead status
        paid_count = db.query(Agreement).filter(Agreement.signing_status == AgreementStatus.PAID).count()
        active_clients = db.query(Lead).filter(Lead.status == LeadStatus.ACTIVE_CLIENT).count()
        return jsonify(
            {
                "ok": True,
                "paid_count": paid_count,
                "active_clients": active_clients,
                "latest_report": {
                    "id": row.id,
                    "date": row.report_date,
                    "status": row.status,
                    "subject": row.subject,
                }
                if row
                else None,
            }
        )


@app.post("/admin/sanitize-notes")
def sanitize_notes():
    """
    Remove legacy personal references from notes/audit logs.
    """
    banned = ["dad", "father", "family", "personal brand", "stevie", "san antonio"]
    cleaned = 0
    with get_session() as db:
        for lead in db.query(Lead).all():
            txt = (lead.notes or "").strip()
            if txt and any(x in txt.lower() for x in banned):
                lead.notes = ""
                db.add(lead)
                cleaned += 1
        for ag in db.query(Agreement).all():
            txt = (ag.audit_notes or "").strip()
            if txt and any(x in txt.lower() for x in banned):
                ag.audit_notes = ""
                db.add(ag)
                cleaned += 1
        for run in db.query(AutomationRun).all():
            txt = (run.notes or "").strip()
            if txt and any(x in txt.lower() for x in banned):
                run.notes = ""
                db.add(run)
                cleaned += 1
        db.commit()
    return jsonify({"ok": True, "cleaned_records": cleaned})


def _railway_fingerprint() -> dict[str, str]:
    """Proves requests hit THIS Flask deploy (hvac-engine). If you see RevenueBringer HTML instead, DNS/custom domain is on the wrong target."""
    return {
        "app": "server.py Flask (autoyieldsystems backend)",
        "railway_git_sha": (os.getenv("RAILWAY_GIT_COMMIT_SHA") or "unknown")[:12],
        "railway_service": os.getenv("RAILWAY_SERVICE_NAME") or "unknown",
        "railway_environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or "unknown",
    }


@app.get("/")
def public_landing():
    """Marketing site removed. Plain text + deploy fingerprint so you can verify routing."""
    fp = _railway_fingerprint()
    lines = [
        "autoyieldsystems.com — THIS IS THE FLASK API (hvac-engine).",
        "If you still see RevenueBringer HTML in a browser, that traffic is NOT reaching this process.",
        "Fix: Railway → only this service → attach custom domain. Remove domain from any other Railway service.",
        "",
        "Deploy fingerprint:",
        f"  railway_service={fp['railway_service']}",
        f"  railway_git_sha={fp['railway_git_sha']}",
        f"  railway_environment={fp['railway_environment']}",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/success")
def public_checkout_success():
    """Post-Stripe thank-you (STRIPE_SUCCESS_URL); templates removed — land on home."""
    return redirect("/", 302)


@app.get("/pricing")
def public_pricing():
    """Plan context (STRIPE_CANCEL_URL default); templates removed — land on home."""
    return redirect("/", 302)


@app.get("/favicon.ico")
def favicon():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        b'<rect width="32" height="32" rx="8" fill="#06040f"/>'
        b'<path d="M8 16 L14 22 L24 10" stroke="#5eead4" stroke-width="2.5" '
        b'stroke-linecap="round" stroke-linejoin="round"/>'
        b"</svg>"
    )
    return send_file(BytesIO(svg), mimetype="image/svg+xml")


@app.get("/robots.txt")
def robots_txt():
    return """User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /webhooks/\n""", 200, {
        "Content-Type": "text/plain; charset=utf-8"
    }


@app.get("/health")
def health():
    out = {"ok": True, "service": "autoyieldsystems", "marketing_site": False}
    out.update(_railway_fingerprint())
    return jsonify(out)


@app.get("/command-center")
def command_center_not_here():
    """Reflex serves the Command Center on its own process/URL — this API does not mount that UI."""
    body = (
        "This URL path is for the Reflex Command Center UI.\n\n"
        "This Railway service (hvac-engine) runs Flask (server.py) only — it does not serve Reflex pages.\n"
        "Run the Reflex app separately (e.g. `reflex run` locally, or a second Railway service / Reflex Hosting)\n"
        "and open the URL that Reflex gives you, usually with path /command-center on THAT host.\n\n"
        "On THIS host, working endpoints include:  GET /  GET /health  (and POST /automation/*, /webhooks/*, etc.)\n"
    )
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.errorhandler(404)
def handle_404(_e):
    """Replace generic Werkzeug HTML with a short hint (common mistake: Command Center on API host)."""
    p = request.path
    if p.startswith("/webhooks/") or p.startswith("/automation"):
        return jsonify({"ok": False, "error": "not_found", "path": p}), 404
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return jsonify(
            {
                "ok": False,
                "error": "not_found",
                "path": p,
                "hint": "Flask API only. Try GET /health . Command Center is Reflex — not on this host unless you add a Reflex service or proxy.",
            }
        ), 404
    msg = (
        f"404 — no route for: {p}\n\n"
        "This service is the autoyieldsystems Flask API (hvac-engine).\n"
        "Try:  GET /     and  GET /health\n"
        "Command Center UI: run Reflex separately; it is not served at this path on this process.\n"
        "See GET /command-center for a longer explanation.\n"
    )
    return msg, 404, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=flask_debug_enabled())
