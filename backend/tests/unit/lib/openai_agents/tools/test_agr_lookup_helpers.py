"""Unit tests for shared AGR lookup response helper contracts."""

from __future__ import annotations

import pytest

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
    bulk_item_status_from_lookup_status,
    bulk_resolution_summary,
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


def test_bulk_item_status_only_classifies_known_detail_stages_as_detail_failure():
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
                        "lookup_stage": "batch_fetch_gene_details",
                    },
                },
            ],
        )
        == "detail_failure"
    )
