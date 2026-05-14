from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SERVICE = "RevenueBringer"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
RAILWAY_BIN = Path(os.environ.get("APPDATA", "")) / "npm" / "railway.cmd"


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def railway_vars(service: str) -> dict[str, str]:
    proc = subprocess.run(
        [str(RAILWAY_BIN), "variable", "list", "--json", "-s", service],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "railway variable list failed")
    return json.loads(proc.stdout or "{}")


def set_var(service: str, key: str, value: str) -> bool:
    proc = subprocess.run(
        [str(RAILWAY_BIN), "variable", "set", f"{key}={value}", "-s", service],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def main() -> int:
    local = parse_env(ENV_PATH)
    railway = railway_vars(SERVICE)

    overrides = {
        "SMTP_HOST": "mail.privateemail.com",
        "SMTP_PORT": "465",
        "SMTP_USER": "michael@autoyieldagency.com",
        "PUBLIC_BASE_URL": "https://autoyieldsystems.com",
    }
    if local.get("SMTP_USERNAME", "").strip():
        overrides.setdefault("SMTP_USERNAME", local["SMTP_USERNAME"].strip())
    if overrides.get("SMTP_USER"):
        overrides["SMTP_USERNAME"] = overrides["SMTP_USER"]

    priority = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "PUBLIC_BASE_URL",
        "VAPI_API_KEY",
        "VAPI_ASSISTANT_ID",
        "OPENAI_API_KEY",
        "APIFY_API_TOKEN",
        "INSTANTLY_API_KEY",
        "STRIPE_SECRET_KEY",
        "TELNYX_API_KEY",
        "STRIPE_WEBHOOK_SECRET",
    ]

    desired: dict[str, str] = {}
    for key, value in local.items():
        if value != "":
            desired[key] = value
    for key, value in overrides.items():
        if value != "":
            desired[key] = value
    if not desired.get("SMTP_USERNAME") and desired.get("SMTP_USER"):
        desired["SMTP_USERNAME"] = desired["SMTP_USER"]

    already: list[str] = []
    added: list[str] = []
    failed: list[str] = []
    missing_both: list[str] = []
    skipped_empty: list[str] = []

    railway_user_keys = {k for k in railway if not k.startswith("RAILWAY_")}

    force_keys = set(overrides) | set(priority)

    for key in sorted(desired):
        value = desired[key]
        if not value:
            skipped_empty.append(key)
            continue
        if key in railway_user_keys:
            if key not in force_keys or railway.get(key, "") == value:
                already.append(key)
                continue
        if set_var(SERVICE, key, value):
            added.append(key)
        else:
            failed.append(key)

    for key in sorted(local):
        if local.get(key, "") == "" and key not in railway_user_keys:
            missing_both.append(key)

    for key in priority:
        if key not in desired and key not in railway_user_keys and key not in missing_both:
            missing_both.append(key)

    print(
        json.dumps(
            {
                "service": SERVICE,
                "already_set": sorted(set(already)),
                "added": sorted(set(added)),
                "failed_to_add": sorted(set(failed)),
                "missing_from_both": sorted(set(missing_both)),
                "skipped_empty_in_desired": sorted(set(skipped_empty)),
            },
            indent=2,
        )
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
