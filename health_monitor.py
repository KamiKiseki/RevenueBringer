from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import stripe
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import exists, inspect, not_, or_, text
from sqlalchemy.exc import SQLAlchemyError

from models import (
    AutomationRun,
    EmailSequence,
    Lead,
    LeadStatus,
    MessageEvent,
    MessageStatus,
    SystemLog,
    get_session,
    init_db,
    log_system_event,
)
from outreach import _smtp_send
from scraper import _upsert_scraped_leads_cli, fetch_business_leads

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent
REPORT_DIR = REPO_ROOT / "health_reports"
FULL_REPORT_PATH = REPO_ROOT / "system_check_report.txt"
HEALTH_BASE_URL = os.getenv("HEALTH_BASE_URL", "https://autoyieldsystems.com").rstrip("/")
ALERT_EMAIL = os.getenv("HEALTH_ALERT_EMAIL", "michael@autoyieldsystems.com").strip()
REQUIRED_TABLES = ("leads", "email_sequences", "message_events", "system_logs")
REQUIRED_WEBHOOKS = ("/webhooks/reply", "/webhooks/vapi", "/webhooks/stripe")
SERVER_PY = REPO_ROOT / "server.py"
TZ = os.getenv("HEALTH_MONITOR_TZ", "America/Chicago")

_scheduler = None
_lock_handle = None


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    error: str | None = None
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["status"] = "PASS" if self.passed else "FAIL"
        return row


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stripe_key() -> str:
    return (os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or "").strip()


def _stripe_attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _instantly_headers(api_key: str) -> list[dict[str, str]]:
    return [
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        {"Authorization": api_key, "Content-Type": "application/json"},
        {"X-Api-Key": api_key, "Content-Type": "application/json"},
        {"x-api-key": api_key, "Content-Type": "application/json"},
    ]


def _request_json(method: str, url: str, *, headers: dict | None = None, timeout: int = 25, **kwargs) -> tuple[int, Any]:
    resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = (resp.text or "").strip()
    return resp.status_code, body


def _check_postgres() -> CheckResult:
    name = "Database (Postgres/Railway)"
    try:
        init_db()
        with get_session() as session:
            session.execute(text("SELECT 1"))
            inspector = inspect(session.bind)
            tables = set(inspector.get_table_names())
            missing = [t for t in REQUIRED_TABLES if t not in tables]
            counts = {}
            for table in REQUIRED_TABLES:
                if table in tables:
                    counts[table] = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            if missing:
                return CheckResult(
                    name,
                    False,
                    f"Connected but missing tables: {', '.join(missing)}",
                    error="missing_tables",
                    extra={"tables": sorted(tables), "row_counts": counts},
                )
            return CheckResult(
                name,
                True,
                "Connected; required tables present.",
                extra={"tables": sorted(tables), "row_counts": counts},
            )
    except SQLAlchemyError as exc:
        return CheckResult(name, False, "Connection failed.", error=str(exc))
    except Exception as exc:
        return CheckResult(name, False, "Connection failed.", error=str(exc))


def _check_scraper_test() -> CheckResult:
    name = "Scraper"
    try:
        scraped = fetch_business_leads(niche="HVAC", location="San Antonio", limit=3)
        fetched = len(scraped)
        if fetched <= 0:
            return CheckResult(
                name,
                False,
                "Scrape returned zero leads.",
                error="zero_leads",
                extra={"fetched": fetched, "saved": 0, "skipped_duplicates": 0},
            )
        inserted, skipped = _upsert_scraped_leads_cli(scraped)
        return CheckResult(
            name,
            True,
            f"Fetched {fetched}; saved {inserted}; skipped duplicates {skipped}.",
            extra={"fetched": fetched, "saved": inserted, "skipped_duplicates": skipped},
        )
    except Exception as exc:
        return CheckResult(name, False, "Scraper test failed.", error=str(exc))


def _check_enrichment_backlog() -> CheckResult:
    name = "Email Enrichment"
    try:
        with get_session() as session:
            count = (
                session.query(Lead)
                .filter(
                    Lead.website.isnot(None),
                    Lead.website != "",
                    or_(Lead.email.is_(None), Lead.email == ""),
                )
                .count()
            )
        return CheckResult(
            name,
            True,
            f"{count} leads have websites but no email.",
            extra={"leads_needing_enrichment": count},
        )
    except Exception as exc:
        return CheckResult(name, False, "Could not count enrichment backlog.", error=str(exc))


def _check_sequence_backlog() -> CheckResult:
    name = "Sequence Generator"
    try:
        sent_outbound = exists().where(
            MessageEvent.lead_id == Lead.id,
            MessageEvent.direction == "outbound",
            MessageEvent.channel == "email",
            MessageEvent.status == MessageStatus.SENT,
        )
        with get_session() as session:
            count = (
                session.query(Lead)
                .filter(
                    Lead.email.isnot(None),
                    Lead.email != "",
                    Lead.status == LeadStatus.QUEUED,
                    not_(sent_outbound),
                    not_(exists().where(EmailSequence.lead_id == Lead.id)),
                )
                .count()
            )
        return CheckResult(
            name,
            True,
            f"{count} leads have email but no generated sequence yet.",
            extra={"leads_needing_sequences": count},
        )
    except Exception as exc:
        return CheckResult(name, False, "Could not count sequence backlog.", error=str(exc))


def _check_instantly() -> CheckResult:
    name = "Instantly"
    api_key = os.getenv("INSTANTLY_API_KEY", "").strip()
    campaign_id = os.getenv("INSTANTLY_CAMPAIGN_ID", "").strip()
    if not api_key:
        return CheckResult(name, False, "INSTANTLY_API_KEY is not set.", error="missing_api_key")
    if not campaign_id:
        return CheckResult(name, False, "INSTANTLY_CAMPAIGN_ID is not set.", error="missing_campaign_id")
    errors: list[str] = []
    for headers in _instantly_headers(api_key):
        status, body = _request_json(
            "GET",
            f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}",
            headers=headers,
        )
        if status < 300 and isinstance(body, dict):
            campaign = body.get("data") if isinstance(body.get("data"), dict) else body
            status_label = str(campaign.get("status") or campaign.get("campaign_status") or "unknown")
            lead_count = campaign.get("leads_count") or campaign.get("lead_count") or campaign.get("total_leads")
            emails_sent = campaign.get("emails_sent") or campaign.get("sent_count")
            active = status_label.lower() in {"active", "running", "live", "enabled", "1", "true"}
            return CheckResult(
                name,
                active,
                f"Campaign status={status_label}; leads={lead_count}; emails_sent={emails_sent}",
                extra={
                    "campaign_status": status_label,
                    "leads_count": lead_count,
                    "emails_sent": emails_sent,
                },
            )
        errors.append(f"{status}:{str(body)[:160]}")
    return CheckResult(name, False, "Instantly campaign lookup failed.", error=" | ".join(errors)[:500])


def _check_vapi() -> CheckResult:
    name = "Vapi / Elliot"
    api_key = os.getenv("VAPI_API_KEY", "").strip()
    assistant_id = os.getenv("VAPI_ASSISTANT_ID", "").strip()
    phone_id = os.getenv("VAPI_PHONE_NUMBER_ID", "").strip()
    missing = [k for k, v in {
        "VAPI_API_KEY": api_key,
        "VAPI_ASSISTANT_ID": assistant_id,
        "VAPI_PHONE_NUMBER_ID": phone_id,
    }.items() if not v]
    if missing:
        return CheckResult(name, False, f"Missing env: {', '.join(missing)}", error="missing_env")
    headers = {"Authorization": f"Bearer {api_key}"}
    status, body = _request_json("GET", f"https://api.vapi.ai/assistant/{assistant_id}", headers=headers)
    if status >= 300:
        return CheckResult(name, False, "Assistant lookup failed.", error=f"HTTP {status}: {body}")
    assistant_name = str(body.get("name") or "unknown")
    assistant_status = str(body.get("status") or "unknown")
    phone_status = "unknown"
    phone_number = ""
    p_status, p_body = _request_json("GET", f"https://api.vapi.ai/phone-number/{phone_id}", headers=headers)
    if p_status < 300 and isinstance(p_body, dict):
        phone_status = str(p_body.get("status") or "unknown")
        phone_number = str(p_body.get("number") or p_body.get("phoneNumber") or "")
    ok = assistant_status.lower() in {"active", "enabled", "ready", "unknown", ""} or bool(assistant_name)
    return CheckResult(
        name,
        ok,
        f"Assistant={assistant_name}; status={assistant_status}; phone={phone_number or phone_id}; phone_status={phone_status}",
        extra={
            "assistant_name": assistant_name,
            "assistant_status": assistant_status,
            "phone_number": phone_number,
            "phone_status": phone_status,
        },
    )


def _check_telnyx() -> CheckResult:
    name = "Telnyx"
    api_key = os.getenv("TELNYX_API_KEY", "").strip()
    if api_key:
        status, body = _request_json(
            "GET",
            "https://api.telnyx.com/v2/balance",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if status >= 300:
            return CheckResult(name, False, "Telnyx API request failed.", error=f"HTTP {status}: {body}")
        return CheckResult(name, True, "Telnyx API reachable.", extra={"balance": body})

    # Outbound voice uses Telnyx through Vapi BYO; validate the connected number when no Mission Control key is stored.
    vapi_key = os.getenv("VAPI_API_KEY", "").strip()
    phone_id = os.getenv("VAPI_PHONE_NUMBER_ID", "").strip()
    if not (vapi_key and phone_id):
        return CheckResult(
            name,
            False,
            "TELNYX_API_KEY is not set and Vapi Telnyx phone is not configured.",
            error="missing_telnyx_and_vapi_phone",
        )
    status, body = _request_json(
        "GET",
        f"https://api.vapi.ai/phone-number/{phone_id}",
        headers={"Authorization": f"Bearer {vapi_key}"},
    )
    if status >= 300:
        return CheckResult(name, False, "Vapi phone lookup failed.", error=f"HTTP {status}: {body}")
    provider = str(body.get("provider") or "").lower()
    phone_status = str(body.get("status") or "unknown")
    phone_number = str(body.get("number") or body.get("phoneNumber") or "")
    ok = provider == "telnyx" and phone_status.lower() == "active"
    return CheckResult(
        name,
        ok,
        (
            f"Validated via Vapi BYO trunk: provider={provider or 'unknown'}; "
            f"phone={phone_number or phone_id}; status={phone_status}"
        ),
        error=None if ok else "telnyx_not_active_on_vapi",
        extra={
            "validated_via": "vapi_phone_number",
            "vapi_phone_number_id": phone_id,
            "provider": provider,
            "phone_number": phone_number,
            "phone_status": phone_status,
        },
    )


def _check_stripe() -> CheckResult:
    name = "Stripe"
    key = _stripe_key()
    if not key:
        return CheckResult(name, False, "STRIPE_SECRET_KEY is not set.", error="missing_api_key")
    stripe.api_key = key
    mode = "live" if key.startswith("sk_live") else "test" if key.startswith("sk_test") else "unknown"
    trial_id = (os.getenv("STRIPE_PRICE_TRIAL_300") or os.getenv("PRICE_TRIAL_300") or "").strip()
    month_id = (os.getenv("STRIPE_PRICE_MONTH_500") or os.getenv("PRICE_MONTH_500") or "").strip()
    if not trial_id or not month_id:
        return CheckResult(
            name,
            False,
            "Stripe price env vars are missing.",
            error="missing_price_ids",
            extra={"mode": mode, "trial_price_id": trial_id, "monthly_price_id": month_id},
        )
    try:
        trial = stripe.Price.retrieve(trial_id)
        monthly = stripe.Price.retrieve(month_id)
        trial_ok = bool(_stripe_attr(trial, "active")) and int(_stripe_attr(trial, "unit_amount") or 0) == 30000
        monthly_ok = bool(_stripe_attr(monthly, "active")) and int(_stripe_attr(monthly, "unit_amount") or 0) == 50000
        recurring = _stripe_attr(_stripe_attr(monthly, "recurring"), "interval")
        passed = trial_ok and monthly_ok and recurring == "month"
        return CheckResult(
            name,
            passed,
            f"mode={mode}; trial={trial_id}; monthly={month_id}",
            extra={
                "mode": mode,
                "trial_price_id": trial_id,
                "monthly_price_id": month_id,
                "trial_active": _stripe_attr(trial, "active"),
                "monthly_active": _stripe_attr(monthly, "active"),
                "monthly_interval": recurring,
            },
        )
    except Exception as exc:
        return CheckResult(name, False, "Stripe price lookup failed.", error=str(exc))


def _check_flask_api() -> CheckResult:
    name = "Flask API"
    results: dict[str, int] = {}
    errors: list[str] = []
    for path in ("/health", "/checkout"):
        try:
            resp = requests.get(f"{HEALTH_BASE_URL}{path}", timeout=25)
            results[path] = resp.status_code
            if resp.status_code >= 300:
                errors.append(f"{path}={resp.status_code}")
        except Exception as exc:
            results[path] = 0
            errors.append(f"{path}: {exc}")
    passed = results.get("/health") == 200 and results.get("/checkout") == 200
    return CheckResult(
        name,
        passed,
        f"GET /health={results.get('/health')}; GET /checkout={results.get('/checkout')}",
        error="; ".join(errors) if errors else None,
        extra={"status_codes": results, "base_url": HEALTH_BASE_URL},
    )


def _check_webhook_routes() -> CheckResult:
    name = "Webhook Routes"
    try:
        text_body = SERVER_PY.read_text(encoding="utf-8")
    except Exception as exc:
        return CheckResult(name, False, "Could not read server.py.", error=str(exc))
    missing = [route for route in REQUIRED_WEBHOOKS if route not in text_body]
    if missing:
        return CheckResult(name, False, f"Missing routes: {', '.join(missing)}", error="missing_routes")
    return CheckResult(name, True, "All required webhook routes found in server.py.")


def _check_openai() -> CheckResult:
    name = "OpenAI"
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return CheckResult(name, False, "OPENAI_API_KEY is not set.", error="missing_api_key")
    try:
        client = OpenAI(api_key=api_key)
        models = client.models.list()
        first = models.data[0].id if models.data else "unknown"
        return CheckResult(name, True, f"API reachable; sample model={first}", extra={"sample_model": first})
    except Exception as exc:
        return CheckResult(name, False, "OpenAI API call failed.", error=str(exc))


def _check_apify() -> CheckResult:
    name = "Apify"
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        return CheckResult(name, False, "APIFY_API_TOKEN is not set.", error="missing_api_key")
    status, body = _request_json("GET", "https://api.apify.com/v2/users/me", params={"token": token})
    if status >= 300:
        return CheckResult(name, False, "Apify token validation failed.", error=f"HTTP {status}: {body}")
    data = (body or {}).get("data") if isinstance(body, dict) else {}
    username = str((data or {}).get("username") or (data or {}).get("id") or "unknown")
    return CheckResult(name, True, f"Token valid for {username}.", extra={"username": username})


def _last_successful_scraper_run() -> datetime | None:
    with get_session() as session:
        row = (
            session.query(AutomationRun)
            .filter(AutomationRun.status == "completed", AutomationRun.scraped_count > 0)
            .order_by(AutomationRun.ended_at.desc(), AutomationRun.started_at.desc())
            .first()
        )
        if row and row.ended_at:
            return row.ended_at
        if row and row.started_at:
            return row.started_at
    return None


def _last_email_sent() -> datetime | None:
    with get_session() as session:
        row = (
            session.query(MessageEvent)
            .filter(
                MessageEvent.channel == "email",
                MessageEvent.direction == "outbound",
                MessageEvent.status == MessageStatus.SENT,
            )
            .order_by(MessageEvent.created_at.desc())
            .first()
        )
        return row.created_at if row else None


def _last_vapi_call() -> datetime | None:
    with get_session() as session:
        row = (
            session.query(SystemLog)
            .filter(
                SystemLog.source == "vapi",
            )
            .order_by(SystemLog.created_at.desc())
            .first()
        )
        if row:
            return row.created_at
        row = (
            session.query(Lead)
            .filter(Lead.call_status.isnot(None), Lead.call_status != "")
            .order_by(Lead.updated_at.desc())
            .first()
        )
        return row.updated_at if row else None


def _age_phrase(ts: datetime | None) -> str:
    if not ts:
        return "never"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = _now() - ts.astimezone(timezone.utc)
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)} minutes ago"
    if hours < 48:
        return f"{hours:.1f} hours ago"
    return f"{delta.days} days ago"


def _check_last_scraper() -> CheckResult:
    name = "Last Scraper Run"
    try:
        last = _last_successful_scraper_run()
        return CheckResult(
            name,
            last is not None,
            f"Last successful scrape: {_age_phrase(last)}",
            extra={"last_success_at": last.isoformat() if last else None},
        )
    except Exception as exc:
        return CheckResult(name, False, "Could not read scraper history.", error=str(exc))


def _check_last_email() -> CheckResult:
    name = "Last Email Sent"
    try:
        last = _last_email_sent()
        return CheckResult(
            name,
            last is not None,
            f"Last outbound email: {_age_phrase(last)}",
            extra={"last_success_at": last.isoformat() if last else None},
        )
    except Exception as exc:
        return CheckResult(name, False, "Could not read email history.", error=str(exc))


def _check_last_vapi_call() -> CheckResult:
    name = "Last Elliot Call"
    try:
        last = _last_vapi_call()
        return CheckResult(
            name,
            last is not None,
            f"Last Vapi activity: {_age_phrase(last)}",
            extra={"last_success_at": last.isoformat() if last else None},
        )
    except Exception as exc:
        return CheckResult(name, False, "Could not read Vapi history.", error=str(exc))


def run_full_system_check(*, save_report: bool = True, console: bool = True) -> dict[str, Any]:
    checks = [
        _check_postgres(),
        _check_scraper_test(),
        _check_enrichment_backlog(),
        _check_sequence_backlog(),
        _check_instantly(),
        _check_vapi(),
        _check_telnyx(),
        _check_stripe(),
        _check_flask_api(),
        _check_webhook_routes(),
        _check_openai(),
        _check_apify(),
    ]
    return _finalize_report(checks, report_kind="full", save_report=save_report, console=console)


def run_monitor_checks(*, save_report: bool = True, console: bool = True) -> dict[str, Any]:
    checks = [
        _check_postgres(),
        _check_flask_api(),
        _check_vapi(),
        _check_stripe(),
        _check_instantly(),
        _check_openai(),
        _check_apify(),
        _check_last_scraper(),
        _check_last_email(),
        _check_last_vapi_call(),
    ]
    return _finalize_report(checks, report_kind="monitor", save_report=save_report, console=console)


def _finalize_report(
    checks: list[CheckResult],
    *,
    report_kind: str,
    save_report: bool,
    console: bool,
) -> dict[str, Any]:
    passed = sum(1 for c in checks if c.passed)
    failed = len(checks) - passed
    critical = [c for c in checks if not c.passed and c.name in {
        "Database (Postgres/Railway)",
        "Flask API",
        "Stripe",
        "Webhook Routes",
    }]
    summary = {
        "report_kind": report_kind,
        "generated_at": _now().isoformat(),
        "total_checks": len(checks),
        "passed": passed,
        "failed": failed,
        "critical_issues": [c.name for c in critical],
        "checks": [c.to_dict() for c in checks],
    }
    text = _format_report_text(checks, summary)
    if save_report:
        if report_kind == "full":
            FULL_REPORT_PATH.write_text(text, encoding="utf-8")
        _write_rotating_report(text)
    if console:
        print(text)
    return summary


def _format_report_text(checks: list[CheckResult], summary: dict[str, Any]) -> str:
    lines = [
        f"AutoYield Systems Health Report ({summary['report_kind']})",
        f"Generated: {summary['generated_at']}",
        "",
    ]
    for check in checks:
        lines.append(f"=== {check.name} ===")
        lines.append("PASS" if check.passed else "FAIL")
        lines.append(check.detail)
        if check.error:
            lines.append(f"Error: {check.error}")
        if check.extra:
            lines.append(f"Extra: {json.dumps(check.extra, default=str)}")
        lines.append("")
    lines.extend(
        [
            "=== Summary ===",
            f"Total passed: {summary['passed']}",
            f"Total failed: {summary['failed']}",
            f"Critical issues: {', '.join(summary['critical_issues']) if summary['critical_issues'] else 'none'}",
        ]
    )
    return "\n".join(lines)


def _write_rotating_report(text: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%d_%H-%M")
    path = REPORT_DIR / f"report_{stamp}.txt"
    path.write_text(text, encoding="utf-8")
    reports = sorted(REPORT_DIR.glob("report_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in reports[30:]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def _log_failures_to_db(checks: list[CheckResult]) -> None:
    for check in checks:
        if check.passed:
            continue
        try:
            log_system_event(
                source="health_monitor",
                action="check_failed",
                detail=f"{check.name}: {check.error or check.detail}",
                level="error",
            )
        except Exception:
            pass


def _send_alert_email(subject: str, body: str) -> tuple[bool, str]:
    if not ALERT_EMAIL:
        return False, "missing_alert_email"
    return _smtp_send(ALERT_EMAIL, subject, body)


def _format_report_text_from_summary(summary: dict[str, Any]) -> str:
  lines = [
      f"AutoYield Systems Health Report ({summary.get('report_kind', 'monitor')})",
      f"Generated: {summary.get('generated_at')}",
      "",
  ]
  for row in summary.get("checks", []):
      lines.append(f"=== {row.get('name')} ===")
      lines.append(str(row.get("status")))
      lines.append(str(row.get("detail")))
      if row.get("error"):
          lines.append(f"Error: {row.get('error')}")
      if row.get("extra"):
          lines.append(f"Extra: {json.dumps(row.get('extra'), default=str)}")
      lines.append("")
  lines.extend(
      [
          "=== Summary ===",
          f"Total passed: {summary.get('passed')}",
          f"Total failed: {summary.get('failed')}",
          f"Critical issues: {', '.join(summary.get('critical_issues') or []) or 'none'}",
      ]
  )
  return "\n".join(lines)


def _log_failures_from_summary(summary: dict[str, Any]) -> None:
  for row in summary.get("checks", []):
      if row.get("status") == "PASS":
          continue
      try:
          log_system_event(
              source="health_monitor",
              action="check_failed",
              detail=f"{row.get('name')}: {row.get('error') or row.get('detail')}",
              level="error",
          )
      except Exception:
          pass


def execute_monitor_cycle(*, alert_on_fail: bool, email_summary: bool = False) -> dict[str, Any]:
  summary = run_monitor_checks(save_report=True, console=True)
  failures = [row for row in summary.get("checks", []) if row.get("status") == "FAIL"]
  if failures:
      _log_failures_from_summary(summary)
  body = _format_report_text_from_summary(summary)
  if alert_on_fail and failures:
      _send_alert_email("AutoYield health alert", body)
  if email_summary:
      _send_alert_email("AutoYield daily health summary", body)
  return summary


def _acquire_scheduler_lock() -> bool:
  if os.getenv("HEALTH_MONITOR_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
      return False
  lock_path = os.getenv("HEALTH_MONITOR_LOCK_FILE", "/tmp/autoyield_health_monitor.lock")
  global _lock_handle
  try:
      import fcntl

      _lock_handle = open(lock_path, "w", encoding="utf-8")
      fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
      return True
  except (ImportError, OSError, BlockingIOError):
      return False


def _run_weekly_client_reports() -> None:
  try:
      from client_reporting import run_weekly_client_reports

      run_weekly_client_reports(send_emails=True)
  except Exception as exc:
      print(f"[health_monitor] weekly client reports failed: {exc}")


def start_health_monitor() -> bool:
  global _scheduler
  if _scheduler is not None:
      return True
  if not _acquire_scheduler_lock():
      return False
  try:
      from apscheduler.schedulers.background import BackgroundScheduler
      from apscheduler.triggers.cron import CronTrigger
      from apscheduler.triggers.interval import IntervalTrigger
  except ImportError:
      print("[health_monitor] APScheduler is not installed.")
      return False

  _scheduler = BackgroundScheduler(timezone=TZ)
  _scheduler.add_job(
      lambda: execute_monitor_cycle(alert_on_fail=True, email_summary=False),
      IntervalTrigger(hours=12),
      id="health_monitor_interval",
      replace_existing=True,
      max_instances=1,
      coalesce=True,
  )
  _scheduler.add_job(
      lambda: execute_monitor_cycle(alert_on_fail=False, email_summary=True),
      CronTrigger(hour=8, minute=0),
      id="health_monitor_daily_summary",
      replace_existing=True,
      max_instances=1,
      coalesce=True,
  )
  _scheduler.add_job(
      _run_weekly_client_reports,
      CronTrigger(day_of_week="mon", hour=8, minute=0),
      id="client_weekly_reports",
      replace_existing=True,
      max_instances=1,
      coalesce=True,
  )
  _scheduler.start()
  print("[health_monitor] Scheduler started (12h checks + daily 8am summary + Monday client reports).")
  return True


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="AutoYield health monitor")
  parser.add_argument("--full", action="store_true", help="Run full end-to-end system check")
  parser.add_argument("--monitor", action="store_true", help="Run scheduled monitor checks once")
  parser.add_argument("--daemon", action="store_true", help="Start APScheduler background monitor")
  args = parser.parse_args(argv)
  if args.daemon:
      return 0 if start_health_monitor() else 1
  if args.monitor:
      execute_monitor_cycle(alert_on_fail=True, email_summary=False)
      return 0
  run_full_system_check()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())