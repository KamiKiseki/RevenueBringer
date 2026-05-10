from __future__ import annotations

import os

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


def env_flag(name: str, default: bool = False) -> bool:
    """Parse an environment boolean without treating arbitrary strings as true."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def flask_debug_enabled() -> bool:
    """Keep the public Flask process non-debug by default; local debug is opt-in."""
    return env_flag("SERVER_DEBUG", False) or env_flag("FLASK_DEBUG", False)
