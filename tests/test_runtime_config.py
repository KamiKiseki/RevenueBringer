from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from runtime_config import env_flag, flask_debug_enabled


class RuntimeConfigTest(unittest.TestCase):
    def test_flask_debug_is_off_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(flask_debug_enabled())

    def test_flask_debug_requires_explicit_truthy_env(self) -> None:
        truthy_values = ["1", "true", "TRUE", "yes", "on"]
        for value in truthy_values:
            with self.subTest(value=value), patch.dict(os.environ, {"SERVER_DEBUG": value}, clear=True):
                self.assertTrue(flask_debug_enabled())

    def test_falsey_and_unknown_values_do_not_enable_debug(self) -> None:
        values = ["0", "false", "no", "off", "", "production"]
        for value in values:
            with self.subTest(value=value), patch.dict(os.environ, {"FLASK_DEBUG": value}, clear=True):
                self.assertFalse(flask_debug_enabled())

    def test_env_flag_honors_default_for_missing_or_unknown_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_flag("MISSING_FLAG", default=True))
        with patch.dict(os.environ, {"CUSTOM_FLAG": "not-a-bool"}, clear=True):
            self.assertTrue(env_flag("CUSTOM_FLAG", default=True))


if __name__ == "__main__":
    unittest.main()
