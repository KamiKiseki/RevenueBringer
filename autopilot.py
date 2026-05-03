from __future__ import annotations

import os
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.blocking import BlockingScheduler

from automation import run_cycle
from models import get_setting, init_db, log_system_event, set_setting
from scraper import get_random_target


STATE_TZ = {
    "AL": "US/Central", "AK": "US/Alaska", "AZ": "US/Arizona", "AR": "US/Central",
    "CA": "US/Pacific", "CO": "US/Mountain", "CT": "US/Eastern", "DC": "US/Eastern",
    "DE": "US/Eastern", "FL": "US/Eastern", "GA": "US/Eastern", "HI": "US/Hawaii",
    "IA": "US/Central", "ID": "US/Mountain", "IL": "US/Central", "IN": "US/Eastern",
    "KS": "US/Central", "KY": "US/Eastern", "LA": "US/Central", "MA": "US/Eastern",
    "MD": "US/Eastern", "ME": "US/Eastern", "MI": "US/Eastern", "MN": "US/Central",
    "MO": "US/Central", "MS": "US/Central", "MT": "US/Mountain", "NC": "US/Eastern",
    "ND": "US/Central", "NE": "US/Central", "NH": "US/Eastern", "NJ": "US/Eastern",
    "NM": "US/Mountain", "NV": "US/Pacific", "NY": "US/Eastern", "OH": "US/Eastern",
    "OK": "US/Central", "OR": "US/Pacific", "PA": "US/Eastern", "RI": "US/Eastern",
    "SC": "US/Eastern", "SD": "US/Central", "TN": "US/Central", "TX": "US/Central",
    "UT": "US/Mountain", "VA": "US/Eastern", "VT": "US/Eastern", "WA": "US/Pacific",
    "WI": "US/Central", "WV": "US/Eastern", "WY": "US/Mountain",
}


def _timezone_for_location(location: str) -> str:
    # format expected: "City, ST"
    if "," in location:
        state = location.rsplit(",", 1)[-1].strip().upper()
        if state in STATE_TZ:
            return STATE_TZ[state]
    return os.getenv("SCHEDULER_TIMEZONE", "US/Central")


def run_scheduled_cycle() -> dict[str, object]:
    default_target = get_random_target()
    niche = (get_setting("last_niche", "") or "").strip() or default_target["niche"]
    location = (get_setting("last_location", "") or "").strip() or default_target["location"]
    lead_count = int(get_setting("last_lead_count", "50"))
    simulate = (get_setting("simulate_mode", "1") == "1")
    result = run_cycle(
        niche=niche,
        location=location,
        daily_target=lead_count,
        simulate=simulate,
        send_report=True,
    )
    print("[AUTOPILOT] Scheduled cycle:", result)
    return result


def _schedule_next_job(scheduler: BlockingScheduler) -> None:
    target = get_random_target(
        last_location=get_setting("last_location", ""),
        last_niche=get_setting("last_niche", ""),
    )
    set_setting("last_niche", target["niche"])
    set_setting("last_location", target["location"])
    tz = _timezone_for_location(target["location"])
    scheduler.add_job(
        run_scheduled_cycle,
        "cron",
        hour=9,
        minute=0,
        timezone=tz,
        id="daily_target_city_cycle",
        replace_existing=True,
    )
    print(f"[AUTOPILOT] Next run set for 09:00 in {tz} | {target['niche']} @ {target['location']}")
    log_system_event(
        source="autopilot",
        action="schedule_next_job",
        detail=f"Scheduled 09:00 {tz} for {target['niche']} in {target['location']}.",
        level="info",
    )


def main() -> None:
    init_db()
    scheduler = BlockingScheduler(timezone="UTC")

    # Daily auto-start at 9:00 in the timezone of the currently selected random city.
    _schedule_next_job(scheduler)

    # One immediate boot cycle so deployment can be verified.
    run_scheduled_cycle()

    # After each run, rotate target and reschedule for the next city-local 09:00.
    def _on_job_done(event) -> None:
        if getattr(event, "job_id", "") == "daily_target_city_cycle":
            _schedule_next_job(scheduler)

    scheduler.add_listener(_on_job_done, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    scheduler.start()


if __name__ == "__main__":
    main()
