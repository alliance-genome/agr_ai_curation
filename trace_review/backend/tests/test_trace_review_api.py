import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.api import traces
from src.models.requests import AnalyzeTraceRequest
from src.services.cache_manager import CacheManager


EMPTY_TRACE_SUMMARY = {
    "has_errors": False,
    "domain_envelope": {"found": False, "summary": {}},
}


class TraceReviewApiTests(unittest.IsolatedAsyncioTestCase):
    @patch("src.observability._client")
    @patch("src.api.traces.TraceExtractor")
    async def test_unexpected_setup_failures_are_reported_and_reraised(self, extractor_cls, reporter):
        failure = OSError("private-setup-error")
        extractor_cls.side_effect = failure
        reporter.capture_event.side_effect = RuntimeError("reporter down")
        for operation in ("export", "session"):
            reporter.reset_mock()
            from fastapi import HTTPException
            expected = HTTPException if operation == "export" else OSError
            with self.assertRaises(expected) as raised:
                if operation == "export":
                    await traces.export_trace("private-trace", self._make_request(), source="remote", refresh=False)
                else:
                    await traces.export_session("private-session", self._make_request(), source="remote")
            if operation == "export":
                self.assertEqual(getattr(raised.exception, "status_code"), 503)
            else:
                self.assertIs(raised.exception, failure)
            reporter.capture_event.assert_called_once()

    @patch("src.observability._client")
    @patch("src.api.traces.TraceExtractor")
    async def test_session_failures_emit_one_event_and_preserve_bundle(self, extractor_cls, reporter):
        extractor = extractor_cls.return_value
        extractor.list_session_traces.return_value = {
            "traces": [{"id": "private-1"}, {"id": "private-2"}],
            "meta": {"complete": True},
        }
        extractor.extract_complete_trace.side_effect = RuntimeError("private-response")
        for broken in (False, True):
            reporter.reset_mock()
            reporter.capture_event.side_effect = RuntimeError("reporter down") if broken else None
            response = await traces.export_session("private-session", self._make_request(), source="local")
            self.assertEqual(response["status"], "success")
            self.assertEqual(response["session"]["failed_trace_count"], 2)
            self.assertFalse(response["session"]["complete"])
            reporter.capture_event.assert_called_once()
            event = reporter.capture_event.call_args.args[0]
            self.assertEqual(event["contexts"]["trace_review"]["extraction_failures"], 2)
            self.assertNotIn("private-", str(event))

    @patch("src.observability._client")
    @patch("src.api.traces.TraceExtractor")
    async def test_search_and_session_listing_failures_capture(self, extractor_cls, reporter):
        from fastapi import HTTPException
        for failure, status in ((ValueError("private-config"), 503), (RuntimeError("private-response"), 502)):
            extractor_cls.side_effect = failure
            for operation in ("search", "session_listing"):
                reporter.reset_mock()
                with self.assertRaises(HTTPException) as raised:
                    if operation == "search":
                        await traces.search_traces(source="remote", session_id="private-session")
                    else:
                        await traces.export_session("private-session", self._make_request(), source="remote")
                self.assertEqual(raised.exception.status_code, status)
                reporter.capture_event.assert_called_once()
                self.assertNotIn("private-", str(reporter.capture_event.call_args))

    @patch("src.observability._client")
    @patch("src.api.traces.TraceExtractor")
    async def test_provider_extraction_and_analysis_failures_capture(self, extractor_cls, reporter):
        from fastapi import HTTPException
        extractor = extractor_cls.return_value
        extractor.extract_complete_trace.side_effect = RuntimeError("private-response")
        with self.assertRaises(HTTPException) as raised:
            await traces.export_trace("private-trace", self._make_request(), source="remote", refresh=False)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(reporter.capture_event.call_args.args[0]["tags"]["operation"], "extraction")
        reporter.reset_mock()
        extractor.extract_complete_trace.side_effect = None
        extractor.extract_complete_trace.return_value = {}
        with self.assertRaises(HTTPException) as raised:
            await traces.export_trace("private-trace", self._make_request(), source="remote", refresh=False)
        self.assertEqual(raised.exception.status_code, 500)
        reporter.capture_event.assert_called_once()
        self.assertEqual(reporter.capture_event.call_args.args[0]["tags"]["operation"], "analysis")

    @patch("src.observability._client")
    @patch("src.api.traces.TraceExtractor")
    def test_direct_extraction_reports_provider_failure_but_not_missing_trace(self, extractor_cls, reporter):
        from fastapi import HTTPException
        from src.services.trace_extractor import TraceNotFoundError
        for failure, captures in ((RuntimeError("private-response"), 1), (TraceNotFoundError("missing"), 0)):
            reporter.reset_mock()
            extractor_cls.return_value.extract_complete_trace.side_effect = failure
            with self.assertRaises(HTTPException) as raised:
                traces._extract_langfuse_trace("private-trace", "remote")
            self.assertEqual(raised.exception.status_code, 503 if captures else 404)
            self.assertEqual(reporter.capture_event.call_count, captures)

    @patch("src.observability._client")
    async def test_score_outage_captured_once_through_api_and_session(self, reporter):
        from fastapi import HTTPException
        from src.services.trace_extractor import TraceExtractor
        extractor = object.__new__(TraceExtractor)
        extractor.source = "remote"
        extractor.client = Mock()
        extractor.client.api.scores.get_many.side_effect = RuntimeError("private-response")
        extractor.get_observations = Mock(return_value=[{"id": "root", "type": "SPAN"}])
        extractor.list_session_traces = Mock(return_value={
            "traces": [{"id": "failed"}], "meta": {"complete": True},
        })
        for broken in (False, True):
            reporter.capture_event.side_effect = RuntimeError("reporter down") if broken else None
            for operation in ("direct", "export", "analyze", "session"):
                reporter.reset_mock()
                request = self._make_request()
                with patch("src.api.traces.TraceExtractor", return_value=extractor):
                    if operation == "session":
                        result = await traces.export_session("session", request, source="remote")
                        self.assertEqual(result["session"]["failed_trace_count"], 1)
                        self.assertFalse(result["session"]["complete"])
                        self.assertEqual(result["traces"][0]["status"], "error")
                    else:
                        with self.assertRaises(HTTPException) as raised:
                            if operation == "direct":
                                traces._extract_langfuse_trace("failed", "remote")
                            elif operation == "export":
                                await traces.export_trace("failed", request, source="remote", refresh=False)
                            else:
                                await traces.analyze_trace(AnalyzeTraceRequest(trace_id="failed"), request)
                        self.assertEqual(raised.exception.status_code, 503)
                        self.assertNotIn("private-", raised.exception.detail)
                reporter.capture_event.assert_called_once()
                event = reporter.capture_event.call_args.args[0]
                self.assertNotIn("private-", str(event))
                if operation == "session":
                    self.assertEqual(event["contexts"]["trace_review"]["scores_failures"], 1)
                    self.assertNotIn("extraction_failures", event["contexts"]["trace_review"])

    @patch("src.api.traces.TraceExtractor")
    async def test_missing_trace_is_404_for_analyze_and_export(self, extractor_cls):
        from fastapi import HTTPException
        from src.services.trace_extractor import TraceNotFoundError
        extractor_cls.return_value.extract_complete_trace.side_effect = TraceNotFoundError("missing")
        for operation in ("analyze", "export"):
            with self.assertRaises(HTTPException) as raised:
                if operation == "analyze":
                    await traces.analyze_trace(AnalyzeTraceRequest(trace_id="missing"), self._make_request())
                else:
                    await traces.export_trace("missing", self._make_request(), source="remote", refresh=False)
            self.assertEqual(raised.exception.status_code, 404)

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_distinguishes_empty_complete_and_stopped_scans(self, extractor_cls):
        for complete in (True, False):
            with self.subTest(complete=complete):
                meta = {"complete": complete, "truncated": not complete,
                        "stop_reason": None if complete else "request_limit"}
                extractor_cls.return_value.list_session_traces.return_value = {
                    "traces": [], "meta": meta,
                }
                response = await traces.export_session("session-1", self._make_request(), source="remote")
                self.assertEqual(response["status"], "success" if complete else "partial")
                self.assertEqual(response["session"]["complete"], complete)
                self.assertEqual(response["session"]["langfuse_meta"], meta)
                self.assertEqual(response["traces"], [])

    @patch("src.api.traces._get_or_analyze_trace_export")
    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_retains_discovered_traces_when_partial(self, extractor_cls, analyze):
        meta = {"complete": False, "truncated": True, "stop_reason": "observation_limit"}
        extractor_cls.return_value.list_session_traces.return_value = {
            "traces": [{"id": "trace-1"}], "meta": meta,
        }
        analyze.return_value = ({"analysis": {
            key: {} for key in (
                "summary", "conversation", "tool_calls", "pdf_citations", "token_analysis",
                "agent_context", "trace_summary", "domain_envelope", "document_hierarchy",
                "agent_configs", "group_context",
            )
        }}, "cached", True)
        analyze.return_value[0]["analysis"]["tool_calls"] = {
            "tool_calls": [], "total_count": 0, "unique_tools": [], "duplicates": {},
        }
        response = await traces.export_session("session-1", self._make_request(), source="remote")
        self.assertEqual(response["status"], "partial")
        self.assertFalse(response["session"]["complete"])
        self.assertEqual(response["session"]["successful_trace_count"], 1)
        self.assertEqual(response["traces"][0]["trace_id"], "trace-1")

    def _make_request(self) -> SimpleNamespace:
        cache_manager = CacheManager(ttl_hours=1)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cache_manager=cache_manager)))

    def _make_trace_data(
        self,
        output,
        trace_id="trace-inflight-1234",
        session_id="session-123",
        name="chat: Example",
        timestamp="2026-03-26T00:00:00Z",
    ):
        return {
            "raw_trace": {
                "id": trace_id,
                "name": name,
                "timestamp": timestamp,
                "sessionId": session_id,
                "input": {"message": f"Question for {trace_id}"},
                "metadata": {},
                "output": output,
            },
            "observations": [],
            "scores": [],
            "trace_id_short": trace_id[:8],
            "metadata": {
                "trace_name": name,
                "duration_seconds": 3.5,
                "total_cost": 0.0,
                "total_tokens": 0,
                "observation_count": 0,
                "score_count": 0,
                "timestamp": timestamp,
            },
        }

    @patch("src.api.traces.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.api.traces.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value=EMPTY_TRACE_SUMMARY)
    @patch("src.api.traces.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.api.traces.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": [], "duplicates": {}})
    @patch("src.api.traces.ConversationAnalyzer.extract_conversation", return_value={"user_input": "Question", "assistant_response": "N/A"})
    @patch("src.api.traces.TraceExtractor")
    async def test_analyze_trace_transient_cache_supports_immediate_view_fetch(
        self,
        extractor_cls: Mock,
        _conversation: Mock,
        _tool_calls: Mock,
        _pdf_citations: Mock,
        _token_analysis: Mock,
        _agent_context: Mock,
        _trace_summary: Mock,
        _document_hierarchy: Mock,
        _agent_configs: Mock,
    ):
        request = self._make_request()
        extractor_cls.return_value.extract_complete_trace.return_value = self._make_trace_data(
            {"response_length": 120, "tool_calls": 2}
        )

        response = await traces.analyze_trace(
            AnalyzeTraceRequest(trace_id="trace-inflight-1234", source="local"),
            request,
        )

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["cache_status"], "transient")

        cached = request.app.state.cache_manager.get("trace-inflight-1234")
        self.assertIsNotNone(cached)

        summary_view = await traces.get_trace_view(
            "trace-inflight-1234",
            "summary",
            request,
        )

        self.assertEqual(summary_view["trace_id"], "trace-inflight-1234")
        self.assertEqual(summary_view["data"]["trace_id"], "trace-inflight-1234")

    @patch("src.api.traces.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.api.traces.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value=EMPTY_TRACE_SUMMARY)
    @patch("src.api.traces.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.api.traces.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": [], "duplicates": {}})
    @patch("src.api.traces.ConversationAnalyzer.extract_conversation", return_value={"user_input": "Question", "assistant_response": "Final answer"})
    @patch("src.api.traces.TraceExtractor")
    async def test_analyze_trace_caches_nested_final_output_as_stable(
        self,
        extractor_cls: Mock,
        _conversation: Mock,
        _tool_calls: Mock,
        _pdf_citations: Mock,
        _token_analysis: Mock,
        _agent_context: Mock,
        _trace_summary: Mock,
        _document_hierarchy: Mock,
        _agent_configs: Mock,
    ):
        request = self._make_request()
        extractor_cls.return_value.extract_complete_trace.return_value = self._make_trace_data(
            {"output": {"final_output": {"answer": "Final answer"}}}
        )

        response = await traces.analyze_trace(
            AnalyzeTraceRequest(trace_id="trace-inflight-1234", source="local"),
            request,
        )

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["cache_status"], "miss")
        self.assertEqual(request.app.state.cache_manager.get_status("trace-inflight-1234"), "stable")

    @patch("src.api.traces.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.api.traces.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value=EMPTY_TRACE_SUMMARY)
    @patch("src.api.traces.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.api.traces.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": [], "duplicates": {}})
    @patch("src.api.traces.ConversationAnalyzer.extract_conversation", return_value={"user_input": "Question", "assistant_response": "N/A"})
    @patch("src.api.traces.TraceExtractor")
    async def test_analyze_trace_keeps_placeholder_output_transient(
        self,
        extractor_cls: Mock,
        _conversation: Mock,
        _tool_calls: Mock,
        _pdf_citations: Mock,
        _token_analysis: Mock,
        _agent_context: Mock,
        _trace_summary: Mock,
        _document_hierarchy: Mock,
        _agent_configs: Mock,
    ):
        request = self._make_request()
        extractor_cls.return_value.extract_complete_trace.return_value = self._make_trace_data(
            {"assistant_response": "N/A", "response_length": 3}
        )

        response = await traces.analyze_trace(
            AnalyzeTraceRequest(trace_id="trace-inflight-1234", source="local"),
            request,
        )

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["cache_status"], "transient")
        self.assertEqual(request.app.state.cache_manager.get_status("trace-inflight-1234"), "transient")

    @patch("src.api.traces.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.api.traces.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value=EMPTY_TRACE_SUMMARY)
    @patch("src.api.traces.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.api.traces.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": [], "duplicates": {}})
    @patch("src.api.traces.ConversationAnalyzer.extract_conversation", return_value={"user_input": "Question", "assistant_response": "N/A"})
    @patch("src.api.traces.TraceExtractor")
    async def test_analyze_trace_preserves_transient_status_on_cached_hit(
        self,
        extractor_cls: Mock,
        _conversation: Mock,
        _tool_calls: Mock,
        _pdf_citations: Mock,
        _token_analysis: Mock,
        _agent_context: Mock,
        _trace_summary: Mock,
        _document_hierarchy: Mock,
        _agent_configs: Mock,
    ):
        request = self._make_request()
        extractor_cls.return_value.extract_complete_trace.return_value = self._make_trace_data(
            {"response_length": 120, "tool_calls": 2}
        )

        first_response = await traces.analyze_trace(
            AnalyzeTraceRequest(trace_id="trace-inflight-1234", source="local"),
            request,
        )
        second_response = await traces.analyze_trace(
            AnalyzeTraceRequest(trace_id="trace-inflight-1234", source="local"),
            request,
        )

        self.assertEqual(first_response["cache_status"], "transient")
        self.assertEqual(second_response["cache_status"], "transient")
        extractor_cls.return_value.extract_complete_trace.assert_called_once()

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_returns_multiple_trace_summaries_and_uses_source(
        self,
        extractor_cls: Mock,
    ):
        request = self._make_request()
        extractor = extractor_cls.return_value
        extractor.list_session_traces.return_value = {
            "session_id": "session-123",
            "source": "local",
            "traces": [
                {
                    "id": "trace-session-1",
                    "name": "query_supervisor_config",
                    "timestamp": "2026-03-26T00:00:00Z",
                    "sessionId": "session-123",
                },
                {
                    "id": "trace-session-2",
                    "name": "pdf_specialist_config",
                    "timestamp": "2026-03-26T00:01:00Z",
                    "sessionId": "session-123",
                },
            ],
            "meta": {"complete": True, "truncated": False, "stop_reason": None, "page": 1, "limit": 100, "totalItems": 2, "totalPages": 1},
        }
        extractor.extract_complete_trace.side_effect = [
            self._make_trace_data(
                {"answer": "First answer"},
                trace_id="trace-session-1",
                name="query_supervisor_config",
                timestamp="2026-03-26T00:00:00Z",
            ),
            self._make_trace_data(
                {"answer": "Second answer"},
                trace_id="trace-session-2",
                name="pdf_specialist_config",
                timestamp="2026-03-26T00:01:00Z",
            ),
        ]

        response = await traces.export_session("session-123", request, source="local")

        extractor_cls.assert_called_once_with(source="local")
        extractor.list_session_traces.assert_called_once_with("session-123")
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["session"]["source"], "local")
        self.assertEqual(response["session"]["trace_count"], 2)
        self.assertEqual(response["session"]["successful_trace_count"], 2)
        self.assertEqual(response["session"]["failed_trace_count"], 0)
        self.assertEqual(response["session"]["trace_ids"], ["trace-session-1", "trace-session-2"])
        self.assertEqual(len(response["traces"]), 2)
        self.assertEqual(response["traces"][0]["status"], "success")
        self.assertEqual(response["traces"][0]["summary"]["trace_name"], "query_supervisor_config")
        self.assertEqual(response["traces"][0]["conversation"]["assistant_response"], "First answer")
        self.assertEqual(response["traces"][0]["tool_summary"]["total_count"], 0)
        self.assertIn("trace_summary", response["traces"][0]["analyzer_outputs"])
        self.assertEqual(response["errors"], [])

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_includes_domain_envelope_summary(
        self,
        extractor_cls: Mock,
    ):
        request = self._make_request()
        extractor = extractor_cls.return_value
        extractor.list_session_traces.return_value = {
            "session_id": "session-domain",
            "source": "local",
            "traces": [
                {
                    "id": "trace-domain",
                    "name": "curation_prep",
                    "timestamp": "2026-03-26T00:00:00Z",
                    "sessionId": "session-domain",
                },
            ],
            "meta": {"complete": True, "truncated": False, "stop_reason": None, "page": 1, "limit": 100, "totalItems": 1, "totalPages": 1},
        }
        trace_data = self._make_trace_data(
            {
                "domain_envelopes": [
                    {
                        "envelope_id": "env-domain-1",
                        "domain_pack_id": "agr.test.gene",
                        "objects": [
                            {
                                "object_id": "gene-expression-object-1",
                                "object_type": "gene_expression",
                                "payload": {"gene": {"symbol": "tmem67"}},
                                "definition_state": "stable",
                            }
                        ],
                        "validation_findings": [
                            {
                                "finding_id": "finding-1",
                                "severity": "blocker",
                                "status": "open",
                                "code": "domain_envelope.required_field_missing",
                                "message": "Required export field is missing: gene.symbol.",
                                "field_ref": {
                                    "object_ref": {
                                        "object_id": "gene-expression-object-1",
                                        "object_type": "gene_expression",
                                    },
                                    "field_path": "gene.symbol",
                                },
                            }
                        ],
                    }
                ]
            },
            trace_id="trace-domain",
            name="curation_prep",
        )
        trace_data["metadata"]["domain_envelope"] = {
            "found": True,
            "envelope_ids": ["stale-metadata-envelope"],
        }
        extractor.extract_complete_trace.return_value = trace_data

        response = await traces.export_session("session-domain", request, source="local")
        item = response["traces"][0]

        self.assertTrue(item["summary"]["domain_envelope"]["found"])
        self.assertEqual(item["summary"]["domain_envelope"]["envelope_ids"], ["env-domain-1"])
        self.assertIn("domain_envelope", item["analyzer_outputs"])
        self.assertEqual(
            item["analyzer_outputs"]["domain_envelope"]["summary"]["blocker_count"],
            1,
        )

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_keeps_partial_trace_failures(
        self,
        extractor_cls: Mock,
    ):
        request = self._make_request()
        extractor = extractor_cls.return_value
        extractor.list_session_traces.return_value = {
            "session_id": "session-123",
            "source": "remote",
            "traces": [
                {
                    "id": "trace-good",
                    "name": "query_supervisor_config",
                    "timestamp": "2026-03-26T00:00:00Z",
                    "sessionId": "session-123",
                },
                {
                    "id": "trace-missing",
                    "name": "pdf_specialist_config",
                    "timestamp": "2026-03-26T00:01:00Z",
                    "sessionId": "session-123",
                },
            ],
            "meta": {"complete": True, "truncated": False, "stop_reason": None, "page": 1, "limit": 100, "totalItems": 2, "totalPages": 1},
        }
        extractor.extract_complete_trace.side_effect = [
            self._make_trace_data(
                {"answer": "Good answer"},
                trace_id="trace-good",
                name="query_supervisor_config",
            ),
            RuntimeError("trace not found"),
        ]

        response = await traces.export_session("session-123", request, source="remote")

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["session"]["trace_count"], 2)
        self.assertEqual(response["session"]["successful_trace_count"], 1)
        self.assertEqual(response["session"]["failed_trace_count"], 1)
        self.assertEqual(response["traces"][0]["status"], "success")
        self.assertEqual(response["traces"][1]["status"], "error")
        self.assertEqual(response["traces"][1]["error"]["trace_id"], "trace-missing")
        self.assertEqual("Trace provider is temporarily unavailable.", response["traces"][1]["error"]["message"])
        self.assertEqual(response["errors"][0]["trace_id"], "trace-missing")

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_serializes_v2_datetime_on_partial_failure(
        self,
        extractor_cls: Mock,
    ):
        request = self._make_request()
        listed_at = datetime.fromisoformat("2026-03-26T00:01:00+00:00")
        extractor = extractor_cls.return_value
        extractor.list_session_traces.return_value = {
            "session_id": "session-123",
            "source": "local",
            "traces": [
                {
                    "id": "trace-missing",
                    "name": "pdf_specialist_config",
                    "timestamp": listed_at,
                    "sessionId": "session-123",
                },
            ],
            "meta": {"complete": True, "truncated": False, "stop_reason": None, "page": 1, "limit": 100, "totalItems": 1, "totalPages": 1},
        }
        extractor.extract_complete_trace.side_effect = RuntimeError("trace not found")

        response = await traces.export_session("session-123", request, source="local")

        expected = "2026-03-26T00:01:00+00:00"
        self.assertEqual(response["traces"][0]["listed_trace"]["timestamp"], expected)
        self.assertEqual(response["traces"][0]["error"]["timestamp"], expected)
        self.assertEqual(response["errors"][0]["timestamp"], expected)
        self.assertEqual(response["session"]["first_timestamp"], expected)
        self.assertEqual(response["session"]["last_timestamp"], expected)

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_invalidates_stale_compact_bundle_cache(
        self,
        extractor_cls: Mock,
    ):
        request = self._make_request()
        request.app.state.cache_manager.set("trace-corrupt", {"analysis": {"summary": {}}})

        extractor = extractor_cls.return_value
        extractor.list_session_traces.return_value = {
            "session_id": "session-123",
            "source": "remote",
            "traces": [
                {
                    "id": "trace-corrupt",
                    "name": "query_supervisor_config",
                    "timestamp": "2026-03-26T00:00:00Z",
                    "sessionId": "session-123",
                },
            ],
            "meta": {"complete": True, "truncated": False, "stop_reason": None, "page": 1, "limit": 100, "totalItems": 1, "totalPages": 1},
        }
        extractor.extract_complete_trace.return_value = self._make_trace_data(
            {"answer": "Recovered answer"},
            trace_id="trace-corrupt",
            name="query_supervisor_config",
        )

        response = await traces.export_session("session-123", request, source="remote")

        extractor.extract_complete_trace.assert_called_once_with("trace-corrupt")
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["session"]["successful_trace_count"], 1)
        self.assertEqual(response["traces"][0]["status"], "success")

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_surfaces_session_listing_contract_errors(
        self,
        extractor_cls: Mock,
    ):
        for listing, missing_key in [
            ({"meta": {"page": 1, "limit": 100}}, "traces"),
            ({"traces": []}, "meta"),
        ]:
            with self.subTest(missing_key=missing_key):
                request = self._make_request()
                extractor = extractor_cls.return_value
                extractor.list_session_traces.return_value = listing
                extractor.extract_complete_trace.reset_mock()

                with self.assertRaises(KeyError) as context:
                    await traces.export_session("session-123", request, source="remote")

                self.assertEqual(context.exception.args[0], missing_key)
                extractor.extract_complete_trace.assert_not_called()

    @patch("src.api.traces.TraceExtractor")
    async def test_search_traces_by_document_id_uses_langfuse_listing(
        self,
        extractor_cls: Mock,
    ):
        extractor = extractor_cls.return_value
        extractor.list_traces.return_value = {
            "source": "local",
            "query": {
                "document_id": "doc-1",
                "session_id": None,
                "user_id": None,
                "name": None,
                "run_id": None,
                "extraction_id": None,
                "limit": 5,
            },
            "meta": {"page": 1, "totalPages": 1},
            "traces": [
                {
                    "id": "trace-doc",
                    "name": "AI Curation chat",
                    "timestamp": "2026-06-06T03:00:00Z",
                    "sessionId": "session-1",
                    "metadata": {"document_id": "doc-1"},
                }
            ],
        }

        response = await traces.search_traces(
            source="local",
            session_id=None,
            user_id=None,
            name=None,
            document_id="doc-1",
            run_id=None,
            extraction_id=None,
            from_timestamp=None,
            to_timestamp=None,
            limit=5,
        )

        extractor_cls.assert_called_once_with(source="local")
        extractor.list_traces.assert_called_once()
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["trace_count"], 1)
        self.assertEqual(response["traces"][0]["trace_id"], "trace-doc")

    @patch("src.api.traces.TraceExtractor")
    async def test_new_payload_and_reconstruction_endpoints_read_langfuse_trace(
        self,
        extractor_cls: Mock,
    ):
        trace_data = self._make_trace_data(
            {"answer": "Done"},
            trace_id="trace-new-api",
            session_id="session-new",
            name="AI Curation chat",
        )
        trace_data["raw_trace"]["metadata"] = {"document_id": "doc-1"}
        trace_data["raw_trace"]["input"] = {"question": "inspect payload"}
        trace_data["observations"] = [
            {
                "id": "obs-tool",
                "type": "SPAN",
                "name": "tool call fetch_entities",
                "startTime": "2026-06-06T03:00:00Z",
                "metadata": {"tool_name": "fetch_entities"},
                "input": {"query": "genes"},
                "output": {"rows": [1, 2]},
            }
        ]
        extractor_cls.return_value.extract_complete_trace.return_value = trace_data

        payloads = await traces.get_trace_payloads(
            "trace-new-api",
            source="local",
            include_values=False,
            sort="largest",
            limit=10,
            offset=0,
        )
        exact_payload = await traces.get_trace_payload(
            "trace-new-api",
            source="local",
            payload_id=None,
            scope="observation",
            observation_id="obs-tool",
            field="output",
            start=0,
            max_chars=0,
        )
        reconstruction = await traces.get_trace_reconstruction(
            "trace-new-api",
            source="local",
            include_payloads=False,
        )
        costs = await traces.get_trace_costs("trace-new-api", source="local")
        duplicates = await traces.get_trace_duplicate_payloads("trace-new-api", source="local")

        self.assertEqual(payloads["status"], "success")
        self.assertIn("observation:obs-tool:output", {item["payload_id"] for item in payloads["payloads"]})
        self.assertEqual(exact_payload["payload"]["value"], {"rows": [1, 2]})
        self.assertEqual(reconstruction["data"]["events"][1]["kind"], "tool")
        self.assertEqual(costs["data"]["trace_id"], "trace-new-api")
        self.assertEqual(duplicates["data"]["trace_id"], "trace-new-api")

    @patch("src.api.traces.TraceExtractor")
    async def test_reconstruction_ndjson_returns_event_lines(
        self,
        extractor_cls: Mock,
    ):
        trace_data = self._make_trace_data(
            {"answer": "Done"},
            trace_id="trace-ndjson",
            session_id="session-ndjson",
        )
        trace_data["raw_trace"]["input"] = {"question": "inspect"}
        trace_data["observations"] = [
            {
                "id": "obs-model",
                "type": "GENERATION",
                "name": "OpenAI response",
                "startTime": "2026-06-06T03:00:00Z",
                "providedModelName": "gpt-5-mini",
                "input": "prompt",
                "output": "answer",
            }
        ]
        extractor_cls.return_value.extract_complete_trace.return_value = trace_data

        response = await traces.get_trace_reconstruction_ndjson(
            "trace-ndjson",
            source="local",
            include_payloads=False,
        )

        body = response.body.decode("utf-8").strip().splitlines()
        self.assertEqual(response.media_type, "application/x-ndjson")
        self.assertEqual(len(body), 4)
        self.assertIn('"record_type": "trace"', body[0])
        self.assertIn('"record_type": "event"', body[1])


if __name__ == "__main__":
    unittest.main()
