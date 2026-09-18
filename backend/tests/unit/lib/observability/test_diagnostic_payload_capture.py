"""Production failures must arrive diagnosable (KANBAN-1771 / ALL-1250).

Michelle Perry's flow failed at 12:31:54 UTC on 2026-09-18 (Sentry
2a6c928f92464e14bc0d5b754290814d). The conformance check raised an error
carrying an issues list with the exact field path, expected shape and actual
kind. None of it reached Sentry, and the trace payloads were depth-redacted, so
the offending field stayed unknown until it was traced by hand.

Chris's decision (2026-09-18): stop redacting these payloads. They carry no
curator or personal data. Credential-shaped keys stay redacted.
"""

import json

import pytest

from src.lib.openai_agents import extraction_trace_events as trace_events



# Credential-shaped fixtures are assembled at runtime so no literal in this
# file matches a secret-scanning rule. The scrubber under test matches on
# shape, so the fixture has to have the shape.
def _fake_openai_key() -> str:
    return "sk-" + ("abcdefghijklmnop" + "0123456789")


def _fake_bearer_header() -> str:
    return "Authorization: Bearer " + ("abcdef0123456789" + "xyz")


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


class TestStructuredDetailIsUsefulAndSafe:
    """Chris's decision (2026-09-18): capture the diagnostic payload.

    Sentry here is self-hosted inside this compose stack. Curator and document
    content is published literature and extraction output, not PII, and
    over-scrubbing it is what made these failures undiagnosable. So the content
    goes in.

    Two things stay out, for reasons that are not privacy:

    * Credential-shaped VALUES. The ticket says keep credential redaction. A
      SQLAlchemy connection error stringifies with its DSN, so key-based
      redaction alone is not enough on this path.
    * Unbounded size. Sentry drops an over-large event outright, which would
      lose the alert entirely and defeat the point of the ticket.
    """

    def test_specialist_output_error_details_are_captured(self):
        """This is the payload Chris wants: the real persistence failure."""
        from src.lib.observability.sentry import _structured_error_detail
        from src.lib.openai_agents.streaming_tools import SpecialistOutputError

        error = SpecialistOutputError(
            specialist_name="allele_extractor",
            output_type_name="inline_extraction_persistence",
            message="Validated extraction could not be persisted inline.",
            details=[{
                "reason": "inline_extraction_persistence_failed",
                "error": "IntegrityError [SQL: INSERT INTO extraction_results] "
                         "[parameters: ('Adgrl1 was detected in embryonic brain', ...)]",
            }],
        )

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert detail is not None
        assert "Adgrl1 was detected in embryonic brain" in str(detail["details"]), (
            "the extracted content is the diagnostic value; it must survive"
        )

    def test_a_credential_in_a_value_is_still_redacted(self):
        """Key-based redaction misses a DSN inside an exception string."""
        from src.lib.observability.sentry import _structured_error_detail
        from src.lib.openai_agents.streaming_tools import SpecialistOutputError

        error = SpecialistOutputError(
            specialist_name="allele_extractor",
            output_type_name="x",
            message="connection failed",
            details=[{
                "reason": "db_unavailable",
                "error": "could not connect: " + _fake_bearer_header(),
            }],
        )

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert "abcdef0123456789xyz" not in str(detail)

    def test_an_openai_key_in_a_value_is_redacted(self):
        from src.lib.observability.sentry import _structured_error_detail
        from src.lib.agent_studio.profile_conformance import ProfileConformanceError

        error = ProfileConformanceError([
            {"field_path": "attributes.note", "reason": "wrong_type",
             "message": "value was " + _fake_openai_key()}
        ])

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert _fake_openai_key() not in str(detail)

    def test_a_foreign_library_exception_is_ignored(self):
        """Not privacy: an untyped .code match would tag most events with noise."""
        from src.lib.observability.sentry import _structured_error_detail

        class ForeignApiError(Exception):
            def __init__(self):
                super().__init__("upstream failed")
                self.code = "rate_limit"

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

    def test_detail_is_bounded_so_the_event_is_not_dropped(self):
        """Sentry drops an over-large event; that would lose the alert."""
        import json

        from src.lib.agent_studio.profile_conformance import ProfileConformanceError
        from src.lib.observability.sentry import _structured_error_detail

        error = ProfileConformanceError([
            {"field_path": f"attributes.f{index}", "reason": "wrong_type",
             "message": "x" * 5000}
            for index in range(200)
        ])

        detail = _structured_error_detail({"exc_info": (type(error), error, None)})

        assert len(json.dumps(detail, default=str)) <= 60000
        assert detail["issues"], "it must still carry usable diagnosis after trimming"


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


class TestContentRedactionIsOffByDefault:
    """KANBAN-1771: the redaction itself was harming debugging.

    Chris, 2026-09-18: Sentry is self-hosted on a private VPC address
    (verified: 172.31.70.182:9000), patched, behind login. Content redaction
    cost more than it protected. The alert for Michelle Perry's failure showed
    "[Filtered]" instead of the error, because every exception value, the
    message and every content-marker key were replaced wholesale.

    Content scrubbing is now OFF by default with an explicit switch to restore
    it. Credential scrubbing is always on.
    """

    def _event(self):
        return {
            "level": "error",
            "message": "Domain-envelope validator dispatch failed",
            "exception": {"values": [{
                "type": "ProfileConformanceError",
                "value": "metadata.evidence_records.0.status extra_forbidden",
            }]},
            "extra": {"verified_quote": "Adgrl1 was detected in embryonic brain."},
        }

    def test_the_exception_value_survives(self, monkeypatch):
        monkeypatch.delenv("SENTRY_CONTENT_REDACTION_ENABLED", raising=False)
        from src.lib.observability.sentry import before_send

        sent = before_send(self._event(), None)

        assert "extra_forbidden" in sent["exception"]["values"][0]["value"], (
            "this is the field-level reason the alert exists to deliver"
        )

    def test_the_message_survives(self, monkeypatch):
        monkeypatch.delenv("SENTRY_CONTENT_REDACTION_ENABLED", raising=False)
        from src.lib.observability.sentry import before_send

        sent = before_send(self._event(), None)
        assert sent["message"] == "Domain-envelope validator dispatch failed"

    def test_content_marker_keys_survive(self, monkeypatch):
        monkeypatch.delenv("SENTRY_CONTENT_REDACTION_ENABLED", raising=False)
        from src.lib.observability.sentry import before_send

        sent = before_send(self._event(), None)
        assert "embryonic brain" in str(sent["extra"])

    def test_the_old_behavior_is_restorable(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", "true")
        from src.lib.observability.sentry import before_send

        sent = before_send(self._event(), None)
        assert sent["exception"]["values"][0]["value"] == "[Filtered]"
        assert sent["message"] == "[Filtered]"

    def test_credentials_are_redacted_either_way(self, monkeypatch):
        from src.lib.observability.sentry import before_send

        for setting in ("false", "true"):
            monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", setting)
            event = {
                "level": "error",
                "extra": {"api_key": _fake_openai_key()},
            }
            sent = before_send(event, None)
            assert _fake_openai_key() not in str(sent), setting


class TestTraceEventTotalBudget:
    """Review finding: the caps are per-node, so they multiply.

    depth 64 x 5000 list items x 100000 chars is not a bound in any useful
    sense. The docstring claimed one event cannot grow without limit; that was
    not what the code did. These files land on a production volume that has no
    retention policy, so a single pathological event could be enormous.

    This is a size guard, not a content guard. The cap is generous enough that
    a realistic diagnostic payload is untouched.
    """

    def test_a_realistic_payload_is_untouched(self, monkeypatch):
        monkeypatch.delenv("EXTRACTION_TRACE_EVENT_MAX_TOTAL_CHARS", raising=False)
        payload = {"tool_calls": [{"name": f"call-{i}", "text": "x" * 2000} for i in range(128)]}

        redacted = trace_events._redact_value(payload)

        assert len(redacted["tool_calls"]) == 128
        assert redacted["tool_calls"][0]["text"] == "x" * 2000

    def test_a_pathological_payload_is_bounded(self, monkeypatch):
        monkeypatch.delenv("EXTRACTION_TRACE_EVENT_MAX_TOTAL_CHARS", raising=False)
        payload = {"chunks": [{"text": "x" * 100_000} for _ in range(5000)]}

        redacted = trace_events._redact_value(payload)

        assert len(json.dumps(redacted, default=str)) <= trace_events.DEFAULT_MAX_TOTAL_CHARS * 2

    def test_many_small_keys_cannot_evade_the_budget(self, monkeypatch):
        """Review finding C2: keys were never charged, so a wide dict was free."""
        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_MAX_TOTAL_CHARS", "50000")
        payload = {f"key_number_{index:08d}": index for index in range(200_000)}

        redacted = trace_events._redact_value(payload)

        assert len(json.dumps(redacted, default=str)) < 2_000_000

    def test_many_short_strings_cannot_inflate_past_the_budget(self, monkeypatch):
        """Review finding C2: the dict marker was larger than the string it replaced."""
        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_MAX_TOTAL_CHARS", "50000")
        payload = {"values": ["x" * 20 for _ in range(200_000)]}

        raw = len(json.dumps(payload))
        redacted = trace_events._redact_value(payload)

        assert len(json.dumps(redacted, default=str)) < raw

    def test_the_budget_is_configurable(self, monkeypatch):
        import json

        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_MAX_TOTAL_CHARS", "5000")
        payload = {"chunks": [{"text": "x" * 1000} for _ in range(500)]}

        redacted = trace_events._redact_value(payload)

        assert len(json.dumps(redacted, default=str)) < 200_000

    def test_truncation_is_visible_to_the_reader(self, monkeypatch):
        monkeypatch.setenv("EXTRACTION_TRACE_EVENT_MAX_TOTAL_CHARS", "2000")
        payload = {"chunks": [{"text": "x" * 1000} for _ in range(100)]}

        redacted = trace_events._redact_value(payload)

        assert "budget" in json.dumps(redacted, default=str)
