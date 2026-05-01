import json
import os
import tempfile
import unittest
from pathlib import Path

import server


class ServerSecurityTest(unittest.TestCase):
    def setUp(self):
        self._original_db_path = server.DB_PATH
        self._original_admin_token = os.environ.get("ADMIN_CONTACTS_TOKEN")
        self._temp_dir = tempfile.TemporaryDirectory()
        server.DB_PATH = Path(self._temp_dir.name) / "contacts.json"
        os.environ.pop("ADMIN_CONTACTS_TOKEN", None)
        self.client = server.app.test_client()

    def tearDown(self):
        server.DB_PATH = self._original_db_path
        if self._original_admin_token is None:
            os.environ.pop("ADMIN_CONTACTS_TOKEN", None)
        else:
            os.environ["ADMIN_CONTACTS_TOKEN"] = self._original_admin_token
        self._temp_dir.cleanup()

    def test_contact_database_and_source_files_are_not_public_static_files(self):
        response = self.client.post(
            "/submit-contact",
            json={
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "business": "Analytics",
                "message": "Please contact me.",
            },
        )

        self.assertEqual(response.status_code, 200)
        saved_contacts = json.loads(server.DB_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved_contacts[0]["email"], "ada@example.com")

        self.assertEqual(self.client.get("/contacts.json").status_code, 404)
        self.assertEqual(self.client.get("/server.py").status_code, 404)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/style.css").status_code, 200)

    def test_admin_contacts_requires_configured_token(self):
        server.save_contacts(
            [
                {
                    "name": "Grace Hopper",
                    "email": "grace@example.com",
                    "business": "Software",
                    "message": "",
                    "submitted_at": "2026-05-01T11:02:00Z",
                }
            ]
        )

        self.assertEqual(self.client.get("/admin/contacts").status_code, 404)

        os.environ["ADMIN_CONTACTS_TOKEN"] = "secret-token"
        self.assertEqual(self.client.get("/admin/contacts").status_code, 401)
        self.assertEqual(
            self.client.get("/admin/contacts", headers={"X-Admin-Token": "wrong"}).status_code,
            401,
        )

        response = self.client.get(
            "/admin/contacts",
            headers={"Authorization": "Bearer secret-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()[0]["email"], "grace@example.com")


if __name__ == "__main__":
    unittest.main()
