from __future__ import annotations

import os


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in _TRUE_VALUES


def flask_debug_enabled() -> bool:
    """Production-safe Flask debug toggle; opt in explicitly for local debugging."""
    return _env_truthy("SERVER_DEBUG") or _env_truthy("FLASK_DEBUG")
