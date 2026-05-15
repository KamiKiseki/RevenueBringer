"""Reflex State — DB-backed vars, scout/outreach, KPI polling."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

import reflex as rx
import requests
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from models import (
    Agreement,
    AgreementStatus,
    ContactSubmission,
    Lead,
    LeadStatus,
    MessageEvent,
    MessageStatus,
    SystemLog,
    get_session,
    get_setting,
    init_db,
    set_setting,
)
from outreach import process_queued_leads
from scout import fetch_hvac_leads, upsert_scraped_leads
from tracking import count_verified_replies, verified_inbound_reply_clause


def _webhook_base_url() -> str:
    return os.getenv("WEBHOOK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


class State(rx.State):
    """Stateful brain: UI binds here; DB + scout/outreach run inside these methods."""

    niche_val: str = ""
    location_val: str = ""
    lead_count: int = 100
    lead_count_str: str = "100"
    search_query: str = ""
    agreement_search_query: str = ""
    command_center_tab: str = "dashboard"
    status_filter: str = "all"
    system_message: str = ""
    vapi_total_calls_cache: int = 0
    db_sync_tick: int = 0
    automation_running: bool = False
    simulate_mode: bool = True
    daily_target: int = 50
    backend_health_ok: bool = False
    backend_total_leads: int = 0
    backend_conversions: int = 0
    backend_revenue_cents: int = 0
    backend_contacted: int = 0
    backend_replies: int = 0
    stripe_webhook_ready: bool = False
    stripe_last_event_type: str = ""
    stripe_last_event_at: str = ""
    latest_report_subject: str = ""
    latest_report_status: str = ""
    cap_limit: int = 50
    cap_used_today: int = 0
    cap_remaining_today: int = 50
    current_target_niche: str = ""
    current_target_location: str = ""
    automation_error_feed: list[dict[str, str]] = []
    system_logs_rows: list[dict[str, str]] = []
    daily_reports_enabled: bool = True
    today_leads_scraped: int = 0
    today_emails_sent: int = 0
    today_calls_made: int = 0
    today_replies_received: int = 0
    month_deals_closed: int = 0
    month_revenue_collected: int = 0
    cc_calls_interested: int = 0
    cc_calls_not_interested: int = 0
    cc_calls_callbacks: int = 0

    def set_niche_val(self, value: str):
        self.niche_val = value

    def set_location_val(self, value: str):
        self.location_val = value

    def set_search_query(self, value: str):
        self.search_query = value

    def set_status_filter(self, value: str):
        self.status_filter = value

    def set_agreement_search_query(self, value: str):
        self.agreement_search_query = value

    def cc_dashboard(self):
        self.command_center_tab = "dashboard"

    def cc_lead_engine(self):
        self.command_center_tab = "lead_engine"

    def cc_outreach(self):
        self.command_center_tab = "outreach"

    def cc_contact_submissions(self):
        self.command_center_tab = "contact_submissions"
        self.sync_db_views()

    def cc_tracking(self):
        self.command_center_tab = "tracking"

    def cc_payments(self):
        self.command_center_tab = "payments"

    def cc_behavior_ai(self):
        self.command_center_tab = "behavior_ai"

    def cc_deal_vault(self):
        self.command_center_tab = "deal_vault"

    def cc_dm_generator(self):
        self.command_center_tab = "dm_generator"

    def cc_cold_caller(self):
        self.command_center_tab = "cold_caller"

    def cc_automation(self):
        self.command_center_tab = "automation"

    def cc_live_monitor(self):
        self.command_center_tab = "live_monitor"

    def cc_outreach_config(self):
        self.command_center_tab = "outreach_config"

    def cc_system_logs(self):
        self.command_center_tab = "system_logs"

    def cc_system_health(self):
        self.command_center_tab = "system_health"

    @rx.var
    def webhook_health_url(self) -> str:
        return _webhook_base_url() + "/health"

    @rx.var
    def webhook_stripe_url(self) -> str:
        return _webhook_base_url() + "/webhooks/stripe"

    @rx.var
    def webhook_vapi_url(self) -> str:
        return _webhook_base_url() + "/webhooks/vapi"

    @rx.var
    def webhook_pandadoc_url(self) -> str:
        return _webhook_base_url() + "/webhooks/pandadoc"

    @rx.var
    def queued_leads_count(self) -> int:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                return int(session.query(Lead).filter(Lead.status == LeadStatus.QUEUED).count())
        except OperationalError:
            return 0

    @rx.var
    def outreach_env_summary(self) -> str:
        parts = []
        parts.append("Instantly API key: set" if os.getenv("INSTANTLY_API_KEY") else "Instantly API key: not set")
        parts.append("Campaign ID: set" if os.getenv("INSTANTLY_CAMPAIGN_ID") else "Campaign ID: not set")
        return " · ".join(parts)

    def on_load(self):
        init_db()
        self.niche_val = get_setting("last_niche", self.niche_val)
        self.location_val = get_setting("last_location", self.location_val)
        self.daily_reports_enabled = get_setting("daily_reports_enabled", "1") in {"1", "true", "True"}
        try:
            self.lead_count = int(get_setting("last_lead_count", str(self.lead_count)))
        except ValueError:
            pass
        self.lead_count_str = str(self.lead_count)
        self.refresh_kpis()
        self.refresh_backend_snapshot()
        self.refresh_today_metrics()
        self.refresh_cold_caller_summary()

    def set_lead_count_str(self, value: str):
        self.lead_count_str = value
        try:
            self.lead_count = max(1, min(500, int(value)))
        except ValueError:
            pass

    def run_scout(self):
        set_setting("last_niche", self.niche_val.strip())
        set_setting("last_location", self.location_val.strip())
        set_setting("last_lead_count", str(self.lead_count))
        scraped = fetch_hvac_leads(
            niche=self.niche_val.strip() or "HVAC",
            location=self.location_val.strip() or "United States",
            limit=max(1, min(500, self.lead_count)),
        )
        inserted, skipped = upsert_scraped_leads(scraped)
        self.system_message = f"Scout done. New: {inserted}, Skipped: {skipped}"
        self.sync_db_views()
        self.refresh_today_metrics()

    def send_outreach(self):
        try:
            sent = process_queued_leads()
            self.system_message = f"Instantly: queued leads emailed = {sent}"
        except Exception as exc:
            self.system_message = f"Outreach error: {exc}"
        self.refresh_today_metrics()

    def clear_message(self):
        self.system_message = ""

    def sync_db_views(self, _event=None, **_kw):
        """Bump revision so SQL-backed @rx.var reruns.

        Bound from `on_click` (one event arg), `rx.moment` on_change (may pass `_args=...`), or called with no args.
        """
        self.db_sync_tick += 1

    def toggle_daily_reports_enabled(self, value: bool):
        self.daily_reports_enabled = bool(value)
        set_setting("daily_reports_enabled", "1" if self.daily_reports_enabled else "0")

    def run_health_check(self):
        """Run local watchdog and refresh status panels."""
        try:
            subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    "scripts\\watchdog.ps1",
                    "-SkipWriteTest",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.system_message = "Health check complete: all systems healthy."
        except subprocess.CalledProcessError as exc:
            detail = (exc.stdout or exc.stderr or "unknown error").strip()
            self.system_message = f"Health check failed: {detail[:180]}"
        self.refresh_backend_snapshot()
        self.sync_db_views()

    def refresh_signals(self):
        """Manual refresh: reload DB-backed tables plus Vapi totals."""
        self.sync_db_views()
        self.refresh_kpis()
        self.refresh_backend_snapshot()

    def refresh_kpis(self):
        """Pull call volume from Vapi and keep local fallback."""
        api_key = os.getenv("VAPI_API_KEY", "")
        if not api_key:
            return
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            resp = requests.get("https://api.vapi.ai/call", headers=headers, timeout=20)
            if resp.status_code < 300:
                body = resp.json()
                if isinstance(body, list):
                    self.vapi_total_calls_cache = len(body)
                elif isinstance(body, dict):
                    if isinstance(body.get("data"), list):
                        self.vapi_total_calls_cache = len(body["data"])
                    else:
                        self.vapi_total_calls_cache = int(body.get("total") or 0)
        except Exception:
            pass

    def toggle_automation(self):
        endpoint = "start" if not self.automation_running else "stop"
        payload = {
            "niche": self.niche_val.strip() or None,
            "location": self.location_val.strip() or None,
            "daily_target": int(self.daily_target or self.lead_count or 50),
            "simulate": bool(self.simulate_mode),
        }
        try:
            resp = requests.post(f"{_webhook_base_url()}/automation/{endpoint}", json=payload, timeout=20)
            if resp.status_code < 300:
                data = resp.json()
                self.automation_running = bool(data.get("enabled", False))
                self.system_message = (
                    "Automation started." if self.automation_running else "Automation stopped."
                )
            else:
                self.system_message = f"Automation toggle failed: HTTP {resp.status_code}"
        except Exception as exc:
            self.system_message = f"Automation toggle error: {exc}"
        self.refresh_backend_snapshot()

    def global_killswitch(self):
        """Immediate hard stop for automation loop."""
        try:
            resp = requests.post(f"{_webhook_base_url()}/automation/stop", timeout=20)
            if resp.status_code < 300:
                self.automation_running = False
                self.system_message = "Global killswitch activated. Automation is now STOPPED."
            else:
                self.system_message = f"Killswitch failed: HTTP {resp.status_code}"
        except Exception as exc:
            self.system_message = f"Killswitch error: {exc}"
        self.refresh_backend_snapshot()

    def run_simulation_cycle(self):
        payload = {
            "niche": self.niche_val.strip() or None,
            "location": self.location_val.strip() or None,
            "daily_target": int(self.daily_target or self.lead_count or 50),
            "simulate": True,
            "send_report": False,
        }
        try:
            resp = requests.post(f"{_webhook_base_url()}/automation/run-once", json=payload, timeout=60)
            data = resp.json()
            if resp.status_code < 300 and data.get("ok"):
                self.system_message = (
                    "Simulation cycle complete. "
                    f"Leads inserted={data.get('lead_engine', {}).get('inserted', 0)}, "
                    f"outreach={data.get('outreach_sent', 0)}, "
                    f"conversions={data.get('simulated_conversions', 0)}"
                )
            else:
                self.system_message = f"Simulation failed: {data.get('error', 'unknown error')}"
        except Exception as exc:
            self.system_message = f"Simulation error: {exc}"
        self.refresh_backend_snapshot()
        self.sync_db_views()

    def send_daily_report_now(self):
        try:
            resp = requests.post(f"{_webhook_base_url()}/reports/daily/send", timeout=30)
            data = resp.json()
            if resp.status_code < 300 and data.get("ok"):
                self.system_message = f"Daily report sent ({data.get('status', 'ok')})."
            else:
                self.system_message = f"Daily report failed: HTTP {resp.status_code}"
        except Exception as exc:
            self.system_message = f"Daily report error: {exc}"
        self.refresh_backend_snapshot()

    def force_next_target(self):
        try:
            resp = requests.post(f"{_webhook_base_url()}/automation/next-target", timeout=20)
            if resp.status_code < 300:
                data = resp.json()
                tgt = data.get("target") or {}
                self.current_target_niche = str(tgt.get("niche") or "")
                self.current_target_location = str(tgt.get("location") or "")
                self.system_message = (
                    f"Target rotated to {self.current_target_niche} in {self.current_target_location}."
                )
            else:
                self.system_message = f"Target rotation failed: HTTP {resp.status_code}"
        except Exception as exc:
            self.system_message = f"Target rotation error: {exc}"
        self.refresh_backend_snapshot()

    def retry_last_failed(self):
        try:
            resp = requests.post(f"{_webhook_base_url()}/automation/retry-last-failed", timeout=90)
            data = resp.json()
            if resp.status_code < 300 and data.get("ok"):
                self.system_message = "Retry executed successfully."
            else:
                self.system_message = f"Retry failed: {data.get('error', 'unknown error')}"
        except Exception as exc:
            self.system_message = f"Retry error: {exc}"
        self.refresh_backend_snapshot()
        self.sync_db_views()

    def refresh_backend_snapshot(self):
        base = _webhook_base_url()
        self.cap_limit = int(os.getenv("OUTREACH_DAILY_CAP", "50") or "50")
        # health
        try:
            h = requests.get(f"{base}/health", timeout=10)
            self.backend_health_ok = h.status_code < 300 and bool(h.json().get("ok"))
        except Exception:
            self.backend_health_ok = False

        # automation status
        try:
            s = requests.get(f"{base}/automation/status", timeout=10)
            if s.status_code < 300:
                data = s.json()
                self.automation_running = bool(data.get("enabled", False))
                self.simulate_mode = bool(data.get("simulate", self.simulate_mode))
                self.daily_target = int(data.get("daily_target", self.daily_target))
        except Exception:
            pass

        # metrics
        try:
            m = requests.get(f"{base}/tracking/metrics", timeout=15)
            if m.status_code < 300:
                d = m.json()
                self.backend_total_leads = int(d.get("total_leads") or 0)
                self.backend_contacted = int(d.get("contacted") or 0)
                self.backend_replies = int(d.get("replies") or 0)
                self.backend_conversions = int(d.get("conversions") or 0)
                self.backend_revenue_cents = int(d.get("revenue_cents") or 0)
        except Exception:
            # No mock values: fallback directly to DB for real numbers.
            try:
                with get_session() as session:
                    self.backend_total_leads = int(session.query(Lead).count())
                    self.backend_contacted = int(
                        session.query(Lead).filter(Lead.status != LeadStatus.QUEUED).count()
                    )
                    self.backend_replies = count_verified_replies(session)
                    self.backend_conversions = int(
                        session.query(Lead).filter(Lead.status == LeadStatus.ACTIVE_CLIENT).count()
                    )
                    cents = (
                        session.query(func.coalesce(func.sum(Agreement.stripe_plan_amount_cents), 0))
                        .filter(
                            Agreement.signing_status == AgreementStatus.PAID,
                            Agreement.stripe_transaction_id.isnot(None),
                        )
                        .scalar()
                    )
                    self.backend_revenue_cents = int(cents or 0)
            except Exception:
                pass

        # latest report
        try:
            r = requests.get(f"{base}/reports/daily/latest", timeout=10)
            if r.status_code < 300:
                d = r.json()
                rep = d.get("latest_report") or {}
                self.latest_report_subject = str(rep.get("subject") or "")
                self.latest_report_status = str(rep.get("status") or "")
        except Exception:
            pass

        # stripe handshake status
        try:
            s = requests.get(f"{base}/integrations/stripe/status", timeout=10)
            if s.status_code < 300:
                d = s.json()
                self.stripe_webhook_ready = bool(d.get("api_key_set")) and bool(d.get("webhook_secret_set"))
                self.stripe_last_event_type = str(d.get("last_event_type") or "")
                self.stripe_last_event_at = str(d.get("last_event_at") or "")
        except Exception:
            self.stripe_webhook_ready = False

        # daily cap + current random target
        try:
            t = requests.get(f"{base}/automation/today-summary", timeout=10)
            if t.status_code < 300:
                d = t.json()
                self.cap_limit = int(d.get("daily_cap") or self.cap_limit)
                self.cap_used_today = int(d.get("sent_today") or 0)
                self.cap_remaining_today = int(d.get("remaining_today") or 0)
                tgt = d.get("current_target") or {}
                self.current_target_niche = str(tgt.get("niche") or "")
                self.current_target_location = str(tgt.get("location") or "")
        except Exception:
            try:
                with get_session() as session:
                    sent = int(
                        session.query(MessageEvent)
                        .filter(
                            MessageEvent.direction == "outbound",
                            MessageEvent.status == MessageStatus.SENT,
                        )
                        .count()
                    )
                    self.cap_used_today = sent
                    self.cap_remaining_today = max(0, self.cap_limit - sent)
            except Exception:
                pass

        # error safety net feed
        try:
            e = requests.get(f"{base}/automation/errors", timeout=15)
            if e.status_code < 300:
                data = e.json()
                out: list[dict[str, str]] = []
                for row in data.get("automation_runs", [])[:5]:
                    out.append(
                        {
                            "kind": "run",
                            "at": str(row.get("ended_at") or row.get("started_at") or ""),
                            "detail": str(row.get("notes") or "automation failed"),
                        }
                    )
                for row in data.get("message_failures", [])[:5]:
                    out.append(
                        {
                            "kind": "message",
                            "at": str(row.get("at") or ""),
                            "detail": str(row.get("error") or "message failed"),
                        }
                    )
                self.automation_error_feed = out[:5]
        except Exception:
            self.automation_error_feed = []

        # system logs feed
        try:
            logs = requests.get(f"{base}/system/logs", timeout=15)
            if logs.status_code < 300:
                payload = logs.json()
                rows: list[dict[str, str]] = []
                for row in payload.get("rows", [])[:20]:
                    rows.append(
                        {
                            "at": str(row.get("at") or ""),
                            "level": str(row.get("level") or ""),
                            "source": str(row.get("source") or ""),
                            "action": str(row.get("action") or ""),
                            "detail": str(row.get("detail") or ""),
                            "correlation_id": str(row.get("correlation_id") or ""),
                        }
                    )
                self.system_logs_rows = rows
        except Exception:
            self.system_logs_rows = []
        self.refresh_today_metrics()
        self.refresh_cold_caller_summary()

    def refresh_today_metrics(self):
        now = datetime.now(timezone.utc)
        start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        try:
            with get_session() as session:
                self.today_leads_scraped = int(
                    session.query(Lead).filter(Lead.created_at >= start_today).count()
                )
                self.today_emails_sent = int(
                    session.query(MessageEvent)
                    .filter(
                        MessageEvent.created_at >= start_today,
                        MessageEvent.direction == "outbound",
                        MessageEvent.channel == "email",
                        MessageEvent.status == MessageStatus.SENT,
                    )
                    .count()
                )
                self.today_replies_received = int(
                    session.query(MessageEvent)
                    .filter(
                        MessageEvent.created_at >= start_today,
                        verified_inbound_reply_clause(),
                    )
                    .count()
                )
                self.today_calls_made = int(
                    session.query(Lead)
                    .filter(
                        Lead.updated_at >= start_today,
                        Lead.call_status.isnot(None),
                    )
                    .count()
                )
                self.month_deals_closed = int(
                    session.query(Agreement)
                    .filter(
                        Agreement.created_at >= start_month,
                        Agreement.signing_status == AgreementStatus.PAID,
                    )
                    .count()
                )
                cents = (
                    session.query(func.coalesce(func.sum(Agreement.stripe_plan_amount_cents), 0))
                    .filter(
                        Agreement.created_at >= start_month,
                        Agreement.signing_status == AgreementStatus.PAID,
                    )
                    .scalar()
                )
                self.month_revenue_collected = int((cents or 0) // 100)
        except OperationalError:
            pass

    def refresh_cold_caller_summary(self):
        try:
            with get_session() as session:
                leads = (
                    session.query(Lead.call_status)
                    .filter(Lead.call_status.isnot(None))
                    .order_by(Lead.updated_at.desc())
                    .limit(250)
                    .all()
                )
            vals = [str(v[0] or "").replace("_", " ").title() for v in leads]
            self.cc_calls_interested = len([v for v in vals if "Interested" in v and "Not Interested" not in v])
            self.cc_calls_not_interested = len([v for v in vals if "Not Interested" in v])
            self.cc_calls_callbacks = len([v for v in vals if "Callback" in v])
        except OperationalError:
            pass

    @rx.var
    def filtered_leads(self) -> list[dict[str, str | int]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                q = session.query(Lead).order_by(Lead.id.desc())
                if self.status_filter != "all":
                    q = q.filter(Lead.status == LeadStatus(self.status_filter))
                sq = (self.search_query or "").strip()
                if sq:
                    q = q.filter(Lead.business_name.ilike(f"%{sq}%"))
                rows = q.all()
                out: list[dict[str, str | int]] = []
                for l in rows:
                    st = l.status.value if hasattr(l.status, "value") else str(l.status)
                    out.append(
                        {
                            "id": int(l.id),
                            "business": str(l.business_name),
                            "niche": str(l.niche),
                            "location": str(l.location or "—"),
                            "status": str(st),
                            "email": str(l.email or "—"),
                            "phone": str(l.phone or "—"),
                            "leads_sent": int(l.leads_sent or 0),
                        }
                    )
                return out
        except OperationalError:
            return []

    @rx.var
    def agreement_rows(self) -> list[dict[str, str | int]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                q = session.query(Agreement).order_by(Agreement.id.desc())
                sq = (self.agreement_search_query or "").strip()
                if sq:
                    q = q.filter(Agreement.client_name.ilike(f"%{sq}%"))
                rows = q.all()
                out: list[dict[str, str | int]] = []
                for a in rows:
                    status = a.signing_status.value if hasattr(a.signing_status, "value") else str(a.signing_status)
                    out.append(
                        {
                            "id": int(a.id),
                            "client_name": str(a.client_name),
                            "correlation_id": str(a.correlation_id),
                            "pandadoc_id": str(a.pandadoc_id or "—"),
                            "signing_status": str(status),
                            "offer_kind": str(a.offer_kind or "—"),
                            "plan_amount_cents": int(a.stripe_plan_amount_cents or 0),
                            "stripe_transaction_id": str(a.stripe_transaction_id or "—"),
                            "stripe_checkout_url": str(a.stripe_checkout_url or ""),
                            "signed_pdf_url": str(a.signed_pdf_url or ""),
                            "created_at": str(a.created_at),
                        }
                    )
                return out
        except OperationalError:
            return []

    @rx.var
    def total_calls_made(self) -> int:
        if self.vapi_total_calls_cache > 0:
            return int(self.vapi_total_calls_cache)
        return len(self.filtered_leads)

    @rx.var
    def conversion_rate_text(self) -> str:
        total = self.total_calls_made
        agreements = len(self.agreement_rows)
        if total <= 0:
            return "0.0%"
        return f"{(agreements / total) * 100:.1f}%"

    @rx.var
    def proof_leads_total(self) -> int:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                rows = session.query(Lead.leads_sent).all()
                return int(sum(int((r[0] or 0)) for r in rows))
        except OperationalError:
            return 0

    @rx.var
    def signed_agreements_count(self) -> int:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                return int(
                    session.query(Agreement)
                    .filter(
                        Agreement.signing_status.in_(
                            [AgreementStatus.SIGNED, AgreementStatus.PAID]
                        )
                    )
                    .count()
                )
        except OperationalError:
            return 0

    @rx.var
    def conversion_funnel_text(self) -> str:
        return (
            f"Leads Sent: {self.proof_leads_total}  |  "
            f"Vapi Calls: {self.total_calls_made}  |  "
            f"Signed: {self.signed_agreements_count}"
        )

    @rx.var
    def revenue_total(self) -> int:
        total = 0
        for row in self.agreement_rows:
            if row["signing_status"] == AgreementStatus.PAID.value and row["stripe_transaction_id"] != "—":
                cents = int(row.get("plan_amount_cents") or 0)
                total += cents // 100
        return total

    @rx.var
    def backend_revenue_dollars(self) -> int:
        return int(self.backend_revenue_cents // 100)

    @rx.var
    def contact_submission_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                rows = (
                    session.query(ContactSubmission)
                    .order_by(ContactSubmission.id.desc())
                    .limit(100)
                    .all()
                )
                out: list[dict[str, str]] = []
                for r in rows:
                    created = r.created_at.isoformat() if r.created_at else ""
                    out.append(
                        {
                            "id": str(r.id),
                            "created_at": created,
                            "name": str(r.name),
                            "email": str(r.email),
                            "company": str(r.company or "—"),
                            "message": str(r.message)[:500],
                            "source": str(r.source or "—"),
                            "ip": str(r.ip_address or "—"),
                        }
                    )
                return out
        except OperationalError:
            return []

    @rx.var
    def raw_db_rows(self) -> list[dict[str, str | int]]:
        _ = self.db_sync_tick
        out: list[dict[str, str | int]] = []
        try:
            with get_session() as session:
                leads = session.query(Lead).order_by(Lead.id.desc()).all()
                for lead in leads:
                    out.append(
                        {
                            "table": "leads",
                            "pk": int(lead.id),
                            "name": str(lead.business_name),
                            "status": str(lead.status.value if hasattr(lead.status, "value") else lead.status),
                            "correlation_id": str(lead.correlation_id or ""),
                        }
                    )
                agreements = session.query(Agreement).order_by(Agreement.id.desc()).all()
                for agreement in agreements:
                    out.append(
                        {
                            "table": "agreements",
                            "pk": int(agreement.id),
                            "name": str(agreement.client_name),
                            "status": str(
                                agreement.signing_status.value
                                if hasattr(agreement.signing_status, "value")
                                else agreement.signing_status
                            ),
                            "correlation_id": str(agreement.correlation_id),
                        }
                    )
        except OperationalError:
            return []
        return out

    @rx.var
    def recent_activity_feed(self) -> list[dict[str, str]]:
        """
        Last 5 message events for dashboard heartbeat.
        """
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                rows = session.query(MessageEvent).order_by(MessageEvent.id.desc()).limit(5).all()
                out: list[dict[str, str]] = []
                for row in rows:
                    created = row.created_at.isoformat() if row.created_at else ""
                    snippet = (row.error or row.body or "")[:120].replace("\n", " ")
                    out.append(
                        {
                            "at": created,
                            "channel": str(row.channel or "n/a"),
                            "status": str(row.status.value if hasattr(row.status, "value") else row.status),
                            "detail": snippet or "event",
                        }
                    )
                return out
        except OperationalError:
            return []

    @rx.var
    def activity_feed_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        rows: list[dict[str, str]] = []
        try:
            with get_session() as session:
                logs = session.query(SystemLog).order_by(SystemLog.id.desc()).limit(10).all()
                for row in logs:
                    rows.append(
                        {
                            "at": row.created_at.isoformat() if row.created_at else "",
                            "text": f"{row.source}: {row.action} — {row.detail[:140]}",
                        }
                    )
        except OperationalError:
            return []
        return rows

    @rx.var
    def lead_engine_total(self) -> int:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                return int(session.query(Lead).count())
        except OperationalError:
            return 0

    @rx.var
    def lead_engine_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                leads = session.query(Lead).order_by(Lead.id.desc()).limit(50).all()
                return [
                    {
                        "business_name": str(l.business_name),
                        "phone": str(l.phone or "—"),
                        "city": str(l.location or "—"),
                        "niche": str(l.niche or "—"),
                        "date_scraped": l.created_at.isoformat() if l.created_at else "",
                        "status": "new",
                    }
                    for l in leads
                ]
        except OperationalError:
            return []

    @rx.var
    def outreach_campaign_rows(self) -> list[dict[str, str]]:
        # Placeholder row until campaign telemetry is stored in DB/API.
        campaign = os.getenv("INSTANTLY_CAMPAIGN_ID", "").strip()
        if not campaign:
            return []
        return [
            {
                "campaign_name": f"Instantly {campaign[:8]}",
                "emails_sent": str(self.today_emails_sent),
                "open_rate": "0%",
                "reply_rate": "0%",
                "leads_in_sequence": str(self.queued_leads_count),
                "status": "active",
            }
        ]

    @rx.var
    def outreach_reply_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                events = (
                    session.query(MessageEvent)
                    .filter(verified_inbound_reply_clause())
                    .order_by(MessageEvent.id.desc())
                    .limit(25)
                    .all()
                )
                return [
                    {
                        "who": str(e.correlation_id or "unknown"),
                        "what": str((e.body or e.error or "replied")[:140]).replace("\n", " "),
                        "when": e.created_at.isoformat() if e.created_at else "",
                    }
                    for e in events
                ]
        except OperationalError:
            return []

    @rx.var
    def cold_caller_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                leads = (
                    session.query(Lead)
                    .filter(Lead.call_status.isnot(None))
                    .order_by(Lead.updated_at.desc())
                    .limit(100)
                    .all()
                )
                out: list[dict[str, str]] = []
                for l in leads:
                    status = str(l.call_status or "No Answer")
                    outcome = status.replace("_", " ").title()
                    out.append(
                        {
                            "prospect_name": str(l.owner_name or l.business_name),
                            "business": str(l.business_name),
                            "phone": str(l.phone or "—"),
                            "call_time": l.updated_at.isoformat() if l.updated_at else "",
                            "call_duration": "—",
                            "outcome": outcome,
                            "recording": str(l.transcript_url or ""),
                        }
                    )
                return out
        except OperationalError:
            return []

    @rx.var
    def cold_caller_summary(self) -> dict[str, int]:
        return {
            "total_calls_today": self.today_calls_made,
            "interested": self.cc_calls_interested,
            "not_interested": self.cc_calls_not_interested,
            "callbacks": self.cc_calls_callbacks,
        }

    @rx.var
    def deal_kanban(self) -> dict[str, list[dict[str, str]]]:
        _ = self.db_sync_tick
        board = {
            "new_reply": [],
            "call_booked": [],
            "proposal_sent": [],
            "agreement_signed": [],
            "payment_received": [],
            "active_client": [],
        }
        try:
            with get_session() as session:
                leads = session.query(Lead).order_by(Lead.id.desc()).limit(200).all()
                agreements = {
                    a.lead_id: a
                    for a in session.query(Agreement).order_by(Agreement.id.desc()).limit(200).all()
                    if a.lead_id is not None
                }
                for lead in leads:
                    card = {
                        "business_name": str(lead.business_name),
                        "niche": str(lead.niche or "—"),
                        "city": str(lead.location or "—"),
                        "last_action": str(lead.status.value if hasattr(lead.status, "value") else lead.status),
                    }
                    ag = agreements.get(lead.id)
                    if lead.status == LeadStatus.ACTIVE_CLIENT:
                        board["active_client"].append(card)
                    elif ag and ag.signing_status == AgreementStatus.PAID:
                        board["payment_received"].append(card)
                    elif ag and ag.signing_status == AgreementStatus.SIGNED:
                        board["agreement_signed"].append(card)
                    elif ag and ag.signing_status in {AgreementStatus.SENT, AgreementStatus.DRAFT}:
                        board["proposal_sent"].append(card)
                    elif lead.call_status:
                        board["call_booked"].append(card)
                    else:
                        board["new_reply"].append(card)
        except OperationalError:
            pass
        return board

    @rx.var
    def payment_summary(self) -> dict[str, int]:
        _ = self.db_sync_tick
        now = datetime.now(timezone.utc)
        start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        out = {
            "month_revenue": 0,
            "all_time_revenue": 0,
            "active_clients": 0,
            "failed_payments": 0,
        }
        try:
            with get_session() as session:
                month_cents = (
                    session.query(func.coalesce(func.sum(Agreement.stripe_plan_amount_cents), 0))
                    .filter(
                        Agreement.created_at >= start_month,
                        Agreement.signing_status == AgreementStatus.PAID,
                    )
                    .scalar()
                )
                all_time_cents = (
                    session.query(func.coalesce(func.sum(Agreement.stripe_plan_amount_cents), 0))
                    .filter(Agreement.signing_status == AgreementStatus.PAID)
                    .scalar()
                )
                out["month_revenue"] = int((month_cents or 0) // 100)
                out["all_time_revenue"] = int((all_time_cents or 0) // 100)
                out["active_clients"] = int(
                    session.query(Lead).filter(Lead.status == LeadStatus.ACTIVE_CLIENT).count()
                )
                out["failed_payments"] = int(
                    session.query(SystemLog)
                    .filter(SystemLog.source == "stripe", SystemLog.level == "error")
                    .count()
                )
        except OperationalError:
            pass
        return out

    @rx.var
    def payment_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                agreements = session.query(Agreement).order_by(Agreement.id.desc()).limit(100).all()
                out: list[dict[str, str]] = []
                for a in agreements:
                    cents = int(a.stripe_plan_amount_cents or 0)
                    out.append(
                        {
                            "client_name": str(a.client_name),
                            "plan": str(a.offer_kind or "—"),
                            "amount": f"${cents // 100}",
                            "date": a.created_at.isoformat() if a.created_at else "",
                            "status": str(a.signing_status.value if hasattr(a.signing_status, "value") else a.signing_status),
                        }
                    )
                return out
        except OperationalError:
            return []

    @rx.var
    def contact_priority_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                rows = (
                    session.query(ContactSubmission)
                    .order_by(ContactSubmission.id.desc())
                    .limit(100)
                    .all()
                )
                return [
                    {
                        "name": str(r.name),
                        "email": str(r.email),
                        "phone": "—",
                        "message": str(r.message)[:220],
                        "date_submitted": r.created_at.isoformat() if r.created_at else "",
                        "status": "New",
                    }
                    for r in rows
                ]
        except OperationalError:
            return []

    @rx.var
    def tracking_niche_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                leads = session.query(Lead).all()
            bucket: dict[str, dict[str, int]] = {}
            for lead in leads:
                niche = str(lead.niche or "Unknown")
                if niche not in bucket:
                    bucket[niche] = {"leads": 0, "conversions": 0}
                bucket[niche]["leads"] += 1
                if lead.status == LeadStatus.ACTIVE_CLIENT:
                    bucket[niche]["conversions"] += 1
            out = []
            for niche, vals in bucket.items():
                leads_count = max(1, vals["leads"])
                rate = (vals["conversions"] / leads_count) * 100
                out.append({"niche": niche, "conversion": f"{rate:.1f}%", "lead_count": str(vals["leads"])})
            out.sort(key=lambda r: float(r["conversion"].replace("%", "")), reverse=True)
            return out
        except OperationalError:
            return []

    @rx.var
    def tracking_city_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                leads = session.query(Lead).all()
            bucket: dict[str, int] = {}
            for lead in leads:
                city = str(lead.location or "Unknown")
                bucket[city] = bucket.get(city, 0) + 1
            out = [{"city": city, "leads": str(count)} for city, count in bucket.items()]
            out.sort(key=lambda r: int(r["leads"]), reverse=True)
            return out[:20]
        except OperationalError:
            return []

    @rx.var
    def tracking_subject_rows(self) -> list[dict[str, str]]:
        # Subject-level attribution isn't fully persisted; show placeholder when sparse.
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                rows = (
                    session.query(MessageEvent.subject, func.count(MessageEvent.id))
                    .filter(verified_inbound_reply_clause())
                    .group_by(MessageEvent.subject)
                    .order_by(func.count(MessageEvent.id).desc())
                    .limit(10)
                    .all()
                )
            out = []
            for subject, count in rows:
                out.append({"subject": str(subject or "(no subject)"), "replies": str(int(count or 0))})
            return out if out else [{"subject": "(placeholder)", "replies": "0"}]
        except OperationalError:
            return [{"subject": "(placeholder)", "replies": "0"}]

    @rx.var
    def tracking_reply_time_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        try:
            with get_session() as session:
                events = (
                    session.query(MessageEvent)
                    .filter(verified_inbound_reply_clause())
                    .order_by(MessageEvent.id.desc())
                    .limit(200)
                    .all()
                )
            bucket: dict[str, int] = {}
            for e in events:
                if not e.created_at:
                    continue
                label = e.created_at.strftime("%a %H:00")
                bucket[label] = bucket.get(label, 0) + 1
            out = [{"window": k, "replies": str(v)} for k, v in bucket.items()]
            out.sort(key=lambda r: int(r["replies"]), reverse=True)
            return out[:15] if out else [{"window": "(placeholder)", "replies": "0"}]
        except OperationalError:
            return [{"window": "(placeholder)", "replies": "0"}]

    @rx.var
    def tracking_cpl_rows(self) -> list[dict[str, str]]:
        # Cost data is not yet modeled; placeholder series.
        return [
            {"period": "Week 1", "cost_per_lead": "$0"},
            {"period": "Week 2", "cost_per_lead": "$0"},
            {"period": "Week 3", "cost_per_lead": "$0"},
            {"period": "Week 4", "cost_per_lead": "$0"},
        ]

    @rx.var
    def system_health_rows(self) -> list[dict[str, str]]:
        _ = self.db_sync_tick
        warmup_days = 14 if os.getenv("INSTANTLY_DOMAIN_WARMUP_ACTIVE", "false").lower() in {"1", "true", "yes"} else 0
        last_scraper = ""
        last_vapi = ""
        last_payment = self.stripe_last_event_at or ""
        try:
            with get_session() as session:
                run = session.query(SystemLog).filter(SystemLog.source == "automation").order_by(SystemLog.id.desc()).first()
                if run and run.created_at:
                    last_scraper = run.created_at.isoformat()
                vapi = session.query(Lead).filter(Lead.call_status.isnot(None)).order_by(Lead.updated_at.desc()).first()
                if vapi and vapi.updated_at:
                    last_vapi = vapi.updated_at.isoformat()
        except OperationalError:
            pass
        return [
            {"component": "Database (Postgres)", "status": "green" if self.backend_health_ok else "red", "detail": "Connected" if self.backend_health_ok else "Unavailable"},
            {"component": "Scraper", "status": "green" if last_scraper else "red", "detail": f"Last run: {last_scraper or 'never'}"},
            {"component": "Instantly Warmup", "status": "green" if warmup_days >= 14 else "red", "detail": f"{warmup_days}/14 days"},
            {"component": "Vapi / Elliot", "status": "green" if last_vapi else "red", "detail": f"Last call: {last_vapi or 'none'}"},
            {"component": "Stripe Webhooks", "status": "green" if self.stripe_webhook_ready else "red", "detail": f"Last payment event: {last_payment or 'none'}"},
            {"component": "Railway", "status": "green", "detail": "Uptime indicator available via /health"},
        ]
