"""ALL-1278: shared size-bounded tool result contract."""

from __future__ import annotations

import json

import pytest

from agr_ai_curation_runtime.tool_result_bounds import (
    ToolResultBudgetError,
    bounded_json_result,
    canonical_json,
    clamp_page_limit,
    detail_chunk,
    fit_page,
    parse_offset,
    resolve_path,
    serialized_size,
    tool_result_max_bytes,
)
from src.lib.openai_agents.config import get_tool_result_max_bytes


def test_size_counts_the_largest_model_visible_serialization():
    text = "表达😀" * 100
    value = {"text": text}

    assert serialized_size(value) >= len(json.dumps(value))  # ASCII-escaped JSON
    assert serialized_size(value) >= len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    assert serialized_size(value) >= len(str(value).encode("utf-8"))


def test_backend_and_package_read_the_same_budget(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "9000")
    assert tool_result_max_bytes() == get_tool_result_max_bytes() == 9000
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "10")
    assert tool_result_max_bytes() == get_tool_result_max_bytes() == 2048
    monkeypatch.delenv("TOOL_RESULT_MAX_BYTES")
    assert tool_result_max_bytes() == get_tool_result_max_bytes() == 32768


def test_page_limit_clamps_with_explicit_metadata_and_rejects_malformed_values():
    assert clamp_page_limit(None, default=20, maximum=50) == (
        20,
        {"requested_limit": None, "effective_limit": 20, "limit_clamped": False},
    )
    effective, metadata = clamp_page_limit(10**9, default=20, maximum=50)
    assert effective == 50 and metadata["limit_clamped"] is True
    for bad in (-1, 2.5, "10", True):
        with pytest.raises(ValueError):
            clamp_page_limit(bad, default=20, maximum=50)


def test_offsets_are_validated_against_the_current_total():
    assert parse_offset(None, total=3) == 0
    assert parse_offset(3, total=3) == 3
    for bad in (-1, 4, "1", True):
        with pytest.raises(ValueError):
            parse_offset(bad, total=3)


def test_fit_page_measures_the_whole_response_and_replaces_oversized_first_item():
    items = ["a" * 50, "b" * 5000, "c" * 50]

    def render(page, returned):
        return {"items": page, "returned": returned}

    first, returned = fit_page(items, start=0, limit=3, render=render, budget=1000)
    assert returned == 1 and serialized_size(first) <= 1000
    second, returned = fit_page(
        items, start=1, limit=3, render=render, budget=1000,
        oversized=lambda item, index: {"withheld": index},
    )
    assert second["items"][0] == {"withheld": 1} and returned == 1
    with pytest.raises(ToolResultBudgetError):
        fit_page(items, start=1, limit=3, render=render, budget=1000)


def test_detail_chunks_are_exact_contiguous_and_unicode_safe():
    value = {"quote": "β-catenin 表达 😀 " * 700}
    chunks, cursor = [], 0
    while cursor is not None:
        chunk = detail_chunk(value, path="quote", cursor=cursor, budget=2048)
        assert serialized_size(chunk) <= 2048
        chunks.append(chunk["detail"]["content"])
        cursor = chunk["detail"]["next_cursor"]
    assert "".join(chunks) == value["quote"]


def test_paths_with_dotted_keys_resolve_exactly():
    value = {"data": {"a.b": {"c": [1, {"d.e": "x"}]}}}
    assert resolve_path(value, "data.a.b.c.1.d.e") == "x"
    with pytest.raises(ValueError):
        resolve_path(value, "data.missing")


def test_wide_single_record_pages_by_field_and_reassembles():
    record = {f"field_{index:03d}": "表达 " * 60 for index in range(80)}
    result = {"status": "ok", "data": record}
    pages = [bounded_json_result(result, budget=4096)]
    while pages[-1]["result_page"]["next_call"]:
        pages.append(bounded_json_result(
            result, budget=4096,
            offset=pages[-1]["result_page"]["next_call"]["result_offset"],
            expected_sha256=pages[-1]["result_page"]["result_sha256"],
        ))
    assert all(serialized_size(page) <= 4096 for page in pages)
    rebuilt = {}
    for page in pages:
        rebuilt.update(page["data"])
    assert rebuilt == record


def test_compact_results_are_untouched_and_views_are_explicit():
    result = {"status": "ok", "data": [1, 2, 3]}
    assert bounded_json_result(result, budget=4096) is None
    page = bounded_json_result(result, budget=4096, offset=0)
    assert page["data"] == [1, 2, 3] and page["result_page"]["complete"] is True
    assert canonical_json(result) == canonical_json(json.loads(canonical_json(result)))
