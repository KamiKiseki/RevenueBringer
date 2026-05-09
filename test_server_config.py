import os
import unittest
from unittest.mock import patch

import server


class FlaskDebugConfigTests(unittest.TestCase):
    def test_debug_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLASK_DEBUG", None)
            self.assertFalse(server._flask_debug_enabled())

    def test_debug_requires_explicit_truthy_env(self):
        truthy_values = ["1", "true", "TRUE", "yes", "on"]
        for value in truthy_values:
            with self.subTest(value=value), patch.dict(os.environ, {"FLASK_DEBUG": value}, clear=False):
                self.assertTrue(server._flask_debug_enabled())

    def test_debug_stays_disabled_for_falsey_env(self):
        falsey_values = ["", "0", "false", "no", "off", "production"]
        for value in falsey_values:
            with self.subTest(value=value), patch.dict(os.environ, {"FLASK_DEBUG": value}, clear=False):
                self.assertFalse(server._flask_debug_enabled())


if __name__ == "__main__":
    unittest.main()
