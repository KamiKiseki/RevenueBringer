from __future__ import annotations

import os
from dataclasses import dataclass
import inspect

import requests


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str


class HealthCheck:
    """
    Pre-flight launcher gate that blocks live runs when external handshakes are not ready.
    """

    def __init__(self, *, intended_simulate: bool):
        self.intended_simulate = bool(intended_simulate)
        self.webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

    def gate_stripe_webhook(self) -> GateResult:
        try:
            resp = requests.get(f"{self.webhook_base_url}/integrations/stripe/status", timeout=10)
            if resp.status_code >= 300:
                return GateResult("stripe_webhook_reachable", False, f"HTTP {resp.status_code}")
            payload = resp.json()
            ready = bool(payload.get("api_key_set")) and bool(payload.get("webhook_secret_set"))
            detail = (
                "Stripe endpoint reachable and credentials loaded."
                if ready
                else "Stripe endpoint reachable, but API key or webhook secret is missing."
            )
            return GateResult("stripe_webhook_reachable", ready, detail)
        except Exception as exc:
            return GateResult("stripe_webhook_reachable", False, f"Stripe status check failed: {exc}")

    def gate_apify_balance(self) -> GateResult:
        token = os.getenv("APIFY_API_TOKEN", "").strip()
        if not token:
            return GateResult("apify_token_authorized", False, "APIFY_API_TOKEN missing.")
        try:
            resp = requests.get("https://api.apify.com/v2/users/me", params={"token": token}, timeout=15)
            if resp.status_code >= 300:
                return GateResult("apify_token_authorized", False, f"APIFY API HTTP {resp.status_code}")
            data = (resp.json() or {}).get("data") or {}
            # Some Apify plans/workspaces can report 0.00 remaining in this field
            # while token-authenticated actor runs still work. Treat auth + active
            # account metadata as the primary gate.
            account_id = str(data.get("id") or "").strip()
            username = str(data.get("username") or "").strip()
            ok = bool(account_id or username)
            plan = data.get("plan") or {}
            monthly_limit = float(plan.get("monthlyUsageUsd") or 0)
            current_usage = float(data.get("monthlyUsageUsd") or 0)
            return GateResult(
                "apify_token_authorized",
                ok,
                (
                    f"Apify token authorized for '{username or account_id}'. "
                    f"Plan limit={monthly_limit:.2f} used={current_usage:.2f}"
                ),
            )
        except Exception as exc:
            return GateResult("apify_token_authorized", False, f"Apify check failed: {exc}")

    def gate_instantly_warmup(self) -> GateResult:
        # Instantly warmup verification is currently provided via explicit operator flag.
        # This avoids false positives when accounts vary across API plans.
        warmup_on = _as_bool(os.getenv("INSTANTLY_DOMAIN_WARMUP_ACTIVE"), default=False)
        if warmup_on:
            return GateResult("instantly_warmup_active", True, "INSTANTLY_DOMAIN_WARMUP_ACTIVE=true")
        return GateResult(
            "instantly_warmup_active",
            False,
            "Set INSTANTLY_DOMAIN_WARMUP_ACTIVE=true after confirming warmup in Instantly dashboard.",
        )

    def gate_simulation_intent(self) -> GateResult:
        env_sim = _as_bool(os.getenv("SIMULATE_MODE", "true"), default=True)
        if env_sim == self.intended_simulate:
            mode = "simulation" if env_sim else "live"
            return GateResult("simulate_mode_intent_match", True, f"SIMULATE_MODE matches intended {mode} run.")
        expected = "true" if self.intended_simulate else "false"
        return GateResult(
            "simulate_mode_intent_match",
            False,
            f"SIMULATE_MODE mismatch. Expected {expected}, found {str(env_sim).lower()}.",
        )

    def gate_clean_business_name_active(self) -> GateResult:
        """
        Guardrail: ensure ingestion path still sanitizes business names.
        """
        try:
            import scout

            src = inspect.getsource(scout.fetch_hvac_leads)
            if "clean_business_name(" in src:
                return GateResult(
                    "clean_business_name_active",
                    True,
                    "Ingestion path calls clean_business_name in scout.fetch_hvac_leads.",
                )
            return GateResult(
                "clean_business_name_active",
                False,
                "clean_business_name call not found in scout.fetch_hvac_leads ingestion path.",
            )
        except Exception as exc:
            return GateResult("clean_business_name_active", False, f"Unable to verify ingestion sanitization: {exc}")

    def run(self) -> list[GateResult]:
        return [
            self.gate_stripe_webhook(),
            self.gate_apify_balance(),
            self.gate_instantly_warmup(),
            self.gate_simulation_intent(),
            self.gate_clean_business_name_active(),
        ]

    def assert_ready(self, *, allow_simulation_bypass: bool = True) -> list[GateResult]:
        results = self.run()
        failures = [g for g in results if not g.ok]
        if not failures:
            return results
        if allow_simulation_bypass and self.intended_simulate:
            # In simulation we allow go-through while still printing failures.
            return results
        details = "; ".join(f"{g.name}: {g.detail}" for g in failures)
        raise RuntimeError(f"Pre-flight blocked launch. {details}")

