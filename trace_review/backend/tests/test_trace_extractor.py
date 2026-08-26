import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.services.trace_extractor import OBSERVATION_FIELDS, TraceExtractor


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

    def _make_trace_list_response(self, payload):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response

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

    @patch("src.services.trace_extractor.requests.get")
    def test_list_session_traces_queries_public_api_with_pagination(self, get: Mock):
        extractor = self._make_extractor()
        get.side_effect = [
            self._make_trace_list_response({
                "data": [{"id": "trace-1", "name": "first"}],
                "meta": {"page": 1, "limit": 1, "totalItems": 2, "totalPages": 2},
            }),
            self._make_trace_list_response({
                "data": [{"id": "trace-2", "name": "second"}],
                "meta": {"page": 2, "limit": 1, "totalItems": 2, "totalPages": 2},
            }),
        ]

        result = extractor.list_session_traces("session-1", limit=1)

        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["source"], "remote")
        self.assertEqual([trace["id"] for trace in result["traces"]], ["trace-1", "trace-2"])
        self.assertEqual(get.call_count, 2)

        first_call = get.call_args_list[0]
        second_call = get.call_args_list[1]
        self.assertEqual(first_call.args[0], "https://langfuse.example/api/public/traces")
        self.assertEqual(first_call.kwargs["params"], {
            "sessionId": "session-1",
            "limit": 1,
            "page": 1,
            "orderBy": "timestamp.asc",
        })
        self.assertEqual(second_call.kwargs["params"]["page"], 2)
        self.assertEqual(first_call.kwargs["auth"].username, "pk-test")
        self.assertEqual(first_call.kwargs["auth"].password, extractor.secret_key)

    @patch("src.services.trace_extractor.requests.get")
    def test_list_session_traces_preserves_zero_total_pages(self, get: Mock):
        extractor = self._make_extractor()
        get.return_value = self._make_trace_list_response({
            "data": [],
            "meta": {"page": 1, "limit": 100, "totalItems": 0, "totalPages": 0},
        })

        result = extractor.list_session_traces("session-empty")

        self.assertEqual(result["traces"], [])
        self.assertEqual(result["meta"]["totalPages"], 0)
        self.assertEqual(get.call_count, 1)

    @patch("src.services.trace_extractor.requests.get")
    def test_list_session_traces_error_message_omits_credentials(self, get: Mock):
        extractor = self._make_extractor()
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        get.return_value = response

        with self.assertRaises(RuntimeError) as error:
            extractor.list_session_traces("session-1")

        self.assertIn("session-1", str(error.exception))
        self.assertIn("remote", str(error.exception))
        self.assertNotIn(extractor.public_key, str(error.exception))
        self.assertNotIn(extractor.secret_key, str(error.exception))


if __name__ == "__main__":
    unittest.main()
