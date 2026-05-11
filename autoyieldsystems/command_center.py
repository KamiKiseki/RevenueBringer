"""Command Center shell — tabbed modules + landing redirect."""

from __future__ import annotations

import reflex as rx

from .layout import (
    dash_background,
    glass_card,
    module_tab_button,
    neo_stat_tile,
    neo_table_shell,
)
from .state import State


def command_center_page() -> rx.Component:
    dashboard_panel = glass_card(
        rx.text("Dashboard", weight="bold", color="white", size="4"),
        rx.text(
            "Real-time operating snapshot for today and this month.",
            color="#94a3b8",
            size="2",
        ),
        rx.hstack(
            neo_stat_tile("Total Leads Scraped Today", rx.text(State.today_leads_scraped, size="6", color="#a5f3fc", weight="bold")),
            neo_stat_tile("Emails Sent Today", rx.text(State.today_emails_sent, size="6", color="#a5f3fc", weight="bold")),
            neo_stat_tile("Calls Made Today", rx.text(State.today_calls_made, size="6", color="#a5f3fc", weight="bold")),
            spacing="3", width="100%", flex_wrap="wrap",
        ),
        rx.hstack(
            neo_stat_tile("Replies Received Today", rx.text(State.today_replies_received, size="6", color="#d8b4fe", weight="bold")),
            neo_stat_tile("Deals Closed This Month", rx.text(State.month_deals_closed, size="6", color="#d8b4fe", weight="bold")),
            neo_stat_tile(
                "Revenue Collected This Month",
                rx.hstack(rx.text("$", color="#67e8f9"), rx.text(State.month_revenue_collected, size="6", color="#67e8f9", weight="bold"), spacing="1"),
            ),
            spacing="3", width="100%", flex_wrap="wrap",
        ),
        rx.callout(
            "Machine status: if these cards are moving, your growth engine is actively running.",
            icon="activity",
            color_scheme="cyan",
            width="100%",
        ),
        rx.text("Activity Feed", weight="bold", color="white", size="3"),
        rx.vstack(
            rx.foreach(
                State.activity_feed_rows,
                lambda row: rx.box(
                    rx.text(row["at"], size="1", color="#94a3b8"),
                    rx.text(row["text"], size="2", color="#dbeafe"),
                    border="1px solid rgba(56, 189, 248, 0.16)", border_radius="10px", padding="0.6rem", width="100%", background="rgba(0,0,0,0.2)",
                ),
            ),
            width="100%", align_items="stretch",
        ),
        width="100%",
    )

    lead_engine_panel = glass_card(
        rx.text("Lead Engine", weight="bold", color="white", size="4"),
        rx.text("Run scrape jobs and monitor newly discovered businesses.", color="#94a3b8", size="2"),
        rx.hstack(
            neo_stat_tile("Total Leads In Database", rx.text(State.lead_engine_total, size="6", color="#a5f3fc", weight="bold")),
            width="100%",
        ),
        rx.hstack(
            rx.select(
                ["HVAC", "Roofing", "Dental", "Law Firms", "Real Estate", "Plumbers", "Auto Body", "Med Spas", "Gyms", "Insurance"],
                value=State.niche_val,
                on_change=State.set_niche_val,
                placeholder="Select niche",
                width="260px",
            ),
            rx.input(placeholder="City", value=State.location_val, on_change=State.set_location_val, width="260px"),
            rx.button("Run Scraper", on_click=State.run_scout, color_scheme="cyan", variant="solid"),
            spacing="3", flex_wrap="wrap",
        ),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Business Name"),
                    rx.table.column_header_cell("Phone Number"),
                    rx.table.column_header_cell("City"),
                    rx.table.column_header_cell("Niche"),
                    rx.table.column_header_cell("Date Scraped"),
                    rx.table.column_header_cell("Status"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.lead_engine_rows,
                        lambda row: rx.table.row(
                            rx.table.cell(row["business_name"]),
                            rx.table.cell(row["phone"]),
                            rx.table.cell(row["city"]),
                            rx.table.cell(row["niche"]),
                            rx.table.cell(row["date_scraped"]),
                            rx.table.cell(row["status"]),
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        width="100%",
    )

    outreach_panel = glass_card(
        rx.text("Outreach", weight="bold", color="white", size="4"),
        rx.text("Campaign momentum and reply signals from outbound email.", color="#94a3b8", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Campaign Name"),
                    rx.table.column_header_cell("Emails Sent"),
                    rx.table.column_header_cell("Open Rate"),
                    rx.table.column_header_cell("Reply Rate"),
                    rx.table.column_header_cell("Leads In Sequence"),
                    rx.table.column_header_cell("Status"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.outreach_campaign_rows,
                        lambda row: rx.table.row(
                            rx.table.cell(row["campaign_name"]),
                            rx.table.cell(row["emails_sent"]),
                            rx.table.cell(row["open_rate"]),
                            rx.table.cell(row["reply_rate"]),
                            rx.table.cell(row["leads_in_sequence"]),
                            rx.table.cell(
                                rx.cond(
                                    row["status"] == "active",
                                    rx.badge("active", color_scheme="green", variant="soft"),
                                    rx.badge("paused", color_scheme="gray", variant="soft"),
                                )
                            ),
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        rx.text("Recent Replies", weight="bold", color="white", size="3"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Who Replied"),
                    rx.table.column_header_cell("What They Said"),
                    rx.table.column_header_cell("Time"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.outreach_reply_rows,
                        lambda row: rx.table.row(
                            rx.table.cell(row["who"]),
                            rx.table.cell(row["what"]),
                            rx.table.cell(row["when"]),
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        width="100%",
    )

    cold_caller_panel = glass_card(
        rx.text("Cold Caller", weight="bold", color="white", size="4"),
        rx.text("Elliot call outcomes and live call-performance breakdown.", color="#94a3b8", size="2"),
        rx.hstack(
            neo_stat_tile("Total Calls Today", rx.text(State.cold_caller_summary["total_calls_today"], size="6", color="#a5f3fc", weight="bold")),
            neo_stat_tile("Interested", rx.text(State.cold_caller_summary["interested"], size="6", color="#34d399", weight="bold")),
            neo_stat_tile("Not Interested", rx.text(State.cold_caller_summary["not_interested"], size="6", color="#fca5a5", weight="bold")),
            neo_stat_tile("Callbacks", rx.text(State.cold_caller_summary["callbacks"], size="6", color="#facc15", weight="bold")),
            spacing="3", width="100%", flex_wrap="wrap",
        ),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Prospect Name"),
                    rx.table.column_header_cell("Business"),
                    rx.table.column_header_cell("Phone Number"),
                    rx.table.column_header_cell("Call Time"),
                    rx.table.column_header_cell("Call Duration"),
                    rx.table.column_header_cell("Outcome"),
                    rx.table.column_header_cell("Recording"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.cold_caller_rows,
                        lambda row: rx.table.row(
                            rx.table.cell(row["prospect_name"]),
                            rx.table.cell(row["business"]),
                            rx.table.cell(row["phone"]),
                            rx.table.cell(row["call_time"]),
                            rx.table.cell(row["call_duration"]),
                            rx.table.cell(
                                rx.cond(
                                    row["outcome"] == "Interested",
                                    rx.badge("Interested", color_scheme="green", variant="soft"),
                                    rx.cond(
                                        row["outcome"] == "Not Interested",
                                        rx.badge("Not Interested", color_scheme="red", variant="soft"),
                                        rx.cond(
                                            row["outcome"] == "Callback Requested",
                                            rx.badge("Callback Requested", color_scheme="yellow", variant="soft"),
                                            rx.badge(row["outcome"], color_scheme="gray", variant="soft"),
                                        ),
                                    ),
                                )
                            ),
                            rx.table.cell(rx.cond(row["recording"] != "", rx.text("Play Recording", color="#7dd3fc"), rx.text("—"))),
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        width="100%",
    )

    def kanban_col(title: str, rows):
        return rx.box(
            rx.text(title, weight="bold", color="#e2e8f0", size="2"),
            rx.vstack(
                rx.foreach(
                    rows,
                    lambda row: rx.box(
                        rx.text(row["business_name"], color="white", weight="bold", size="2"),
                        rx.text(f"{row['niche']} · {row['city']}", color="#94a3b8", size="1"),
                        rx.text(f"Last: {row['last_action']}", color="#7dd3fc", size="1"),
                        border="1px solid rgba(56, 189, 248, 0.16)", border_radius="10px", padding="0.55rem", width="100%", background="rgba(0,0,0,0.22)",
                    ),
                ),
                align_items="stretch", width="100%", spacing="2",
            ),
            min_width="200px", width="100%",
        )

    deal_vault_panel = glass_card(
        rx.text("Deal Vault", weight="bold", color="white", size="4"),
        rx.text("Pipeline stage visibility from first signal to active client.", color="#94a3b8", size="2"),
        rx.hstack(
            kanban_col("New Reply", State.deal_kanban["new_reply"]),
            kanban_col("Call Booked", State.deal_kanban["call_booked"]),
            kanban_col("Proposal Sent", State.deal_kanban["proposal_sent"]),
            kanban_col("Agreement Signed", State.deal_kanban["agreement_signed"]),
            kanban_col("Payment Received", State.deal_kanban["payment_received"]),
            kanban_col("Active Client", State.deal_kanban["active_client"]),
            spacing="3", width="100%", overflow_x="auto", align_items="start",
        ),
        width="100%",
    )

    payments_panel = glass_card(
        rx.text("Payments", weight="bold", color="white", size="4"),
        rx.text("Revenue intelligence and transaction-level Stripe visibility.", color="#94a3b8", size="2"),
        rx.hstack(
            neo_stat_tile("Total Revenue This Month", rx.hstack(rx.text("$"), rx.text(State.payment_summary["month_revenue"], weight="bold"), spacing="1")),
            neo_stat_tile("Total Revenue All Time", rx.hstack(rx.text("$"), rx.text(State.payment_summary["all_time_revenue"], weight="bold"), spacing="1")),
            neo_stat_tile("Active Paying Clients", rx.text(State.payment_summary["active_clients"], weight="bold")),
            neo_stat_tile("Failed Payments", rx.text(State.payment_summary["failed_payments"], weight="bold")),
            spacing="3", width="100%", flex_wrap="wrap",
        ),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Client Name"),
                    rx.table.column_header_cell("Plan"),
                    rx.table.column_header_cell("Amount"),
                    rx.table.column_header_cell("Date"),
                    rx.table.column_header_cell("Status"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.payment_rows,
                        lambda row: rx.table.row(
                            rx.table.cell(row["client_name"]),
                            rx.table.cell(row["plan"]),
                            rx.table.cell(row["amount"]),
                            rx.table.cell(row["date"]),
                            rx.table.cell(
                                rx.cond(
                                    row["status"] == "paid",
                                    rx.badge("paid", color_scheme="green", variant="soft"),
                                    rx.cond(
                                        row["status"] == "failed",
                                        rx.badge("failed", color_scheme="red", variant="soft"),
                                        rx.badge(row["status"], color_scheme="gray", variant="soft"),
                                    ),
                                )
                            ),
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        width="100%",
    )

    contact_submissions_panel = glass_card(
        rx.text("Contact Submissions", weight="bold", color="white", size="4"),
        rx.text("Inbound hand-raisers from autoyieldsystems.com (priority pipeline).", color="#94a3b8", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Name"),
                    rx.table.column_header_cell("Email"),
                    rx.table.column_header_cell("Phone"),
                    rx.table.column_header_cell("Message"),
                    rx.table.column_header_cell("Date Submitted"),
                    rx.table.column_header_cell("Status"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.contact_priority_rows,
                        lambda row: rx.table.row(
                            rx.table.cell(row["name"]),
                            rx.table.cell(row["email"]),
                            rx.table.cell(row["phone"]),
                            rx.table.cell(row["message"]),
                            rx.table.cell(row["date_submitted"]),
                            rx.table.cell(row["status"]),
                            background="rgba(250, 204, 21, 0.08)",
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        width="100%",
    )

    tracking_panel = glass_card(
        rx.text("Tracking", weight="bold", color="white", size="4"),
        rx.text("Performance analytics for niche, geography, messaging, and timing.", color="#94a3b8", size="2"),
        rx.text("Niche conversion performance", color="#cbd5e1", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(rx.table.column_header_cell("Niche"), rx.table.column_header_cell("Conversion"), rx.table.column_header_cell("Lead Count"))),
                rx.table.body(rx.foreach(State.tracking_niche_rows, lambda r: rx.table.row(rx.table.cell(r["niche"]), rx.table.cell(r["conversion"]), rx.table.cell(r["lead_count"])))),
                variant="surface", size="2", width="100%",
            )
        ),
        rx.text("Cities with most leads", color="#cbd5e1", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(rx.table.column_header_cell("City"), rx.table.column_header_cell("Leads"))),
                rx.table.body(rx.foreach(State.tracking_city_rows, lambda r: rx.table.row(rx.table.cell(r["city"]), rx.table.cell(r["leads"])))),
                variant="surface", size="2", width="100%",
            )
        ),
        rx.text("Best email subject lines (reply count)", color="#cbd5e1", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(rx.table.column_header_cell("Subject"), rx.table.column_header_cell("Replies"))),
                rx.table.body(rx.foreach(State.tracking_subject_rows, lambda r: rx.table.row(rx.table.cell(r["subject"]), rx.table.cell(r["replies"])))),
                variant="surface", size="2", width="100%",
            )
        ),
        rx.text("Best day/time for replies", color="#cbd5e1", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(rx.table.column_header_cell("Window"), rx.table.column_header_cell("Replies"))),
                rx.table.body(rx.foreach(State.tracking_reply_time_rows, lambda r: rx.table.row(rx.table.cell(r["window"]), rx.table.cell(r["replies"])))),
                variant="surface", size="2", width="100%",
            )
        ),
        rx.text("Cost per lead over time", color="#cbd5e1", size="2"),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(rx.table.column_header_cell("Period"), rx.table.column_header_cell("Cost / Lead"))),
                rx.table.body(rx.foreach(State.tracking_cpl_rows, lambda r: rx.table.row(rx.table.cell(r["period"]), rx.table.cell(r["cost_per_lead"])))),
                variant="surface", size="2", width="100%",
            )
        ),
        width="100%",
    )

    system_health_panel = glass_card(
        rx.hstack(
            rx.text("System Health", weight="bold", color="white", size="4"),
            rx.spacer(),
            rx.button("Run Health Check", on_click=State.run_health_check, color_scheme="green", variant="solid"),
            width="100%",
        ),
        rx.text("End-to-end operational readiness across core infrastructure and integrations.", color="#94a3b8", size="2"),
        rx.cond(State.system_message != "", rx.callout(State.system_message, icon="info", color_scheme="blue", width="100%"), rx.fragment()),
        neo_table_shell(
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("Component"),
                    rx.table.column_header_cell("Status"),
                    rx.table.column_header_cell("Detail"),
                )),
                rx.table.body(
                    rx.foreach(
                        State.system_health_rows,
                        lambda r: rx.table.row(
                            rx.table.cell(r["component"]),
                            rx.table.cell(rx.cond(r["status"] == "green", rx.badge("Healthy", color_scheme="green"), rx.badge("Issue", color_scheme="red"))),
                            rx.table.cell(r["detail"]),
                        ),
                    )
                ),
                variant="surface", size="2", width="100%",
            )
        ),
        rx.hstack(
            rx.text("Daily Email Reports (8:00 AM)", color="#cbd5e1", size="2"),
            rx.switch(checked=State.daily_reports_enabled, on_change=State.toggle_daily_reports_enabled, color_scheme="cyan"),
            spacing="3",
        ),
        width="100%",
    )

    panels: dict[str, rx.Component] = {
        "dashboard": dashboard_panel,
        "lead_engine": lead_engine_panel,
        "outreach": outreach_panel,
        "cold_caller": cold_caller_panel,
        "deal_vault": deal_vault_panel,
        "payments": payments_panel,
        "contact_submissions": contact_submissions_panel,
        "tracking": tracking_panel,
        "system_health": system_health_panel,
    }

    tab_content = rx.match(
        State.command_center_tab,
        ("dashboard", panels["dashboard"]),
        ("lead_engine", panels["lead_engine"]),
        ("outreach", panels["outreach"]),
        ("cold_caller", panels["cold_caller"]),
        ("deal_vault", panels["deal_vault"]),
        ("payments", panels["payments"]),
        ("contact_submissions", panels["contact_submissions"]),
        ("tracking", panels["tracking"]),
        ("system_health", panels["system_health"]),
        panels["dashboard"],
    )

    module_tabs = [
        ("Dashboard", "dashboard", State.cc_dashboard),
        ("Lead Engine", "lead_engine", State.cc_lead_engine),
        ("Outreach", "outreach", State.cc_outreach),
        ("Cold Caller", "cold_caller", State.cc_cold_caller),
        ("Deal Vault", "deal_vault", State.cc_deal_vault),
        ("Payments", "payments", State.cc_payments),
        ("Contact Submissions", "contact_submissions", State.cc_contact_submissions),
        ("Tracking", "tracking", State.cc_tracking),
        ("System Health", "system_health", State.cc_system_health),
    ]

    return rx.box(
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
                        "AutoYield Command Center",
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
                "Executive command surface for the full growth machine: scrape → outreach → calls → deals → payments.",
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
