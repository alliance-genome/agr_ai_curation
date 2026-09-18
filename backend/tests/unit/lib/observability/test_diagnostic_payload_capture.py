"""Production failures must arrive diagnosable (KANBAN-1771 / ALL-1250).

Michelle Perry's flow failed at 12:31:54 UTC on 2026-09-18 (Sentry
2a6c928f92464e14bc0d5b754290814d). The conformance check raised an error
carrying an issues list with the exact field path, expected shape and actual
kind. None of it reached Sentry, and the trace payloads were depth-redacted, so
the offending field stayed unknown until it was traced by hand.

Chris's decision (2026-09-18): stop redacting these payloads. They carry no
curator or personal data. Credential-shaped keys stay redacted.
"""

import pytest

from src.lib.openai_agents import extraction_trace_events as trace_events


class TestTracePayloadDepth:
    def test_deep_payloads_are_captured_in_full(self, monkeypatch):
        monkeypatch.delenv("EXTRACTION_TRACE_EVENT_MAX_DEPTH", raising=False)
        payload = current = {}
        for level in range(20):
            current["level"] = {"depth": level}
            current = current["level"]
        current["field_path"] = "metadata.evidence_records.0.status"

        redacted = trace_events._redact_value(payload)

        probe = redacted
        for _ in range(20):
            assert probe != "<redacted:depth_limit>"
            probe = probe["level"]
        assert probe["field_path"] == "metadata.evidence_records.0.status"

    def test_depth_remains_bounded_and_configurable(self, monkeypatch):
        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_MAX_DEPTH", "3")
        payload = current = {}
        for _ in range(10):
            current["level"] = {}
            current = current["level"]

        redacted = trace_events._redact_value(payload)

        flattened = str(redacted)
        assert "<redacted:depth_limit>" in flattened


class TestTracePayloadStrings:
    def test_a_long_reason_is_not_truncated_by_default(self, monkeypatch):
        monkeypatch.delenv("EXTRACTION_TRACE_EVENT_PREVIEW_LIMIT", raising=False)
        reason = "Extra inputs are not permitted. " * 200

        redacted = trace_events._redact_value({"message": reason})

        assert isinstance(redacted["message"], str)
        assert "truncated" not in str(redacted["message"])

    def test_truncation_still_available_by_configuration(self, monkeypatch):
        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_PREVIEW_LIMIT", "100")

        redacted = trace_events._redact_value({"message": "x" * 500})

        assert redacted["message"]["truncated"] is True
        assert redacted["message"]["length"] == 500


class TestTracePayloadLists:
    def test_more_than_twenty_five_items_are_kept(self, monkeypatch):
        monkeypatch.delenv("EXTRACTION_TRACE_EVENT_MAX_LIST_ITEMS", raising=False)
        payload = {"tool_calls": [{"index": index} for index in range(128)]}

        redacted = trace_events._redact_value(payload)

        assert len(redacted["tool_calls"]) == 128
        assert all("truncated" not in str(item) for item in redacted["tool_calls"])

    def test_list_size_remains_bounded_and_configurable(self, monkeypatch):
        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_MAX_LIST_ITEMS", "10")
        payload = {"tool_calls": [{"index": index} for index in range(50)]}

        redacted = trace_events._redact_value(payload)

        assert len(redacted["tool_calls"]) == 11
        assert redacted["tool_calls"][-1]["omitted_count"] == 40


class TestCredentialsStayRedacted:
    @pytest.mark.parametrize("key", [
        "api_key",
        "authorization",
        "password",
        "secret",
        "openai_api_key",
    ])
    def test_credential_shaped_keys_are_still_redacted(self, key):
        redacted = trace_events._redact_value({key: "super-secret-value"})
        assert redacted[key] == "<redacted>"
        assert "super-secret-value" not in str(redacted)

    def test_token_metrics_are_not_mistaken_for_credentials(self):
        redacted = trace_events._redact_value({"total_tokens": 1234})
        assert redacted["total_tokens"] == 1234


class TestSentryStructuredErrorDetail:
    def test_conformance_issues_reach_the_event(self):
        from src.lib.agent_studio.profile_conformance import ProfileConformanceError
        from src.lib.observability.sentry import _structured_error_detail

        issues = [{
            "field_path": "attributes.symbol",
            "reason": "wrong_type",
            "expected": "string",
            "actual_kind": "integer",
        }]
        error = ProfileConformanceError(issues)

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert detail["issues"] == issues
        assert detail["exception_type"] == "ProfileConformanceError"

    def test_envelope_integrity_issues_and_code_reach_the_event(self):
        from src.lib.agent_studio.profile_conformance import EnvelopeIntegrityError
        from src.lib.observability.sentry import _structured_error_detail

        issues = [{
            "field_path": "metadata.evidence_records.0.status",
            "reason": "invalid_envelope",
        }]
        error = EnvelopeIntegrityError(issues)

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert detail["issues"] == issues
        assert detail["code"] == "envelope_integrity"

    def test_an_ordinary_exception_adds_nothing(self):
        from src.lib.observability.sentry import _structured_error_detail

        error = RuntimeError("something broke")
        assert _structured_error_detail({"exc_info": (type(error), error, None)}) is None

    def test_no_hint_adds_nothing(self):
        from src.lib.observability.sentry import _structured_error_detail

        assert _structured_error_detail(None) is None
        assert _structured_error_detail({}) is None

    def test_detail_survives_scrubbing_in_before_send(self):
        from src.lib.agent_studio.profile_conformance import EnvelopeIntegrityError
        from src.lib.observability.sentry import before_send

        error = EnvelopeIntegrityError([
            {"field_path": "metadata.evidence_records.0.span_ids", "reason": "invalid_envelope"}
        ])
        event = {"level": "error", "exception": {"values": [{"type": "EnvelopeIntegrityError"}]}}

        sent = before_send(event, {"exc_info": (type(error), error, None)})

        detail = sent["contexts"]["error_detail"]
        assert detail["issues"][0]["field_path"] == "metadata.evidence_records.0.span_ids"
        assert detail["code"] == "envelope_integrity"


class TestStructuredDetailCannotLeakContent:
    """Review finding: the `details` branch was a raw-content path to Sentry.

    SpecialistOutputError.details carries `str(exc)` from a persistence failure
    (streaming_tools.py:2954). A SQLAlchemy error stringifies as
    "... [SQL: INSERT ...] [parameters: (...)]", and those parameters are the
    extracted envelope: labels, descriptions and quotes from the curator's PDF.
    It also carries `last_rejection`, the model's rejected output including the
    evidence reference report.

    contexts.error_detail is attached after _redact_event, so nothing scrubs it.
    """

    def test_specialist_output_error_details_are_not_emitted(self):
        from src.lib.observability.sentry import _structured_error_detail

        class FakeSpecialistOutputError(Exception):
            def __init__(self):
                super().__init__("persist failed")
                self.details = [{
                    "reason": "inline_extraction_persistence_failed",
                    "error": "IntegrityError [SQL: INSERT INTO extraction_results] "
                             "[parameters: ('Adgrl1 was detected in embryonic brain', ...)]",
                }]

        error = FakeSpecialistOutputError()
        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert detail is None, "details must not reach Sentry unscrubbed"

    def test_a_foreign_exception_with_issues_is_ignored(self):
        """The helper was duck-typed, so any library error with .code matched."""
        from src.lib.observability.sentry import _structured_error_detail

        class ForeignApiError(Exception):
            def __init__(self):
                super().__init__("upstream failed")
                self.code = "rate_limit"
                self.issues = [{"raw": "could contain anything"}]

        error = ForeignApiError()
        assert _structured_error_detail({"exc_info": (type(error), error, None)}) is None

    def test_owned_errors_still_reach_sentry(self):
        from src.lib.agent_studio.profile_conformance import (
            EnvelopeIntegrityError,
            ProfileConformanceError,
        )
        from src.lib.observability.sentry import _structured_error_detail

        for error in (
            ProfileConformanceError([{"field_path": "attributes.symbol", "reason": "wrong_type"}]),
            EnvelopeIntegrityError([{"field_path": "metadata.evidence_records.0.status"}]),
        ):
            detail = _structured_error_detail({"exc_info": (type(error), error, None)})
            assert detail is not None
            assert detail["issues"]

    def test_detail_is_bounded_by_total_size(self):
        """An unbounded context can get the whole Sentry event dropped."""
        from src.lib.agent_studio.profile_conformance import ProfileConformanceError
        from src.lib.observability.sentry import _structured_error_detail
        import json

        error = ProfileConformanceError([
            {"field_path": f"attributes.f{index}", "reason": "wrong_type",
             "message": "x" * 5000}
            for index in range(40)
        ])

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert len(json.dumps(detail, default=str)) <= 20000


class TestConformanceDegradationPreserved:
    """Review finding: EnvelopeIntegrityError escaped a graceful-skip handler.

    curation_prep_service catches (ValueError, DomainEnvelopeMaterializationError)
    around ensure_domain_envelope_materialization and skips that one extraction
    result with a warning. ProfileConformanceError subclassed ValueError, so one
    contaminated result was skipped and prep continued. A sibling that is not a
    ValueError would fail the whole request instead.
    """

    def test_curation_prep_still_skips_one_unmappable_result(self):
        import inspect

        from src.lib.curation_workspace import curation_prep_service

        source = inspect.getsource(curation_prep_service)
        assert "EnvelopeIntegrityError" in source, (
            "curation_prep must handle the integrity sibling or it loses the "
            "skip-one-and-continue behavior for every other result"
        )
