import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.cost_report_cli import fetch_window


def page(rows, cursor=None):
    return SimpleNamespace(data=rows, meta=SimpleNamespace(cursor=cursor))


def test_generation_discovery_pagination_and_bounded_ancestry():
    extractor = MagicMock()
    extractor._normalize_v2_observation.side_effect = lambda item: item
    extractor.client.api.observations.get_many.side_effect = [
        page([{"id": "a", "traceId": "trace"}], "next"),
        page([{"id": "b", "traceId": "trace"}]),
    ]
    extractor._get_observations_bounded.return_value = ([{"id": "root", "type": "AGENT"}], 1, True)
    traces, source = fetch_window(extractor, "2026-09-07T00:00:00Z", "2026-09-08T00:00:00Z", 3, 100)
    assert source == {"complete": True, "requests": 3, "trace_count": 1}
    assert {o["id"] for o in traces[0]["observations"]} == {"a", "b", "root"}
    filters = json.loads(extractor.client.api.observations.get_many.call_args.kwargs["filter"])
    assert [f["operator"] for f in filters] == [">=", "<"]
    assert "io" not in extractor.client.api.observations.get_many.call_args.kwargs["fields"].split(",")


def test_partial_discovery_never_claims_complete():
    extractor = MagicMock()
    extractor._normalize_v2_observation.side_effect = lambda item: item
    extractor.client.api.observations.get_many.return_value = page([{"id": "a", "traceId": "trace"}], "more")
    traces, source = fetch_window(extractor, "2026-09-07T00:00:00Z", "2026-09-08T00:00:00Z", 1, 100)
    assert source["complete"] is False
    assert source["requests"] == 1
    assert traces[0]["observations"][0]["id"] == "a"
    extractor._get_observations_bounded.assert_not_called()
