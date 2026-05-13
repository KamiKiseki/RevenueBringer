from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from runtime_config import flask_debug_enabled


class FlaskDebugConfigTest(unittest.TestCase):
    def test_debug_is_off_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(flask_debug_enabled())

    def test_server_debug_explicitly_enables_debug(self) -> None:
        with patch.dict(os.environ, {"SERVER_DEBUG": "true"}, clear=True):
            self.assertTrue(flask_debug_enabled())

    def test_flask_debug_explicitly_enables_debug(self) -> None:
        with patch.dict(os.environ, {"FLASK_DEBUG": "1"}, clear=True):
            self.assertTrue(flask_debug_enabled())

    def test_falsey_values_do_not_enable_debug(self) -> None:
        with patch.dict(os.environ, {"SERVER_DEBUG": "false", "FLASK_DEBUG": "0"}, clear=True):
            self.assertFalse(flask_debug_enabled())


if __name__ == "__main__":
    unittest.main()
