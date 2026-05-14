from __future__ import annotations

import os


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def _env_flag_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_ENV_VALUES


def flask_debug_enabled() -> bool:
    """Debug mode is unsafe on a public host; require an explicit local opt-in."""
    return _env_flag_enabled(os.getenv("SERVER_DEBUG")) or _env_flag_enabled(os.getenv("FLASK_DEBUG"))
