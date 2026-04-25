import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import config
from src.main import _preflight_payload
from src.services.cache_manager import CacheManager
from src.services.trace_extractor import TraceExtractor


class TraceReviewPreflightTests(unittest.TestCase):
    def _make_app(self) -> SimpleNamespace:
        return SimpleNamespace(
            state=SimpleNamespace(cache_manager=CacheManager(ttl_hours=1))
        )

    def test_diagnostics_redact_url_credentials(self):
        langfuse_url = "https://{}@langfuse.example.org:3000".format(
            "diagnostic-user:diagnostic-token"
        )
        env = {
            "LANGFUSE_HOST": langfuse_url,
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_LOCAL_HOST": "http://localhost:3000",
            "LANGFUSE_LOCAL_PUBLIC_KEY": "pk-lf-local",
            "LANGFUSE_LOCAL_SECRET_KEY": "sk-lf-local",
        }

        with patch.dict(os.environ, env, clear=True):
            diagnostics = config.get_trace_review_preflight_diagnostics("remote")

        remote = diagnostics["langfuse_sources"]["remote"]
        self.assertEqual(remote["host"], "https://[redacted]@langfuse.example.org:3000")
        self.assertTrue(remote["credentials"]["public_key_present"])
        self.assertTrue(remote["credentials"]["secret_key_present"])
        self.assertNotIn("diagnostic-token", str(diagnostics))

    def test_preflight_payload_reports_missing_selected_source_config(self):
        env = {
            "LANGFUSE_HOST": "http://remote.example:3000",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_LOCAL_HOST": "http://localhost:3000",
        }

        with patch.dict(os.environ, env, clear=True):
            payload, status_code = _preflight_payload(self._make_app(), "local")

        self.assertEqual(status_code, 503)
        self.assertEqual(payload["status"], "config_error")
        self.assertEqual(payload["diagnostics"]["source_selection"]["selected"], "local")
        self.assertFalse(payload["diagnostics"]["source_selection"]["selected_ready"])
        self.assertIn("LANGFUSE_LOCAL_PUBLIC_KEY", payload["next_actions"][0])

    def test_trace_extractor_rejects_unknown_source(self):
        with self.assertRaisesRegex(ValueError, "Unsupported trace source 'stale'"):
            TraceExtractor(source="stale")


if __name__ == "__main__":
    unittest.main()
