"""Unit tests for the shared bounded-list helpers."""

from __future__ import annotations

from src.lib.openai_agents.bounded_list import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    bounded_envelope,
    normalize_page_limit,
    offset_page,
    parse_offset_cursor,
    recent_page,
    search_and_paginate,
    substring_match,
)


def test_normalize_page_limit_uses_default_for_missing_and_bad_values():
    assert normalize_page_limit(None) == DEFAULT_PAGE_LIMIT
    assert normalize_page_limit("not-a-number") == DEFAULT_PAGE_LIMIT
    assert normalize_page_limit(None, default=7) == 7


def test_normalize_page_limit_clamps_to_floor_and_ceiling():
    assert normalize_page_limit(0) == 1
    assert normalize_page_limit(-5) == 1
    assert normalize_page_limit(10_000) == MAX_PAGE_LIMIT
    assert normalize_page_limit(10_000, maximum=250) == 250
    assert normalize_page_limit(3) == 3


def test_parse_offset_cursor_is_non_negative_and_tolerant():
    assert parse_offset_cursor(None) == 0
    assert parse_offset_cursor("") == 0
    assert parse_offset_cursor("bad") == 0
    assert parse_offset_cursor("-4") == 0
    assert parse_offset_cursor("12") == 12
    assert parse_offset_cursor(5) == 5


def test_offset_page_returns_forward_pages_and_stops_at_end():
    items = list(range(10))

    page, has_more, next_cursor = offset_page(items, limit=4, cursor=None)
    assert page == [0, 1, 2, 3]
    assert has_more is True
    assert next_cursor == "4"

    page, has_more, next_cursor = offset_page(items, limit=4, cursor=next_cursor)
    assert page == [4, 5, 6, 7]
    assert has_more is True
    assert next_cursor == "8"

    page, has_more, next_cursor = offset_page(items, limit=4, cursor=next_cursor)
    assert page == [8, 9]
    assert has_more is False
    assert next_cursor is None


def test_offset_page_empty_sequence():
    page, has_more, next_cursor = offset_page([], limit=5, cursor=None)
    assert page == []
    assert has_more is False
    assert next_cursor is None


def test_recent_page_serves_from_the_end_backwards():
    items = list(range(10))

    page, has_more, next_cursor = recent_page(items, limit=3, cursor=None)
    assert page == [7, 8, 9]
    assert has_more is True
    assert next_cursor == "3"

    page, has_more, next_cursor = recent_page(items, limit=3, cursor=next_cursor)
    assert page == [4, 5, 6]
    assert has_more is True
    assert next_cursor == "6"


def test_bounded_envelope_reports_totals_and_next_page():
    envelope = bounded_envelope(
        ["a", "b"],
        total_count=5,
        offset=0,
        has_more=True,
        next_cursor="2",
    )
    assert envelope == {
        "total_count": 5,
        "returned_count": 2,
        "truncated": True,
        "next_offset": 2,
        "next_cursor": "2",
    }


def test_bounded_envelope_marks_final_page():
    envelope = bounded_envelope(
        ["c"],
        total_count=3,
        offset=2,
        has_more=False,
        next_cursor=None,
    )
    assert envelope["truncated"] is False
    assert envelope["next_offset"] is None
    assert envelope["next_cursor"] is None
    assert envelope["returned_count"] == 1


def test_substring_match_is_case_insensitive_and_blank_matches_all():
    assert substring_match(None, "anything") is True
    assert substring_match("   ", "anything") is True
    assert substring_match("GENE", "gene_extractor", "Gene Specialist") is True
    assert substring_match("zzz", "gene_extractor", "Gene Specialist") is False
    assert substring_match("spec", None, "Gene Specialist") is True


def test_search_and_paginate_filters_then_pages():
    items = [
        {"id": "gene_extractor", "label": "Gene Specialist"},
        {"id": "disease_validation", "label": "Disease Validator"},
        {"id": "gene_validation", "label": "Gene Validator"},
    ]

    page, envelope = search_and_paginate(
        items,
        query="gene",
        search_fields=lambda item: (item["id"], item["label"]),
        limit=1,
        cursor=None,
    )
    assert [item["id"] for item in page] == ["gene_extractor"]
    assert envelope["total_count"] == 2
    assert envelope["returned_count"] == 1
    assert envelope["truncated"] is True
    assert envelope["next_offset"] == 1
    assert envelope["next_cursor"] == "1"
    assert envelope["limit"] == 1

    page, envelope = search_and_paginate(
        items,
        query="gene",
        search_fields=lambda item: (item["id"], item["label"]),
        limit=1,
        cursor=envelope["next_cursor"],
    )
    assert [item["id"] for item in page] == ["gene_validation"]
    assert envelope["truncated"] is False
    assert envelope["next_cursor"] is None


def test_search_and_paginate_blank_query_returns_everything():
    items = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    page, envelope = search_and_paginate(
        items,
        query="",
        search_fields=lambda item: (item["id"], item["label"]),
        limit=10,
        cursor=None,
    )
    assert len(page) == 2
    assert envelope["total_count"] == 2
    assert envelope["truncated"] is False
