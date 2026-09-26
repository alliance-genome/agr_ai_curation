import csv
import io
import json
from copy import deepcopy
from decimal import Decimal

import pytest

from src.services.cost_report import build_report, cost_events, report_csv
from src.services.langfuse_run_reconstruction import usage_cost_summary

START = "2026-09-07T00:00:00Z"
END = "2026-09-14T00:00:00Z"


def generation(key, paper, run, agent, cost, **context):
    metadata = {
        "paper": {"namespace": "example", "id": paper} if paper else None,
        "paper_category": "paper" if paper else "unknown",
        "run_id": run, "agent_id": agent, "agent_name": agent,
        "environment": "production", "provider": "openai", **context,
    }
    return {"id": key, "traceId": "trace-" + str(run), "type": "GENERATION",
            "startTime": START, "model": "fixture-model",
            "usage": {"input": 100, "output": 20},
            "costDetails": {"total": cost} if cost is not None else {},
            "metadata": {"cost_context": metadata}}


def fixture():
    rows = [
        generation("a1e", "paper-A", "run-A1", "extraction", 1),
        generation("a1vf", "paper-A", "run-A1", "validation", .10, attempt_outcome="error"),
        generation("a1vr", "paper-A", "run-A1", "validation", .20),
        generation("a1f", "paper-A", "run-A1", "formatter", .05),
        generation("a2e", "paper-A", "run-A2", "extraction", 1.50),
        generation("a2v", "paper-A", "run-A2", "validation", .25),
        generation("a2f", "paper-A", "run-A2", "formatter", .05),
        generation("be", "paper-B", "run-B1", "extraction", 2),
        generation("author", None, "authoring", "author", .4, paper_category="not_associated"),
        generation("missing", None, None, "unknown", .3),
        generation("shared", None, "shared", "shared", .6, paper_category="shared",
                   related_papers=[{"namespace": "example", "id": "paper-A"}, {"namespace": "example", "id": "paper-B"}]),
    ]
    rows.append(deepcopy(rows[2]))
    rows.append(generation("dev", "paper-A", "dev-run", "extraction", 99, environment="dev"))
    return [{"observations": rows}]


@pytest.mark.parametrize("encoded", [False, True])
def test_openinference_export_preserves_cost_partitions(encoded):
    traces = fixture()
    for row in traces[0]["observations"]:
        metadata = row["metadata"]
        row["metadata"] = {
            "attributes.metadata": json.dumps(metadata) if encoded else metadata,
        }
    expected = build_report(fixture(), start=START, end=END,
                            group_by=("paper", "run_id", "agent_id"),
                            filters={"environment": "production"})
    actual = build_report(traces, start=START, end=END,
                          group_by=("paper", "run_id", "agent_id"),
                          filters={"environment": "production"})
    actual["as_of"] = expected["as_of"]  # Report creation time is not accounting data.
    assert actual == expected
    assert report_csv(actual) == report_csv(expected)


def test_exported_context_respects_direct_and_nearest_ancestor_precedence():
    leaf = generation("leaf", None, None, "leaf-agent", 1,
                      paper_category="not_associated")
    direct = leaf["metadata"]["cost_context"]
    leaf["parentObservationId"] = "owner"
    leaf["metadata"] = {
        "cost_context": direct,
        "attributes.metadata": json.dumps({"cost_context": {
            "agent_id": "must-not-win", "run_id": "must-not-win",
        }}),
    }
    owner = {"id": "owner", "type": "AGENT", "metadata": {
        "attributes.metadata": json.dumps({"cost_context": {
            "agent_id": "owner-agent", "run_id": "owner-run",
            "paper": {"namespace": "example", "id": "owner-paper"},
            "paper_category": "paper",
        }}),
    }}
    event = cost_events({"observations": [owner, leaf]})[0]
    assert event["agent_id"] == "leaf-agent"
    assert event["run_id"] is None  # Explicit unknown must not inherit another run.
    assert event["paper_category"] == "not_associated"
    assert event["paper"] is None
    leaf["metadata"] = {}
    inherited = cost_events({"observations": [owner, leaf]})[0]
    assert inherited["agent_id"] == "owner-agent"
    assert inherited["run_id"] == "owner-run"
    assert inherited["paper"] == '["example","owner-paper"]'


@pytest.mark.parametrize("embedded", ["{broken", "[]", "null", [], None])
def test_invalid_exported_context_does_not_invent_attribution(embedded):
    leaf = generation("leaf", None, None, "ignored", 1)
    leaf["metadata"] = {"attributes.metadata": embedded}
    event = cost_events({"observations": [leaf]})[0]
    assert event["agent_id"] is None
    assert event["run_id"] is None
    assert event["paper_category"] == "unknown"


@pytest.mark.parametrize("dimensions", [
    ("paper",), ("paper", "agent_id"), ("paper", "run_id"),
    ("paper", "run_id", "agent_id"),
])
def test_required_numerical_partitions(dimensions):
    report = build_report(fixture(), start=START, end=END, group_by=dimensions,
                          filters={"environment": "production"})
    assert Decimal(report["totals"]["total_cost"]) == Decimal("6.45")
    assert report["totals"]["calls"] == 11
    assert report["duplicate_observations"] == 1
    assert sum(Decimal(row["total_cost"]) for row in report["rows"]) == Decimal("6.45")
    paper_a = json.dumps(["example", "paper-A"], separators=(",", ":"))
    selected = [r for r in report["rows"] if r["paper"] == paper_a]
    assert sum(Decimal(r["total_cost"]) for r in selected) == Decimal("3.15")
    if "run_id" in dimensions:
        assert sum(Decimal(r["total_cost"]) for r in selected if r["run_id"] == "run-A1") == Decimal("1.35")
    if "agent_id" in dimensions:
        assert sum(Decimal(r["total_cost"]) for r in selected if r["agent_id"] == "validation") == Decimal(".55")
    exported = list(csv.DictReader(io.StringIO(report_csv(report))))
    assert sum(Decimal(r["total_cost"]) for r in exported) == Decimal("6.45")
    categories = {r["paper_category"] for r in exported}
    assert categories == {"paper", "not_associated", "unknown", "shared"}


def test_missing_price_is_incomplete_without_losing_priced_subtotal():
    traces = fixture()
    traces[0]["observations"].append(generation("unknown-price", "paper-A", "run-A2", "validation", None))
    report = build_report(traces, start=START, end=END, group_by=("paper", "run_id", "agent_id"), filters={"environment": "production"})
    assert report["totals"]["total_cost"] is None
    assert Decimal(report["totals"]["priced_subtotal"]) == Decimal("6.45")
    assert report["totals"]["unpriced_calls"] == 1


def test_window_filters_calls_not_root_start_and_labels_partial_source():
    traces = fixture()
    traces[0]["observations"][0]["startTime"] = END
    report = build_report(traces, start=START, end=END, filters={"environment": "production"}, source_complete=False)
    assert Decimal(report["totals"]["priced_subtotal"]) == Decimal("5.45")
    assert report["totals"]["total_cost"] is None
    assert not report["source_complete"]


def test_nearest_agent_ancestry_and_no_wrapper_charge():
    leaf = generation("leaf", None, None, "ignored", 2)
    leaf["metadata"] = {}
    leaf["parentObservationId"] = "chain"
    trace = {"observations": [
        {"id": "owner", "type": "AGENT", "name": "Validator", "usage": {"input": 999}, "totalCost": 12,
         "metadata": {"cost_context": {"agent_id": "validator", "agent_role": "validation"}}},
        {"id": "chain", "type": "CHAIN", "parentObservationId": "owner"}, leaf,
    ]}
    report = build_report([trace], start=START, end=END)
    assert report["rows"][0]["agent_id"] == "validator"
    assert report["totals"]["calls"] == 1
    assert Decimal(report["totals"]["total_cost"]) == 2


def test_same_name_different_agents_stay_distinct():
    observations = [generation("x", "A", "r", "id-A", 1, agent_name="Same"),
                    generation("y", "A", "r", "id-B", 2, agent_name="Same")]
    report = build_report([{"observations": observations}], start=START, end=END, group_by=("agent_name",))
    assert len(report["rows"]) == 2


def test_provider_and_langfuse_usage_are_not_double_counted():
    provider = usage_cost_summary({"usage": {
        "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 300},
        "output_tokens_details": {"reasoning_tokens": 150},
    }})
    langfuse = usage_cost_summary({"usageDetails": {
        "input": 100, "input_cached_tokens": 600, "input_cache_creation": 300,
        "output": 50, "output_reasoning_tokens": 150,
    }})
    for key in ("input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        assert provider[key] == langfuse[key]
    assert provider["uncached_input_tokens"] == 100
    assert provider["total_tokens"] == 1200


def test_missing_usage_zero_cost_and_mismatched_totals():
    missing = usage_cost_summary({"calculatedTotalCost": 0})
    assert missing["total_cost"] is None
    assert missing["usage_status"] == "missing"
    free = usage_cost_summary({"usageDetails": {"input": 3}, "costDetails": {"total": 0}, "internalModelId": "free-model"})
    assert free["total_cost"] == 0
    mismatch = usage_cost_summary({"usage": {"input": 10, "output": 5, "total": 20}})
    assert mismatch["total_tokens"] == 15
    assert mismatch["usage_issues"] == ["total_tokens_mismatch"]


def test_model_request_measurement_events_add_no_calls_or_cost():
    """ALL-1279: per-request measurement events are EVENT observations.

    They carry provider usage for correlation only, so the exclusive cost
    report must not count them as calls, add cost, or turn missing usage into
    reported usage.
    """
    baseline = build_report(fixture(), start=START, end=END, filters={"environment": "production"})
    traces = fixture()
    measured = traces[0]["observations"][0]
    for index in range(2):  # a retried request produces two measurement records
        traces[0]["observations"].append({
            "id": f"measurement-{index}", "traceId": measured["traceId"], "type": "EVENT",
            "name": "extraction_trace_event", "startTime": START,
            "metadata": {"event_payload": {
                "event_type": "runtime.model_request_measurement",
                "input_summary": {"preview": {
                    "provider_response_id": "resp-a1e", "attempt": index + 1,
                    "provider_usage": {"status": "reported", "input_tokens": 100, "output_tokens": 20},
                    "model_visible": {"estimated_tokens": 30},
                }},
            }},
        })
    report = build_report(traces, start=START, end=END, filters={"environment": "production"})
    assert report["totals"]["calls"] == baseline["totals"]["calls"] == 11
    assert report["totals"]["total_cost"] == baseline["totals"]["total_cost"]
    assert report["duplicate_observations"] == baseline["duplicate_observations"] == 1
    assert report["totals"]["missing_usage_calls"] == baseline["totals"]["missing_usage_calls"]


def attempt(key, usage_status, cost=None, usage=True, **context):
    """One ALL-1288 model turn: cost_context declares usage_status and model_request_id."""
    row = generation(key, "paper-A", "run-S", "extraction", cost,
                     usage_status=usage_status, model_request_id="mr-" + key, **context)
    if not usage:
        del row["usage"]
    return row


def status_fixture():
    return [{"observations": [
        attempt("rec", "recorded", .5, provider_response_id="resp-rec"),
        attempt("inc", "inconsistent", usage=False),
        attempt("omit", "provider_omitted", usage=False, attempt_outcome="success"),
        attempt("fail", "failed", usage=False, attempt_outcome="error"),
        attempt("cancel", "cancelled", usage=False, attempt_outcome="cancelled"),
    ]}]


STATUS_COUNT_FIELDS = (
    "recorded_usage_calls", "inconsistent_usage_calls", "provider_omitted_usage_calls",
    "failed_usage_calls", "cancelled_usage_calls", "missing_status_unknown_usage_calls",
)


def test_declared_usage_statuses_are_counted_and_never_zero():
    report = build_report(status_fixture(), start=START, end=END)
    totals = report["totals"]
    assert {field: totals[field] for field in STATUS_COUNT_FIELDS} == {
        "recorded_usage_calls": 1, "inconsistent_usage_calls": 1,
        "provider_omitted_usage_calls": 1, "failed_usage_calls": 1,
        "cancelled_usage_calls": 1, "missing_status_unknown_usage_calls": 0,
    }
    assert totals["calls"] == 5
    assert totals["missing_usage_calls"] == 4
    assert totals["usage_complete"] is False
    # Usage-less attempts are unpriced, not free: no complete total, subtotal kept.
    assert totals["unpriced_calls"] == 4
    assert totals["total_cost"] is None
    assert Decimal(totals["priced_subtotal"]) == Decimal(".5")
    assert totals["input_tokens"] == 100
    by_span = {e["span_id"]: e for e in report["events"]}
    assert {k: e["usage_status"] for k, e in by_span.items()} == {
        "rec": "recorded", "inc": "inconsistent", "omit": "provider_omitted",
        "fail": "failed", "cancel": "cancelled",
    }
    assert {e["model_request_id"] for e in by_span.values()} == {
        "mr-rec", "mr-inc", "mr-omit", "mr-fail", "mr-cancel",
    }
    assert by_span["fail"]["usage_status_declared"] == "failed"


def test_usage_status_is_a_report_dimension():
    report = build_report(status_fixture(), start=START, end=END, group_by=("usage_status",))
    rows = {row["usage_status"]: row for row in report["rows"]}
    assert set(rows) == {"recorded", "inconsistent", "provider_omitted", "failed", "cancelled"}
    assert rows["cancelled"]["cancelled_usage_calls"] == 1
    assert rows["cancelled"]["total_cost"] is None
    filtered = build_report(status_fixture(), start=START, end=END,
                            filters={"usage_status": "failed"})
    assert filtered["totals"]["calls"] == 1
    exported = list(csv.DictReader(io.StringIO(report_csv(report))))
    assert {r["usage_status"]: r["failed_usage_calls"] for r in exported}["failed"] == "1"


def test_legacy_spans_without_status_keep_the_observed_classification():
    traces = fixture()
    legacy_missing = generation("legacy-missing", "paper-A", "run-A1", "extraction", None)
    del legacy_missing["usage"]
    # The pre-ALL-1288 emitter wrote "missing" without a cause.
    old_missing = generation("old-missing", "paper-A", "run-A1", "extraction", None,
                             usage_status="missing")
    del old_missing["usage"]
    legacy_inconsistent = generation("legacy-inconsistent", "paper-A", "run-A1", "extraction", None)
    legacy_inconsistent["usage"] = {"input": 10, "output": 5, "total": 20}
    traces[0]["observations"] += [legacy_missing, old_missing, legacy_inconsistent]
    report = build_report(traces, start=START, end=END, filters={"environment": "production"})
    totals = report["totals"]
    assert totals["calls"] == 14
    assert totals["recorded_usage_calls"] == 11
    assert totals["missing_status_unknown_usage_calls"] == 2
    assert totals["missing_usage_calls"] == 2
    assert totals["inconsistent_usage_calls"] == 1
    assert totals["provider_omitted_usage_calls"] == totals["failed_usage_calls"] == totals["cancelled_usage_calls"] == 0
    assert Decimal(totals["priced_subtotal"]) == Decimal("6.45")
    assert totals["total_cost"] is None
    events = {e["span_id"]: e for e in report["events"]}
    assert events["legacy-missing"]["usage_status_declared"] is None
    assert events["legacy-missing"]["model_request_id"] is None
    assert events["old-missing"]["usage_status"] == "missing_status_unknown"


def test_fully_recorded_legacy_window_stays_usage_complete():
    report = build_report(fixture(), start=START, end=END, filters={"environment": "production"})
    assert report["totals"]["recorded_usage_calls"] == report["totals"]["calls"] == 11
    assert report["totals"]["usage_complete"] is True
    assert report["totals"]["total_cost"] == "6.45"


def test_declared_recorded_without_retained_usage_is_not_recorded():
    """A span that says usage was recorded but whose observation holds none is
    never counted as recorded zero-token usage, and is never estimated."""
    lost = attempt("lost", "recorded", usage=False)
    declared_bad = attempt("declared-bad", "inconsistent")  # observation carries tokens
    definitions = [{"id": "fixture", "matchPattern": "^fixture-model$", "startDate": START,
                    "prices": {"input": .001, "output": .002}}]
    report = build_report([{"observations": [lost, declared_bad]}], start=START, end=END,
                          model_definitions=definitions)
    events = {e["span_id"]: e for e in report["events"]}
    assert events["lost"]["usage_status"] == "inconsistent"
    assert events["lost"]["usage_status_declared"] == "recorded"
    assert events["declared-bad"]["usage_status"] == "inconsistent"
    for event in events.values():
        assert event["cost"] is None
        assert event["estimate_unavailable_reason"] == "missing_or_inconsistent_usage"
    assert report["totals"]["inconsistent_usage_calls"] == 2
    assert report["totals"]["recorded_usage_calls"] == 0
    assert report["totals"]["usage_complete"] is False


def test_model_request_id_deduplicates_attempts_without_provider_response_id():
    cancelled = attempt("cancel", "cancelled", usage=False)
    reexported = deepcopy(cancelled)
    reexported["id"] = "cancel-reexported"
    retry = attempt("cancel-retry", "cancelled", usage=False)
    report = build_report([{"observations": [cancelled, reexported, retry]}], start=START, end=END)
    assert report["duplicate_observations"] == 1
    assert report["totals"]["calls"] == 2
    assert report["totals"]["cancelled_usage_calls"] == 2


def test_declared_usage_less_status_with_retained_usage_is_inconsistent():
    """A span that declares no usable usage while its observation holds usage
    or cost contradicts itself; it is reported inconsistent, not as declared."""
    failed = attempt("failed-with-usage", "failed", .25, attempt_outcome="error")
    omitted = attempt("omitted-with-usage", "provider_omitted")
    cost_only = attempt("cancelled-with-cost", "cancelled", .1, usage=False)
    cost_only["internalModelId"] = "fixture-model"
    report = build_report([{"observations": [failed, omitted, cost_only]}], start=START, end=END)
    events = {e["span_id"]: e for e in report["events"]}
    assert {k: e["usage_status"] for k, e in events.items()} == {
        "failed-with-usage": "inconsistent", "omitted-with-usage": "inconsistent",
        "cancelled-with-cost": "inconsistent",
    }
    assert events["failed-with-usage"]["usage_status_declared"] == "failed"
    assert report["totals"]["inconsistent_usage_calls"] == 3
    assert report["totals"]["failed_usage_calls"] == report["totals"]["provider_omitted_usage_calls"] == 0
    assert report["totals"]["usage_complete"] is False


def test_csv_keeps_existing_column_order_and_appends_status_columns():
    header = report_csv(build_report(status_fixture(), start=START, end=END)).splitlines()[0].split(",")
    assert header[:14] == [
        "agent_id", "agent_name", "calls", "known_runs", "known_papers", "missing_run_calls",
        "unpriced_calls", "missing_usage_calls", "inconsistent_usage_calls",
        "unknown_agent_calls", "unknown_paper_calls", "measured_cost", "estimated_cost",
        "estimated_cost_upper",
    ]
    tail = header[header.index("trace_references") + 1:header.index("start")]
    assert tail == ["recorded_usage_calls", "provider_omitted_usage_calls", "failed_usage_calls",
                    "cancelled_usage_calls", "missing_status_unknown_usage_calls", "usage_complete"]
