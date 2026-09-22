"""ALL-1278: evidence workspace tools stay inside the model-facing result budget."""

from __future__ import annotations

import pytest

import src.lib.openai_agents.tool_result_bounds as tool_result_bounds
import src.lib.openai_agents.tools.evidence_workspace as evidence_workspace
from src.lib.openai_agents.tool_result_bounds import serialized_size

UNICODE_TEXT = "β-catenin 表达 in Drosophila wing disc 😀 "


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(evidence_workspace, "function_tool", lambda fn: fn)


@pytest.fixture
def reported(monkeypatch):
    calls = []

    def _capture(violation, **kwargs):
        calls.append((violation, kwargs))
        return True

    monkeypatch.setattr(tool_result_bounds, "report_payload_contract_violation", _capture)
    return calls


def _record(index: int, *, note_chars: int = 0, quote: str | None = None) -> dict:
    record_id = f"ev-{index:04d}"
    return {
        "evidence_record_id": record_id,
        "entity": f"gene-{index}",
        "verified_quote": quote if quote is not None else f"{UNICODE_TEXT}{index}",
        "page": 1 + index % 9,
        "section": "Results",
        "chunk_id": f"chunk-{index}",
        "document_id": "doc-1",
        "source_span_ids": [f"chunk-{index}:s0000:c0000-c0010:aaaabbbb"],
        "envelope_targets": [{"object_id": f"obj-{index}", "field_path": "gene"}],
        "agent_note": (UNICODE_TEXT * (note_chars // len(UNICODE_TEXT) + 1))[:note_chars]
        if note_chars
        else None,
    }


def _tool(factory, records, **kwargs):
    return factory("doc-1", "user-1", workspace_records=records, **kwargs)


async def _page_all(list_tool, **kwargs):
    seen = []
    offset = 0
    pages = 0
    while True:
        page = await list_tool(offset=offset, **kwargs)
        assert page["status"] == "ok"
        pages += 1
        seen.extend(item["evidence_record_id"] for item in page["evidence_records"])
        if page["next_offset"] is None:
            return seen, pages
        assert page["next_offset"] > offset
        offset = page["next_offset"]


@pytest.mark.asyncio
async def test_huge_requested_limit_clamps_to_configured_maximum(monkeypatch, reported):
    monkeypatch.setenv("EVIDENCE_LIST_MAX_LIMIT", "25")
    list_tool = _tool(
        evidence_workspace.create_list_recorded_evidence_tool,
        [_record(index) for index in range(300)],
    )

    page = await list_tool(limit=10**9)

    assert page["returned_count"] <= 25
    assert page["requested_limit"] == 10**9
    assert page["effective_limit"] == 25
    assert page["limit_clamped"] is True
    assert page["next_offset"] == page["returned_count"]
    assert page["count"] == 300
    assert reported == []  # clamping is normal paging, not an alert


@pytest.mark.asyncio
async def test_wide_records_page_by_serialized_size_without_duplicates(
    monkeypatch, reported
):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "8192")
    records = [_record(index, note_chars=900) for index in range(120)]
    list_tool = _tool(evidence_workspace.create_list_recorded_evidence_tool, records)

    first = await list_tool(limit=100)
    assert serialized_size(first) <= 8192
    assert first["page_ended_by"] == "size_budget"
    assert 0 < first["returned_count"] < 100

    seen, pages = await _page_all(list_tool, limit=100)
    assert seen == [record["evidence_record_id"] for record in records]
    assert pages > 1
    assert reported == []


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, 121])
async def test_invalid_or_stale_offset_is_an_explicit_error(offset):
    list_tool = _tool(
        evidence_workspace.create_list_recorded_evidence_tool,
        [_record(index) for index in range(120)],
    )

    result = await list_tool(offset=offset)

    assert result["status"] == "invalid_request"
    assert result["error_code"] == "invalid_result_cursor"
    assert result["count"] == 120


@pytest.mark.asyncio
async def test_oversized_single_quote_is_read_exactly_in_bounded_chunks(
    monkeypatch
):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    quote = (UNICODE_TEXT * 2500)[:60000]
    get_tool = _tool(evidence_workspace.create_get_recorded_evidence_tool, [_record(1, quote=quote)])

    first = await get_tool("ev-0001")

    assert serialized_size(first) <= 4096
    withheld = {item["detail_path"]: item for item in first["result_page"]["withheld_fields"]}
    assert "record.verified_quote" in withheld
    descriptor = withheld["record.verified_quote"]
    assert descriptor["total_chars"] == len(quote)

    chunks = []
    cursor = 0
    while cursor is not None:
        chunk = await get_tool(
            "ev-0001",
            detail_path="record.verified_quote",
            detail_cursor=cursor,
            result_sha256=first["result_page"]["result_sha256"],
        )
        assert serialized_size(chunk) <= 4096
        assert chunk["detail"]["sha256"] == descriptor["sha256"]
        chunks.append(chunk["detail"]["content"])
        cursor = chunk["detail"]["next_cursor"]
    assert "".join(chunks) == quote


@pytest.mark.asyncio
async def test_detail_read_reports_changed_record_as_stale(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    records = [_record(1, quote="x" * 20000)]
    get_tool = _tool(evidence_workspace.create_get_recorded_evidence_tool, records)
    first = await get_tool("ev-0001")
    records[0]["verified_quote"] = "y" * 20000

    stale = await get_tool(
        "ev-0001",
        detail_path="record.verified_quote",
        detail_cursor=0,
        result_sha256=first["result_page"]["result_sha256"],
    )

    assert stale["error_code"] == "stale_result_cursor"


@pytest.mark.asyncio
async def test_detail_reads_keep_document_and_validator_scope():
    records = [_record(1, quote="q" * 50)]
    records.append({**_record(2), "document_id": "doc-2"})
    get_tool = _tool(evidence_workspace.create_get_recorded_evidence_tool, records)
    scoped = _tool(
        evidence_workspace.create_get_recorded_evidence_tool,
        records,
        allowed_evidence_record_ids={"ev-0001"},
    )

    other_document = await get_tool(
        "ev-0002", detail_path="record.verified_quote", detail_cursor=0
    )
    out_of_scope = await scoped(
        "ev-0002", detail_path="record.verified_quote", detail_cursor=0
    )

    assert other_document["status"] == "not_found"
    assert out_of_scope["status"] == "forbidden"


@pytest.mark.asyncio
async def test_mutation_acknowledgment_omits_quote_and_stays_bounded(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    records = [_record(1, quote="long quote " * 3000)]
    attach = _tool(evidence_workspace.create_attach_evidence_to_object_tool, records)
    update = _tool(evidence_workspace.create_update_recorded_evidence_metadata_tool, records)

    attached = await attach("ev-0001", field_path="anatomy", object_id="obj-9")
    noted = await update("ev-0001", agent_note="n" * 50000)

    assert attached["status"] == "ok"
    assert "verified_quote" not in attached["record"]
    assert attached["record"]["verified_quote_chars"] == len(records[0]["verified_quote"])
    assert serialized_size(attached) <= 4096
    assert noted["status"] == "ok"
    assert noted["record"]["withheld"] is True
    assert noted["record"]["evidence_record_id"] == "ev-0001"
    assert serialized_size(noted) <= 4096
    # The workspace keeps the full values.
    assert records[0]["agent_note"] == "n" * 50000


@pytest.mark.asyncio
async def test_unmeetable_budget_returns_compact_failure_and_reports_once(
    monkeypatch, reported
):
    monkeypatch.setattr(evidence_workspace, "tool_result_budget", lambda: 64)
    list_tool = _tool(
        evidence_workspace.create_list_recorded_evidence_tool,
        [_record(index) for index in range(5)],
    )

    result = await list_tool()

    assert result["error_code"] == "tool_result_budget_unmet"
    assert result["result_bounds"]["limit_bytes"] == 64
    assert len(reported) == 1
    violation, kwargs = reported[0]
    assert violation.category == "tool_result_budget_escape"
    assert kwargs["tool_name"] == "list_recorded_evidence"
