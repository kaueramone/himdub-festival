"""Exercise configuration without reading real .env files or contacting services."""
import os
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch


CONFIG = Path(__file__).resolve().parents[1] / "config.py"


class ConfigSecurityTests(unittest.TestCase):
    def load(self, environment):
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        with patch.dict(os.environ, environment, clear=True), patch.dict(sys.modules, {"dotenv": dotenv}):
            return runpy.run_path(str(CONFIG))["Config"]

    def test_local_fallback_is_random_per_process_configuration(self):
        first = self.load({}).SECRET_KEY
        second = self.load({}).SECRET_KEY
        self.assertGreaterEqual(len(first), 64)
        self.assertNotEqual(first, second)

    def test_render_without_secret_fails(self):
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
            self.load({"RENDER": "true"})

    def test_https_without_secret_fails(self):
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
            self.load({"DOMAIN": "https://festival.example"})

    def test_production_uses_explicit_secret(self):
        value = "isolated-test-value".ljust(32, "x")
        config = self.load({"RENDER": "true", "SECRET_KEY": value,
                            "DOMAIN": "https://festival.example", "DATABASE_URL": "postgresql://localhost/test",
                            "STRIPE_SECRET_KEY": "isolated", "STRIPE_WEBHOOK_SECRET": "isolated",
                            "RESEND_API_KEY": "isolated"})
        self.assertEqual(config.SECRET_KEY, value)


if __name__ == "__main__":
    unittest.main()
