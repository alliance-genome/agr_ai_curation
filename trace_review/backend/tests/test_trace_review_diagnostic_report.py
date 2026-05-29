import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.analyzers.extraction_timeline import ExtractionTimelineAnalyzer
from src.api import claude, traces
from src.models.requests import AnalyzeTraceRequest
from src.services.cache_manager import CacheManager


class ExtractionDiagnosticReportTests(unittest.IsolatedAsyncioTestCase):
    def _make_request(self) -> SimpleNamespace:
        cache_manager = CacheManager(ttl_hours=1)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cache_manager=cache_manager)))

    def _make_trace_data(self, trace_id="trace-extraction-123"):
        return {
            "raw_trace": {
                "id": trace_id,
                "name": "gene expression extraction",
                "timestamp": "2026-05-29T00:00:00Z",
                "input": {"message": "Extract gene expression."},
                "metadata": {},
                "output": {"answer": "done"},
            },
            "observations": [],
            "scores": [],
            "trace_id_short": trace_id[:8],
            "metadata": {
                "trace_name": "gene expression extraction",
                "duration_seconds": 2.0,
                "total_cost": 0.0,
                "total_tokens": 0,
                "observation_count": 0,
                "score_count": 0,
                "timestamp": "2026-05-29T00:00:00Z",
            },
        }

    def test_diagnostic_report_summarizes_reasoning_validation_and_timeline(self):
        timeline = {
            "schema_version": "extraction_timeline_analyzer.v1",
            "trace_id": "trace-report",
            "event_count": 3,
            "durable_event_count": 2,
            "reasoning_summary": {
                "status": "present",
                "summaries": ["Checked evidence and resolver output."],
            },
            "timeline": [
                {"event_type": "model.reasoning_summary.output", "validation": {}},
                {"event_type": "specialist_tool_call.completed", "validation": {"status": "ok"}},
                {
                    "event_type": "validation.failure",
                    "validation": {"status": "needs_patch", "errors": [{"message": "missing evidence"}]},
                },
            ],
        }

        report = ExtractionTimelineAnalyzer.diagnostic_report(timeline)

        self.assertEqual(report["summary"]["event_count"], 3)
        self.assertEqual(report["summary"]["tool_event_count"], 1)
        self.assertEqual(report["summary"]["validation_failure_count"], 1)
        self.assertEqual(report["summary"]["reasoning_summary_status"], "present")
        self.assertEqual(len(report["validation_failures"]), 1)
        self.assertEqual(report["timeline"], timeline["timeline"])

    @patch("src.api.traces.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.api.traces.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch(
        "src.api.traces.TraceSummaryAnalyzer.analyze",
        return_value={"has_errors": False, "domain_envelope": {"found": False, "summary": {}}},
    )
    @patch("src.api.traces.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.api.traces.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.api.traces.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.traces.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": [], "duplicates": {}})
    @patch("src.api.traces.ConversationAnalyzer.extract_conversation", return_value={"user_input": "Question", "assistant_response": "N/A"})
    @patch("src.api.traces.TraceExtractor")
    async def test_trace_review_endpoint_renders_extraction_timeline_and_refreshes_stale_cache(
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
        request.app.state.cache_manager.set(
            "trace-extraction-123",
            {
                "analyzer_schema_version": "old",
                "analysis": {"summary": {"trace_id": "trace-extraction-123"}},
            },
        )
        extractor_cls.return_value.extract_complete_trace.return_value = self._make_trace_data()

        analyze_response = await traces.analyze_trace(
            AnalyzeTraceRequest(trace_id="trace-extraction-123", source="auto"),
            request,
        )
        view_response = await traces.get_trace_view(
            "trace-extraction-123",
            "extraction_timeline",
            request,
            source="auto",
            refresh=True,
        )

        extractor_cls.assert_called_with(source="local")
        self.assertEqual(analyze_response["status"], "success")
        self.assertEqual(view_response["view"], "extraction_timeline")
        self.assertEqual(view_response["data"]["schema_version"], "extraction_timeline_analyzer.v1")
        self.assertEqual(view_response["data"]["reasoning_summary"]["status"], "unavailable")

    @patch("src.analyzers.agent_config.AgentConfigAnalyzer.extract_agent_configs", return_value={})
    @patch("src.analyzers.document_hierarchy.DocumentHierarchyAnalyzer.analyze", return_value={})
    @patch(
        "src.api.claude.TraceSummaryAnalyzer.analyze",
        return_value={"has_errors": False, "domain_envelope": {"found": False, "summary": {}}},
    )
    @patch("src.analyzers.agent_context.AgentContextAnalyzer.analyze", return_value={})
    @patch("src.analyzers.token_analysis.TokenAnalysisAnalyzer.analyze", return_value={})
    @patch("src.analyzers.pdf_citations.PDFCitationsAnalyzer.analyze", return_value={})
    @patch("src.api.claude.ToolCallAnalyzer.extract_tool_calls", return_value={"total_count": 0, "unique_tools": [], "tool_calls": [], "duplicates": {}})
    @patch("src.api.claude.ConversationAnalyzer.extract_conversation", return_value={"user_input": "Question", "assistant_response": "N/A"})
    @patch("src.api.claude.TraceExtractor")
    async def test_claude_diagnostic_report_endpoint_returns_token_metadata(
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
        extractor_cls.return_value.extract_complete_trace.return_value = self._make_trace_data()

        response = await claude.get_extraction_diagnostic_report(
            "trace-extraction-123",
            request,
            source="auto",
            session_id=None,
            include_sibling_traces=False,
        )

        extractor_cls.assert_called_once_with(source="local")
        self.assertEqual(response.status, "success")
        self.assertEqual(response.data["trace_id"], "trace-extraction-123")
        self.assertEqual(response.data["summary"]["reasoning_summary_status"], "unavailable")
        self.assertGreaterEqual(response.token_info.estimated_tokens, 1)

    @patch(
        "src.api.claude.fetch_feedback_trace_artifacts",
        return_value={
            "status": "available",
            "trace_data": {
                "captured_at": "2026-05-29T00:00:00Z",
                "traces": [
                    {
                        "trace_id": "trace-extraction-123",
                        "timestamp": "2026-05-29T00:00:01Z",
                        "tool_calls": [
                            {
                                "name": "resolve_domain_field_term",
                                "duration_ms": 11,
                                "status": "ok",
                            }
                        ],
                    }
                ],
            },
        },
    )
    @patch("src.api.claude.TraceExtractor")
    async def test_claude_extraction_timeline_uses_feedback_artifacts_when_langfuse_unavailable(
        self,
        extractor_cls: Mock,
        _feedback_artifacts: Mock,
    ):
        request = self._make_request()
        extractor_cls.return_value.extract_complete_trace.side_effect = RuntimeError("langfuse down")

        response = await claude.get_extraction_timeline(
            "trace-extraction-123",
            request,
            source="auto",
            feedback_id="feedback-123",
            include_sibling_traces=False,
            include_raw_args=False,
            include_raw_outputs=False,
            tool_name=None,
            event_type=None,
            candidate_id=None,
        )

        self.assertEqual(response.status, "success")
        self.assertEqual(response.data["feedback_artifact_event_count"], 1)
        self.assertEqual(response.data["query"]["feedback_artifact_status"], "available")
        self.assertEqual(response.data["timeline"][0]["tool_name"], "resolve_domain_field_term")
