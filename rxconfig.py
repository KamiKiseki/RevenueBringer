"""Reflex config — autoyieldsystems dashboard (`autoyieldsystems/`). DATABASE_URL via .env."""

import os
from dotenv import load_dotenv
from reflex_base.plugins.sitemap import SitemapPlugin
import reflex as rx

load_dotenv()

# Single source of truth with models.py (placeholders / internal Railway host → SQLite).
from models import DATABASE_URL as _db_url

config = rx.Config(
    app_name="autoyieldsystems",
    db_url=_db_url,
    # Critical: Reflex UI events (button/tab clicks) must go to Reflex backend, not Flask.
    api_url=os.getenv("REFLEX_API_URL", "http://127.0.0.1:3000"),
    deploy_url=os.getenv("REFLEX_DEPLOY_URL", "http://127.0.0.1:3001"),
    frontend_port=int(os.getenv("REFLEX_FRONTEND_PORT", "3001")),
    backend_port=int(os.getenv("REFLEX_BACKEND_PORT", "3000")),
    disable_plugins=[SitemapPlugin],
)
