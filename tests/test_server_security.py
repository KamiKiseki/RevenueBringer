from __future__ import annotations

import importlib
import os
import tempfile
import unittest


class ServerSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.tmp.name}/security.db"
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        os.environ.pop("FLASK_DEBUG", None)

        cls.models = importlib.import_module("models")
        cls.server = importlib.import_module("server")
        cls.models.init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_unsigned_stripe_webhook_is_rejected_when_secret_is_unset(self) -> None:
        correlation_id = "cid-test-forged-payment"
        with self.models.get_session() as db:
            lead = self.models.Lead(
                business_name="Forged Payment HVAC",
                email="owner@example.com",
                correlation_id=correlation_id,
            )
            agreement = self.models.Agreement(
                client_name="Forged Payment HVAC",
                client_email="owner@example.com",
                business_name="Forged Payment HVAC",
                correlation_id=correlation_id,
                signing_status=self.models.AgreementStatus.DRAFT,
            )
            db.add_all([lead, agreement])
            db.commit()

        forged_event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_forged",
                    "payment_intent": "pi_forged",
                    "amount_total": 50000,
                    "client_reference_id": correlation_id,
                    "customer_details": {"email": "owner@example.com"},
                    "metadata": {"correlation_id": correlation_id},
                }
            },
        }

        response = self.server.app.test_client().post("/webhooks/stripe", json=forged_event)

        self.assertEqual(response.status_code, 400)
        with self.models.get_session() as db:
            lead = db.query(self.models.Lead).filter_by(correlation_id=correlation_id).one()
            agreement = db.query(self.models.Agreement).filter_by(correlation_id=correlation_id).one()
            self.assertEqual(lead.status, self.models.LeadStatus.QUEUED)
            self.assertEqual(agreement.signing_status, self.models.AgreementStatus.DRAFT)
            self.assertIsNone(agreement.stripe_transaction_id)

    def test_flask_debug_is_disabled_by_default(self) -> None:
        os.environ.pop("FLASK_DEBUG", None)
        self.assertFalse(self.server._env_flag("FLASK_DEBUG"))

    def test_flask_debug_can_be_enabled_explicitly(self) -> None:
        os.environ["FLASK_DEBUG"] = "true"
        try:
            self.assertTrue(self.server._env_flag("FLASK_DEBUG"))
        finally:
            os.environ.pop("FLASK_DEBUG", None)


if __name__ == "__main__":
    unittest.main()
