"""Unit tests for shared AGR lookup response helper contracts."""

from __future__ import annotations

import pytest

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
    LookupProjectionMetadata,
    bulk_item_status_from_lookup_status,
    bulk_resolution_summary,
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
