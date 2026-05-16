from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class ServerSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.tmp.name}/security-test.db"
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        os.environ.pop("ADMIN_ACTION_TOKEN", None)
        cls.server = importlib.import_module("server")
        cls.server.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.client = self.server.app.test_client()
        self.server.STRIPE_WEBHOOK_SECRET = ""
        os.environ.pop("SERVER_DEBUG", None)
        os.environ.pop("FLASK_DEBUG", None)
        os.environ.pop("ADMIN_ACTION_TOKEN", None)

    def test_flask_debug_is_disabled_by_default(self) -> None:
        self.assertFalse(self.server._flask_debug_enabled())

    def test_flask_debug_can_be_explicitly_enabled(self) -> None:
        with patch.dict(os.environ, {"SERVER_DEBUG": "true"}):
            self.assertTrue(self.server._flask_debug_enabled())

    def test_unsigned_stripe_webhook_is_rejected_before_state_writes(self) -> None:
        with (
            patch.object(self.server, "_record_error"),
            patch.object(self.server, "set_setting") as set_setting,
        ):
            response = self.client.post(
                "/webhooks/stripe",
                json={"type": "checkout.session.completed", "data": {"object": {}}},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("STRIPE_WEBHOOK_SECRET", response.get_json()["error"])
        set_setting.assert_not_called()

    def test_stripe_webhook_requires_correlation_id_not_email_only(self) -> None:
        self.server.STRIPE_WEBHOOK_SECRET = "whsec_test"
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer_details": {"email": "shared@example.com"},
                    "metadata": {},
                    "amount_total": 50000,
                    "id": "cs_test_123",
                }
            },
        }

        with (
            patch.object(self.server.stripe.Webhook, "construct_event", return_value=event),
            patch.object(self.server, "_record_error"),
            patch.object(self.server, "set_setting"),
            patch.object(self.server, "get_session") as get_session,
        ):
            response = self.client.post(
                "/webhooks/stripe",
                data=b"{}",
                headers={"Stripe-Signature": "t=1,v1=test"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("correlation_id", response.get_json()["error"])
        get_session.assert_not_called()

    def test_sanitize_notes_requires_admin_token(self) -> None:
        response = self.client.post("/admin/sanitize-notes")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "admin authorization required")


if __name__ == "__main__":
    unittest.main()
