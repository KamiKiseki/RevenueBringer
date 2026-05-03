"""Minimal placeholder for a separate Railway service (marketing / public site)."""

from __future__ import annotations

import os

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/")
def root():
    return (
        "autoyieldsystems — marketing service placeholder.\n"
        "Replace this app with your real site when ready.\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "marketing-placeholder"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
