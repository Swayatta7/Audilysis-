import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from services.production_diagnostics import (
    BASE_DIR,
    DEV_FALLBACK_SECRET_KEY,
    apply_runtime_settings,
    classify_env_value,
    is_local_host,
)


class ProductionReadinessTestCase(unittest.TestCase):
    def test_local_host_detection_handles_common_forms(self):
        self.assertTrue(is_local_host("localhost"))
        self.assertTrue(is_local_host("localhost:5000"))
        self.assertTrue(is_local_host("127.0.0.1"))
        self.assertTrue(is_local_host("127.0.0.1:8000"))
        self.assertTrue(is_local_host("[::1]"))
        self.assertFalse(is_local_host("app.audilysis.com"))

    def test_classify_env_value_flags_short_secret_key_as_malformed(self):
        status, detail = classify_env_value("FLASK_SECRET_KEY", "short-key")
        self.assertEqual(status, "malformed")
        self.assertIn("longer secret key", detail)

    def test_classify_env_value_flags_invalid_redirect_uri_as_malformed(self):
        status, detail = classify_env_value("GOOGLE_ADS_REDIRECT_URI", "/callback")
        self.assertEqual(status, "malformed")
        self.assertIn("absolute URL", detail)

    def test_classify_env_value_accepts_local_and_production_redirect_uris(self):
        local_status, local_detail = classify_env_value(
            "GOOGLE_ADS_REDIRECT_URI",
            "http://127.0.0.1:5000/integrations/google-ads/callback",
        )
        production_status, production_detail = classify_env_value(
            "GOOGLE_ADS_REDIRECT_URI",
            "https://app.audilysis.com/integrations/google-ads/callback",
        )
        self.assertEqual(local_status, "configured")
        self.assertIn("local redirect URI", local_detail)
        self.assertEqual(production_status, "configured")
        self.assertIn("non-local redirect URI", production_detail)

    def test_apply_runtime_settings_uses_secure_cookies_in_production_like_mode(self):
        app = Flask(__name__)
        with patch.dict(os.environ, {"FLASK_DEBUG": "0"}, clear=False):
            apply_runtime_settings(app)
        self.assertEqual(app.config["SESSION_COOKIE_HTTPONLY"], True)
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(app.config["SESSION_COOKIE_SECURE"], True)
        self.assertEqual(app.config["PREFERRED_URL_SCHEME"], "https")
        self.assertTrue(app.config["AUDILYSIS_PRODUCTION_LIKE"])

    def test_apply_runtime_settings_keeps_local_http_in_debug_mode(self):
        app = Flask(__name__)
        with patch.dict(os.environ, {"FLASK_DEBUG": "1"}, clear=False):
            apply_runtime_settings(app)
        self.assertEqual(app.config["SESSION_COOKIE_SECURE"], False)
        self.assertEqual(app.config["PREFERRED_URL_SCHEME"], "http")
        self.assertFalse(app.config["AUDILYSIS_PRODUCTION_LIKE"])

    def test_dev_fallback_secret_key_constant_is_stable_for_single_process_family(self):
        self.assertEqual(DEV_FALLBACK_SECRET_KEY, "audilysis-dev-insecure-secret-key")

    def test_gitignore_excludes_tracker_database(self):
        gitignore = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/tracker.db", gitignore)

    def test_production_check_script_outputs_json(self):
        result = subprocess.run(
            ["./.venv/bin/python", "scripts/production_check.py"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["base_dir"], str(BASE_DIR))
        self.assertIn("environment", payload)
        self.assertIn("paths", payload)
        self.assertIn("packages", payload)
        self.assertIn("wsgi", payload)


if __name__ == "__main__":
    unittest.main()
