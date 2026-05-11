# Agent handoff (Ziel / AutoYield)

Read this file at the start of a new chat when working on this repository. It replaces “shared memory” between conversations.

## Product

- **AutoYield Systems** — lead automation, value-first funnel (free proof leads → tier offer → PandaDoc → Stripe), Stevie/Elliot-style copy in `templates.py`.
- **Command Center UI** — Reflex app under `autoyieldsystems/` (not served by Flask; run separately). State and integration hints live in `autoyieldsystems/state.py` and `command_center.py`.
- **API / automation core** — `server.py` (Flask): intake, webhooks (Stripe, Vapi, PandaDoc, offer selection), agreements, scraper hooks.

## Critical files

| Area | File |
|------|------|
| HTTP API + funnel | `server.py` |
| DB models | `models.py` |
| CEO / proof / tier copy | `templates.py` |
| Queue → Instantly or SMTP | `outreach.py` |
| Scrape / targets | `scraper.py`, `automation.py` |
| Ops gates | `healthcheck.py` |
| Secrets template | `.env.example` (never commit real `.env`) |

## Environment (high level)

See `.env.example` for full list. Commonly touched:

- `DATABASE_URL` — Postgres in prod; local may use SQLite via `models.py` normalization.
- `INSTANTLY_API_KEY`, `INSTANTLY_CAMPAIGN_ID` — cold email / campaign push from `outreach.py`.
- `INSTANTLY_DOMAIN_WARMUP_ACTIVE` — health gate in `healthcheck.py`.
- `VAPI_*`, `PANDADOC_*`, `STRIPE_*` — voice, contracts, payments.
- `VALUE_FIRST_FUNNEL`, `FREE_HOOK_LEAD_TARGET` — funnel behavior in `server.py`.

## Instantly.ai (email outreach)

**Intent:** All *campaign* cold outreach goes through Instantly (warm inboxes, sequences), not one-off transactional providers. SMTP in `outreach.py` remains for optional `OUTREACH_TRANSPORT` hybrid / simulation paths.

**Current code (`outreach.py`):**

- Posts to `https://api.instantly.ai/api/v2/leads` via `push_lead_to_instantly`.
- Payload today uses `campaign_id` and minimal `custom_variables` (`niche`, `lead_id`).

**Known follow-ups (verify against [Instantly API v2](https://developer.instantly.ai/) before production):**

1. OpenAPI **Create lead** expects the field name **`campaign`** (UUID), not `campaign_id`. Legacy `POST /1/lead/add` maps to **`POST /api/v2/leads`**. If pushes fail with 400, switch the JSON key to `campaign`.
2. Add **`custom_variables`** (or equivalent) for template merge tags, at minimum: **`owner_name`**, **`business_name`**, plus **`correlation_id`** for webhook joins.
3. Consider **`skip_if_in_campaign`: true** to avoid duplicate rows when intake and queue both push.
4. Optional: thin wrapper **`add_to_instantly_campaign(lead)`** that pushes + logs `MessageEvent`, and call it from **`server.py`** `automation/intake` when `INSTANTLY_PUSH_ON_INTAKE` is desired.
5. Optional webhook **`POST /webhooks/instantly`** in `server.py` — log reply/engagement; use `correlation_id` from custom variables to find `Lead` and avoid redundant Vapi follow-ups (cancel logic is product-specific).

**Instantly dashboard:** sequence copy references merge tags that must match the variable keys Instantly exposes for those custom fields.

## Conventions

- Prefer small, task-scoped diffs; match existing naming and patterns in touched files.
- User date context: trust `Today's date` from the IDE user_info when relevant.

## How to update this file

After meaningful decisions in chat, append a dated one-line note under **Changelog** below so the next session inherits it.

## Changelog

- **2026-05-03** — Added this `AGENTS.md` for cross-chat continuity; documented Instantly v2 field-name and custom-variable follow-ups against current `outreach.py`.
- **2026-05-05** — Rewrote public Flask marketing templates (`_flask_stub/templates/index.html`, `base.html`, `contact.html`) to client-facing, non-technical AutoYield messaging.
- **2026-05-07** — Hardened Vapi reply/tier call prompts in `server.py` to use concise non-monologue instructions and explicit `first_message` for webhook routers.
