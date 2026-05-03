"""Shared Command Center chrome — glass panels, tabs, placeholders."""

from __future__ import annotations

import reflex as rx

from .state import State


def dash_background() -> str:
    return (
        "radial-gradient(ellipse 120% 80% at 50% -30%, rgba(56, 189, 248, 0.18), transparent 55%), "
        "radial-gradient(ellipse 70% 50% at 100% 100%, rgba(167, 139, 250, 0.14), transparent 50%), "
        "linear-gradient(168deg, #050810 0%, #0a1528 38%, #080616 100%)"
    )


def glass_card(*children: rx.Component, width: str | None = None) -> rx.Component:
    """Neo-glass module panel — holographic edge + depth."""
    return rx.box(
        rx.vstack(*children, spacing="3", width="100%", align_items="stretch"),
        padding="1.5rem",
        border_radius="20px",
        background="linear-gradient(152deg, rgba(14, 24, 46, 0.92) 0%, rgba(8, 12, 28, 0.82) 55%, rgba(12, 10, 32, 0.88) 100%)",
        border="1px solid rgba(129, 230, 217, 0.22)",
        box_shadow=(
            "0 0 0 1px rgba(167, 139, 250, 0.12), "
            "0 16px 48px rgba(0, 0, 0, 0.55), "
            "inset 0 1px 0 rgba(255, 255, 255, 0.07)"
        ),
        backdrop_filter="blur(18px)",
        width=width or "100%",
    )


def neo_table_shell(inner: rx.Component) -> rx.Component:
    return rx.box(
        inner,
        width="100%",
        border_radius="14px",
        overflow_x="auto",
        border="1px solid rgba(56, 189, 248, 0.12)",
        box_shadow="inset 0 0 48px rgba(0, 0, 0, 0.22)",
        background="rgba(0, 0, 0, 0.15)",
    )


def neo_stat_tile(title: str, value_el: rx.Component) -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.text(
                title,
                color="#94a3b8",
                size="2",
                weight="medium",
                style={"letter_spacing": "0.12em", "text_transform": "uppercase"},
            ),
            value_el,
            spacing="2",
            align_items="start",
            width="100%",
        ),
        padding="1.15rem",
        border_radius="14px",
        background="rgba(0, 0, 0, 0.28)",
        border="1px solid rgba(56, 189, 248, 0.14)",
        box_shadow="inset 0 1px 0 rgba(255,255,255,0.04)",
        flex="1",
        width="100%",
    )


def module_tab_button(label: str, tab_key: str, handler) -> rx.Component:
    active = State.command_center_tab == tab_key
    return rx.box(
        rx.button(
            label,
            on_click=handler,
            variant="ghost",
            size="2",
            color_scheme=rx.cond(active, "cyan", "gray"),
            style={
                "letter_spacing": "0.04em",
                "font_weight": "600",
                "border_radius": "10px",
                "white_space": "nowrap",
                "padding_left": "0.75rem",
                "padding_right": "0.75rem",
            },
        ),
        flex="none",
        min_width="max-content",
        border_radius="12px",
        border=rx.cond(active, "1px solid rgba(56, 189, 248, 0.55)", "1px solid transparent"),
        background=rx.cond(active, "rgba(56, 189, 248, 0.12)", "transparent"),
        box_shadow=rx.cond(
            active,
            "0 0 28px rgba(56, 189, 248, 0.22), inset 0 1px 0 rgba(255,255,255,0.08)",
            "none",
        ),
        transition="all 0.2s ease",
    )


def placeholder_module(title: str, body: str) -> rx.Component:
    return glass_card(
        rx.text(title, weight="bold", color="white", size="4"),
        rx.text(body, color="#94a3b8", size="2", style={"line_height": "1.6"}),
        rx.callout(
            "Stub module — connect pipelines from this unified shell.",
            icon="construction",
            color_scheme="purple",
            width="100%",
        ),
        width="100%",
    )
