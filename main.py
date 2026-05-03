from __future__ import annotations

import argparse
import os

from automation import run_cycle
from automation import run_loop
from healthcheck import HealthCheck
from models import get_setting, init_db, set_setting
from scraper import get_random_target


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoYield automation runner")
    parser.add_argument("--limit", type=int, default=None, help="Run one cycle with this lead target and exit.")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode for one-off run.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live mode for one-off run.",
    )
    parser.add_argument(
        "--force-live",
        action="store_true",
        help="Alias for --live.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Bypass hard launch gate checks.",
    )
    parser.add_argument(
        "--randomize-city",
        action="store_true",
        help="Force a new random city+niche target for this one-off run.",
    )
    parser.add_argument(
        "--niche",
        type=str,
        default=None,
        help="Override target niche for one-off run.",
    )
    args = parser.parse_args()

    init_db()
    env_sim = os.getenv("SIMULATE_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
    live_intent = bool(args.live or args.force_live)
    intended_simulate = bool(args.simulate or (not live_intent and env_sim))
    # Autopilot and run_loop read `simulate_mode` from the DB; keep it aligned with
    # explicit CLI flags, or with SIMULATE_MODE for long-running daemon mode.
    if args.limit is not None or args.simulate or live_intent:
        set_setting("simulate_mode", "1" if intended_simulate else "0")
    else:
        set_setting("simulate_mode", "1" if env_sim else "0")

    if not args.skip_preflight:
        gate = HealthCheck(intended_simulate=intended_simulate)
        try:
            checks = gate.assert_ready(allow_simulation_bypass=True)
        except RuntimeError as exc:
            print(f"[PREFLIGHT] BLOCKED: {exc}")
            raise SystemExit(2)
        print("[PREFLIGHT] Results:")
        for item in checks:
            status = "PASS" if item.ok else "FAIL"
            print(f"- {status} {item.name}: {item.detail}")

    if args.limit is not None:
        last_location = get_setting("last_location", "")
        last_niche = get_setting("last_niche", "")
        target = get_random_target(
            last_location=last_location,
            last_niche=last_niche,
        )
        if args.randomize_city:
            niche = target["niche"]
            location = target["location"]
        else:
            niche = last_niche.strip() or target["niche"]
            location = last_location.strip() or target["location"]
        if args.niche and args.niche.strip():
            niche = args.niche.strip()
        result = run_cycle(
            niche=niche,
            location=location,
            daily_target=max(1, int(args.limit)),
            simulate=intended_simulate,
            send_report=False,
        )
        print("[MAIN] One-off cycle result:", result)
        return

    if not intended_simulate and not args.skip_preflight:
        # Enforce hard gate for daemon mode in live intent.
        try:
            HealthCheck(intended_simulate=False).assert_ready(allow_simulation_bypass=False)
        except RuntimeError as exc:
            print(f"[PREFLIGHT] BLOCKED: {exc}")
            raise SystemExit(2)

    interval = int(os.getenv("AUTOMATION_INTERVAL_SECONDS", "900"))
    run_loop(interval_seconds=max(30, interval))


if __name__ == "__main__":
    main()

