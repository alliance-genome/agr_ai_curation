import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.services.trace_extractor import (
    OBSERVATION_FIELDS,
    SESSION_OBSERVATION_FIELDS,
    TRACE_LIST_OBSERVATION_FIELDS,
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
            "project_id": "project-1",
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
        self.assertEqual(data["metadata"]["duration_seconds"], 1.25)
        self.assertEqual(
            data["raw_trace"]["htmlPath"],
            "/project/project-1/traces/trace-12345678",
        )
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
                data=[
                    {"id": "obs-1", "trace_id": "trace-1"},
                    {"id": "obs-2", "name": "second", "trace_id": "trace-1"},
                ],
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

    def test_get_observations_rejects_missing_or_mismatched_trace_identity(self):
        for returned_trace_id in (None, "other-trace"):
            with self.subTest(returned_trace_id=returned_trace_id):
                extractor = self._make_extractor()
                observation = {"id": "obs-1"}
                if returned_trace_id is not None:
                    observation["trace_id"] = returned_trace_id
                extractor.client.api.observations.get_many.return_value = SimpleNamespace(
                    data=[observation],
                    meta=SimpleNamespace(cursor=None),
                )

                with self.assertRaisesRegex(RuntimeError, "outside requested trace"):
                    extractor.get_observations("trace-1")

    @patch("src.services.trace_extractor.get_langfuse_request_timeout_seconds", return_value=9)
    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=2)
    @patch("src.services.trace_extractor.get_langfuse_search_observation_limit", return_value=10)
    def test_list_traces_uses_v2_filters_pagination_and_deduplication(
        self,
        _search_limit: Mock,
        _page_limit: Mock,
        _timeout: Mock,
    ):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(
                data=[
                    {
                        "id": "obs-2",
                        "trace_id": "trace-1",
                        "parent_observation_id": "obs-1",
                        "project_id": "project-1",
                        "trace_name": "run",
                        "session_id": "session-1",
                        "user_id": "user-1",
                        "start_time": "2026-09-03T19:00:01Z",
                        "end_time": "2026-09-03T19:00:03Z",
                        "cost_details": {"total": 0.02},
                        "metadata": {"document_id": "doc-1", "run_id": "run-1"},
                    },
                    {
                        "id": "wrong-session",
                        "trace_id": "wrong-trace",
                        "trace_name": "run",
                        "session_id": "other-session",
                        "user_id": "user-1",
                        "metadata": {"document_id": "doc-1", "run_id": "run-1"},
                    },
                ],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
            SimpleNamespace(
                data=[
                    {
                        "id": "obs-1",
                        "trace_id": "trace-1",
                        "project_id": "project-1",
                        "trace_name": "run",
                        "session_id": "session-1",
                        "user_id": "user-1",
                        "start_time": "2026-09-03T19:00:00Z",
                        "end_time": "2026-09-03T19:00:05Z",
                        "cost_details": {"total": 0.01},
                        "metadata": {"document_id": "doc-1", "run_id": "run-1"},
                    },
                    {
                        "id": "obs-3",
                        "trace_id": "trace-2",
                        "project_id": "project-1",
                        "trace_name": "run",
                        "session_id": "session-1",
                        "user_id": "user-1",
                        "start_time": "2026-09-03T18:00:00Z",
                        "end_time": "2026-09-03T18:00:02Z",
                        "cost_details": {"total": 0.04},
                        "metadata": {"document_id": "doc-1", "run_id": "run-1"},
                    },
                ],
                meta=SimpleNamespace(cursor=None),
            ),
        ]
        extractor._get_observations_bounded = Mock(side_effect=[
            ([
                {
                    "id": "obs-2",
                    "traceId": "trace-1",
                    "parentObservationId": "obs-1",
                    "projectId": "project-1",
                    "trace_name": "run",
                    "session_id": "session-1",
                    "user_id": "user-1",
                    "startTime": "2026-09-03T19:00:01Z",
                    "endTime": "2026-09-03T19:00:03Z",
                    "costDetails": {"total": 0.02},
                    "metadata": {"document_id": "doc-1", "run_id": "run-1"},
                },
                {
                    "id": "obs-1",
                    "traceId": "trace-1",
                    "projectId": "project-1",
                    "trace_name": "run",
                    "session_id": "session-1",
                    "user_id": "user-1",
                    "startTime": "2026-09-03T19:00:00Z",
                    "endTime": "2026-09-03T19:00:05Z",
                    "costDetails": {"total": 0.01},
                    "metadata": {"document_id": "doc-1", "run_id": "run-1"},
                },
            ], 1, True),
            ([{
                "id": "obs-3",
                "traceId": "trace-2",
                "projectId": "project-1",
                "trace_name": "run",
                "session_id": "session-1",
                "user_id": "user-1",
                "startTime": "2026-09-03T18:00:00Z",
                "endTime": "2026-09-03T18:00:02Z",
                "costDetails": {"total": 0.04},
                "metadata": {"document_id": "doc-1", "run_id": "run-1"},
            }], 1, True),
        ])

        result = extractor.list_traces(
            session_id="session-1",
            user_id="user-1",
            name="run",
            document_id="doc-1",
            run_id="run-1",
            limit=10,
        )

        self.assertEqual(result["source"], "remote")
        self.assertEqual([trace["id"] for trace in result["traces"]], ["trace-2", "trace-1"])
        self.assertEqual(result["traces"][0]["latency"], 2.0)
        self.assertEqual(result["traces"][0]["totalCost"], 0.04)
        self.assertEqual(
            result["traces"][0]["htmlPath"],
            "/project/project-1/traces/trace-2",
        )
        self.assertEqual(result["traces"][1]["latency"], 5.0)
        self.assertAlmostEqual(result["traces"][1]["totalCost"], 0.03)
        self.assertEqual(result["meta"]["observationsRejected"], 1)
        self.assertTrue(result["source_exhausted"])
        self.assertFalse(result["scan_truncated"])
        calls = extractor.client.api.observations.get_many.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["fields"], TRACE_LIST_OBSERVATION_FIELDS)
        self.assertEqual(calls[0].kwargs["cursor"], None)
        self.assertEqual(calls[1].kwargs["cursor"], "next-cursor")
        self.assertNotIn("user_id", calls[0].kwargs)
        self.assertNotIn("from_start_time", calls[0].kwargs)
        self.assertNotIn("to_start_time", calls[0].kwargs)
        self.assertEqual(calls[0].kwargs["request_options"], {"timeout_in_seconds": 9})
        self.assertIn('"column": "sessionId"', calls[0].kwargs["filter"])
        self.assertIn('"column": "userId"', calls[0].kwargs["filter"])
        self.assertIn('"column": "traceName"', calls[0].kwargs["filter"])
        self.assertIn('"key": "document_id"', calls[0].kwargs["filter"])
        extractor.client.api.trace.list.assert_not_called()

    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=100)
    @patch("src.services.trace_extractor.get_langfuse_search_observation_limit", return_value=150)
    def test_list_traces_offset_uses_unique_trace_order(self, _search_limit: Mock, _page_limit: Mock):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[
                {
                    "id": f"obs-{index}",
                    "trace_id": f"trace-{index}",
                    "start_time": f"2026-09-03T18:{index:02d}:00Z",
                }
                for index in range(20)
            ],
            meta=SimpleNamespace(cursor=None),
        )

        result = extractor.list_traces(offset=10, limit=5)

        self.assertEqual(
            [trace["id"] for trace in result["traces"]],
            [f"trace-{index}" for index in range(10, 15)],
        )
        self.assertEqual(result["total_items"], 20)
        self.assertTrue(result["source_exhausted"])

    @patch("src.services.trace_extractor.get_langfuse_request_timeout_seconds", return_value=9)
    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=10)
    @patch("src.services.trace_extractor.get_langfuse_search_observation_limit", return_value=10)
    def test_list_traces_composes_and_rechecks_user_and_time_filters(
        self,
        _search_limit: Mock,
        _page_limit: Mock,
        _timeout: Mock,
    ):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[
                {
                    "id": "matching-child",
                    "trace_id": "trace-1",
                    "parent_observation_id": "root-1",
                    "trace_name": "run",
                    "user_id": "user-1",
                    "start_time": "2026-09-03T12:30:00Z",
                },
                {
                    "id": "outside-window",
                    "trace_id": "trace-outside",
                    "trace_name": "run",
                    "user_id": "user-1",
                    "start_time": "2026-09-03T14:00:00Z",
                },
            ],
            meta=SimpleNamespace(cursor=None),
        )
        extractor._get_observations_bounded = Mock(return_value=([{
            "id": "root-1",
            "traceId": "trace-1",
            "projectId": "project-1",
            "trace_name": "run",
            "user_id": "user-1",
            "startTime": "2026-09-03T12:00:00Z",
            "endTime": "2026-09-03T12:45:00Z",
        }], 1, True))
        start = datetime.fromisoformat("2026-09-03T12:00:00+00:00")
        end = datetime.fromisoformat("2026-09-03T13:00:00+00:00")

        result = extractor.list_traces(
            user_id="user-1",
            name="run",
            from_timestamp=start,
            to_timestamp=end,
            limit=10,
        )

        call = extractor.client.api.observations.get_many.call_args
        filters = json.loads(call.kwargs["filter"])
        self.assertIn(
            {"type": "string", "column": "userId", "operator": "=", "value": "user-1"},
            filters,
        )
        self.assertIn(
            {
                "type": "datetime",
                "column": "startTime",
                "operator": ">=",
                "value": start.isoformat(),
            },
            filters,
        )
        self.assertIn(
            {
                "type": "datetime",
                "column": "startTime",
                "operator": "<",
                "value": end.isoformat(),
            },
            filters,
        )
        self.assertNotIn("user_id", call.kwargs)
        self.assertNotIn("from_start_time", call.kwargs)
        self.assertNotIn("to_start_time", call.kwargs)
        self.assertEqual([trace["id"] for trace in result["traces"]], ["trace-1"])
        self.assertEqual(result["meta"]["observationsRejected"], 1)

    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=10)
    @patch("src.services.trace_extractor.get_langfuse_search_observation_limit", return_value=10)
    def test_list_traces_rejects_child_match_when_root_timestamp_is_outside_window(
        self,
        _search_limit: Mock,
        _page_limit: Mock,
    ):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[{
                "id": "matching-child",
                "trace_id": "trace-1",
                "parent_observation_id": "root-1",
                "start_time": "2026-09-03T12:30:00Z",
            }],
            meta=SimpleNamespace(cursor=None),
        )
        extractor._get_observations_bounded = Mock(return_value=([
            {
                "id": "root-1",
                "traceId": "trace-1",
                "startTime": "2026-09-03T10:00:00Z",
                "endTime": "2026-09-03T10:30:00Z",
            },
            {
                "id": "matching-child",
                "traceId": "trace-1",
                "parentObservationId": "root-1",
                "startTime": "2026-09-03T12:30:00Z",
            },
        ], 1, True))

        result = extractor.list_traces(
            from_timestamp=datetime.fromisoformat("2026-09-03T12:00:00+00:00"),
            to_timestamp=datetime.fromisoformat("2026-09-03T13:00:00+00:00"),
            limit=10,
        )

        self.assertEqual(result["traces"], [])
        self.assertEqual(result["meta"]["tracesRejected"], 1)

    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=2)
    @patch("src.services.trace_extractor.get_langfuse_search_observation_limit", return_value=2)
    def test_list_traces_surfaces_provider_scan_truncation(self, _search_limit: Mock, _page_limit: Mock):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[
                {"id": "obs-1", "trace_id": "trace-1"},
                {"id": "obs-2", "trace_id": "trace-1"},
            ],
            meta=SimpleNamespace(cursor="more"),
        )
        extractor._get_observations_bounded = Mock(return_value=([
            {"id": "obs-1", "traceId": "trace-1", "startTime": "2026-09-03T18:00:00Z"},
            {
                "id": "obs-2",
                "traceId": "trace-1",
                "parentObservationId": "obs-1",
                "startTime": "2026-09-03T18:00:01Z",
            },
        ], 1, True))

        result = extractor.list_traces(limit=2)

        self.assertTrue(result["scan_truncated"])
        self.assertFalse(result["source_exhausted"])
        self.assertTrue(result["meta"]["scanTruncated"])

    @patch("src.services.trace_extractor.get_langfuse_search_request_limit", return_value=3)
    @patch("src.services.trace_extractor.get_langfuse_observation_page_limit", return_value=10)
    @patch("src.services.trace_extractor.get_langfuse_search_observation_limit", return_value=100)
    def test_list_traces_caps_discovery_and_hydration_requests(
        self,
        _search_limit: Mock,
        _page_limit: Mock,
        _request_limit: Mock,
    ):
        extractor = self._make_extractor()

        def get_many(**kwargs):
            trace_id = kwargs.get("trace_id")
            if trace_id:
                return SimpleNamespace(
                    data=[{
                        "id": f"root-{trace_id}",
                        "trace_id": trace_id,
                        "start_time": "2026-09-03T18:00:00Z",
                    }],
                    meta=SimpleNamespace(cursor=None),
                )
            return SimpleNamespace(
                data=[
                    {
                        "id": f"child-{index}",
                        "trace_id": f"trace-{index}",
                        "parent_observation_id": f"root-trace-{index}",
                        "start_time": f"2026-09-03T18:00:0{index}Z",
                    }
                    for index in range(4)
                ],
                meta=SimpleNamespace(cursor=None),
            )

        extractor.client.api.observations.get_many.side_effect = get_many

        result = extractor.list_traces(limit=4)

        self.assertEqual(extractor.client.api.observations.get_many.call_count, 3)
        self.assertEqual(result["meta"]["requestsMade"], 3)
        self.assertEqual(result["meta"]["requestLimit"], 3)
        self.assertTrue(result["meta"]["hydrationTruncated"])
        self.assertTrue(result["scan_truncated"])
        self.assertIsNone(result["total_items"])
        self.assertEqual(result["local_result_count"], 2)

    def test_list_traces_rejects_empty_page_with_cursor(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[],
            meta=SimpleNamespace(cursor="unexpected-continuation"),
        )

        with self.assertRaisesRegex(RuntimeError, "empty trace-search page"):
            extractor.list_traces(limit=1)

    def test_probe_v4_capabilities_detects_widened_session_filter(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(data=[], meta=SimpleNamespace(cursor=None)),
            SimpleNamespace(
                data=[{"trace_id": "trace-1", "session_id": "some-real-session"}],
                meta=SimpleNamespace(cursor=None),
            ),
        ]

        result = extractor.probe_v4_capabilities()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["checks"]["explicit_trace"]["status"], "ok")
        self.assertTrue(result["checks"]["session_discovery"]["filter_widened"])

    def test_get_observations_rejects_repeated_cursor(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.side_effect = [
            SimpleNamespace(
                data=[{"id": "obs-1", "trace_id": "trace-1"}],
                meta=SimpleNamespace(cursor="next-cursor"),
            ),
            SimpleNamespace(
                data=[{"id": "obs-1", "trace_id": "trace-1"}],
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

    def test_list_session_traces_fails_closed_when_provider_ignores_filter(self):
        extractor = self._make_extractor()
        extractor.client.api.observations.get_many.return_value = SimpleNamespace(
            data=[{
                "id": "obs-1",
                "trace_id": "trace-1",
                "session_id": "different-session",
            }],
            meta=SimpleNamespace(cursor=None),
        )

        with self.assertRaisesRegex(RuntimeError, "another session"):
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
