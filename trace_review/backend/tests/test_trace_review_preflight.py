import os
import asyncio
import unittest
from types import SimpleNamespace
from typing import get_args, get_type_hints
from unittest.mock import patch

from src import config
from src.main import _health_payload, _preflight_payload, langfuse_health, preflight_health
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

    def test_unparseable_diagnostic_url_does_not_echo_credentials(self):
        langfuse_url = "https://{}@langfuse.example.org:not-a-port".format(
            "diagnostic-user:diagnostic-token"
        )

        safe_url = config.sanitize_url_for_diagnostics(langfuse_url)

        self.assertEqual(safe_url, "[unparseable-url]")
        self.assertNotIn("diagnostic-token", safe_url)

    def test_preflight_health_query_source_uses_literal_validation(self):
        source_hint = get_type_hints(preflight_health)["source"]

        self.assertEqual(get_args(source_hint), ("remote", "local"))

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

    @patch("src.main.TraceExtractor")
    def test_preflight_payload_degrades_when_v4_capability_probe_fails(
        self,
        extractor_cls,
    ):
        env = {
            "LANGFUSE_HOST": "http://remote.example:3000",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }
        extractor_cls.return_value.probe_v4_capabilities.return_value = {
            "status": "degraded",
            "query_surface": "observations_v2",
            "checks": {"session_discovery": {"status": "error"}},
        }

        with patch.dict(os.environ, env, clear=True):
            payload, status_code = _preflight_payload(self._make_app(), "remote")

        self.assertEqual(status_code, 503)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["langfuse_capabilities"]["status"], "degraded")

    def test_health_payload_exposes_build_identity(self):
        env = {
            "TRACE_REVIEW_RUNTIME_VERSION": "v0.9.4",
            "TRACE_REVIEW_SOURCE_REVISION": "abc123",
        }

        with patch.dict(os.environ, env, clear=True):
            payload, status_code = _health_payload(self._make_app())

        self.assertEqual(status_code, 200)
        self.assertEqual(
            payload["runtime"],
            {"image_version": "v0.9.4", "source_revision": "abc123"},
        )

    @patch("requests.get")
    @patch("src.main.TraceExtractor")
    def test_langfuse_health_degrades_when_capability_probe_fails(
        self,
        extractor_cls,
        requests_get,
    ):
        requests_get.return_value.status_code = 200
        extractor_cls.return_value.probe_v4_capabilities.return_value = {
            "status": "degraded",
            "query_surface": "observations_v2",
            "checks": {"explicit_trace": {"status": "error"}},
        }
        env = {
            "LANGFUSE_HOST": "http://remote.example:3000",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "TRACE_REVIEW_RUNTIME_VERSION": "v0.9.4",
            "TRACE_REVIEW_SOURCE_REVISION": "abc123",
        }

        with patch.dict(os.environ, env, clear=True):
            payload = asyncio.run(langfuse_health("remote"))

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["langfuse"]["remote"]["capabilities"]["status"], "degraded")
        self.assertEqual(payload["runtime"]["image_version"], "v0.9.4")

    def test_trace_extractor_rejects_unknown_source(self):
        with self.assertRaisesRegex(ValueError, "Unsupported trace source 'stale'"):
            TraceExtractor(source="stale")


if __name__ == "__main__":
    unittest.main()
