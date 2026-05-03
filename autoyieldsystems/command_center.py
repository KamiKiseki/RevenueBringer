"""Command Center shell — tabbed modules + landing redirect."""

from __future__ import annotations

import reflex as rx

from .layout import (
    dash_background,
    glass_card,
    module_tab_button,
    neo_stat_tile,
    neo_table_shell,
    placeholder_module,
)
from .state import State
from .tables import agreement_table, lead_rows, raw_db_table


def command_center_page() -> rx.Component:
    dashboard_panel = glass_card(
        rx.hstack(
            rx.text("Dashboard", weight="bold", color="white", size="4"),
            rx.spacer(),
            rx.button(
                "Global Killswitch",
                on_click=State.global_killswitch,
                color_scheme="red",
                variant="solid",
                style={"letter_spacing": "0.06em"},
            ),
            width="100%",
            align_items="center",
        ),
        rx.text(
            "Executive pulse for the whole stack — calls, conversion, revenue. Deeper drill-downs "
            "live in sibling modules.",
            color="#94a3b8",
            size="2",
        ),
        rx.cond(
            State.system_message != "",
            rx.callout(State.system_message, icon="info", color_scheme="blue", width="100%"),
        ),
        rx.hstack(
            neo_stat_tile(
                "Pulse · Calls",
                rx.text(State.total_calls_made, size="7", color="#a5f3fc", weight="bold"),
            ),
            neo_stat_tile(
                "Yield · Conversion",
                rx.text(State.conversion_rate_text, size="7", color="#d8b4fe", weight="bold"),
            ),
            neo_stat_tile(
                "Capture · Yield",
                rx.hstack(
                    rx.text("$", size="7", color="#67e8f9", weight="bold"),
                    rx.text(State.revenue_total, size="7", color="#67e8f9", weight="bold"),
                    spacing="1",
                    align_items="center",
                ),
            ),
            spacing="4",
            width="100%",
            align_items="stretch",
            flex_wrap="wrap",
        ),
        rx.hstack(
            neo_stat_tile(
                "Backend · Leads",
                rx.text(State.backend_total_leads, size="6", color="#93c5fd", weight="bold"),
            ),
            neo_stat_tile(
                "Backend · Conversions",
                rx.text(State.backend_conversions, size="6", color="#c4b5fd", weight="bold"),
            ),
            neo_stat_tile(
                "Backend · Yield",
                rx.hstack(
                    rx.text("$", size="6", color="#67e8f9", weight="bold"),
                    rx.text(State.backend_revenue_dollars, size="6", color="#67e8f9", weight="bold"),
                    spacing="1",
                    align_items="center",
                ),
            ),
            spacing="3",
            width="100%",
            align_items="stretch",
            flex_wrap="wrap",
        ),
        rx.hstack(
            neo_stat_tile(
                "Daily Cap",
                rx.text(State.cap_limit, size="5", color="#e2e8f0", weight="bold"),
            ),
            neo_stat_tile(
                "Sent Today",
                rx.text(State.cap_used_today, size="5", color="#facc15", weight="bold"),
            ),
            neo_stat_tile(
                "Remaining Today",
                rx.text(State.cap_remaining_today, size="5", color="#34d399", weight="bold"),
            ),
            spacing="3",
            width="100%",
            align_items="stretch",
            flex_wrap="wrap",
        ),
        rx.callout(
            rx.vstack(
                rx.text("Current Random Target", weight="bold"),
                rx.text(
                    rx.cond(
                        State.current_target_niche != "",
                        State.current_target_niche,
                        "Pending cycle",
                    ),
                    color="#cbd5e1",
                    size="2",
                ),
                rx.text(
                    rx.cond(
                        State.current_target_location != "",
                        State.current_target_location,
                        "Pending cycle",
                    ),
                    color="#94a3b8",
                    size="2",
                ),
                spacing="1",
                align_items="start",
            ),
            icon="map_pin",
            color_scheme="cyan",
            width="100%",
        ),
        rx.hstack(
            rx.button(
                "Reset Target",
                on_click=State.force_next_target,
                color_scheme="cyan",
                variant="outline",
            ),
            rx.button(
                "Retry Last Failed",
                on_click=State.retry_last_failed,
                color_scheme="orange",
                variant="outline",
            ),
            spacing="3",
            flex_wrap="wrap",
        ),
        rx.callout(
            rx.vstack(
                rx.text("Conversion Funnel", weight="bold"),
                rx.text(State.conversion_funnel_text, color="#cbd5e1", size="2"),
                spacing="1",
                align_items="start",
            ),
            icon="bar_chart_3",
            color_scheme="purple",
            width="100%",
        ),
        rx.box(
            rx.text("Live Activity Feed", weight="bold", color="white", size="3"),
            rx.vstack(
                rx.foreach(
                    State.recent_activity_feed,
                    lambda row: rx.box(
                        rx.hstack(
                            rx.badge(row["channel"], color_scheme="cyan", variant="soft"),
                            rx.badge(row["status"], color_scheme="gray", variant="surface"),
                            rx.text(row["at"], color="#94a3b8", size="1"),
                            spacing="2",
                            align_items="center",
                            flex_wrap="wrap",
                        ),
                        rx.text(row["detail"], color="#dbeafe", size="2"),
                        padding="0.55rem",
                        border_radius="10px",
                        border="1px solid rgba(56, 189, 248, 0.16)",
                        background="rgba(0,0,0,0.2)",
                        width="100%",
                    ),
                ),
                spacing="2",
                width="100%",
                align_items="stretch",
            ),
            width="100%",
        ),
        rx.box(
            rx.text("Errors Safety Net", weight="bold", color="white", size="3"),
            rx.vstack(
                rx.cond(
                    State.automation_error_feed != [],
                    rx.foreach(
                        State.automation_error_feed,
                        lambda row: rx.box(
                            rx.hstack(
                                rx.badge(row["kind"], color_scheme="red", variant="soft"),
                                rx.text(row["at"], color="#94a3b8", size="1"),
                                spacing="2",
                                align_items="center",
                                flex_wrap="wrap",
                            ),
                            rx.text(row["detail"], color="#fecaca", size="2"),
                            padding="0.55rem",
                            border_radius="10px",
                            border="1px solid rgba(248, 113, 113, 0.22)",
                            background="rgba(40,0,0,0.16)",
                            width="100%",
                        ),
                    ),
                    rx.text("No recent critical errors.", color="#86efac", size="2"),
                ),
                spacing="2",
                width="100%",
                align_items="stretch",
            ),
            width="100%",
        ),
        rx.button(
            "Refresh signals",
            on_click=State.refresh_signals,
            color_scheme="cyan",
            variant="outline",
            style={"letter_spacing": "0.08em"},
        ),
        width="100%",
    )

    lead_engine_panel = glass_card(
        rx.text("Lead Engine", weight="bold", color="white", size="4"),
        rx.text(
            "Scout pipeline: niche, geo, batch size → database. Queue handoff happens in Outreach.",
            color="#94a3b8",
            size="2",
        ),
        rx.hstack(
            rx.input(
                placeholder="Niche (e.g. HVAC, Med Spa)",
                value=State.niche_val,
                on_change=State.set_niche_val,
                flex="1",
            ),
            rx.input(
                placeholder="Location",
                value=State.location_val,
                on_change=State.set_location_val,
                flex="1",
            ),
            rx.input(
                placeholder="Lead count",
                value=State.lead_count_str,
                on_change=State.set_lead_count_str,
                width="120px",
            ),
            rx.button(
                "Deploy scout",
                on_click=State.run_scout,
                color_scheme="cyan",
                variant="solid",
                style={"letter_spacing": "0.06em"},
            ),
            spacing="4",
            width="100%",
            flex_wrap="wrap",
            align_items="center",
        ),
        rx.text("Lead grid", weight="bold", color="white", size="3"),
        rx.hstack(
            rx.input(
                placeholder="Search business name…",
                value=State.search_query,
                on_change=State.set_search_query,
                flex="1",
            ),
            rx.select(
                ["all", "queued", "emailed", "paid", "delivered", "active_client"],
                value=State.status_filter,
                on_change=State.set_status_filter,
                width="200px",
            ),
            spacing="3",
            width="100%",
        ),
        neo_table_shell(lead_rows()),
        width="100%",
    )

    outreach_panel = glass_card(
        rx.text("Outreach", weight="bold", color="white", size="4"),
        rx.text(
            "Transmit queued leads into Instantly — paired with Outreach Config for keys/campaign.",
            color="#94a3b8",
            size="2",
        ),
        neo_stat_tile(
            "Queued · Ready",
            rx.text(State.queued_leads_count, size="7", color="#a5f3fc", weight="bold"),
        ),
        rx.button(
            "Transmit queue → Instantly",
            on_click=State.send_outreach,
            variant="solid",
            color_scheme="cyan",
            style={"letter_spacing": "0.06em"},
        ),
        width="100%",
    )

    contact_submissions_panel = glass_card(
        rx.text("Contact Submissions", weight="bold", color="white", size="4"),
        rx.text(
            "Public web intake has been removed from the deployment. Pipeline leads remain in Lead Engine below.",
            color="#94a3b8",
            size="2",
        ),
        neo_table_shell(lead_rows()),
        width="100%",
    )

    tracking_panel = glass_card(
        rx.text("Tracking", weight="bold", color="white", size="4"),
        rx.text(
            "Court-ready lineage: correlation IDs across leads + agreements — use with Payments & Deal Vault.",
            color="#94a3b8",
            size="2",
        ),
        neo_table_shell(raw_db_table()),
        width="100%",
    )

    payments_panel = glass_card(
        rx.text("Payments", weight="bold", color="white", size="4"),
        rx.text(
            "Stripe Checkout sessions, receipts, and paid states — mapped by correlation_id / client_reference_id.",
            color="#94a3b8",
            size="2",
        ),
        rx.input(
            placeholder="Filter agreements by client name…",
            value=State.agreement_search_query,
            on_change=State.set_agreement_search_query,
            width="100%",
        ),
        neo_table_shell(agreement_table()),
        width="100%",
    )

    behavior_ai_panel = glass_card(
        rx.text("Behavior AI", weight="bold", color="white", size="4"),
        rx.text(
            "Conversation / scoring layer (Vapi & downstream models). Surface area for inference + transcripts.",
            color="#94a3b8",
            size="2",
        ),
        neo_stat_tile(
            "Signals · Call volume",
            rx.text(State.total_calls_made, size="7", color="#d8b4fe", weight="bold"),
        ),
        rx.button(
            "Refresh signals",
            on_click=State.refresh_signals,
            color_scheme="violet",
            variant="outline",
        ),
        rx.callout(
            "Extend here for transcripts, embeddings, or disposition scoring.",
            icon="zap",
            color_scheme="purple",
            width="100%",
        ),
        width="100%",
    )

    deal_vault_panel = glass_card(
        rx.text("Deal Vault", weight="bold", color="white", size="4"),
        rx.text(
            "Legal-grade agreements — PandaDoc lifecycle, signatures, PDF artifacts.",
            color="#94a3b8",
            size="2",
        ),
        rx.input(
            placeholder="Search by client name…",
            value=State.agreement_search_query,
            on_change=State.set_agreement_search_query,
            width="100%",
        ),
        neo_table_shell(agreement_table()),
        width="100%",
    )

    dm_generator_panel = placeholder_module(
        "DM Generator",
        "Compose outbound DM variants from lead context — templates, tone rails, and approval queues.",
    )

    cold_caller_panel = glass_card(
        rx.text("Cold Caller", weight="bold", color="white", size="4"),
        rx.text(
            "Voice agent ingress posts here via webhook — pair Vapi assistants with Lead Engine correlation IDs.",
            color="#94a3b8",
            size="2",
        ),
        rx.text("Vapi webhook target:", weight="bold", color="#e2e8f0", size="2"),
        rx.text(State.webhook_vapi_url, color="#7dd3fc", size="2", style={"word_break": "break-all"}),
        rx.callout(
            "Dialer disposition → Interested triggers PandaDoc path in server automation.",
            icon="phone",
            color_scheme="cyan",
            width="100%",
        ),
        width="100%",
    )

    automation_panel = glass_card(
        rx.text("Automation", weight="bold", color="white", size="4"),
        rx.text(
            "One-click backend control tower. Start/stop automation, run safe simulation cycles, and send reports.",
            color="#94a3b8",
            size="2",
        ),
        rx.hstack(
            neo_stat_tile(
                "Backend health",
                rx.cond(
                    State.backend_health_ok,
                    rx.text("ONLINE", size="5", color="#34d399", weight="bold"),
                    rx.text("OFFLINE", size="5", color="#f87171", weight="bold"),
                ),
            ),
            neo_stat_tile(
                "Automation",
                rx.cond(
                    State.automation_running,
                    rx.text("RUNNING", size="5", color="#22d3ee", weight="bold"),
                    rx.text("STOPPED", size="5", color="#94a3b8", weight="bold"),
                ),
            ),
            neo_stat_tile(
                "Mode",
                rx.cond(
                    State.simulate_mode,
                    rx.text("SIMULATION", size="5", color="#facc15", weight="bold"),
                    rx.text("LIVE", size="5", color="#fb7185", weight="bold"),
                ),
            ),
            spacing="3",
            width="100%",
            flex_wrap="wrap",
        ),
        rx.hstack(
            rx.button(
                rx.cond(State.automation_running, "Stop automation", "Start automation"),
                on_click=State.toggle_automation,
                color_scheme=rx.cond(State.automation_running, "red", "green"),
                variant="solid",
            ),
            rx.button(
                "Run safe simulation cycle",
                on_click=State.run_simulation_cycle,
                color_scheme="cyan",
                variant="outline",
            ),
            rx.button(
                "Send daily report now",
                on_click=State.send_daily_report_now,
                color_scheme="purple",
                variant="outline",
            ),
            rx.button(
                "Refresh backend snapshot",
                on_click=State.refresh_backend_snapshot,
                color_scheme="gray",
                variant="soft",
            ),
            spacing="3",
            flex_wrap="wrap",
        ),
        rx.callout(
            rx.vstack(
                rx.text("Latest report", weight="bold"),
                rx.text(State.latest_report_subject, color="#cbd5e1", size="2"),
                rx.text(
                    rx.cond(
                        State.latest_report_status != "",
                        State.latest_report_status,
                        "No report yet",
                    ),
                    color="#94a3b8",
                    size="2",
                ),
                spacing="1",
                align_items="start",
            ),
            icon="activity",
            color_scheme="blue",
            width="100%",
        ),
        rx.callout(
            rx.vstack(
                rx.text("Stripe Webhook Handshake", weight="bold"),
                rx.cond(
                    State.stripe_webhook_ready,
                    rx.text("READY", color="#86efac", size="2"),
                    rx.text("NOT READY", color="#fca5a5", size="2"),
                ),
                rx.text(
                    rx.cond(
                        State.stripe_last_event_type != "",
                        f"Last event: {State.stripe_last_event_type}",
                        "Last event: none yet",
                    ),
                    color="#cbd5e1",
                    size="2",
                ),
                rx.text(State.stripe_last_event_at, color="#94a3b8", size="1"),
                spacing="1",
                align_items="start",
            ),
            icon="credit_card",
            color_scheme="green",
            width="100%",
        ),
        rx.text(
            "Run both services: `python server.py` and `python main.py`",
            color="#64748b",
            size="2",
            font_family="ui-monospace, monospace",
        ),
        rx.text("Webhook URLs (WEBHOOK_BASE_URL):", weight="bold", color="#e2e8f0", size="2"),
        rx.text(State.webhook_health_url, color="#7dd3fc", size="2", style={"word_break": "break-all"}),
        rx.text(State.webhook_stripe_url, color="#7dd3fc", size="2", style={"word_break": "break-all"}),
        rx.text(State.webhook_vapi_url, color="#7dd3fc", size="2", style={"word_break": "break-all"}),
        rx.text(State.webhook_pandadoc_url, color="#7dd3fc", size="2", style={"word_break": "break-all"}),
        rx.text(
            "Set WEBHOOK_BASE_URL in .env for public tunnels or Railway.",
            color="#64748b",
            size="2",
        ),
        width="100%",
    )

    live_monitor_panel = glass_card(
        rx.text("Live Monitor", weight="bold", color="white", size="4"),
        rx.text(
            "Operational feed + quick vitals — pair with Automation when webhooks spike or fail.",
            color="#94a3b8",
            size="2",
        ),
        rx.cond(
            State.system_message != "",
            rx.callout(State.system_message, icon="activity", color_scheme="blue", width="100%"),
        ),
        rx.hstack(
            neo_stat_tile(
                "Calls",
                rx.text(State.total_calls_made, size="6", color="#a5f3fc", weight="bold"),
            ),
            neo_stat_tile(
                "Queued",
                rx.text(State.queued_leads_count, size="6", color="#fde68a", weight="bold"),
            ),
            neo_stat_tile(
                "Yield",
                rx.hstack(
                    rx.text("$", size="6", color="#67e8f9", weight="bold"),
                    rx.text(State.revenue_total, size="6", color="#67e8f9", weight="bold"),
                    spacing="1",
                    align_items="center",
                ),
            ),
            spacing="3",
            width="100%",
            flex_wrap="wrap",
        ),
        rx.button(
            "Refresh monitor",
            on_click=State.refresh_signals,
            color_scheme="cyan",
            variant="soft",
        ),
        width="100%",
    )

    outreach_config_panel = glass_card(
        rx.text("Outreach Config", weight="bold", color="white", size="4"),
        rx.text(
            "Instantly credentials and campaign routing — sourced from environment (never commit secrets).",
            color="#94a3b8",
            size="2",
        ),
        rx.callout(rx.text(State.outreach_env_summary), icon="settings", color_scheme="gray", width="100%"),
        rx.text(
            "Set INSTANTLY_API_KEY and INSTANTLY_CAMPAIGN_ID in .env, then redeploy / restart Reflex.",
            color="#64748b",
            size="2",
        ),
        width="100%",
    )

    system_logs_panel = glass_card(
        rx.text("System Logs", weight="bold", color="white", size="4"),
        rx.text(
            "Operational trace from try/except capture points across automation, webhooks, and integrations.",
            color="#94a3b8",
            size="2",
        ),
        rx.button(
            "Refresh logs",
            on_click=State.refresh_backend_snapshot,
            color_scheme="gray",
            variant="soft",
        ),
        rx.vstack(
            rx.cond(
                State.system_logs_rows != [],
                rx.foreach(
                    State.system_logs_rows,
                    lambda row: rx.box(
                        rx.hstack(
                            rx.badge(row["level"], color_scheme="red", variant="soft"),
                            rx.badge(row["source"], color_scheme="cyan", variant="soft"),
                            rx.text(row["action"], color="#dbeafe", size="2"),
                            rx.text(row["at"], color="#94a3b8", size="1"),
                            spacing="2",
                            align_items="center",
                            flex_wrap="wrap",
                        ),
                        rx.text(row["detail"], color="#fca5a5", size="2"),
                        rx.cond(
                            row["correlation_id"] != "",
                            rx.text(f"CID: {row['correlation_id']}", color="#93c5fd", size="1"),
                            rx.fragment(),
                        ),
                        padding="0.55rem",
                        border_radius="10px",
                        border="1px solid rgba(248, 113, 113, 0.2)",
                        background="rgba(0,0,0,0.2)",
                        width="100%",
                    ),
                ),
                rx.text("No log records yet.", color="#86efac", size="2"),
            ),
            spacing="2",
            width="100%",
            align_items="stretch",
        ),
        width="100%",
    )

    module_order = [
        "dashboard",
        "lead_engine",
        "outreach",
        "contact_submissions",
        "tracking",
        "payments",
        "behavior_ai",
        "deal_vault",
        "dm_generator",
        "cold_caller",
        "automation",
        "live_monitor",
        "outreach_config",
        "system_logs",
    ]
    panels: dict[str, rx.Component] = {
        "dashboard": dashboard_panel,
        "lead_engine": lead_engine_panel,
        "outreach": outreach_panel,
        "contact_submissions": contact_submissions_panel,
        "tracking": tracking_panel,
        "payments": payments_panel,
        "behavior_ai": behavior_ai_panel,
        "deal_vault": deal_vault_panel,
        "dm_generator": dm_generator_panel,
        "cold_caller": cold_caller_panel,
        "automation": automation_panel,
        "live_monitor": live_monitor_panel,
        "outreach_config": outreach_config_panel,
        "system_logs": system_logs_panel,
    }

    tab_content = panels["dashboard"]
    for key in reversed(module_order[1:]):
        tab_content = rx.cond(State.command_center_tab == key, panels[key], tab_content)

    module_tabs = [
        ("Dashboard", "dashboard", State.cc_dashboard),
        ("Lead Engine", "lead_engine", State.cc_lead_engine),
        ("Outreach", "outreach", State.cc_outreach),
        ("Contact Submissions", "contact_submissions", State.cc_contact_submissions),
        ("Tracking", "tracking", State.cc_tracking),
        ("Payments", "payments", State.cc_payments),
        ("Behavior AI", "behavior_ai", State.cc_behavior_ai),
        ("Deal Vault", "deal_vault", State.cc_deal_vault),
        ("DM Generator", "dm_generator", State.cc_dm_generator),
        ("Cold Caller", "cold_caller", State.cc_cold_caller),
        ("Automation", "automation", State.cc_automation),
        ("Live Monitor", "live_monitor", State.cc_live_monitor),
        ("Outreach Config", "outreach_config", State.cc_outreach_config),
        ("System Logs", "system_logs", State.cc_system_logs),
    ]

    return rx.box(
        rx.box(
            rx.moment(
                date="2020-06-01T12:00:00",
                from_now=True,
                interval=4000,
                on_change=State.sync_db_views,
            ),
            position="absolute",
            width="1px",
            height="1px",
            overflow="hidden",
            opacity="0",
            pointer_events="none",
            aria_hidden="true",
            top="0",
            left="0",
            z_index="-1",
        ),
        rx.vstack(
            rx.box(
                width="100%",
                height="3px",
                border_radius="999px",
                background="linear-gradient(90deg, transparent 0%, rgba(56,189,248,0.95) 35%, rgba(167,139,250,0.9) 65%, transparent 100%)",
                box_shadow="0 0 24px rgba(56,189,248,0.35)",
                align_self="stretch",
            ),
            rx.hstack(
                rx.vstack(
                    rx.text(
                        "AUTOYIELDSYSTEMS.COM · COMMAND CENTER",
                        color="#64748b",
                        size="1",
                        weight="bold",
                        style={"letter_spacing": "0.28em"},
                    ),
                    rx.heading(
                        "Unified Operations Dashboard",
                        size="8",
                        style={
                            "background": "linear-gradient(92deg, #f0f9ff 0%, #a5f3fc 42%, #e9d5ff 100%)",
                            "WebkitBackgroundClip": "text",
                            "WebkitTextFillColor": "transparent",
                            "background_clip": "text",
                            "filter": "drop-shadow(0 0 28px rgba(56,189,248,0.35))",
                            "letter_spacing": "-0.02em",
                        },
                    ),
                    spacing="2",
                    align_items="start",
                ),
                rx.spacer(),
                rx.badge(
                    "Live stack",
                    color_scheme="cyan",
                    variant="surface",
                    style={"letter_spacing": "0.12em"},
                ),
                width="100%",
                align_items="center",
                flex_wrap="wrap",
                spacing="4",
            ),
            rx.text(
                "Single dashboard shell — thirteen modules as horizontal tabs (left → right). "
                "Each tab owns its viewport; data stays connected through one Reflex State + shared DB.",
                color="#93c5fd",
                size="2",
                style={"max_width": "54rem", "line_height": "1.65"},
            ),
            rx.box(
                rx.hstack(
                    *[module_tab_button(lbl, key, h) for lbl, key, h in module_tabs],
                    spacing="2",
                    align_items="stretch",
                    flex_wrap="nowrap",
                    width="max-content",
                ),
                padding="6px",
                border_radius="18px",
                background="rgba(3, 8, 22, 0.72)",
                border="1px solid rgba(129, 230, 217, 0.18)",
                box_shadow=(
                    "inset 0 1px 0 rgba(255,255,255,0.06), "
                    "0 12px 40px rgba(0,0,0,0.35)"
                ),
                width="100%",
                overflow_x="auto",
            ),
            tab_content,
            spacing="6",
            width="100%",
            max_width="1320px",
            align_items="stretch",
        ),
        min_height="100vh",
        width="100%",
        padding="2rem",
        background=dash_background(),
        background_attachment="fixed",
        display="flex",
        justify_content="center",
        position="relative",
    )


def landing_redirect() -> rx.Component:
    """Send `/` visitors straight to the unified Command Center."""
    return rx.box(
        rx.script("window.location.replace('/command-center')"),
        rx.center(
            rx.vstack(
                rx.box(
                    width="160px",
                    height="160px",
                    border_radius="50%",
                    border="2px solid rgba(56, 189, 248, 0.35)",
                    box_shadow=(
                        "0 0 60px rgba(56,189,248,0.25), "
                        "inset 0 0 40px rgba(167,139,250,0.12)"
                    ),
                    background="radial-gradient(circle at 30% 30%, rgba(56,189,248,0.2), transparent 55%)",
                    margin_bottom="1.5rem",
                ),
                rx.text(
                    "Initializing neural dashboard…",
                    color="#a5f3fc",
                    size="4",
                    weight="medium",
                    style={"letter_spacing": "0.12em"},
                ),
                rx.text(
                    "autoyieldsystems.com",
                    color="#64748b",
                    size="2",
                    style={"letter_spacing": "0.22em", "text_transform": "uppercase"},
                ),
                spacing="3",
                align_items="center",
            ),
            width="100%",
            min_height="100vh",
        ),
        width="100%",
        background=dash_background(),
        background_attachment="fixed",
    )
