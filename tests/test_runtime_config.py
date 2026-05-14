import os
import unittest
from unittest.mock import patch

from runtime_config import flask_debug_enabled


class FlaskDebugEnabledTest(unittest.TestCase):
    def test_debug_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(flask_debug_enabled())

    def test_server_debug_truthy_values_enable_debug(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(os.environ, {"SERVER_DEBUG": value}, clear=True):
                self.assertTrue(flask_debug_enabled())

    def test_flask_debug_truthy_values_enable_debug(self):
        for value in ("1", "true", "yes", "on"):
            with self.subTest(value=value), patch.dict(os.environ, {"FLASK_DEBUG": value}, clear=True):
                self.assertTrue(flask_debug_enabled())

    def test_falsey_values_do_not_enable_debug(self):
        env = {"SERVER_DEBUG": "0", "FLASK_DEBUG": "false"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(flask_debug_enabled())


if __name__ == "__main__":
    unittest.main()
