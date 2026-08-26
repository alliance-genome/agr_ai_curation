import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.services.trace_extractor import (
    OBSERVATION_FIELDS,
    SESSION_OBSERVATION_FIELDS,
    TraceExtractor,
)


class TraceExtractorTests(unittest.TestCase):
    def _make_extractor(self) -> TraceExtractor:
        extractor = object.__new__(TraceExtractor)
        credentials = {"public": "pk-test", "private": "unit-test-credential"}
        extractor.source = "remote"
        extractor.host = "https://langfuse.example"
        extractor.public_key = credentials["public"]
        extractor.secret_key = credentials["private"]
        extractor.client = Mock()
        extractor.client.api = Mock()
        extractor.client.api.trace = Mock()
        extractor.client.api.observations = Mock()
        extractor.client.api.scores = Mock()
        return extractor

    def test_extract_complete_trace_reconstructs_context_and_accounting_from_v2(self):
        extractor = self._make_extractor()
        observations = [{
            "id": "root-1",
            "trace_id": "trace-12345678",
            "type": "GENERATION",
            "name": "OpenAI response",
            "trace_name": "trace name",
            "session_id": "session-1",
            "user_id": "user-1",
            "is_root_observation": True,
            "start_time": "2026-03-25T23:00:00Z",
            "latency": 1.25,
            "input": {"question": "test"},
            "output": {"answer": "done"},
            "usage_details": {"input": 10, "output": 2, "total": 12},
            "cost_details": {"total": 0.75},
        }]
        scores = [{"id": "score-1", "name": "quality"}]
        extractor.get_observations = Mock(return_value=observations)
        extractor.get_scores = Mock(return_value=scores)

        data = extractor.extract_complete_trace("trace-12345678")

        self.assertEqual(data["raw_trace"]["name"], "trace name")
        self.assertEqual(data["raw_trace"]["sessionId"], "session-1")
        self.assertEqual(data["observations"], observations)
        self.assertEqual(data["scores"], scores)
        self.assertEqual(data["metadata"]["total_tokens"], 12)
        self.assertEqual(data["metadata"]["total_cost"], 0.75)
        self.assertEqual(data["metadata"]["observation_count"], 1)
        self.assertEqual(data["metadata"]["score_count"], 1)

    def test_extract_complete_trace_metadata_includes_domain_envelope_signals(self):
        extractor = self._make_extractor()
        observations = [{
            "id": "root-domain",
            "trace_name": "domain trace",
            "is_root_observation": True,
            "start_time": "2026-03-25T23:00:00Z",
            "output": {
                "envelope_id": "env-domain-1",
                "domain_pack_id": "agr.test.gene",
                "objects": [
                    {
                        "object_id": "gene-expression-object-1",
                        "object_type": "gene_expression",
                        "payload": {"gene": {"symbol": "tmem67"}},
                    }
                ],
            },
        }]
        extractor.get_observations = Mock(return_value=observations)
        extractor.get_scores = Mock(return_value=[])

        data = extractor.extract_complete_trace("trace-domain")

        domain = data["metadata"]["domain_envelope"]
        self.assertTrue(domain["found"])
        self.assertEqual(domain["envelope_ids"], ["env-domain-1"])
        self.assertEqual(domain["object_ids"], ["gene-expression-object-1"])
        self.assertEqual(domain["summary"]["object_count"], 1)

    @patch("src.services.trace_extractor.get_langfuse_request_timeout_seconds", return_value=9)
    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=2)
    def test_get_observations_uses_v2_fields_paginates_and_deduplicates(
        self,
        _page_limit: Mock,
        _timeout: Mock,
    ):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(
                data=[SimpleNamespace(model_dump=lambda: {
                    "id": "obs-1",
                    "trace_id": "trace-1",
                    "parent_observation_id": None,
                    "start_time": "2026-08-26T12:00:00Z",
                    "internal_model_id": "managed-model-1",
                    "usage_details": {"input": 10, "output": 2, "total": 12},
                    "cost_details": {"total": 0.03},
                })],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
            SimpleNamespace(
                data=[{"id": "obs-1"}, {"id": "obs-2", "name": "second"}],
                meta=SimpleNamespace(cursor=None),
            ),
        ]

        observations = extractor.get_observations("trace-1")

        self.assertEqual(extractor.client.api.observations.get_many.call_count, 2)
        extractor.client.api.observations.get_many.assert_any_call(
            trace_id="trace-1",
            fields=OBSERVATION_FIELDS,
            limit=2,
            cursor=None,
            request_options={"timeout_in_seconds": 9},
        )
        extractor.client.api.observations.get_many.assert_any_call(
            trace_id="trace-1",
            fields=OBSERVATION_FIELDS,
            limit=2,
            cursor="next-cursor",
            request_options={"timeout_in_seconds": 9},
        )
        self.assertEqual([item["id"] for item in observations], ["obs-1", "obs-2"])
        self.assertEqual(observations[0]["traceId"], "trace-1")
        self.assertEqual(observations[0]["startTime"], "2026-08-26T12:00:00Z")
        self.assertEqual(observations[0]["usage"], {"input": 10, "output": 2, "total": 12})
        self.assertEqual(observations[0]["calculatedTotalCost"], 0.03)
        self.assertEqual(observations[0]["model"], "managed-model-1")

    def test_get_trace_details_requires_v2_observations(self):
        with self.assertRaisesRegex(RuntimeError, "no v2 observations"):
            TraceExtractor.get_trace_details("trace-1", [])

    def test_list_traces_uses_metadata_filters(self):
        extractor = self._make_extractor()
        extractor.client.api.trace.list.return_value = SimpleNamespace(
            data=[SimpleNamespace(dict=lambda: {"id": "trace-1", "name": "run"})],
            meta=SimpleNamespace(dict=lambda: {"page": 1, "totalPages": 1}),
        )

        result = extractor.list_traces(
            session_id="session-1",
            document_id="doc-1",
            run_id="run-1",
            limit=10,
        )

        self.assertEqual(result["source"], "remote")
        self.assertEqual(result["traces"], [{"id": "trace-1", "name": "run"}])
        call = extractor.client.api.trace.list.call_args
        self.assertEqual(call.kwargs["session_id"], "session-1")
        self.assertEqual(call.kwargs["limit"], 10)
        self.assertEqual(call.kwargs["order_by"], "timestamp.asc")
        self.assertIn('"key": "document_id"', call.kwargs["filter"])
        self.assertIn('"value": "doc-1"', call.kwargs["filter"])
        self.assertIn('"key": "run_id"', call.kwargs["filter"])

    def test_get_observations_rejects_repeated_cursor(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(
                data=[{"id": "obs-1"}],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
            SimpleNamespace(
                data=[{"id": "obs-1"}],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
        ]

        with self.assertRaisesRegex(RuntimeError, "repeated observation cursor"):
            extractor.get_observations("trace-1")

    @patch("src.services.trace_extractor.get_langfuse_request_timeout_seconds", return_value=7)
    def test_get_scores_uses_configured_timeout(self, _timeout: Mock):
        extractor = self._make_extractor()
        extractor.client.api.scores.get_many.return_value = SimpleNamespace(
            data=[SimpleNamespace(dict=lambda: {"id": "score-1", "name": "quality"})]
        )

        scores = extractor.get_scores("trace-1")

        extractor.client.api.scores.get_many.assert_called_once_with(
            trace_id="trace-1",
            request_options={"timeout_in_seconds": 7},
        )
        self.assertEqual(scores, [{"id": "score-1", "name": "quality"}])

    @patch("src.services.trace_extractor.get_langfuse_request_timeout_seconds", return_value=8)
    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=50)
    def test_list_session_traces_uses_v2_filter_pagination_and_stable_roots(
        self,
        _page_limit: Mock,
        _timeout: Mock,
    ):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(
                data=[{
                    "id": "child-1",
                    "trace_id": "trace-1",
                    "parent_observation_id": "root-1",
                    "start_time": "2026-08-26T12:00:02Z",
                    "session_id": "session-1",
                }],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
            SimpleNamespace(
                data=[
                    {
                        "id": "root-2",
                        "trace_id": "trace-2",
                        "start_time": "2026-08-26T12:00:03Z",
                        "trace_name": "second",
                        "session_id": "session-1",
                    },
                    {
                        "id": "root-1",
                        "trace_id": "trace-1",
                        "start_time": "2026-08-26T12:00:01Z",
                        "trace_name": "first",
                        "session_id": "session-1",
                    },
                ],
                meta=SimpleNamespace(cursor=None),
            ),
        ]

        result = extractor.list_session_traces("session-1", limit=1)

        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["source"], "remote")
        self.assertEqual([trace["id"] for trace in result["traces"]], ["trace-1", "trace-2"])
        self.assertEqual(result["traces"][0]["name"], "first")
        self.assertEqual(result["meta"], {
            "page": 2,
            "limit": 1,
            "totalItems": 2,
            "totalPages": 2,
        })
        calls = extractor.client.api.observations.get_many.call_args_list
        self.assertEqual(calls[0].kwargs["fields"], SESSION_OBSERVATION_FIELDS)
        self.assertEqual(calls[0].kwargs["cursor"], None)
        self.assertEqual(calls[1].kwargs["cursor"], "next-cursor")
        self.assertEqual(calls[0].kwargs["limit"], 1)
        self.assertEqual(calls[0].kwargs["request_options"], {"timeout_in_seconds": 8})
        self.assertIn('"column": "sessionId"', calls[0].kwargs["filter"])
        self.assertIn('"value": "session-1"', calls[0].kwargs["filter"])

    def test_list_session_traces_preserves_empty_result(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[],
            meta=SimpleNamespace(cursor=None),
        )

        result = extractor.list_session_traces("session-empty")

        self.assertEqual(result["traces"], [])
        self.assertEqual(result["meta"]["totalItems"], 0)
        self.assertEqual(result["meta"]["totalPages"], 1)

    def test_list_session_traces_rejects_repeated_cursor(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(data=[], meta=SimpleNamespace(cursor="same-cursor")),
            SimpleNamespace(data=[], meta=SimpleNamespace(cursor="same-cursor")),
        ]

        with self.assertRaisesRegex(RuntimeError, "repeated session observation cursor"):
            extractor.list_session_traces("session-1")

    def test_list_session_traces_error_message_omits_credentials(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = ValueError(
            f"bad credentials {extractor.public_key} {extractor.secret_key}"
        )

        with self.assertRaises(RuntimeError) as error:
            extractor.list_session_traces("session-1")

        self.assertIn("session-1", str(error.exception))
        self.assertIn("remote", str(error.exception))
        self.assertNotIn(extractor.public_key, str(error.exception))
        self.assertNotIn(extractor.secret_key, str(error.exception))


if __name__ == "__main__":
    unittest.main()
