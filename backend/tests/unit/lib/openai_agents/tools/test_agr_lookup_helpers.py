"""Unit tests for shared AGR lookup response helper contracts."""

from __future__ import annotations

import pytest

from agr_ai_curation_runtime.agr_lookup import (
    DEFAULT_BULK_TOTAL_MATCH_CAP,
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
    LookupProjectionMetadata,
    bulk_item_status_from_lookup_status,
    bulk_resolution_summary,
    candidate_from_result,
    cap_bulk_total_matches,
    lookup_response_payload,
    projection_from_result,
)


def test_bulk_resolution_summary_requires_item_status():
    with pytest.raises(KeyError):
        bulk_resolution_summary([{"count": 0}])


def test_bulk_resolution_summary_requires_item_count():
    with pytest.raises(KeyError):
        bulk_resolution_summary([{"status": "no_matches"}])


def test_bulk_item_status_handles_all_known_nonresolved_lookup_statuses():
    assert (
        bulk_item_status_from_lookup_status(
            LOOKUP_STATUS_NOT_FOUND,
            count=0,
        )
        == "no_matches"
    )
    assert (
        bulk_item_status_from_lookup_status(
            LOOKUP_STATUS_AMBIGUOUS,
            count=0,
        )
        == LOOKUP_STATUS_AMBIGUOUS
    )
    assert (
        bulk_item_status_from_lookup_status(
            LOOKUP_STATUS_UNDER_DEVELOPMENT,
            count=0,
        )
        == LOOKUP_STATUS_UNDER_DEVELOPMENT
    )


def test_bulk_item_status_rejects_unexpected_lookup_status():
    with pytest.raises(ValueError, match="Unexpected bulk lookup status"):
        bulk_item_status_from_lookup_status("future_status", count=0)


def test_bulk_item_status_uses_caller_supplied_detail_stages():
    assert (
        bulk_item_status_from_lookup_status(
            LOOKUP_STATUS_TRANSIENT,
            count=0,
            attempts=[
                {
                    "attempted_query": {
                        "lookup_stage": "prefetch_gene_details_cache",
                    },
                },
            ],
        )
        == "transient_failure"
    )
    assert (
        bulk_item_status_from_lookup_status(
            LOOKUP_STATUS_TRANSIENT,
            count=0,
            attempts=[
                {
                    "attempted_query": {
                        "lookup_stage": "fetch_widget_details",
                    },
                },
            ],
            detail_lookup_stages={"fetch_widget_details"},
        )
        == "detail_failure"
    )


def test_projection_from_result_uses_neutral_defaults_without_method_inference():
    projection = projection_from_result(
        "get_gene_by_id",
        {
            "curie": "FIX:1",
            "symbol": "fixture-1",
            "taxon": "NCBITaxon:9606",
            "gene_type": "protein_coding_gene",
        },
    )

    assert projection["projection_type"] == "lookup_result"
    assert projection["projection_key"] == "FIX:1"
    assert projection["source"] == {"method": "get_gene_by_id"}
    assert "provider" not in projection
    assert "object_type" not in projection
    assert "provider_data" not in projection


def test_lookup_response_payload_accepts_provider_agnostic_projection_metadata():
    payload = lookup_response_payload(
        method="find_widgets",
        data={"curie": "WIDGET:1", "symbol": "alpha", "ignored": "nope"},
        attempted_query={"method": "find_widgets", "widget_id": "WIDGET:1"},
        exact_lookup=True,
        projection_metadata=LookupProjectionMetadata(
            provider="fixture_inventory",
            tool_name="fixture_lookup",
            projection_type="widget_reference",
            object_type="Widget",
            provider_data_keys=("curie", "symbol"),
        ),
    )

    projection = payload["result_projections"][0]
    assert payload["lookup_status"] == "success"
    assert payload["lookup_attempts"][0]["provider"] == "fixture_inventory"
    assert payload["candidate_matches"][0]["provider"] == "fixture_inventory"
    assert projection["provider"] == "fixture_inventory"
    assert projection["projection_type"] == "widget_reference"
    assert projection["object_type"] == "Widget"
    assert projection["source"] == {
        "tool_name": "fixture_lookup",
        "method": "find_widgets",
    }
    assert projection["provider_data"] == {
        "curie": "WIDGET:1",
        "symbol": "alpha",
    }


def test_candidate_from_result_does_not_re_embed_full_projection():
    candidate = candidate_from_result(
        "search_widgets",
        {
            "curie": "WIDGET:1",
            "symbol": "alpha",
            "match_type": "exact",
        },
        projection_metadata=LookupProjectionMetadata(
            provider="fixture_inventory",
            tool_name="fixture_lookup",
            projection_type="widget_reference",
            object_type="Widget",
            provider_data_keys=("curie", "symbol"),
        ),
    )

    # A candidate is only a lightweight pointer; the full projection lives once
    # under result_projections and must not be duplicated here. It keeps a
    # scalar object_type so it stays self-describing on its own.
    assert "projection" not in candidate
    assert candidate["candidate_id"] == "WIDGET:1"
    assert candidate["candidate_label"] == "alpha"
    assert candidate["match_type"] == "exact"
    assert candidate["provider"] == "fixture_inventory"
    assert candidate["object_type"] == "Widget"


def test_lookup_response_payload_lists_each_row_information_once():
    rows = [
        {"curie": "WIDGET:1", "symbol": "alpha", "match_type": "exact"},
        {"curie": "WIDGET:2", "symbol": "beta", "match_type": "exact"},
    ]
    payload = lookup_response_payload(
        method="search_widgets",
        data=rows,
        attempted_query={"method": "search_widgets", "query": "wid"},
        projection_metadata=LookupProjectionMetadata(
            provider="fixture_inventory",
            tool_name="fixture_lookup",
            projection_type="widget_reference",
            object_type="Widget",
            provider_data_keys=("curie", "symbol"),
        ),
    )

    # data carries the raw rows once; result_projections carries the full
    # resolved view once; candidate_matches is a thin summary with no nested
    # projection copy.
    assert payload["data"] == rows
    assert len(payload["result_projections"]) == 2
    assert len(payload["candidate_matches"]) == 2
    for candidate in payload["candidate_matches"]:
        assert "projection" not in candidate
    assert payload["result_projections"][0]["resolved_id"] == "WIDGET:1"
    assert payload["candidate_matches"][0]["candidate_id"] == "WIDGET:1"


def test_cap_bulk_total_matches_default_is_bounded():
    assert DEFAULT_BULK_TOTAL_MATCH_CAP == 500


def test_cap_bulk_total_matches_passes_through_small_totals():
    items = [
        {"input": "a", "status": "resolved", "count": 2, "results": [1, 2]},
        {"input": "b", "status": "resolved", "count": 1, "results": [3]},
    ]
    cap = cap_bulk_total_matches(items, total_match_cap=10)

    assert cap == {
        "total_count": 3,
        "returned_count": 3,
        "truncated": False,
        "total_match_cap": 10,
    }
    assert items[0]["count"] == 2
    assert items[1]["count"] == 1
    assert "match_total_before_cap" not in items[0]


def test_cap_bulk_total_matches_trims_in_input_order_and_reports_truncation():
    items = [
        {
            "input": "a",
            "status": "resolved",
            "count": 3,
            "results": ["a1", "a2", "a3"],
            "candidate_matches": ["c1", "c2", "c3"],
            "result_projections": ["p1", "p2", "p3"],
        },
        {
            "input": "b",
            "status": "resolved",
            "count": 4,
            "results": ["b1", "b2", "b3", "b4"],
            "candidate_matches": ["d1", "d2", "d3", "d4"],
            "result_projections": ["q1", "q2", "q3", "q4"],
        },
    ]
    cap = cap_bulk_total_matches(items, total_match_cap=5)

    assert cap["total_count"] == 7
    assert cap["returned_count"] == 5
    assert cap["truncated"] is True
    assert cap["total_match_cap"] == 5
    # First item is fully kept; the second is trimmed to the remaining budget.
    assert items[0]["count"] == 3
    assert "match_total_before_cap" not in items[0]
    assert items[1]["count"] == 2
    assert items[1]["match_total_before_cap"] == 4
    assert items[1]["results"] == ["b1", "b2"]
    assert items[1]["candidate_matches"] == ["d1", "d2"]
    assert items[1]["result_projections"] == ["q1", "q2"]


def test_cap_bulk_total_matches_zero_cap_drops_all_matches():
    items = [
        {"input": "a", "status": "resolved", "count": 2, "results": [1, 2]},
    ]
    cap = cap_bulk_total_matches(items, total_match_cap=0)

    assert cap["total_count"] == 2
    assert cap["returned_count"] == 0
    assert cap["truncated"] is True
    assert items[0]["count"] == 0
    assert items[0]["results"] == []


def test_cap_bulk_total_matches_rejects_negative_cap():
    with pytest.raises(ValueError, match="non-negative"):
        cap_bulk_total_matches([], total_match_cap=-1)
