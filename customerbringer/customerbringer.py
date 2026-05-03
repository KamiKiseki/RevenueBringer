"""
AutoYield Systems — Reflex app entry.

Modules live under `customerbringer/`: `state`, `layout`, `tables`, `command_center`.
"""

from __future__ import annotations

import reflex as rx

from .command_center import command_center_page, landing_redirect
from .state import State

app = rx.App()
app.add_page(landing_redirect, route="/", title="AutoYield Systems")
app.add_page(
    command_center_page,
    route="/command-center",
    title="AutoYield Systems · Command Center",
    on_load=State.on_load,
)
