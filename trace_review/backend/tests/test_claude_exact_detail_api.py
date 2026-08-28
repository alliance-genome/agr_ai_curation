import hashlib
import json
from typing import Literal
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.api import claude
from src.services.langfuse_run_reconstruction import serialize_payload


TRACE_ID = "856df16f1752cb53ee43dcb2f5ecfd16"


def _conversation_provider_result(chunk: dict) -> dict:
    data = {
        "field": "assistant_response",
        "chunk": chunk,
        "domain_envelope": None,
    }
    return {
        "status": "success",
        "data": data,
        "token_info": claude.create_token_info_dict(data),
        "error": None,
    }


def _large_analysis(call_count: int = 7) -> dict:
    tool_calls = []
    for index in range(call_count):
        tool_calls.append(
            {
                "id": f"observation-{index}",
                "call_id": f"call-{index}",
                "name": "inspect_large_result",
                "time": f"2026-08-28T00:00:{index:02d}Z",
                "duration": "1ms",
                "status": "ok",
                "input": {
                    "index": index,
                    "text": (f"input-{index}-" * 1400),
                },
                "tool_result": {
                    "summary": "S" * 5000,
                    "rows": [f"result-{index}-{row}" * 80 for row in range(200)],
                },
            }
        )
    return {
        "analysis": {
            "tool_calls": {
                "total_count": call_count,
                "unique_tools": ["inspect_large_result"],
                "duplicates": {"has_duplicates": False},
                "tool_calls": tool_calls,
            },
            "conversation": {
                "user_input": "question-" * 2500,
                "assistant_response": "answer-" * 3500,
            },
        }
    }


def _analyzer_shaped_analysis() -> dict:
    large_arguments = "😀\\\"argument-" * 2000
    legacy_result = "legacy-result-\\\"" * 2000
    calls = [
        {
            "id": f"generation-{index}",
            "call_id": "N/A",
            "name": "search_document",
            "input": {"query": large_arguments, "ordinal": index},
            "output": {
                "type": "function_call",
                "name": "search_document",
                "arguments": json.dumps({"query": large_arguments}),
                "status": "completed",
            },
            "tool_result": {"summary": f"result {index}", "raw": f"rows-{index}" * 3000},
        }
        for index in range(2)
    ]
    calls.append(
        {
            "id": "legacy-tool-observation",
            "name": "legacy_tool",
            "input": {"query": "legacy"},
            "output": legacy_result,
            "tool_result": None,
        }
    )
    return {
        "analysis": {
            "tool_calls": {
                "total_count": len(calls),
                "unique_tools": ["search_document", "legacy_tool"],
                "duplicates": {"has_duplicates": False},
                "tool_calls": calls,
            },
            "conversation": {},
        }
    }


@pytest.mark.asyncio
async def test_tool_call_summary_and_page_are_bounded_exact_reference_contracts():
    analyzed = _large_analysis()
    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        summary = await claude.get_tool_calls_summary(
            TRACE_ID,
            Mock(),
            page=2,
            page_size=3,
            source="local",
        )
        page = await claude.get_tool_calls_paginated(
            TRACE_ID,
            Mock(),
            page=1,
            page_size=2,
            tool_name=None,
            source="local",
        )

    assert [item.index for item in summary.data.tool_calls] == [3, 4, 5]
    assert summary.data.pagination.has_next is True
    assert summary.data.pagination.page == 2
    assert summary.data.next_call == {
        "trace_id": TRACE_ID,
        "page": 3,
        "page_size": 3,
    }
    assert all(
        len(item.result_summary) <= claude.TRACE_REVIEW_SUMMARY_MAX_CHARS
        for item in summary.data.tool_calls
    )

    assert len(page.tool_calls) == 2
    assert page.next_call == {
        "trace_id": TRACE_ID,
        "page": 2,
        "page_size": 2,
    }
    first = page.tool_calls[0]
    assert "input" not in first
    assert "tool_result" not in first
    fields = {item["field"]: item for item in first["exact_fields"]}
    expected_input = serialize_payload(analyzed["analysis"]["tool_calls"]["tool_calls"][0]["input"])
    assert fields["input"]["field_id"] == "tool_call:call-0:input"
    assert fields["input"]["sha256"] == hashlib.sha256(expected_input.encode("utf-8")).hexdigest()
    assert fields["input"]["next_call"] == {
        "trace_id": TRACE_ID,
        "call_id": "call-0",
        "field": "input",
        "start": 0,
        "max_chars": claude.TRACE_REVIEW_CHUNK_MAX_CHARS,
    }


@pytest.mark.asyncio
async def test_analyzer_formats_have_unique_selectors_and_no_inline_exact_output():
    analyzed = _analyzer_shaped_analysis()
    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        page = await claude.get_tool_calls_paginated(
            TRACE_ID,
            Mock(),
            page=1,
            page_size=3,
            tool_name=None,
            source="local",
        )
        second_call = await claude.get_tool_call_detail(
            TRACE_ID,
            "generation-1",
            Mock(),
            field="input",
            start=0,
            max_chars=claude.TRACE_REVIEW_CHUNK_MAX_CHARS,
            source="local",
        )
    legacy_reconstructed, legacy_chunks = await _reconstruct_tool_field(
        analyzed,
        "tool_result",
        call_id="legacy-tool-observation",
    )

    assert [call["call_id"] for call in page.tool_calls] == [
        "generation-0",
        "generation-1",
        "legacy-tool-observation",
    ]
    assert all("output" not in call for call in page.tool_calls)
    assert all("input" not in call and "tool_result" not in call for call in page.tool_calls)
    legacy_fields = {item["field"]: item for item in page.tool_calls[2]["exact_fields"]}
    expected_legacy = analyzed["analysis"]["tool_calls"]["tool_calls"][2]["output"]
    assert legacy_fields["tool_result"]["sha256"] == hashlib.sha256(
        expected_legacy.encode("utf-8")
    ).hexdigest()
    assert "output" not in second_call.tool_call
    assert second_call.chunk["field_id"] == "tool_call:generation-1:input"
    assert legacy_reconstructed == expected_legacy
    assert {chunk["sha256"] for chunk in legacy_chunks} == {
        hashlib.sha256(expected_legacy.encode("utf-8")).hexdigest()
    }
    assert len(json.dumps(page.model_dump())) < claude.TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
    assert len(json.dumps(second_call.model_dump())) < claude.TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS


async def _reconstruct_tool_field(
    analyzed: dict,
    field: Literal["input", "tool_result"],
    call_id: str = "call-0",
) -> tuple[str, list[dict]]:
    chunks = []
    start = 0
    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        while True:
            response = await claude.get_tool_call_detail(
                TRACE_ID,
                call_id,
                Mock(),
                field=field,
                start=start,
                max_chars=2048,
                source="local",
            )
            chunks.append(response.chunk)
            if response.chunk["complete"]:
                break
            assert response.chunk["next_call"] == {
                "trace_id": TRACE_ID,
                "call_id": call_id,
                "field": field,
                "start": response.chunk["end"],
                "max_chars": 2048,
            }
            start = response.chunk["next_start"]
    return "".join(chunk["serialized"] for chunk in chunks), chunks


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["input", "tool_result"])
async def test_large_tool_call_fields_reconstruct_exactly_with_stable_hashes(
    field: Literal["input", "tool_result"],
):
    analyzed = _large_analysis(call_count=1)
    reconstructed, chunks = await _reconstruct_tool_field(analyzed, field)
    expected = serialize_payload(analyzed["analysis"]["tool_calls"]["tool_calls"][0][field])

    assert reconstructed == expected
    assert chunks[-1]["complete"] is True
    assert chunks[-1]["next_call"] is None
    assert {chunk["field_id"] for chunk in chunks} == {f"tool_call:call-0:{field}"}
    assert {chunk["sha256"] for chunk in chunks} == {
        hashlib.sha256(expected.encode("utf-8")).hexdigest()
    }
    assert [(chunk["start"], chunk["end"]) for chunk in chunks] == [
        (index * 2048, min((index + 1) * 2048, len(expected)))
        for index in range(len(chunks))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "analyzer_field"),
    [("user_query", "user_input"), ("assistant_response", "assistant_response")],
)
async def test_large_conversation_fields_reconstruct_exactly(
    field: Literal["user_query", "assistant_response"],
    analyzer_field: str,
):
    analyzed = _large_analysis(call_count=0)
    expected = analyzed["analysis"]["conversation"][analyzer_field]
    chunks = []
    start = 0
    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        while True:
            response = await claude.get_trace_conversation(
                TRACE_ID,
                Mock(),
                field=field,
                start=start,
                max_chars=1024,
                source="local",
            )
            chunks.append(response.data.chunk)
            if response.data.chunk["complete"]:
                break
            start = response.data.chunk["next_start"]

    assert "".join(chunk["serialized"] for chunk in chunks) == expected
    assert {chunk["field_id"] for chunk in chunks} == {f"conversation:{field}"}
    assert {chunk["sha256"] for chunk in chunks} == {
        hashlib.sha256(expected.encode("utf-8")).hexdigest()
    }


def test_default_exact_chunk_leaves_json_envelope_headroom():
    value = "realistic payload text " * 1000
    chunk = claude._exact_text_chunk(
        field_id="conversation:assistant_response",
        field="assistant_response",
        value=value,
        start=0,
        max_chars=claude.TRACE_REVIEW_CHUNK_MAX_CHARS,
        next_call={"field": "assistant_response"},
        provider_result_builder=_conversation_provider_result,
    )

    assert chunk["returned_char_count"] == claude.TRACE_REVIEW_CHUNK_MAX_CHARS
    assert len(json.dumps({"status": "success", "data": {"chunk": chunk}})) < 12_000


@pytest.mark.parametrize("value", ["😀" * 10_000, "\\\"quoted\\\\value" * 2000])
def test_default_exact_chunk_accounts_for_provider_json_escaping(value: str):
    chunk = claude._exact_text_chunk(
        field_id="conversation:assistant_response",
        field="assistant_response",
        value=value,
        start=0,
        max_chars=claude.TRACE_REVIEW_CHUNK_MAX_CHARS,
        next_call={"trace_id": TRACE_ID, "field": "assistant_response"},
        provider_result_builder=_conversation_provider_result,
    )
    response = _conversation_provider_result(chunk)

    assert chunk["end"] <= claude.TRACE_REVIEW_CHUNK_MAX_CHARS
    assert chunk["returned_char_count"] == len(chunk["serialized"])
    assert len(json.dumps(response, default=str)) < claude.TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS
    assert chunk["next_call"]["start"] == chunk["end"]


@pytest.mark.asyncio
async def test_conversation_chunks_fit_complete_reduced_provider_envelope(monkeypatch):
    value = "ordinary exact conversation text " * 400
    analyzed = _large_analysis(call_count=0)
    analyzed["analysis"]["conversation"]["assistant_response"] = value
    monkeypatch.setattr(claude, "TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS", 1000)
    chunks = []
    start = 0

    with patch("src.api.claude._ensure_trace_analyzed", new=AsyncMock(return_value=analyzed)):
        while True:
            response = await claude.get_trace_conversation(
                TRACE_ID,
                Mock(),
                field="assistant_response",
                start=start,
                max_chars=claude.TRACE_REVIEW_CHUNK_MAX_CHARS,
                source="local",
            )
            provider_result = {
                "status": "success",
                "data": response.data.model_dump(),
                "token_info": response.token_info.model_dump(),
                "error": None,
            }
            assert len(json.dumps(provider_result, default=str)) <= 1000
            chunks.append(response.data.chunk)
            if response.data.chunk["complete"]:
                break
            start = response.data.chunk["next_start"]

    assert "".join(chunk["serialized"] for chunk in chunks) == value
    assert all(chunk["returned_char_count"] > 0 for chunk in chunks)


def test_exact_chunk_fails_when_required_envelope_cannot_fit(monkeypatch):
    monkeypatch.setattr(claude, "TRACE_REVIEW_PROVIDER_INLINE_MAX_CHARS", 1)

    with pytest.raises(claude.HTTPException) as exc_info:
        claude._exact_text_chunk(
            field_id="conversation:assistant_response",
            field="assistant_response",
            value="exact text",
            start=0,
            max_chars=claude.TRACE_REVIEW_CHUNK_MAX_CHARS,
            next_call={"trace_id": TRACE_ID, "field": "assistant_response"},
            provider_result_builder=_conversation_provider_result,
        )

    assert exc_info.value.status_code == 400
    assert "too small to return even one exact TraceReview character" in exc_info.value.detail


def _large_payload_trace() -> dict:
    observations = []
    for index in range(12):
        observations.append(
            {
                "id": f"observation-{index}",
                "name": f"large-observation-{index}",
                "type": "SPAN",
                "startTime": f"2026-08-28T01:00:{index:02d}Z",
                "input": f"observation-input-{index}-" * 800,
                "output": f"observation-output-{index}-" * 800,
            }
        )
    return {
        "raw_trace": {
            "id": TRACE_ID,
            "name": "large payload trace",
            "timestamp": "2026-08-28T01:00:00Z",
            "input": "trace-input-" * 1800,
            "output": "trace-output-" * 1800,
        },
        "observations": observations,
        "scores": [],
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_payload_inventory_page_and_exact_payload_chunks_stay_bounded_and_reconstruct():
    trace_data = _large_payload_trace()
    with patch("src.api.claude.TraceExtractor") as extractor_cls:
        extractor_cls.return_value.extract_complete_trace.return_value = trace_data
        inventory = await claude.get_langfuse_payloads(
            TRACE_ID,
            source="local",
            sort="chronological",
            limit=claude.TRACE_REVIEW_PAGE_SIZE,
            offset=0,
            section="payloads",
        )

        payloads = inventory.data["page"]["items"]
        assert len(payloads) == claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE
        assert all("value" not in payload and "serialized" not in payload for payload in payloads)
        assert inventory.data["page"]["next_offset"] == claude.TRACE_REVIEW_AGGREGATE_PAGE_SIZE
        assert len(json.dumps(inventory.model_dump())) < 12_000

        payload_id = f"trace:{TRACE_ID}:output"
        expected = trace_data["raw_trace"]["output"]
        chunks = []
        start = 0
        while True:
            response = await claude.get_langfuse_payload(
                TRACE_ID,
                source="local",
                payload_id=payload_id,
                scope=None,
                observation_id=None,
                field=None,
                start=start,
                max_chars=1024,
            )
            chunk = response.data["payload"]
            chunks.append(chunk)
            if chunk["complete"]:
                break
            assert chunk["next_call"] == {
                "trace_id": TRACE_ID,
                "payload_id": payload_id,
                "start": chunk["end"],
                "max_chars": 1024,
            }
            start = chunk["next_start"]

    assert "".join(chunk["serialized"] for chunk in chunks) == expected
    assert {chunk["sha256"] for chunk in chunks} == {
        hashlib.sha256(expected.encode("utf-8")).hexdigest()
    }
    assert chunks[-1]["complete"] is True
    assert chunks[-1]["next_call"] is None
