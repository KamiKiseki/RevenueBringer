from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_

from models import ClientReport, Lead, LeadStatus, MessageEvent, MessageStatus, get_session

REPORT_TZ = ZoneInfo(os.getenv("HEALTH_MONITOR_TZ", "America/Chicago"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://autoyieldsystems.com").rstrip("/")

NICHE_AVERAGE_REVENUE: dict[str, int] = {
    "hvac": 5000,
    "roofing": 8000,
    "dental": 3000,
    "law": 7000,
    "real estate": 6000,
    "plumbers": 2000,
    "auto body": 1500,
    "med spa": 2000,
    "gyms": 1000,
    "insurance": 1500,
}


@dataclass
class WeekRange:
    start: date
    end: date

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=REPORT_TZ).astimezone(timezone.utc)

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.end, time.max, tzinfo=REPORT_TZ).astimezone(timezone.utc)


def _niche_key(niche: str) -> str:
    n = (niche or "").strip().lower()
    if "med" in n and "spa" in n:
        return "med spa"
    if "auto" in n and "body" in n:
        return "auto body"
    if "real" in n and "estate" in n:
        return "real estate"
    if "law" in n or "attorney" in n or "legal" in n:
        return "law"
    if "plumb" in n:
        return "plumbers"
    if "gym" in n or "fitness" in n:
        return "gyms"
    for key in NICHE_AVERAGE_REVENUE:
        if key in n:
            return key
    return "default"


def niche_average_revenue(niche: str) -> int:
    return NICHE_AVERAGE_REVENUE.get(_niche_key(niche), 2500)


def previous_week_range(now: datetime | None = None) -> WeekRange:
    now = now or datetime.now(REPORT_TZ)
    this_monday = now.date() - timedelta(days=now.weekday())
    week_end = this_monday - timedelta(days=1)
    week_start = week_end - timedelta(days=6)
    return WeekRange(start=week_start, end=week_end)


def _city_filter(city: str | None):
    c = (city or "").strip()
    if not c:
        return True
    return func.lower(Lead.location).contains(c.lower())


def _niche_filter(niche: str):
    n = (niche or "").strip().lower()
    if not n:
        return True
    return func.lower(Lead.niche).contains(n)


def _lead_ids_for_scope(session, niche: str, city: str | None, week: WeekRange) -> list[int]:
    rows = (
        session.query(Lead.id)
        .filter(
            _niche_filter(niche),
            _city_filter(city),
            Lead.created_at >= week.start_dt,
            Lead.created_at <= week.end_dt,
        )
        .all()
    )
    return [int(r[0]) for r in rows]


def compute_week_metrics(session, *, niche: str, city: str | None, week: WeekRange) -> dict[str, int]:
    lead_ids = _lead_ids_for_scope(session, niche, city, week)
    leads_found = len(lead_ids)

    outreach_sent = 0
    responses_received = 0
    if lead_ids:
        outreach_sent = (
            session.query(MessageEvent)
            .filter(
                MessageEvent.lead_id.in_(lead_ids),
                MessageEvent.channel == "email",
                MessageEvent.direction == "outbound",
                MessageEvent.status == MessageStatus.SENT,
                MessageEvent.created_at >= week.start_dt,
                MessageEvent.created_at <= week.end_dt,
            )
            .count()
        )
        responses_received = (
            session.query(MessageEvent)
            .filter(
                MessageEvent.lead_id.in_(lead_ids),
                or_(
                    MessageEvent.status == MessageStatus.REPLIED,
                    and_(MessageEvent.direction == "inbound", MessageEvent.channel == "email"),
                ),
                MessageEvent.created_at >= week.start_dt,
                MessageEvent.created_at <= week.end_dt,
            )
            .count()
        )

    calls_made = (
        session.query(Lead)
        .filter(
            _niche_filter(niche),
            _city_filter(city),
            Lead.call_status.isnot(None),
            Lead.call_status != "",
            Lead.updated_at >= week.start_dt,
            Lead.updated_at <= week.end_dt,
        )
        .count()
    )

    appointments_booked = (
        session.query(Lead)
        .filter(
            _niche_filter(niche),
            _city_filter(city),
            or_(
                func.lower(Lead.call_status).contains("interested"),
                Lead.tier_offer_triggered.is_(True),
            ),
            Lead.updated_at >= week.start_dt,
            Lead.updated_at <= week.end_dt,
        )
        .count()
    )

    avg = niche_average_revenue(niche)
    return {
        "leads_found": int(leads_found),
        "outreach_sent": int(outreach_sent),
        "responses_received": int(responses_received),
        "calls_made": int(calls_made),
        "appointments_booked": int(appointments_booked),
        "estimated_revenue": int(avg * appointments_booked),
    }


def _client_email(session, client: Lead) -> str | None:
    em = (client.email or "").strip()
    if em:
        return em
    return None


def _upsert_client_report(session, client: Lead, week: WeekRange, metrics: dict[str, int]) -> ClientReport:
    row = (
        session.query(ClientReport)
        .filter(
            ClientReport.client_id == client.id,
            ClientReport.week_start_date == week.start,
            ClientReport.week_end_date == week.end,
        )
        .first()
    )
    payload = {
        "client_email": _client_email(session, client),
        "client_name": (client.owner_name or client.business_name or "Client").strip(),
        "client_niche": (client.niche or "HVAC").strip(),
        "client_city": (client.location or "").strip() or None,
        "leads_found": metrics["leads_found"],
        "outreach_sent": metrics["outreach_sent"],
        "responses_received": metrics["responses_received"],
        "calls_made": metrics["calls_made"],
        "appointments_booked": metrics["appointments_booked"],
        "estimated_revenue": metrics["estimated_revenue"],
    }
    if row:
        for k, v in payload.items():
            setattr(row, k, v)
        row.created_at = datetime.now(timezone.utc)
        return row
    row = ClientReport(
        client_id=client.id,
        week_start_date=week.start,
        week_end_date=week.end,
        **payload,
    )
    session.add(row)
    return row


def _smtp_send_html(to_email: str, subject: str, html: str) -> tuple[bool, str]:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = os.getenv("SMTP_FROM_EMAIL", username).strip()
    if not (host and username and password and from_email and to_email):
        return False, "smtp_not_configured"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(host=host, port=port, timeout=25) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True, "sent"
    except Exception as exc:
        return False, str(exc)


def _delta_arrow(current: int, previous: int) -> str:
    if current > previous:
        return "&#9650;"
    if current < previous:
        return "&#9660;"
    return "&#9644;"


def _format_week_label(week: WeekRange) -> str:
    return week.start.strftime("%b %d, %Y")


def _dashboard_url(client_id: int) -> str:
    return f"{PUBLIC_BASE_URL}/reports/client/{client_id}"


def _dark_page(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg:#050915; --panel:#0b1222; --panel2:#101a30; --text:#e2e8f0; --muted:#94a3b8;
      --line:#1f2a44; --cyan:#22d3ee; --green:#34d399; --warn:#f59e0b;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; color:var(--text);
      background:radial-gradient(1200px 600px at 10% -10%, #0b203a 0%, transparent 60%),
        radial-gradient(900px 500px at 90% 120%, #2a1150 0%, transparent 62%),
        linear-gradient(180deg, #050915 0%, #040711 100%);
      min-height:100vh;
    }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:28px 18px 60px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:16px; }}
  h1,h2 {{ margin:0 0 8px; color:#f8fafc; }}
    .sub {{ color:var(--muted); margin:0 0 18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
    .stat {{ background:var(--panel2); border:1px solid var(--line); border-radius:12px; padding:14px; }}
    .stat .label {{ color:var(--muted); font-size:.9rem; }}
    .stat .value {{ color:var(--green); font-size:1.4rem; font-weight:700; margin-top:6px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ border-bottom:1px solid var(--line); padding:10px 8px; text-align:left; }}
    th {{ color:var(--muted); font-weight:600; }}
    .brand {{ color:var(--cyan); font-weight:700; letter-spacing:.04em; }}
    svg {{ width:100%; height:220px; background:var(--panel2); border:1px solid var(--line); border-radius:12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <p class="brand">AutoYield Systems</p>
    {body_html}
  </div>
</body>
</html>"""


def build_weekly_email_html(
    *,
    client_name: str,
    business_name: str,
    niche: str,
    week: WeekRange,
    metrics: dict[str, int],
    prior: dict[str, int] | None,
    dashboard_url: str,
) -> str:
    prior = prior or {}
    rows = [
        ("Leads Found This Week", metrics["leads_found"], prior.get("leads_found", 0)),
        ("Outreach Contacts Made", metrics["outreach_sent"], prior.get("outreach_sent", 0)),
        ("Responses Received", metrics["responses_received"], prior.get("responses_received", 0)),
        ("Calls Made by AI", metrics["calls_made"], prior.get("calls_made", 0)),
        ("Estimated Revenue Generated", metrics["estimated_revenue"], prior.get("estimated_revenue", 0), True),
    ]
    metric_html = ""
    for row in rows:
        label, cur, prev = row[0], row[1], row[2]
        money = len(row) > 3 and row[3]
        cur_fmt = f"${cur:,.0f}" if money else f"{cur:,}"
        prev_fmt = f"${prev:,.0f}" if money else f"{prev:,}"
        metric_html += (
            f'<tr><td>{label}</td><td style="color:#34d399;font-weight:700;">{cur_fmt}</td>'
            f'<td style="color:#94a3b8;">{prev_fmt} {_delta_arrow(cur, prev)}</td></tr>'
        )
    return f"""
    <div style="background:#0b1222;color:#e2e8f0;padding:24px;font-family:Segoe UI,Arial,sans-serif;">
      <p style="color:#22d3ee;font-weight:700;">AutoYield Systems</p>
      <h1 style="color:#f8fafc;">Weekly Results — Week of {_format_week_label(week)}</h1>
      <p style="color:#94a3b8;">{business_name} · {niche}</p>
      <table style="width:100%;border-collapse:collapse;margin-top:18px;">
        <tr><th align="left">Metric</th><th align="left">This Week</th><th align="left">vs Last Week</th></tr>
        {metric_html}
      </table>
      <p style="margin-top:22px;"><a href="{dashboard_url}" style="color:#22d3ee;">View your live dashboard</a></p>
    </div>
    """


def send_weekly_email(client: Lead, report: ClientReport, prior: ClientReport | None) -> tuple[bool, str]:
    to_email = (report.client_email or "").strip()
    if not to_email:
        return False, "missing_client_email"
    prior_metrics = (
        {
            "leads_found": prior.leads_found,
            "outreach_sent": prior.outreach_sent,
            "responses_received": prior.responses_received,
            "calls_made": prior.calls_made,
            "estimated_revenue": prior.estimated_revenue,
        }
        if prior
        else {}
    )
    metrics = {
        "leads_found": report.leads_found,
        "outreach_sent": report.outreach_sent,
        "responses_received": report.responses_received,
        "calls_made": report.calls_made,
        "estimated_revenue": report.estimated_revenue,
    }
    week = WeekRange(start=report.week_start_date, end=report.week_end_date)
    html = build_weekly_email_html(
        client_name=report.client_name,
        business_name=client.business_name,
        niche=report.client_niche,
        week=week,
        metrics=metrics,
        prior=prior_metrics,
        dashboard_url=_dashboard_url(client.id),
    )
    subject = f"Your AutoYield Systems Weekly Results — Week of {_format_week_label(week)}"
    return _smtp_send_html(to_email, subject, html)


def _prior_week_report(session, client_id: int, week: WeekRange) -> ClientReport | None:
    prev_start = week.start - timedelta(days=7)
    prev_end = week.end - timedelta(days=7)
    return (
        session.query(ClientReport)
        .filter(
            ClientReport.client_id == client_id,
            ClientReport.week_start_date == prev_start,
            ClientReport.week_end_date == prev_end,
        )
        .first()
    )


def generate_report_for_client(client_id: int, *, send_email: bool = False, week: WeekRange | None = None) -> dict[str, Any]:
    week = week or previous_week_range()
    with get_session() as session:
        client = session.query(Lead).filter(Lead.id == client_id).first()
        if not client:
            return {"ok": False, "error": "client_not_found"}
        metrics = compute_week_metrics(
            session,
            niche=client.niche or "HVAC",
            city=client.location,
            week=week,
        )
        prior = _prior_week_report(session, client.id, week)
        report = _upsert_client_report(session, client, week, metrics)
        session.commit()
        session.refresh(report)
        email_status = "skipped"
        if send_email:
            ok, info = send_weekly_email(client, report, prior)
            email_status = "sent" if ok else f"failed:{info}"
        return {
            "ok": True,
            "client_id": client.id,
            "report_id": report.id,
            "week_start": week.start.isoformat(),
            "week_end": week.end.isoformat(),
            "metrics": metrics,
            "email_status": email_status,
            "dashboard_url": _dashboard_url(client.id),
        }


def run_weekly_client_reports(*, send_emails: bool = True) -> dict[str, Any]:
    week = previous_week_range()
    results: list[dict[str, Any]] = []
    with get_session() as session:
        clients = session.query(Lead).filter(Lead.status == LeadStatus.ACTIVE_CLIENT).all()
    for client in clients:
        results.append(generate_report_for_client(client.id, send_email=send_emails, week=week))
    return {"ok": True, "week_start": week.start.isoformat(), "week_end": week.end.isoformat(), "clients": results}


def _aggregate_totals(session) -> dict[str, int]:
    rows = session.query(ClientReport).all()
    if rows:
        return {
            "leads_found": sum(r.leads_found for r in rows),
            "outreach_sent": sum(r.outreach_sent for r in rows),
            "responses_received": sum(r.responses_received for r in rows),
            "calls_made": sum(r.calls_made for r in rows),
            "estimated_revenue": sum(r.estimated_revenue for r in rows),
        }
    return {
        "leads_found": session.query(Lead).count(),
        "outreach_sent": session.query(MessageEvent)
        .filter(MessageEvent.direction == "outbound", MessageEvent.channel == "email")
        .count(),
        "responses_received": session.query(MessageEvent)
        .filter(MessageEvent.status == MessageStatus.REPLIED)
        .count(),
        "calls_made": session.query(Lead).filter(Lead.call_status.isnot(None), Lead.call_status != "").count(),
        "estimated_revenue": 0,
    }


def _niche_breakdown(session) -> list[dict[str, Any]]:
    rows = (
        session.query(
            ClientReport.client_niche,
            func.sum(ClientReport.leads_found),
            func.sum(ClientReport.outreach_sent),
            func.sum(ClientReport.responses_received),
            func.sum(ClientReport.calls_made),
            func.sum(ClientReport.estimated_revenue),
        )
        .group_by(ClientReport.client_niche)
        .all()
    )
    if rows:
        return [
            {
                "niche": r[0],
                "leads_found": int(r[1] or 0),
                "outreach_sent": int(r[2] or 0),
                "responses_received": int(r[3] or 0),
                "calls_made": int(r[4] or 0),
                "estimated_revenue": int(r[5] or 0),
            }
            for r in rows
        ]
    return [
        {
            "niche": n,
            "leads_found": session.query(Lead).filter(func.lower(Lead.niche).contains(n)).count(),
            "outreach_sent": 0,
            "responses_received": 0,
            "calls_made": 0,
            "estimated_revenue": 0,
        }
        for n in sorted(set(NICHE_AVERAGE_REVENUE.keys()))
    ]


def _line_chart_svg(points: list[tuple[str, int]]) -> str:
    if not points:
        return '<svg viewBox="0 0 400 200"><text x="20" y="100" fill="#94a3b8">No weekly data yet</text></svg>'
    width, height, pad = 400, 200, 24
    vals = [p[1] for p in points]
    vmax = max(vals) or 1
    step = (width - pad * 2) / max(len(points) - 1, 1)
    coords = []
    for i, (_, v) in enumerate(points):
        x = pad + i * step
        y = height - pad - (v / vmax) * (height - pad * 2)
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Leads per week">'
        f'<polyline fill="none" stroke="#22d3ee" stroke-width="3" points="{poly}" />'
        f"</svg>"
    )


def render_client_dashboard(client_id: int) -> tuple[str, int]:
    with get_session() as session:
        client = session.query(Lead).filter(Lead.id == client_id).first()
        if not client:
            return "Client not found.", 404
        reports = (
            session.query(ClientReport)
            .filter(ClientReport.client_id == client_id)
            .order_by(ClientReport.week_start_date.desc())
            .limit(8)
            .all()
        )
        all_reports = (
            session.query(ClientReport)
            .filter(ClientReport.client_id == client_id)
            .all()
        )
        totals = {
            "leads_found": sum(r.leads_found for r in all_reports),
            "outreach_sent": sum(r.outreach_sent for r in all_reports),
            "responses_received": sum(r.responses_received for r in all_reports),
            "calls_made": sum(r.calls_made for r in all_reports),
            "estimated_revenue": sum(r.estimated_revenue for r in all_reports),
        }
        chart_points = [(r.week_start_date.isoformat(), r.leads_found) for r in reversed(reports)]
        rows_html = "".join(
            f"<tr><td>{r.week_start_date} – {r.week_end_date}</td>"
            f"<td>{r.leads_found}</td><td>{r.outreach_sent}</td><td>{r.responses_received}</td>"
            f"<td>{r.calls_made}</td><td>${r.estimated_revenue:,}</td></tr>"
            for r in reports
        )
    body = f"""
    <div class="panel">
      <h1>{client.business_name}</h1>
      <p class="sub">{client.niche} · {client.location or 'San Antonio'}</p>
      <div class="grid">
        <div class="stat"><div class="label">Leads Found</div><div class="value">{totals['leads_found']:,}</div></div>
        <div class="stat"><div class="label">Outreach Sent</div><div class="value">{totals['outreach_sent']:,}</div></div>
        <div class="stat"><div class="label">Responses</div><div class="value">{totals['responses_received']:,}</div></div>
        <div class="stat"><div class="label">AI Calls</div><div class="value">{totals['calls_made']:,}</div></div>
        <div class="stat"><div class="label">Est. Revenue</div><div class="value">${totals['estimated_revenue']:,}</div></div>
      </div>
    </div>
    <div class="panel">
      <h2>Leads Found Per Week</h2>
      {_line_chart_svg(chart_points)}
    </div>
    <div class="panel">
      <h2>Weekly Breakdown</h2>
      <table>
        <tr><th>Week</th><th>Leads</th><th>Outreach</th><th>Responses</th><th>Calls</th><th>Revenue</th></tr>
        {rows_html or '<tr><td colspan="6">No weekly reports yet.</td></tr>'}
      </table>
    </div>
    """
    return _dark_page(f"{client.business_name} — AutoYield Reports", body), 200


def render_proof_dashboard() -> str:
    with get_session() as session:
        totals = _aggregate_totals(session)
        niches = _niche_breakdown(session)
    niche_rows = "".join(
        f"<tr><td>{n['niche']}</td><td>{n['leads_found']:,}</td><td>{n['outreach_sent']:,}</td>"
        f"<td>{n['responses_received']:,}</td><td>{n['calls_made']:,}</td><td>${n['estimated_revenue']:,}</td></tr>"
        for n in niches
    )
    body = f"""
    <div class="panel">
      <h1>AutoYield Proof Dashboard</h1>
      <p class="sub">Aggregated client results across active campaigns.</p>
      <div class="grid">
        <div class="stat"><div class="label">Total Leads</div><div class="value">{totals['leads_found']:,}</div></div>
        <div class="stat"><div class="label">Total Outreach</div><div class="value">{totals['outreach_sent']:,}</div></div>
        <div class="stat"><div class="label">Total Responses</div><div class="value">{totals['responses_received']:,}</div></div>
        <div class="stat"><div class="label">Total AI Calls</div><div class="value">{totals['calls_made']:,}</div></div>
        <div class="stat"><div class="label">Est. Revenue</div><div class="value">${totals['estimated_revenue']:,}</div></div>
      </div>
    </div>
    <div class="panel">
      <h2>Results by Niche</h2>
      <table>
        <tr><th>Niche</th><th>Leads</th><th>Outreach</th><th>Responses</th><th>Calls</th><th>Revenue</th></tr>
        {niche_rows}
      </table>
    </div>
    """
    return _dark_page("AutoYield Proof Dashboard", body)
