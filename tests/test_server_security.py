from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ServerSecurityTests(unittest.TestCase):
    def import_server(self, *, stripe_webhook_secret: str | None = None):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        previous_env = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "STRIPE_WEBHOOK_SECRET": os.environ.get("STRIPE_WEBHOOK_SECRET"),
            "STRIPE_API_KEY": os.environ.get("STRIPE_API_KEY"),
            "STRIPE_SECRET_KEY": os.environ.get("STRIPE_SECRET_KEY"),
            "SERVER_DEBUG": os.environ.get("SERVER_DEBUG"),
            "FLASK_DEBUG": os.environ.get("FLASK_DEBUG"),
        }

        def restore_env() -> None:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore_env)

        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmpdir.name) / 'test.db'}"
        os.environ.pop("STRIPE_API_KEY", None)
        os.environ.pop("STRIPE_SECRET_KEY", None)
        os.environ.pop("SERVER_DEBUG", None)
        os.environ.pop("FLASK_DEBUG", None)
        if stripe_webhook_secret is None:
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        else:
            os.environ["STRIPE_WEBHOOK_SECRET"] = stripe_webhook_secret

        for module_name in ("server", "models"):
            sys.modules.pop(module_name, None)

        models = importlib.import_module("models")
        server = importlib.import_module("server")
        models.init_db()

        self.addCleanup(lambda: sys.modules.pop("server", None))
        self.addCleanup(lambda: sys.modules.pop("models", None))
        return server, models

    def test_stripe_webhook_without_secret_rejects_and_does_not_mark_paid(self) -> None:
        server, models = self.import_server(stripe_webhook_secret=None)
        with models.get_session() as db:
            db.add(
                models.Lead(
                    business_name="Critical HVAC",
                    email="owner@example.com",
                    correlation_id="corr-test",
                )
            )
            db.add(
                models.Agreement(
                    client_name="Owner",
                    client_email="owner@example.com",
                    business_name="Critical HVAC",
                    correlation_id="corr-test",
                )
            )
            db.commit()

        forged_event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"correlation_id": "corr-test"},
                    "customer_details": {"email": "owner@example.com"},
                    "amount_total": 30000,
                    "payment_intent": "pi_forged",
                }
            },
        }

        with patch.object(server, "deliver_paid_lead_package") as deliver:
            response = server.app.test_client().post(
                "/webhooks/stripe",
                data=json.dumps(forged_event),
                content_type="application/json",
            )

        self.assertEqual(400, response.status_code)
        deliver.assert_not_called()
        with models.get_session() as db:
            lead = db.query(models.Lead).filter(models.Lead.correlation_id == "corr-test").one()
            agreement = (
                db.query(models.Agreement)
                .filter(models.Agreement.correlation_id == "corr-test")
                .one()
            )
            self.assertEqual(models.LeadStatus.QUEUED, lead.status)
            self.assertEqual(models.AgreementStatus.DRAFT, agreement.signing_status)
            self.assertIsNone(agreement.stripe_transaction_id)

    def test_flask_debug_mode_is_opt_in(self) -> None:
        server, _models = self.import_server(stripe_webhook_secret="whsec_test")

        self.assertFalse(server._flask_debug_enabled())

        os.environ["SERVER_DEBUG"] = "true"
        self.assertTrue(server._flask_debug_enabled())

        os.environ["SERVER_DEBUG"] = "false"
        os.environ["FLASK_DEBUG"] = "1"
        self.assertTrue(server._flask_debug_enabled())

        os.environ["FLASK_DEBUG"] = "0"
        self.assertFalse(server._flask_debug_enabled())


if __name__ == "__main__":
    unittest.main()
