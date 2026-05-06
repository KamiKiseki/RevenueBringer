from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

from playwright.sync_api import TimeoutError as PwTimeoutError
from playwright.sync_api import sync_playwright


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    ms: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _run_step(name: str, fn) -> StepResult:
    start = _now_ms()
    try:
        detail = fn() or ""
        return StepResult(name=name, ok=True, detail=str(detail), ms=_now_ms() - start)
    except Exception as exc:
        return StepResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}", ms=_now_ms() - start)


def main() -> int:
    p = argparse.ArgumentParser(description="UI smoke: open Command Center and click through tabs.")
    p.add_argument("--cc-url", default="http://127.0.0.1:3021/command-center")
    p.add_argument("--live-url", default="https://autoyieldsystems.com")
    p.add_argument("--timeout-ms", type=int, default=8000)
    p.add_argument("--slow-tab-ms", type=int, default=2500)
    p.add_argument("--headful", action="store_true")
    args = p.parse_args()

    results: list[StepResult] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        def go_live():
            page.goto(args.live_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(200)
            title = page.title()
            if "AutoYield" not in title:
                raise RuntimeError(f"Unexpected live title: {title}")
            return title

        results.append(_run_step("live_homepage_load", go_live))

        def go_cc():
            page.goto(args.cc_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(300)
            # Page should include a recognizable header.
            page.get_by_text("Unified Operations Dashboard").first.wait_for(timeout=args.timeout_ms)

        results.append(_run_step("command_center_load", go_cc))

        # Click through tabs by label. We validate by waiting for a unique heading per panel.
        tab_checks = [
            ("Dashboard", "Dashboard"),
            ("Lead Engine", "Lead Engine"),
            ("Outreach", "Outreach"),
            ("Contact Submissions", "Contact Submissions"),
            ("Tracking", "Tracking"),
            ("Payments", "Payments"),
            ("Behavior AI", "Behavior AI"),
            ("Deal Vault", "Deal Vault"),
            ("DM Generator", "DM Generator"),
            ("Cold Caller", "Cold Caller"),
            ("Automation", "Automation"),
            ("Live Monitor", "Live Monitor"),
            ("Outreach Config", "Outreach Config"),
            ("System Logs", "System Logs"),
        ]

        for tab_label, expected_heading in tab_checks:
            def click_and_wait():
                t0 = _now_ms()
                page.get_by_role("button", name=tab_label, exact=True).click(timeout=args.timeout_ms)
                # The panel heading appears inside the glass card.
                page.get_by_text(expected_heading).first.wait_for(timeout=args.timeout_ms)
                elapsed = _now_ms() - t0
                if elapsed > args.slow_tab_ms:
                    return f"slow={elapsed}ms"
                return f"ok={elapsed}ms"

            results.append(_run_step(f"tab:{tab_label}", click_and_wait))

        # Screenshot for debugging if anything failed.
        failed = [r for r in results if not r.ok]
        if failed:
            try:
                page.screenshot(path="ui_smoke_fail.png", full_page=True)
            except Exception:
                pass
        browser.close()

    print("=== UI SMOKE REPORT ===")
    ok_all = True
    for r in results:
        status = "OK" if r.ok else "FAIL"
        ok_all = ok_all and r.ok
        extra = f" ({r.detail})" if r.detail else ""
        print(f"{status:<4} {r.name:<26} {r.ms:>5}ms{extra}")

    if not ok_all:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

