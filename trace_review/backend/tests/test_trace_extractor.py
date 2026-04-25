import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.services.trace_extractor import TraceExtractor, OBSERVATION_FIELDS


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
        extractor.client.api.observations = Mock()
        extractor.client.api.scores = Mock()
        return extractor

    def _make_trace_list_response(self, payload):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        return response

    def test_extract_complete_trace_prefers_embedded_observations_and_scores(self):
        extractor = self._make_extractor()
        trace = {
            "id": "trace-12345678",
            "name": "trace name",
            "latency": 1.25,
            "timestamp": "2026-03-25T23:00:00Z",
            "observations": [
                {
                    "id": "obs-1",
                    "usage": {"total": 12},
                    "calculatedTotalCost": 0.75,
                }
            ],
            "scores": [{"id": "score-1", "name": "quality"}],
        }

        extractor.get_trace_details = Mock(return_value=trace)
        extractor.client.api.observations.get_many.side_effect = AssertionError(
            "embedded observations should avoid get_many"
        )
        extractor.client.api.scores.get_many.side_effect = AssertionError(
            "embedded scores should avoid get_many"
        )

        data = extractor.extract_complete_trace("trace-12345678")

        self.assertEqual(data["observations"], trace["observations"])
        self.assertEqual(data["scores"], trace["scores"])
        self.assertEqual(data["metadata"]["total_tokens"], 12)
        self.assertEqual(data["metadata"]["total_cost"], 0.75)
        self.assertEqual(data["metadata"]["observation_count"], 1)
        self.assertEqual(data["metadata"]["score_count"], 1)

    def test_get_observations_falls_back_to_get_many(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(dict=lambda: {"id": "obs-1", "name": "first"}),
                {"id": "obs-2", "name": "second"},
            ]
        )

        observations = extractor.get_observations("trace-1", trace={"id": "trace-1"})

        extractor.client.api.observations.get_many.assert_called_once_with(
            trace_id="trace-1",
            fields=OBSERVATION_FIELDS,
            limit=1000,
            cursor=None,
        )
        self.assertEqual(
            observations,
            [
                {"id": "obs-1", "name": "first"},
                {"id": "obs-2", "name": "second"},
            ],
        )

    def test_get_observations_paginates_cursor_results(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(
                data=[SimpleNamespace(dict=lambda: {"id": "obs-1"})],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
            SimpleNamespace(
                data=[SimpleNamespace(dict=lambda: {"id": "obs-2"})],
                meta=SimpleNamespace(cursor=None),
            ),
        ]

        observations = extractor.get_observations("trace-1", trace={"id": "trace-1"})

        self.assertEqual(observations, [{"id": "obs-1"}, {"id": "obs-2"}])
        self.assertEqual(extractor.client.api.observations.get_many.call_count, 2)
        first_call = extractor.client.api.observations.get_many.call_args_list[0]
        second_call = extractor.client.api.observations.get_many.call_args_list[1]
        self.assertEqual(first_call.kwargs["cursor"], None)
        self.assertEqual(second_call.kwargs["cursor"], "next-cursor")

    def test_get_scores_falls_back_to_scores_client(self):
        extractor = self._make_extractor()
        extractor.client.api.scores.get_many.return_value = SimpleNamespace(
            data=[SimpleNamespace(dict=lambda: {"id": "score-1", "name": "quality"})]
        )

        scores = extractor.get_scores("trace-1", trace={"id": "trace-1"})

        extractor.client.api.scores.get_many.assert_called_once_with(trace_id="trace-1")
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
