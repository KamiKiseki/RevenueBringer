from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import requests
import stripe
from flask import Flask, jsonify, redirect, render_template, render_template_string, request, send_file, url_for
from sqlalchemy.exc import OperationalError

from sqlalchemy.orm import Session

from models import (
    Agreement,
    AgreementStatus,
    AutomationRun,
    ContactSubmission,
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
from templates import (
    DEFAULT_VAPI_SYSTEM_PROMPT,
    ELLIOT_VAPI_IDLE_HOOKS,
    build_ceo_outreach_templates,
    build_proof_templates,
    build_service_agreement_text,
    build_tier_offer_templates,
)

import client_reporting  # noqa: F401 — ship with API; fail import at boot if missing on Railway.

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
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")
VAPI_FALLBACK_PHONE_NUMBER_ID = os.getenv("VAPI_FALLBACK_PHONE_NUMBER_ID", "")
VAPI_ENABLE_FALLBACK = os.getenv("VAPI_ENABLE_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
VAPI_OPENAI_CREDENTIAL_ID = os.getenv("VAPI_OPENAI_CREDENTIAL_ID", "").strip()
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


def _to_e164(raw_phone: str | None) -> str | None:
    if not raw_phone:
        return None
    digits = "".join(ch for ch in str(raw_phone) if ch.isdigit())
    if not digits:
        return None
    # US default: 10-digit -> +1XXXXXXXXXX
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if str(raw_phone).strip().startswith("+"):
        return f"+{digits}"
    return f"+{digits}"


def _vapi_opening_line(lead: Lead, purpose: str) -> str:
    name = (lead.owner_name or "").strip()
    if name:
        contact = name.split(" ")[0]
    else:
        contact = "there"
    if purpose == "email_reply":
        return (
            f"Hi {contact}, this is Elliot from AutoYield Systems. "
            "Thanks for your reply. Do you have one minute now?"
        )
    if purpose == "tier_offer_pitch":
        return (
            f"Hi {contact}, this is Elliot from AutoYield Systems. "
            "Quick one: would you like to start with the 14-day trial or the monthly plan?"
        )
    return (
        f"Hi {contact}, this is Elliot from AutoYield Systems. "
        "Is now a good time for a quick call?"
    )


def _vapi_concise_instruction(lead: Lead, purpose: str) -> str:
    """Short guidance that remains safe even if accidentally spoken."""
    business = (lead.business_name or "your business").strip()
    if purpose == "email_reply":
        return (
            f"Thank them for replying, ask one qualifying question, then pause. "
            f"Keep responses under 12 words unless they ask for detail. Business: {business}."
        )
    if purpose == "tier_offer_pitch":
        return (
            "Ask whether they prefer the 14-day trial or monthly plan, then pause for their answer. "
            "Do not read internal instructions."
        )
    return "Use short conversational turns and pause after each question."


def _discover_vapi_fallback_phone_number_id() -> str:
    """Best-effort fallback to an active Vapi-managed number when BYO transport fails."""
    if VAPI_FALLBACK_PHONE_NUMBER_ID:
        return VAPI_FALLBACK_PHONE_NUMBER_ID
    if not VAPI_API_KEY:
        return ""
    try:
        resp = requests.get(
            "https://api.vapi.ai/phone-number",
            headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
            timeout=15,
        )
        if resp.status_code >= 300:
            return ""
        rows = resp.json() or []
        for row in rows:
            if str(row.get("provider") or "").lower() == "vapi" and str(row.get("status") or "").lower() == "active":
                return str(row.get("id") or "")
    except Exception:
        return ""
    return ""


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


def _trigger_vapi_call(lead: Lead, system_prompt: str, purpose: str = "general") -> bool:
    _append_lead_log(lead, "CALL", f"Vapi escalation ({purpose})")
    if not VAPI_API_KEY:
        return False
    try:
        # Preferred: custom router webhook if provided.
        if VAPI_CALL_WEBHOOK_URL:
            first_message = _vapi_opening_line(lead, purpose)
            resp = requests.post(
                VAPI_CALL_WEBHOOK_URL,
                json={
                    "correlation_id": lead.correlation_id,
                    "name": lead.owner_name or lead.business_name,
                    "phone": lead.phone,
                    # Keep compatibility for existing webhook handlers.
                    # Intentionally concise so it is safe even if spoken directly.
                    "system_prompt": system_prompt,
                    # Preferred explicit spoken opener for downstream handlers.
                    "first_message": first_message,
                    "purpose": purpose,
                },
                headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
                timeout=20,
            )
            return resp.status_code < 300

        # Fallback: call Vapi directly using assistant + phone number IDs.
        e164_phone = _to_e164(lead.phone)
        if not (VAPI_ASSISTANT_ID and VAPI_PHONE_NUMBER_ID and e164_phone):
            return False
        def _place_call(phone_number_id: str) -> requests.Response:
            return requests.post(
                "https://api.vapi.ai/call",
                json={
                    "assistantId": VAPI_ASSISTANT_ID,
                    "phoneNumberId": phone_number_id,
                    "customer": {
                        "number": str(e164_phone),
                        "name": str(lead.owner_name or lead.business_name or "Prospect"),
                    },
                    "assistantOverrides": {
                        "variableValues": {
                            "correlation_id": str(lead.correlation_id or ""),
                            "purpose": str(purpose),
                            "business_name": str(lead.business_name or ""),
                        },
                        # Keep first line short so Elliot does not monologue.
                        "firstMessage": _vapi_opening_line(lead, purpose),
                        # Avoid fast disconnects after one turn.
                        "firstMessageMode": "assistant-speaks-first",
                        "silenceTimeoutSeconds": 45,
                        "maxDurationSeconds": 300,
                        "hooks": ELLIOT_VAPI_IDLE_HOOKS,
                        **(
                            {"credentialIds": [VAPI_OPENAI_CREDENTIAL_ID]}
                            if VAPI_OPENAI_CREDENTIAL_ID
                            else {}
                        ),
                    },
                },
                headers={
                    "Authorization": f"Bearer {VAPI_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=20,
            )

        chosen_phone_number_id = VAPI_PHONE_NUMBER_ID
        resp = _place_call(chosen_phone_number_id)
        if resp.status_code == 429:
            # Telnyx transport can return transient 429s; retry with short backoff.
            for wait_seconds in (2, 4):
                _append_lead_log(
                    lead,
                    "CALL",
                    f"Vapi transport rate-limited (429). Retrying in {wait_seconds}s...",
                )
                time.sleep(wait_seconds)
                resp = _place_call(chosen_phone_number_id)
                if resp.status_code != 429:
                    break
        if VAPI_ENABLE_FALLBACK and resp.status_code >= 300 and "status code 403" in (resp.text or "").lower():
            fallback_phone_number_id = _discover_vapi_fallback_phone_number_id()
            if fallback_phone_number_id and fallback_phone_number_id != chosen_phone_number_id:
                _append_lead_log(
                    lead,
                    "CALL",
                    f"Primary phoneNumberId={chosen_phone_number_id} rejected; retrying fallback phoneNumberId={fallback_phone_number_id}",
                )
                retry_resp = _place_call(fallback_phone_number_id)
                if retry_resp.status_code < 300:
                    resp = retry_resp
                    chosen_phone_number_id = fallback_phone_number_id
        if resp.status_code >= 300:
            raise RuntimeError(
                f"vapi direct call error {resp.status_code} for number={e164_phone}: {resp.text[:220]}"
            )
        _append_lead_log(lead, "CALL", f"Vapi call accepted via phoneNumberId={chosen_phone_number_id}")
        return True
    except Exception as exc:
        _append_lead_log(lead, "CALL", f"Vapi trigger error ({purpose}): {exc}")
        _record_error("vapi", f"trigger_{purpose}", exc, correlation_id=lead.correlation_id)
        return False


def _trigger_tier_offer_vapi(lead: Lead) -> None:
    """After free proof leads, Elliot calls with the $300 vs $500 fork question."""
    offer = build_tier_offer_templates(
        lead_name=lead.owner_name or "",
        business_name=lead.business_name,
        correlation_id=_ensure_lead_correlation(lead),
    )
    _append_lead_log(lead, "TEXT", f"Tier-offer SMS drafted: {offer.sms[:120]}...")
    _append_lead_log(lead, "EMAIL", f"Tier-offer email drafted; subject='{offer.email_subject}'")
    pitch_prompt = _vapi_concise_instruction(lead, "tier_offer_pitch")
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
            # Avoid immediate back-to-back dials when voice notification already fired.
            if VOICE_NOTIFICATION_MODE:
                _append_lead_log(
                    lead,
                    "CALL",
                    "Skipping duplicate intake_follow_up call (lead_notification already sent).",
                )
            else:
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
        log_system_event(
            source="vapi",
            action="reply_webhook_eval",
            detail=f"email={email or lead.email or ''} phone_present={bool(lead.phone)} is_optout={is_optout}",
            level="info",
            correlation_id=lead.correlation_id,
        )

        # Trigger Elliot via Vapi on non-opt-out replies.
        if not is_optout and lead.phone:
            try:
                prompt = (
                    _vapi_concise_instruction(lead, "email_reply")
                )
                ok = _trigger_vapi_call(lead, prompt, purpose="email_reply")
                log_system_event(
                    source="vapi",
                    action="trigger_from_email_reply",
                    detail=(
                        f"timestamp={_stamp()} email={email or lead.email or ''} phone={lead.phone or ''} "
                        f"status={'success' if ok else 'failed'}"
                    ),
                    level="info" if ok else "warn",
                    correlation_id=lead.correlation_id,
                )
            except Exception as exc:
                _append_lead_log(lead, "CALL", f"Vapi trigger error from email reply: {exc}")
                _record_error("vapi", "trigger_from_email_reply", exc, correlation_id=lead.correlation_id)
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


@app.get("/reports/client/<int:client_id>")
def reports_client_dashboard(client_id: int):
    from client_reporting import render_client_dashboard

    html, status = render_client_dashboard(client_id)
    return html, status, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/reports/proof")
def reports_proof_dashboard():
    from client_reporting import render_proof_dashboard

    return render_proof_dashboard(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/reports/generate/<int:client_id>", methods=["GET", "POST"])
def reports_generate_client(client_id: int):
    from client_reporting import generate_report_for_client

    payload = request.get_json(force=True, silent=True) or {}
    send_email = bool(payload.get("send_email")) or request.args.get("send_email", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    result = generate_report_for_client(client_id, send_email=send_email)
    return jsonify(result), (200 if result.get("ok") else 404)


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
    """Proves requests hit THIS Flask deploy. Railway may set RAILWAY_SERVICE_NAME to e.g. RevenueBringer—that labels this API, not old static HTML."""
    return {
        "app": "server.py Flask (autoyieldsystems backend)",
        "railway_git_sha": (os.getenv("RAILWAY_GIT_COMMIT_SHA") or "unknown")[:12],
        "railway_service": os.getenv("RAILWAY_SERVICE_NAME") or "unknown",
        "railway_environment": os.getenv("RAILWAY_ENVIRONMENT_NAME") or "unknown",
    }


def _deploy_proof_plain() -> tuple[str, int, dict[str, str]]:
    """Plain-text routing proof (use when you do not want HTML from GET /)."""
    fp = _railway_fingerprint()
    lines = [
        "autoyieldsystems.com — THIS IS THE FLASK API (hvac-engine).",
        "railway_service in the fingerprint is Railway's label for THIS service (often RevenueBringer).",
        "If the browser shows a full styled RevenueBringer marketing page (HTML/CSS), that traffic is NOT reaching this process.",
        "Fix: Railway → only this service → attach custom domain. Remove domain from any other Railway service.",
        "",
        "Deploy fingerprint:",
        f"  railway_service={fp['railway_service']}",
        f"  railway_git_sha={fp['railway_git_sha']}",
        f"  railway_environment={fp['railway_environment']}",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/ops/deploy-proof")
def deploy_proof():
    """Ops: same plain-text fingerprint that used to be served at GET /."""
    return _deploy_proof_plain()


@app.get("/")
def public_landing():
    """Futuristic public site; contact form rows go to contact_submissions (shared DB with Reflex Command Center)."""
    return render_template("index.html")


@app.get("/contact")
def public_contact():
    return render_template("contact.html", sent=request.args.get("sent") == "1", error=request.args.get("err"))


@app.post("/contact")
def public_contact_post():
    """Persist to contact_submissions — same DATABASE_URL as models.py / Reflex rxconfig."""
    hp = (request.form.get("company_url") or "").strip()
    if hp:
        return redirect(url_for("public_contact", err="1"))
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    company = (request.form.get("company") or "").strip() or None
    message = (request.form.get("message") or "").strip()
    if len(name) < 2 or len(name) > 200:
        return redirect(url_for("public_contact", err="1"))
    if "@" not in email or len(email) > 255:
        return redirect(url_for("public_contact", err="1"))
    if len(message) < 10 or len(message) > 8000:
        return redirect(url_for("public_contact", err="1"))
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:64]
    try:
        with get_session() as db:
            db.add(
                ContactSubmission(
                    name=name,
                    email=email,
                    company=company,
                    message=message,
                    source="public_site",
                    ip_address=ip or None,
                )
            )
            db.commit()
        log_system_event(
            source="website",
            action="contact_submission",
            detail=f"name={name[:80]} email={email[:120]}",
            level="info",
        )
    except Exception as exc:
        _record_error("website", "contact_submission", exc)
        return redirect(url_for("public_contact", err="1"))
    return redirect(url_for("public_contact", sent="1"))


@app.get("/terms")
def public_terms():
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Service Agreement — AutoYield Systems</title>
  <style>
    :root { --bg:#050915; --panel:#0b1222; --text:#e2e8f0; --muted:#94a3b8; --line:#1f2a44; --cyan:#22d3ee; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; background:radial-gradient(1200px 500px at 10% -10%, #0b203a 0%, #050915 55%); color:var(--text); }
    .wrap { max-width:920px; margin:0 auto; padding:28px 18px 60px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:24px; }
    h1 { margin:0 0 14px; font-size:1.5rem; color:#f8fafc; }
    pre { margin:0; white-space:pre-wrap; word-break:break-word; color:var(--muted); line-height:1.6; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.95rem; }
    a { color:var(--cyan); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>SERVICE AGREEMENT</h1>
      <pre>This Service Agreement ("Agreement") governs services provided by AutoYield Systems.

---

1. SERVICES

Provider agrees to perform the following services ("Services"):

   - Online customer acquisition and lead generation for Client's business
   - Digital outreach campaigns targeting potential customers in Client's niche and location
   - Delivery of qualified customer leads, calls, or inquiries to Client through dedicated
     tracking channels (tracking phone number and/or landing page)
   - Monthly reporting showing results delivered

Provider will begin Services within [X] business days of receiving full payment.

---

2. PAYMENT TERMS

   a) Client agrees to pay Provider the amount of $[AMOUNT] per [month/week/per lead]
      for the Services described above.

   b) Payment is due BEFORE Services begin. Provider is not obligated to start or
      continue Services until payment is received and confirmed.

   c) Payment shall be made via [Stripe / PayPal] to [YOUR PAYMENT LINK].

   d) Recurring payments are due on the [DATE] of each month. Failure to pay within
      5 business days of the due date will result in immediate suspension of Services.

---

3. NO REFUND POLICY

   a) ALL PAYMENTS ARE FINAL AND NON-REFUNDABLE. Due to the nature of digital
      services, no refunds will be issued once Services have commenced.

   b) Client acknowledges that digital marketing and lead generation services involve
      real costs, labor, and resources that are expended immediately upon commencement
      of Services and cannot be recovered.

   c) In the event Client initiates a chargeback or payment dispute with their payment
      provider after Services have commenced, Client agrees that this Agreement serves
      as documented evidence that Services were agreed upon and delivered, and that
      Client waived their right to a refund by signing this Agreement.

   d) Provider reserves the right to pursue legal action and recover all costs,
      including attorney fees, in the event of a fraudulent chargeback.

---

4. RESULTS DISCLAIMER

   a) Provider does not guarantee a specific number of customers, sales, or revenue
      for Client. Digital marketing results vary based on factors outside Provider's
      control including but not limited to: Client's market, competition, pricing,
      and product/service quality.

   b) Provider guarantees that outreach campaigns will be actively run and that
      all tracking infrastructure will be set up and operational.

   c) Provider will deliver monthly reports showing activity, leads generated,
      and calls/inquiries tracked.

---

5. CLIENT RESPONSIBILITIES

   Client agrees to:

   a) Respond to leads and inquiries in a timely manner (within 24 hours).
   b) Provide accurate business information necessary for campaign setup.
   c) Not interfere with or attempt to replicate Provider's systems or methods.
   d) Keep all campaign strategies, messaging, and methods confidential.

---

6. TERM AND TERMINATION

   a) This Agreement begins on the date of first payment and continues on a
      [monthly / weekly] basis until either party provides written notice of
      termination.

   b) Either party may terminate this Agreement with [7 / 14 / 30] days written
      notice via email.

   c) Termination does not entitle Client to a refund for any period already paid.

   d) Provider may terminate immediately and without notice if Client engages in
      abusive, fraudulent, or illegal activity.

---

7. CONFIDENTIALITY

Both parties agree to keep the terms of this Agreement and any proprietary business
information confidential. Neither party shall disclose the other's business methods,
strategies, or client data to any third party without written consent.

---

8. INTELLECTUAL PROPERTY

All systems, tools, scripts, campaigns, and methods developed by Provider remain
the sole intellectual property of Provider. Client receives no ownership rights
over Provider's systems or methods.

---

9. LIMITATION OF LIABILITY

Provider's total liability under this Agreement shall not exceed the total amount
paid by Client in the most recent 30-day period. Provider is not liable for any
indirect, incidental, or consequential damages.

---

10. GOVERNING LAW

This Agreement shall be governed by the laws of the State of Texas. Any disputes
shall be resolved in the courts of [YOUR COUNTY], Texas.

---

11. ENTIRE AGREEMENT

This Agreement constitutes the entire agreement between the parties and supersedes
all prior discussions, representations, or agreements. Any modifications must be
made in writing and signed by both parties.
</pre>
    </div>
  </div>
</body>
</html>
"""
    return render_template_string(html)


@app.get("/checkout")
def public_checkout():
    selected = (request.args.get("plan") or "trial").strip().lower()
    if selected not in {"trial", "monthly"}:
        selected = "trial"
    err = (request.args.get("err") or "").strip()
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Checkout — AutoYield Systems</title>
  <style>
    :root { --bg:#050915; --panel:#0b1222; --panel2:#101a30; --text:#e2e8f0; --muted:#94a3b8; --line:#1f2a44; --cyan:#22d3ee; --green:#34d399; --warn:#f59e0b; }
    * { box-sizing:border-box; }
    html, body { min-height:100%; }
    body { margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; background:
      radial-gradient(1300px 650px at 10% -10%, #0b203a 0%, transparent 60%),
      radial-gradient(900px 500px at 90% 120%, #2a1150 0%, transparent 62%),
      linear-gradient(180deg, #050915 0%, #040711 100%);
      color:var(--text);
    }
    .wrap { min-height:100vh; max-width:900px; margin:0 auto; padding:28px 18px 60px; display:flex; align-items:center; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:22px; }
    h1 { margin:0 0 8px; font-size:1.6rem; color:#f8fafc; }
    .sub { color:var(--muted); margin:0 0 18px; }
    .plans { display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:12px; margin:10px 0 16px; }
    .card { border:1px solid var(--line); border-radius:12px; padding:14px; background:var(--panel2); cursor:pointer; display:block; text-decoration:none; color:inherit; }
    .card.active { border-color:var(--cyan); box-shadow:0 0 0 1px rgba(34,211,238,.35) inset; }
    .name { font-size:1.02rem; color:#f8fafc; margin:0 0 6px; }
    .price { margin:0; color:var(--green); font-weight:700; }
    .note { color:var(--muted); font-size:.94rem; margin-top:5px; }
    .row { margin-top:14px; }
    .agree-wrap { margin-top:16px; width:100%; display:flex; justify-content:center; align-items:center; }
    .agree {
      display:inline-flex;
      gap:10px;
      align-items:center;
      justify-content:center;
      width:max-content;
      max-width:100%;
      margin:0 auto;
      color:var(--muted);
      text-align:center;
      padding:10px 14px;
      border:1px solid var(--line);
      border-radius:10px;
      background:rgba(16, 26, 48, 0.45);
    }
    .agree label { text-align:center; }
    .agree a { color:var(--cyan); }
    .btn { margin-top:14px; border:0; border-radius:10px; padding:12px 16px; font-weight:700; background:var(--cyan); color:#001119; cursor:pointer; width:100%; }
    .btn:disabled { background:#334155; color:#94a3b8; cursor:not-allowed; }
    .err { margin-bottom:10px; color:#fecaca; background:#3a1118; border:1px solid #7f1d1d; border-radius:10px; padding:10px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Checkout</h1>
      <p class="sub">Choose a plan, accept the service agreement, and continue to secure Stripe checkout.</p>
      {% if err %}
      <div class="err">{{ err }}</div>
      {% endif %}
      <form method="post" action="{{ url_for('public_checkout_post') }}">
        <input type="hidden" id="plan" name="plan" value="{{ selected }}" />
        <div class="plans">
          <a href="#" class="card {% if selected == 'trial' %}active{% endif %}" data-plan="trial">
            <p class="name">14-Day Trial</p>
            <p class="price">$300 one time payment</p>
            <p class="note">One-time charge via Stripe hosted checkout.</p>
          </a>
          <a href="#" class="card {% if selected == 'monthly' %}active{% endif %}" data-plan="monthly">
            <p class="name">Monthly Plan</p>
            <p class="price">$500 per month recurring subscription</p>
            <p class="note">Billed monthly through Stripe subscription checkout.</p>
          </a>
        </div>
        <div class="agree-wrap">
          <div class="row agree">
            <input id="agree" name="agree" type="checkbox" value="yes" />
            <label for="agree">I agree to the <a href="{{ url_for('public_terms') }}" target="_blank" rel="noopener">Service Agreement</a></label>
          </div>
        </div>
        <button id="payBtn" type="submit" class="btn" disabled>Pay Now</button>
      </form>
    </div>
  </div>
  <script>
    const cards = Array.from(document.querySelectorAll(".card"));
    const planInput = document.getElementById("plan");
    const agree = document.getElementById("agree");
    const payBtn = document.getElementById("payBtn");
    cards.forEach((card) => {
      card.addEventListener("click", (e) => {
        e.preventDefault();
        const p = card.getAttribute("data-plan");
        planInput.value = p;
        cards.forEach(c => c.classList.remove("active"));
        card.classList.add("active");
      });
    });
    function syncBtn() { payBtn.disabled = !agree.checked; }
    agree.addEventListener("change", syncBtn);
    syncBtn();
  </script>
</body>
</html>
"""
    return render_template_string(html, selected=selected, err=err)


@app.post("/checkout")
def public_checkout_post():
    plan = (request.form.get("plan") or "").strip().lower()
    agreed = (request.form.get("agree") or "").strip().lower() in {"yes", "on", "true", "1"}
    if plan not in {"trial", "monthly"}:
        return redirect(url_for("public_checkout", err="Please choose a valid plan."))
    if not agreed:
        return redirect(url_for("public_checkout", plan=plan, err="You must agree to the Service Agreement."))
    if not stripe.api_key:
        return redirect(url_for("public_checkout", plan=plan, err="Stripe is not configured."))

    # Court/audit trace before redirecting to Stripe Checkout.
    try:
        log_system_event(
            source="checkout",
            action="agreement_accepted",
            detail=f"timestamp={datetime.now(timezone.utc).isoformat()} plan={plan} agreement_accepted=true",
            level="info",
        )
    except Exception as exc:
        _record_error("checkout", "agreement_log", exc)

    success_url = request.url_root.rstrip("/") + url_for("public_checkout_success")
    cancel_url = request.url_root.rstrip("/") + url_for("public_checkout")

    try:
        if plan == "monthly":
            price_id = (STRIPE_PRICE_MONTH_500 or "").strip()
            if not price_id:
                return redirect(
                    url_for("public_checkout", plan=plan, err="Monthly plan is not configured in Stripe.")
                )
            line_items = [{"price": price_id, "quantity": 1}]
            mode = "subscription"
        else:
            price_id = (STRIPE_PRICE_TRIAL_300 or "").strip()
            if not price_id:
                return redirect(
                    url_for("public_checkout", plan=plan, err="Trial plan is not configured in Stripe.")
                )
            line_items = [{"price": price_id, "quantity": 1}]
            mode = "payment"

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode=mode,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "plan": plan,
                "agreement_accepted": "true",
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        checkout_url = getattr(session, "url", None)
        if not checkout_url:
            try:
                checkout_url = session["url"]
            except Exception:
                checkout_url = None
        if not checkout_url:
            return redirect(url_for("public_checkout", plan=plan, err="Unable to open Stripe checkout."))
        return redirect(str(checkout_url), 303)
    except Exception as exc:
        _record_error("checkout", "create_session", exc)
        return redirect(url_for("public_checkout", plan=plan, err="Could not create Stripe checkout session."))


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


@app.get("/system/health-check")
def system_health_check():
    from health_monitor import run_full_system_check

    return jsonify(run_full_system_check(save_report=True, console=False))


@app.get("/health")
def health():
    out = {"ok": True, "service": "autoyieldsystems", "marketing_site": True}
    out.update(_railway_fingerprint())
    return jsonify(out)


@app.get("/command-center")
def command_center_not_here():
    """Reflex serves the Command Center on its own process/URL — this API does not mount that UI."""
    body = (
        "This URL path is for the Reflex Command Center UI.\n\n"
        "This Railway service (Flask API / server.py; Railway name may be e.g. RevenueBringer) does not serve Reflex pages.\n"
        "Run the Reflex app separately (e.g. `reflex run` locally, or a second Railway service / Reflex Hosting)\n"
        "and open the URL that Reflex gives you, usually with path /command-center on THAT host.\n\n"
        "On THIS host: GET / and GET /contact (marketing), GET /health, GET /ops/deploy-proof (plain-text fingerprint), "
        "POST /contact, POST /automation/*, /webhooks/*, etc.\n"
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
                "hint": "Flask API + marketing site. Try GET /health or GET /ops/deploy-proof. Command Center is Reflex on another process.",
            }
        ), 404
    msg = (
        f"404 — no route for: {p}\n\n"
        "This service is the autoyieldsystems Flask API (hvac-engine).\n"
        "Try:  GET /health   GET /ops/deploy-proof   (marketing: GET /  GET /contact)\n"
        "Command Center UI: run Reflex separately; it is not served at this path on this process.\n"
        "See GET /command-center for a longer explanation.\n"
    )
    return msg, 404, {"Content-Type": "text/plain; charset=utf-8"}


# Gunicorn loads `server:app` without running __main__ — ensure tables exist per worker.
init_db()

try:
    from health_monitor import start_health_monitor

    start_health_monitor()
except Exception as exc:
    print(f"[health_monitor] scheduler not started: {exc}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Never default debug=True in production (Railway); set FLASK_DEBUG=1 locally only.
    _debug = os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=port, debug=_debug, use_reloader=_debug)
