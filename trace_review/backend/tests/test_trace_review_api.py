import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.api import traces
from src.models.requests import AnalyzeTraceRequest
from src.services.cache_manager import CacheManager


class TraceReviewApiTests(unittest.IsolatedAsyncioTestCase):
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
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value={"has_errors": False})
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
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value={"has_errors": False})
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
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value={"has_errors": False})
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
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value={"has_errors": False})
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
            "meta": {"page": 1, "limit": 100, "totalItems": 2, "totalPages": 1},
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
            "meta": {"page": 1, "limit": 100, "totalItems": 2, "totalPages": 1},
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
        self.assertIn("trace not found", response["traces"][1]["error"]["message"])
        self.assertEqual(response["errors"][0]["trace_id"], "trace-missing")

    @patch("src.api.traces.TraceExtractor")
    async def test_export_session_bundle_surfaces_compact_bundle_cache_errors(
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
            "meta": {"page": 1, "limit": 100, "totalItems": 1, "totalPages": 1},
        }

        with self.assertRaises(KeyError):
            await traces.export_session("session-123", request, source="remote")

        extractor.extract_complete_trace.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
