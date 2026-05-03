"""autoyieldsystems — Reflex app entry (modules: state, layout, tables, command_center)."""

from __future__ import annotations

import reflex as rx

from .command_center import command_center_page, landing_redirect
from .state import State

app = rx.App()
app.add_page(landing_redirect, route="/", title="autoyieldsystems.com")
app.add_page(
    command_center_page,
    route="/command-center",
    title="autoyieldsystems.com · Command Center",
    on_load=State.on_load,
)
