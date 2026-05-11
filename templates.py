from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


OPS_SIGNATURE = "Operations Team"
OPS_DESK = "Operations Team"
BRAND_NAME = "AutoYield Systems"
DEFAULT_GOVERNING_COUNTY = "Bexar"

_ELLIOT_PROMPT_PATH = Path(__file__).resolve().parent / "elliot_final_prompt.txt"
_FALLBACK_VAPI_PROMPT = (
    "You are Elliot with the Operations Team at AutoYield Systems. "
    "You are professional, concise, and corporate. "
    "Your key question is: 'Do you want the 14-day trial for 300, or the full month for 500?' "
    "If they choose one, capture that exact choice as trial_14 or month_30. "
    "Do not use personal or family references."
)


def _load_elliot_vapi_prompt() -> str:
    try:
        text = _ELLIOT_PROMPT_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return _FALLBACK_VAPI_PROMPT


DEFAULT_VAPI_SYSTEM_PROMPT = _load_elliot_vapi_prompt()


@dataclass(frozen=True)
class OutreachTemplates:
    sms: str
    email_subject: str
    email_body: str


def build_ceo_outreach_templates(
    lead_name: str,
    business_name: str,
    correlation_id: str,
    street_name: str | None = None,
) -> OutreachTemplates:
    display_name = (lead_name or "").strip() or "there"
    biz = (business_name or "").strip() or "your service business"
    local_line = (
        f"We identified your business on {street_name} as a candidate for our lead-routing program."
        if street_name
        else "We identified your business as a candidate for our lead-routing program."
    )
    sms = (
        f"Hello {display_name}, this is {OPS_DESK} at {BRAND_NAME}. "
        f"{local_line} We prepared a localized growth plan for {biz}. "
        f"Reply YES for details. Ref: {correlation_id}"
    )
    email_subject = f"{BRAND_NAME} Operations - Local growth plan for {biz}"
    email_body = (
        f"Hello {display_name},\n\n"
        f"This is {OPS_DESK} at {BRAND_NAME}. {local_line} "
        f"We reviewed your market and drafted a practical lead-generation plan for {biz}.\n\n"
        "If you want the plan, reply to this email with your preferred callback window.\n\n"
        f"Reference: {correlation_id}\n\n"
        f"- {OPS_SIGNATURE}\n"
        f"{BRAND_NAME}"
    )
    return OutreachTemplates(sms=sms, email_subject=email_subject, email_body=email_body)


@dataclass(frozen=True)
class ProofTemplates:
    sms: str
    email_subject: str
    email_body: str


@dataclass(frozen=True)
class OfferTemplates:
    sms: str
    call_script: str
    email_subject: str
    email_body: str


def build_proof_templates(lead_name: str, business_name: str, correlation_id: str) -> ProofTemplates:
    display_name = (lead_name or "").strip() or "there"
    biz = (business_name or "").strip() or "your business"
    sms = (
        f"{OPS_DESK} update: we dispatched a live lead to your primary contact for {biz}. "
        f"Reply YES to discuss scaling this flow. Ref: {correlation_id}"
    )
    email_subject = f"{BRAND_NAME} Operations - Live lead delivered"
    email_body = (
        f"Hi {display_name},\n\n"
        "Our operations team just delivered a live lead to you as part of your proof phase.\n\n"
        "Once validated, we can activate the next wave immediately.\n\n"
        f"Reference: {correlation_id}\n\n"
        f"- {OPS_DESK}\n"
        f"{BRAND_NAME}"
    )
    return ProofTemplates(sms=sms, email_subject=email_subject, email_body=email_body)


def build_tier_offer_templates(lead_name: str, business_name: str, correlation_id: str) -> OfferTemplates:
    display_name = (lead_name or "").strip() or "there"
    biz = (business_name or "").strip() or "your business"
    sms = (
        "AutoYield Operations confirmed your lead proof. We can open your territory now: "
        "14-day trial for $300 or full month for $500. Reply TRIAL or MONTH. "
        f"Ref: {correlation_id}"
    )
    call_script = (
        "This is Elliot with the Operations Team at AutoYield Systems. "
        "We identified your business as a candidate for our lead-routing program. "
        "As proof of performance, two high-intent leads were dispatched to your primary email. "
        "There is no cost for those leads. "
        "For integration, choose one option: a 14-day trial at $300, or a full month at $500. "
        "Which option should we activate?"
    )
    email_subject = f"{BRAND_NAME} Operations - Choose your growth plan for {biz}"
    email_body = (
        f"Hi {display_name},\n\n"
        "You now have proof from the free lead dispatch.\n\n"
        "Choose your next step:\n"
        "- 14-day trial: $300\n"
        "- Full month: $500\n\n"
        "Reply with TRIAL or MONTH and our technical team will issue the agreement and payment link.\n\n"
        f"Reference: {correlation_id}\n\n"
        f"- {OPS_DESK}\n"
        f"{BRAND_NAME}"
    )
    return OfferTemplates(sms=sms, call_script=call_script, email_subject=email_subject, email_body=email_body)


def build_service_agreement_text(
    *,
    agreement_date: str | None,
    provider_name: str,
    provider_business_name: str,
    provider_email: str,
    client_business_name: str,
    client_contact_name: str,
    client_email: str,
    client_phone: str,
    amount_usd: int,
    billing_term: str = "month",
    start_days: int = 1,
    due_day_text: str = "1st",
    termination_notice_days: int = 14,
    payment_method: str = "Stripe",
    payment_link: str = "",
    governing_county: str = DEFAULT_GOVERNING_COUNTY,
) -> str:
    """
    Build the legal agreement body used for PandaDoc token merge.
    Keep this synchronized with the approved contract language.
    """
    date_text = (agreement_date or "").strip() or datetime.utcnow().strftime("%Y-%m-%d")
    link_text = (payment_link or "").strip() or "Stripe Checkout link provided by Provider"
    provider_name = (provider_name or "").strip() or OPS_DESK
    provider_business_name = (provider_business_name or "").strip() or BRAND_NAME
    provider_email = (provider_email or "").strip() or "operations@autoyieldsystems.com"
    client_business_name = (client_business_name or "").strip() or "Client Business"
    client_contact_name = (client_contact_name or "").strip() or "Client Contact"
    client_email = (client_email or "").strip() or "client@example.com"
    client_phone = (client_phone or "").strip() or "N/A"
    billing_term = (billing_term or "month").strip()
    payment_method = (payment_method or "Stripe").strip()
    governing_county = (governing_county or DEFAULT_GOVERNING_COUNTY).strip()

    return f"""SERVICE AGREEMENT

This Service Agreement ("Agreement") is entered into as of {date_text}, by and between:

Service Provider:
Name: {provider_name}
Business Name: {provider_business_name}
Email: {provider_email}
("Provider")

Client:
Business Name: {client_business_name}
Contact Name: {client_contact_name}
Email: {client_email}
Phone: {client_phone}
("Client")

---

1. SERVICES

Provider agrees to perform the following services ("Services"):

   - Online customer acquisition and lead generation for Client's business
   - Digital outreach campaigns targeting potential customers in Client's niche and location
   - Delivery of qualified customer leads, calls, or inquiries to Client through dedicated
     tracking channels (tracking phone number and/or landing page)
   - Monthly reporting showing results delivered

Provider will begin Services within {start_days} business days of receiving full payment.

---

2. PAYMENT TERMS

   a) Client agrees to pay Provider the amount of ${amount_usd} per {billing_term}
      for the Services described above.

   b) Payment is due BEFORE Services begin. Provider is not obligated to start or
      continue Services until payment is received and confirmed.

   c) Payment shall be made via {payment_method} to {link_text}.

   d) Recurring payments are due on the {due_day_text} of each month. Failure to pay within
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
      {billing_term} basis until either party provides written notice of
      termination.

   b) Either party may terminate this Agreement with {termination_notice_days} days written
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
shall be resolved in the courts of {governing_county}, Texas.

---

11. ENTIRE AGREEMENT

This Agreement constitutes the entire agreement between the parties and supersedes
all prior discussions, representations, or agreements. Any modifications must be
made in writing and signed by both parties.

---

SIGNATURES

By signing below, both parties agree to the terms of this Agreement.

Service Provider:

Signature: _______________________________
Printed Name: ____________________________
Date: ___________________________________


Client:

Signature: _______________________________
Printed Name: ____________________________
Business Name: ___________________________
Date: ___________________________________"""
