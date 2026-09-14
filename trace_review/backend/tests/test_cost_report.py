import csv
import io
import json
from copy import deepcopy
from decimal import Decimal

import pytest

from src.services.cost_report import build_report, report_csv
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
