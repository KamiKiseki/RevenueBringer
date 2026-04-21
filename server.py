from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import sys

from flask import Flask, request, jsonify
from pyngrok import ngrok

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "contacts.json"

app = Flask(__name__, static_folder=str(HERE), static_url_path="")


def load_contacts() -> list[dict[str, str]]:
    if not DB_PATH.exists():
        return []
    try:
        with DB_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def save_contacts(entries: list[dict[str, str]]) -> None:
    with DB_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


@app.route("/", methods=["GET"])
def index() -> str:
    return app.send_static_file("index.html")


@app.route("/submit-contact", methods=["POST"])
def submit_contact():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    business = (data.get("business") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email or not business:
        return jsonify({"status": "error", "message": "Name, email, and business type are required."}), 400

    contacts = load_contacts()
    contacts.append(
        {
            "name": name,
            "email": email,
            "business": business,
            "message": message,
            "submitted_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
    )
    save_contacts(contacts)
    return jsonify({"status": "ok", "message": "Contact request saved."})


@app.route("/admin/contacts", methods=["GET"])
def list_contacts():
    return jsonify(load_contacts())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🚀 Starting RevenueBringer server on http://localhost:{port}\n")
    
    try:
        # Create ngrok tunnel
        public_url = ngrok.connect(port, "http")
        print(f"✅ Public URL: {public_url}\n")
        print("Share this URL with anyone to access your site publicly.\n")
    except Exception as e:
        print(f"⚠️  Could not create ngrok tunnel: {e}")
        print(f"   Local access only at http://localhost:{port}\n")
    
    app.run(host="0.0.0.0", port=port, debug=False)
