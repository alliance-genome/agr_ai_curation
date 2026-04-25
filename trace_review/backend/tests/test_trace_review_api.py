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

    def _make_trace_data(self, output):
        return {
            "raw_trace": {
                "id": "trace-inflight-1234",
                "name": "chat: Example",
                "timestamp": "2026-03-26T00:00:00Z",
                "metadata": {},
                "output": output,
            },
            "observations": [],
            "scores": [],
            "trace_id_short": "trace-in",
            "metadata": {
                "trace_name": "chat: Example",
                "duration_seconds": 3.5,
                "total_cost": 0.0,
                "total_tokens": 0,
                "observation_count": 0,
                "score_count": 0,
                "timestamp": "2026-03-26T00:00:00Z",
            },
        }

    @patch("src.api.traces.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.api.traces.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TraceSummaryAnalyzer.analyze", return_value={"has_errors": False})
    @patch("src.api.traces.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.api.traces.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": []})
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
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": []})
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
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": []})
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
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": []})
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


if __name__ == "__main__":
    unittest.main()
