"""Exact result selection; model capacity never becomes a per-tool quota."""
import json

import pytest

from agr_ai_curation_runtime.tool_result_bounds import (
    bounded_json_result, canonical_json, clamp_page_limit, detail_chunk,
    fit_page, parse_offset, requested_text_length, resolve_path, serialized_size,
)


def test_serialization_measurement_is_observational_only():
    value = {"quote": "NOT expressed 表达😀" * 10000}
    assert serialized_size(value) >= len(json.dumps(value))
    assert bounded_json_result(value) is None


def test_requested_page_preserves_every_large_item():
    values = ["A" * 50000, "B" * 90000, "C" * 80000]
    result, count = fit_page(values, start=1, limit=2,
        render=lambda items, count: {"items": items, "count": count})
    assert count == 2 and result == {"items": values[1:], "count": 2}


def test_exact_detail_preserves_unicode_hash_and_cursor_validation():
    value = {"quote": "β-catenin NOT expressed 😀 " * 4000}
    result = detail_chunk(value, path="quote", cursor=0)
    assert result["detail"]["content"] == value["quote"]
    assert result["detail"]["complete"] and result["detail"]["next_cursor"] is None
    digest = result["detail"]["sha256"]
    assert detail_chunk(value, path="quote", cursor=3, expected_sha256=digest)["detail"]["content"] == value["quote"][3:]
    assert detail_chunk(value, path="quote", cursor=0, expected_sha256="wrong")["error_code"] == "stale_result_cursor"
    with pytest.raises(ValueError):
        detail_chunk(value, path="quote", cursor=-1)


def test_full_structured_results_and_explicit_views_keep_revision_contract():
    result = {"status": "ok", "data": [{"quote": "Exact " * 10000}, {"quote": "Next " * 10000}]}
    assert bounded_json_result(result) is None
    page = bounded_json_result(result, offset=0)
    assert page["data"] == result["data"] and page["result_page"]["complete"]
    digest = page["result_page"]["result_sha256"]
    assert bounded_json_result(result, offset=1, expected_sha256=digest)["data"] == result["data"][1:]
    assert bounded_json_result(result, offset=1, expected_sha256="wrong")["error_code"] == "stale_result_cursor"
    assert canonical_json(result) == canonical_json(json.loads(canonical_json(result)))


def test_paths_with_dotted_keys_resolve_exactly():
    value = {"data": {"a.b": {"c": [1, {"d.e": "x"}]}}}
    assert resolve_path(value, "data.a.b.c.1.d.e") == "x"
    with pytest.raises(ValueError):
        resolve_path(value, "data.missing")


def test_database_page_limits_remain_explicit_and_malformed_values_rejected():
    size, meta = clamp_page_limit(200, default=20, maximum=100)
    assert size == 100 and meta["limit_clamped"]
    for bad in (-1, 2.5, "10", True):
        with pytest.raises(ValueError):
            clamp_page_limit(bad, default=20, maximum=100)
    assert parse_offset(None, total=3) == 0
    assert parse_offset(3, total=3) == 3
    for bad in (-1, 4, "1", True):
        with pytest.raises(ValueError):
            parse_offset(bad, total=3)
    assert requested_text_length(None, 90000) == 90000
    assert requested_text_length(50000, 90000) == 50000
    for bad in (True, -1, 0, "1"):
        with pytest.raises(ValueError):
            requested_text_length(bad, 90000)
