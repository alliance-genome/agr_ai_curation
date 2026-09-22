"""ALL-1278: builder acknowledgments and candidate pages stay inside result budgets."""

from __future__ import annotations

from typing import Any

import pytest

from agr_ai_curation_alliance.tools import agr_curation
from agr_ai_curation_runtime.tool_result_bounds import serialized_size
from src.lib.openai_agents import extraction_builder_workspace as builder

UNICODE_NOTE = "Ω-Expression 表达 im Flügel 😀 "


def _workspace(candidate_count: int, *, wide: bool = False) -> builder.ExtractionBuilderWorkspace:
    workspace = builder.ExtractionBuilderWorkspace(
        run_id="trace-bounds",
        document_id="doc-1",
        domain_pack_id=agr_curation.GENE_EXPRESSION_DOMAIN_PACK_ID,
        agent_id="gene_expression_extraction",
    )
    for index in range(candidate_count):
        staged = {"where_expressed_statement": f"GFP {index}"}
        if wide:
            staged.update({f"field_{key:03d}_{UNICODE_NOTE.strip()}": key for key in range(60)})
        candidate = workspace.upsert_candidate(
            candidate_id=f"cand-{index:04d}",
            staged_fields=staged,
            pending_ref_ids=[f"pending-{index:04d}"],
            evidence_record_ids=[f"evidence-{index:04d}-{part}" for part in range(3)],
            resolver_selection_refs=[f"call-{index:04d}"],
        )
        if wide:
            candidate.validation_errors = [
                {"field_path": "relation.name", "reason": "unresolved", "message": UNICODE_NOTE * 4}
            ]
    return workspace


def _page_all(page_fn, **kwargs) -> tuple[list[str], list[dict[str, Any]]]:
    seen: list[str] = []
    pages: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = page_fn(offset=offset, **kwargs)
        pages.append(page)
        seen.extend(candidate["candidate_id"] for candidate in page["candidates"])
        if page["next_offset"] is None:
            return seen, pages
        assert page["next_offset"] > offset
        offset = page["next_offset"]


def test_builder_ack_does_not_grow_with_the_workspace():
    small = agr_curation._builder_summary(_workspace(3))
    large = agr_curation._builder_summary(_workspace(600))

    assert "candidate_ids" not in large
    assert "pending_ref_ids" not in large
    assert "evidence_record_ids" not in large
    assert "resolver_selection_refs" not in large
    assert large["candidate_count"] == 600
    assert large["pending_ref_count"] == 600
    assert large["evidence_record_count"] == 1800
    assert large["resolver_selection_ref_count"] == 600
    # Only the digits of the counts differ.
    assert serialized_size(large) - serialized_size(small) < 16


def test_discard_ack_names_the_changed_candidate_and_stays_constant(monkeypatch):
    monkeypatch.setattr(agr_curation, "write_extraction_trace_event", lambda **event: event)
    sizes = []
    for count in (5, 400):
        workspace = _workspace(count)
        token = builder.set_active_extraction_builder_workspace(workspace)
        try:
            result = agr_curation._discard_gene_expression_observation_impl(
                candidate_id="cand-0001", reason="duplicate"
            )
        finally:
            builder.reset_active_extraction_builder_workspace(token)
        assert result.status == "ok"
        assert result.data["discarded_candidate_id"] == "cand-0001"
        sizes.append(serialized_size(result))
    assert sizes[1] - sizes[0] < 16


def test_huge_page_limit_clamps_to_builder_maximum(monkeypatch):
    monkeypatch.setenv("BUILDER_LIST_MAX_LIMIT", "40")
    workspace = _workspace(300)

    page = agr_curation._builder_candidate_list(workspace, limit=10**6, offset=0)
    found = agr_curation._search_builder_candidates(workspace, limit=10**6, offset=0)

    for result in (page, found):
        assert result["returned_candidate_count"] <= 40
        assert result["requested_limit"] == 10**6
        assert result["effective_limit"] == 40
        assert result["limit_clamped"] is True
        assert result["next_offset"] == result["returned_candidate_count"]


def test_wide_candidates_page_by_size_completely_without_duplicates(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "8192")
    workspace = _workspace(90, wide=True)
    expected = [f"cand-{index:04d}" for index in range(90)]

    listed, list_pages = _page_all(
        lambda **kw: agr_curation._builder_candidate_list(workspace, limit=100, **kw)
    )
    found, find_pages = _page_all(
        lambda **kw: agr_curation._search_builder_candidates(workspace, limit=100, **kw)
    )

    assert listed == expected
    assert found == expected
    for page in [*list_pages, *find_pages]:
        assert serialized_size(page) <= 8192
    assert list_pages[0]["page_ended_by"] == "size_budget"


def test_per_row_decorations_count_toward_the_page_budget(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    workspace = _workspace(40)
    notice = {"code": "generic_attribute_key_drift", "message": UNICODE_NOTE * 20}

    def decorate(candidate):
        return {**candidate, "attribute_key_notices": [notice, notice]}

    listed, pages = _page_all(
        lambda **kw: agr_curation._builder_candidate_list(
            workspace, limit=100, decorate=decorate, **kw
        )
    )

    assert listed == [f"cand-{index:04d}" for index in range(40)]
    for page in pages:
        assert serialized_size(page) <= 4096
        assert all(candidate["attribute_key_notices"] for candidate in page["candidates"])


def test_oversized_single_candidate_is_withheld_with_identity_and_counts(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    workspace = _workspace(2)
    workspace.get_candidate("cand-0000").validation_errors = [
        {"field_path": f"field_{index}", "message": UNICODE_NOTE * 10} for index in range(40)
    ]

    page = agr_curation._builder_candidate_list(workspace, limit=10, offset=0)

    assert serialized_size(page) <= 4096
    withheld = page["candidates"][0]
    assert withheld["candidate_id"] == "cand-0000"
    assert withheld["withheld"] is True
    assert withheld["validation_error_count"] == 40
    assert page["next_offset"] == 1


@pytest.mark.parametrize("offset", [-3, 99])
def test_invalid_builder_offset_is_explicit(offset):
    workspace = _workspace(5)

    page = agr_curation._builder_candidate_list(workspace, limit=10, offset=offset)

    assert page["status"] == "invalid_request"
    assert page["error_code"] == "invalid_result_cursor"
    assert page["candidates"] == []
    assert page["candidate_count"] == 5
