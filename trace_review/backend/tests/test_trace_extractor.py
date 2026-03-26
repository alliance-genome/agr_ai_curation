import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.services.trace_extractor import TraceExtractor, OBSERVATION_FIELDS


class TraceExtractorTests(unittest.TestCase):
    def _make_extractor(self) -> TraceExtractor:
        extractor = object.__new__(TraceExtractor)
        extractor.client = Mock()
        extractor.client.api = Mock()
        extractor.client.api.observations = Mock()
        extractor.client.api.scores = Mock()
        return extractor

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


if __name__ == "__main__":
    unittest.main()
