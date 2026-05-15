from __future__ import annotations

import html
import random
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import func

from client_reporting import PUBLIC_BASE_URL, _dark_page, niche_average_revenue
from models import Lead, LeadStatus, ReferralClick, ReferralVoucher, get_session

_SKIP_SLUG_WORDS = frozenset(
    {
        "llc",
        "inc",
        "corp",
        "co",
        "company",
        "the",
        "and",
        "of",
        "a",
        "an",
        "services",
        "service",
        "san",
        "antonio",
        "tx",
        "texas",
    }
)

_NICHE_SLUG_PREFIX: dict[str, str] = {
    "hvac": "hvac",
    "roofing": "roof",
    "dental": "dental",
    "law": "law",
    "real estate": "re",
    "plumbers": "plumb",
    "auto body": "auto",
    "med spa": "medspa",
    "gyms": "gym",
    "insurance": "ins",
    "default": "local",
}


def _niche_slug_prefix(niche: str) -> str:
    n = (niche or "").strip().lower()
    if "med" in n and "spa" in n:
        return "medspa"
    if "auto" in n and "body" in n:
        return "auto"
    if "real" in n and "estate" in n:
        return "re"
    if "law" in n or "legal" in n or "attorney" in n:
        return "law"
    if "plumb" in n:
        return "plumb"
    if "gym" in n or "fitness" in n:
        return "gym"
    for key, prefix in _NICHE_SLUG_PREFIX.items():
        if key != "default" and key in n:
            return prefix
    return "local"


def _slugify_business(business_name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (business_name or "").lower())
    kept = [w for w in words if len(w) > 1 and w not in _SKIP_SLUG_WORDS]
    return "-".join(kept[:4]) or "partner"


def ensure_client_slug(session, client: Lead) -> str:
    if client.client_slug:
        return client.client_slug
    prefix = _niche_slug_prefix(client.niche or "")
    base = re.sub(r"-+", "-", f"{prefix}-{_slugify_business(client.business_name)}").strip("-")[:96]
    slug = base
    n = 2
    while session.query(Lead).filter(Lead.client_slug == slug, Lead.id != client.id).first():
        slug = f"{base}-{n}"[:120]
        n += 1
    client.client_slug = slug
    session.add(client)
    session.commit()
    return slug


def referral_url(client: Lead) -> str:
    return f"{PUBLIC_BASE_URL}/ref/{client.client_slug or ''}"


def _qr_img_url(url: str) -> str:
    return f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={quote(url, safe='')}"


def _visitor_ip(request) -> str | None:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    return (request.remote_addr or "")[:64] or None


def _unique_voucher_code(session, niche: str) -> str:
    prefix = _niche_slug_prefix(niche).upper()[:8]
    for _ in range(40):
        code = f"AY-{prefix}-{random.randint(1000, 9999)}"
        if not session.query(ReferralVoucher).filter(ReferralVoucher.voucher_code == code).first():
            return code
    return f"AY-{prefix}-{random.randint(100000, 999999)}"


def log_referral_visit(session, client: Lead, *, request, source: str | None = None) -> tuple[ReferralClick, ReferralVoucher]:
    click = ReferralClick(
        client_id=client.id,
        client_slug=client.client_slug or "",
        visitor_ip=_visitor_ip(request),
        user_agent=(request.headers.get("User-Agent") or "")[:512] or None,
        source=(source or request.args.get("utm_source") or request.args.get("src") or "direct")[:255],
        converted=False,
        conversion_value=0,
    )
    session.add(click)
    session.flush()
    voucher = ReferralVoucher(
        voucher_code=_unique_voucher_code(session, client.niche or ""),
        client_id=client.id,
        referral_click_id=click.id,
        redeemed=False,
    )
    session.add(voucher)
    session.commit()
    session.refresh(click)
    session.refresh(voucher)
    return click, voucher


def mark_referral_conversion(session, click_id: int, client: Lead) -> tuple[bool, str]:
    click = (
        session.query(ReferralClick)
        .filter(ReferralClick.id == click_id, ReferralClick.client_id == client.id)
        .first()
    )
    if not click:
        return False, PUBLIC_BASE_URL
    if not click.converted:
        click.converted = True
        click.conversion_value = niche_average_revenue(client.niche or "")
        session.add(click)
        session.commit()
    return True, _cta_href(client)[1]


def redeem_voucher(session, client_id: int, voucher_code: str) -> dict[str, Any]:
    code = (voucher_code or "").strip().upper()
    row = (
        session.query(ReferralVoucher)
        .filter(ReferralVoucher.client_id == client_id, ReferralVoucher.voucher_code == code)
        .first()
    )
    if not row:
        return {"ok": False, "error": "voucher_not_found"}
    if row.redeemed:
        return {"ok": False, "error": "already_redeemed"}
    row.redeemed = True
    row.redeemed_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return {"ok": True, "voucher_code": code}


def client_referral_stats(session, client_id: int) -> dict[str, int]:
    clicks = session.query(ReferralClick).filter(ReferralClick.client_id == client_id).count()
    conversions = (
        session.query(ReferralClick)
        .filter(ReferralClick.client_id == client_id, ReferralClick.converted.is_(True))
        .count()
    )
    vouchers = session.query(ReferralVoucher).filter(ReferralVoucher.client_id == client_id).count()
    redeemed = (
        session.query(ReferralVoucher)
        .filter(ReferralVoucher.client_id == client_id, ReferralVoucher.redeemed.is_(True))
        .count()
    )
    client = session.query(Lead).filter(Lead.id == client_id).first()
    avg = niche_average_revenue(client.niche or "") if client else 2500
    return {
        "clicks": int(clicks),
        "conversions": int(conversions),
        "vouchers_generated": int(vouchers),
        "vouchers_redeemed": int(redeemed),
        "estimated_revenue": int(redeemed * avg),
    }


def aggregate_referral_proof(session) -> dict[str, Any]:
    total_clicks = session.query(ReferralClick).count()
    total_conversions = session.query(ReferralClick).filter(ReferralClick.converted.is_(True)).count()
    total_vouchers = session.query(ReferralVoucher).count()
    total_redeemed = session.query(ReferralVoucher).filter(ReferralVoucher.redeemed.is_(True)).count()
    revenue = 0
    for v in session.query(ReferralVoucher).filter(ReferralVoucher.redeemed.is_(True)).all():
        lead = session.get(Lead, v.client_id)
        revenue += niche_average_revenue(lead.niche or "") if lead else 2500
    niche_rows = (
        session.query(Lead.niche, func.count(ReferralVoucher.id))
        .join(ReferralVoucher, ReferralVoucher.client_id == Lead.id)
        .filter(ReferralVoucher.redeemed.is_(True))
        .group_by(Lead.niche)
        .all()
    )
    by_niche = [
        {
            "niche": niche or "Unknown",
            "redeemed": int(count or 0),
            "estimated_revenue": int((count or 0) * niche_average_revenue(niche or "")),
        }
        for niche, count in niche_rows
    ]
    by_niche.sort(key=lambda x: x["estimated_revenue"], reverse=True)
    return {
        "total_clicks": int(total_clicks),
        "total_conversions": int(total_conversions),
        "total_vouchers": int(total_vouchers),
        "total_redeemed": int(total_redeemed),
        "total_revenue": int(revenue),
        "by_niche": by_niche,
    }


def _cta_href(client: Lead) -> tuple[str, str]:
    phone = re.sub(r"[^\d+]", "", client.phone or "")
    if phone:
        return "Call Now", f"tel:{phone}"
    if client.website:
        site = client.website.strip()
        if not site.startswith(("http://", "https://")):
            site = f"https://{site}"
        return "Book Now", site
    return "Contact Us", PUBLIC_BASE_URL


def render_referral_landing(client: Lead, voucher: ReferralVoucher, click: ReferralClick) -> str:
    biz = html.escape(client.business_name or "Our Partner")
    niche = html.escape(client.niche or "Local Services")
    code = html.escape(voucher.voucher_code)
    cta_label, _ = _cta_href(client)
    go_url = f"{PUBLIC_BASE_URL}/ref/{client.client_slug}/go/{click.id}"
    body = f"""
    <div class="ref-hero">
      <p class="brand">AutoYield Systems</p>
      <p class="eyebrow">Exclusive referral offer</p>
      <h1>You've been referred by {biz}</h1>
      <p class="sub">{niche}</p>
      <p class="lead">Claim your exclusive offer. Show this voucher when you visit or call.</p>
      <div class="voucher-box">
        <p class="voucher-label">Your voucher code</p>
        <p class="voucher-code">{code}</p>
      </div>
      <a class="cta" href="{html.escape(go_url)}">{html.escape(cta_label)}</a>
      <p class="footer-note">This offer was brought to you by AutoYield Systems.</p>
    </div>
    <style>
      .ref-hero {{ max-width:520px; margin:0 auto; text-align:center; padding:12px 8px 40px; }}
      .brand {{ color:#64748b; font-size:.8rem; margin:0 0 12px; }}
      .eyebrow {{ color:#22d3ee; font-size:.85rem; letter-spacing:.08em; text-transform:uppercase; margin:0 0 8px; }}
      .lead {{ color:#94a3b8; line-height:1.5; margin:0 0 22px; }}
      .voucher-box {{ background:#101a30; border:1px dashed #22d3ee; border-radius:14px; padding:20px; margin:0 0 24px; }}
      .voucher-label {{ color:#94a3b8; margin:0 0 6px; font-size:.9rem; }}
      .voucher-code {{ color:#34d399; font-size:1.8rem; font-weight:700; letter-spacing:.12em; margin:0; }}
      .cta {{
        display:block; width:100%; max-width:320px; margin:0 auto 18px;
        background:linear-gradient(90deg,#0891b2,#22d3ee); color:#041018;
        font-weight:700; font-size:1.1rem; padding:16px 20px; border-radius:12px; text-decoration:none;
      }}
      .footer-note {{ color:#64748b; font-size:.85rem; margin:18px 0 0; }}
      @media (max-width:480px) {{ .voucher-code {{ font-size:1.4rem; }} h1 {{ font-size:1.35rem; }} }}
    </style>
    """
    return _dark_page(f"Referral — {client.business_name}", body)


def _unredeemed_vouchers_html(client_id: int) -> str:
    with get_session() as session:
        rows = (
            session.query(ReferralVoucher)
            .filter(ReferralVoucher.client_id == client_id, ReferralVoucher.redeemed.is_(False))
            .order_by(ReferralVoucher.created_at.desc())
            .limit(25)
            .all()
        )
    if not rows:
        return "<p class='sub'>No unredeemed vouchers yet.</p>"
    items = "".join(
        f"""<tr>
          <td><code>{html.escape(v.voucher_code)}</code></td>
          <td>{v.created_at.strftime('%Y-%m-%d %H:%M') if v.created_at else ''}</td>
          <td>
            <form method="post" action="/reports/client/{client_id}/redeem-voucher" style="margin:0;">
              <input type="hidden" name="voucher_code" value="{html.escape(v.voucher_code)}" />
              <button type="submit" class="btn-redeem">Mark redeemed</button>
            </form>
          </td>
        </tr>"""
        for v in rows
    )
    return f"""
    <h3 style="margin-top:20px;">Unredeemed vouchers</h3>
    <table>
      <tr><th>Code</th><th>Issued</th><th>Action</th></tr>
      {items}
    </table>
    """


def render_referral_client_panel(client: Lead, stats: dict[str, int]) -> str:
    url = referral_url(client)
    qr = _qr_img_url(url)
    return f"""
    <div class="panel">
      <h2>Referral Program</h2>
      <p class="sub">Share your link or QR code. Mark vouchers redeemed when the customer shows up.</p>
      <p><strong>Your referral URL</strong><br>
        <a href="{html.escape(url)}" style="color:#22d3ee;word-break:break-all;">{html.escape(url)}</a></p>
      <div style="text-align:center;margin:16px 0;">
        <img src="{html.escape(qr)}" alt="Referral QR code" width="220" height="220" style="border-radius:12px;border:1px solid #1f2a44;" />
        <p class="sub" style="margin-top:8px;">Print or display this QR at your business</p>
      </div>
      <div class="grid">
        <div class="stat"><div class="label">Link Clicks</div><div class="value">{stats['clicks']:,}</div></div>
        <div class="stat"><div class="label">CTA Clicks</div><div class="value">{stats['conversions']:,}</div></div>
        <div class="stat"><div class="label">Vouchers Issued</div><div class="value">{stats['vouchers_generated']:,}</div></div>
        <div class="stat"><div class="label">Redeemed</div><div class="value">{stats['vouchers_redeemed']:,}</div></div>
        <div class="stat"><div class="label">Est. Revenue</div><div class="value">${stats['estimated_revenue']:,}</div></div>
      </div>
      {_unredeemed_vouchers_html(client.id)}
    </div>
    <style>
      .btn-redeem {{ background:#22d3ee;color:#041018;border:0;padding:6px 12px;border-radius:8px;font-weight:600;cursor:pointer; }}
    </style>
    """


def render_proof_referral_section(proof: dict[str, Any]) -> str:
    niche_rows = "".join(
        f"<tr><td>{html.escape(n['niche'])}</td><td>{n['redeemed']:,}</td><td>${n['estimated_revenue']:,}</td></tr>"
        for n in proof.get("by_niche", [])
    )
    return f"""
    <div class="panel">
      <h2>Customer Referrals (Proof)</h2>
      <p class="sub">Customers referred to active clients via AutoYield tracking links and vouchers.</p>
      <div class="grid">
        <div class="stat"><div class="label">Referral Clicks</div><div class="value">{proof['total_clicks']:,}</div></div>
        <div class="stat"><div class="label">Vouchers Generated</div><div class="value">{proof['total_vouchers']:,}</div></div>
        <div class="stat"><div class="label">Vouchers Redeemed</div><div class="value">{proof['total_redeemed']:,}</div></div>
        <div class="stat"><div class="label">CTA Conversions</div><div class="value">{proof['total_conversions']:,}</div></div>
        <div class="stat"><div class="label">Est. Client Revenue</div><div class="value">${proof['total_revenue']:,}</div></div>
      </div>
      <table style="margin-top:16px;">
        <tr><th>Niche</th><th>Redeemed vouchers</th><th>Est. revenue</th></tr>
        {niche_rows or '<tr><td colspan="3">No redeemed vouchers yet.</td></tr>'}
      </table>
    </div>
    """


def render_admin_clients() -> str:
    with get_session() as session:
        clients = session.query(Lead).filter(Lead.status == LeadStatus.ACTIVE_CLIENT).order_by(Lead.id.asc()).all()
        rows_html = ""
        for c in clients:
            ensure_client_slug(session, c)
            session.refresh(c)
            stats = client_referral_stats(session, c.id)
            url = referral_url(c)
            rows_html += f"""
            <tr>
              <td>{html.escape(c.business_name)}</td>
              <td>{html.escape(c.niche or '')}</td>
              <td><a href="{html.escape(url)}" style="color:#22d3ee;">/ref/{html.escape(c.client_slug or '')}</a></td>
              <td>{stats['clicks']:,}</td>
              <td>{stats['conversions']:,}</td>
              <td>${stats['estimated_revenue']:,}</td>
              <td><a href="/reports/client/{c.id}" style="color:#22d3ee;">Dashboard</a></td>
            </tr>
            """
    body = f"""
    <div class="panel">
      <h1>Active Clients — Referrals</h1>
      <p class="sub">Admin view of referral performance per client.</p>
      <table>
        <tr><th>Business</th><th>Niche</th><th>Referral URL</th><th>Clicks</th><th>Conversions</th><th>Est. revenue</th><th></th></tr>
        {rows_html or '<tr><td colspan="7">No active clients.</td></tr>'}
      </table>
    </div>
    """
    return _dark_page("AutoYield — Client Referrals Admin", body)
