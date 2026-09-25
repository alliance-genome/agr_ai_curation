import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.cost_report_cli import fetch_window, main


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


def test_offline_report_warns_with_each_usage_status(tmp_path, capsys):
    def turn(key, status, usage=None):
        row = {"id": key, "traceId": "trace", "type": "GENERATION", "startTime": "2026-09-07T01:00:00Z",
               "model": "fixture-model", "metadata": {"cost_context": {"usage_status": status}}}
        if usage:
            row.update(usage=usage, costDetails={"total": 0.5})
        return row
    source = tmp_path / "retained.json"
    source.write_text(json.dumps({"source_complete": True, "traces": [{"observations": [
        turn("rec", "recorded", {"input": 10, "output": 2}),
        turn("cancel", "cancelled"),
        {**turn("legacy", None), "metadata": {}},
    ]}]}))
    assert main(["--input", str(source), "--start", "2026-09-07T00:00:00Z",
                 "--end", "2026-09-08T00:00:00Z"]) == 0
    captured = capsys.readouterr()
    assert ("Coverage incomplete: 2 unpriced calls; usage status: 1 recorded, 0 inconsistent, "
            "0 provider_omitted, 0 failed, 1 cancelled, 1 missing_status_unknown.") in captured.err
    totals = json.loads(captured.out)["totals"]
    assert totals["usage_complete"] is False
    assert totals["total_cost"] is None
