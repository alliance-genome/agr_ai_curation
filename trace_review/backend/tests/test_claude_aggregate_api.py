import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.api import claude


TRACE_ID = "856df16f1752cb53ee43dcb2f5ecfd16"
GENERIC_VIEWS = (
    "token_analysis",
    "agent_context",
    "pdf_citations",
    "document_hierarchy",
    "agent_configs",
    "group_context",
    "trace_summary",
    "domain_envelope",
    "extraction_timeline",
)


def _provider_chars(response) -> int:
    return len(json.dumps({
        "status": response.status,
        "data": response.data,
        "token_info": response.token_info.model_dump(),
        "error": None,
    }, default=str))


def _large_view() -> dict:
    return {
        "status": "complete",
        "records": [
            {
                "record_id": f"record-{index}",
                "lossless_detail": f"detail-{index}-" * 80,
            }
            for index in range(40)
        ],
    }


@pytest.mark.asyncio
async def test_every_registered_generic_view_is_summary_first_and_provider_bounded():
    analyzed = {"analysis": {name: _large_view() for name in GENERIC_VIEWS}}
    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        for view_name in GENERIC_VIEWS:
            response = await claude.get_trace_view(
                TRACE_ID,
                view_name,
                Mock(),
                source="local",
                section=None,
                offset=0,
                limit=claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            )
            assert response.data["view"] == view_name
            assert response.data["page"] is None
            assert {item["section"] for item in response.data["collections"]} == {
                "status",
                "records",
            }
            records_inventory = next(
                item for item in response.data["collections"]
                if item["section"] == "records"
            )
            assert records_inventory["complete"] is False
            assert records_inventory["truncated"] is True
            assert records_inventory["next_call"] == {
                "trace_id": TRACE_ID,
                "view_name": view_name,
                "section": "records",
                "offset": 0,
                "limit": claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            }
            assert _provider_chars(response) <= claude.TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
            assert response.token_info.serialized_chars == _provider_chars(response)


@pytest.mark.asyncio
async def test_large_generic_view_pages_are_lossless_with_executable_continuations():
    view = _large_view()
    analyzed = {"analysis": {"token_analysis": view}}
    reconstructed = []
    offset = 0
    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        while True:
            response = await claude.get_trace_view(
                TRACE_ID,
                "token_analysis",
                Mock(),
                source="local",
                section="records",
                offset=offset,
                limit=claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            )
            page = response.data["page"]
            reconstructed.extend(page["items"])
            assert page["returned_items"] > 0
            assert _provider_chars(response) <= claude.TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
            assert response.token_info.serialized_chars == _provider_chars(response)
            if page["complete"]:
                assert page["truncated"] is False
                assert page["next_call"] is None
                break
            assert page["truncated"] is True
            assert page["next_call"] == {
                "trace_id": TRACE_ID,
                "view_name": "token_analysis",
                "section": "records",
                "offset": page["next_offset"],
                "limit": claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
            }
            offset = page["next_offset"]

    assert reconstructed == view["records"]


@pytest.mark.asyncio
async def test_evidence_revisions_view_has_bounded_inventory_and_filter_metadata():
    evidence = {
        "schema_version": "evidence_revisions.v1",
        "summary": {"evidence_record_count": 100, "scope_refusal_count": 20},
        "evidence_records": [{"evidence_record_id": f"e-{index}"} for index in range(100)],
        "scope_refusals": [{"message": f"refusal-{index}"} for index in range(20)],
        "query": {"tool_name": "record_evidence", "candidate_id": "candidate-1"},
    }
    with (
        patch(
            "src.api.claude.load_extraction_timeline_context",
            new=AsyncMock(return_value=SimpleNamespace()),
        ),
        patch("src.api.claude.build_evidence_revisions", return_value=evidence),
    ):
        response = await claude.get_trace_view(
            TRACE_ID,
            "evidence_revisions",
            Mock(),
            source="local",
            section=None,
            offset=0,
            limit=claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE,
        )

    assert response.data["summary"]["evidence_record_count"] == 100
    assert response.data["filters"]["tool_name"] == "record_evidence"
    assert response.data["page"] is None
    assert _provider_chars(response) <= claude.TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
