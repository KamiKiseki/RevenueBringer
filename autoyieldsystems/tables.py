"""Data tables bound to Command Center State."""

from __future__ import annotations

import reflex as rx

from .state import State


def lead_rows() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("ID"),
                rx.table.column_header_cell("Business"),
                rx.table.column_header_cell("Niche"),
                rx.table.column_header_cell("Location"),
                rx.table.column_header_cell("Status"),
                rx.table.column_header_cell("Proof leads"),
                rx.table.column_header_cell("Email"),
                rx.table.column_header_cell("Phone"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                State.filtered_leads,
                lambda row: rx.table.row(
                    rx.table.cell(row["id"]),
                    rx.table.cell(row["business"]),
                    rx.table.cell(row["niche"]),
                    rx.table.cell(row["location"]),
                    rx.table.cell(row["status"]),
                    rx.table.cell(row["leads_sent"]),
                    rx.table.cell(row["email"]),
                    rx.table.cell(row["phone"]),
                ),
            ),
        ),
        variant="surface",
        size="2",
        width="100%",
    )


def agreement_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Client"),
                rx.table.column_header_cell("PandaDoc ID"),
                rx.table.column_header_cell("Status"),
                rx.table.column_header_cell("Offer"),
                rx.table.column_header_cell("Plan cents"),
                rx.table.column_header_cell("Stripe TX"),
                rx.table.column_header_cell("Checkout"),
                rx.table.column_header_cell("Correlation ID"),
                rx.table.column_header_cell("Agreement"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                State.agreement_rows,
                lambda row: rx.table.row(
                    rx.table.cell(row["client_name"]),
                    rx.table.cell(row["pandadoc_id"]),
                    rx.table.cell(row["signing_status"]),
                    rx.table.cell(row["offer_kind"]),
                    rx.table.cell(row["plan_amount_cents"]),
                    rx.table.cell(row["stripe_transaction_id"]),
                    rx.table.cell(
                        rx.cond(
                            row["stripe_checkout_url"] != "",
                            row["stripe_checkout_url"],
                            rx.text("—"),
                        )
                    ),
                    rx.table.cell(row["correlation_id"]),
                    rx.table.cell(
                        rx.cond(
                            row["signed_pdf_url"] != "",
                            row["signed_pdf_url"],
                            rx.text("—"),
                        )
                    ),
                ),
            )
        ),
        variant="surface",
        size="2",
        width="100%",
    )


def raw_db_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Table"),
                rx.table.column_header_cell("PK"),
                rx.table.column_header_cell("Name"),
                rx.table.column_header_cell("Status"),
                rx.table.column_header_cell("Correlation ID"),
            )
        ),
        rx.table.body(
            rx.foreach(
                State.raw_db_rows,
                lambda row: rx.table.row(
                    rx.table.cell(row["table"]),
                    rx.table.cell(row["pk"]),
                    rx.table.cell(row["name"]),
                    rx.table.cell(row["status"]),
                    rx.table.cell(row["correlation_id"]),
                ),
            ),
        ),
        variant="surface",
        size="2",
        width="100%",
    )
