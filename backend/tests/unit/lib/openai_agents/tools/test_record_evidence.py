"""Unit tests for the record_evidence document tool."""

import json
from pathlib import Path

import pytest

import src.lib.openai_agents.tools.record_evidence as record_evidence
from src.lib.openai_agents.evidence_summary import build_evidence_record_id
from tests.fixtures.evidence.harness import chunk_map, load_evidence_fixture, tool_case_map


FIXTURE = load_evidence_fixture()
TOOL_CASES = tool_case_map(FIXTURE)
ALL_292_FIXTURE = json.loads(
    (
        Path(__file__).parents[4]
        / "fixtures"
        / "evidence"
        / "all_292_section_label_chunk_ids.json"
    ).read_text()
)


def _expected_verified_result(
    tool_input: dict[str, object],
    expected_tool_result: dict[str, object],
) -> dict[str, object]:
    enriched_result = {
        **expected_tool_result,
        "entity": tool_input["entity"],
        "chunk_id": tool_input["chunk_id"],
        "claimed_quote": tool_input.get("claimed_quote", ""),
    }

    if expected_tool_result.get("status") != "verified":
        return enriched_result

    enriched_result["evidence_record_id"] = build_evidence_record_id(
        evidence_record={
            "entity": tool_input["entity"],
            "verified_quote": expected_tool_result["verified_quote"],
            "page": expected_tool_result.get("page"),
            "section": expected_tool_result.get("section"),
            "chunk_id": tool_input["chunk_id"],
            "subsection": expected_tool_result.get("subsection"),
            "figure_reference": expected_tool_result.get("figure_reference"),
        }
    )
    return enriched_result


class _Tracker:
    def __init__(self):
        self.calls = []

    def record_call(self, name: str):
        self.calls.append(name)


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)


def test_find_verified_quote_handles_fuzzy_quote_variants():
    case = TOOL_CASES["verified_fuzzy"]
    chunk = chunk_map(FIXTURE)[case["tool_input"]["chunk_id"]]
    quote, match = record_evidence._find_verified_quote(
        case["tool_input"]["claimed_quote"],
        chunk["text"],
    )

    assert quote == case["expected_tool_result"]["verified_quote"]
    assert match is not None
    assert match.score >= 0.87


@pytest.mark.asyncio
async def test_record_evidence_records_exact_match_and_tracker_usage(monkeypatch):
    case = TOOL_CASES["verified_exact"]
    chunks = chunk_map(FIXTURE)
    captured = {}

    async def _fake_get_chunk_by_id(**kwargs):
        captured.update(kwargs)
        return chunks.get(kwargs["chunk_id"])

    tracker = _Tracker()
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-12345678", "user-1", tracker=tracker)

    result = await tool(**case["tool_input"])

    assert result == _expected_verified_result(case["tool_input"], case["expected_tool_result"])
    assert captured == {
        "chunk_id": case["tool_input"]["chunk_id"],
        "user_id": "user-1",
        "document_id": "doc-12345678",
    }
    assert tracker.calls == ["record_evidence"]


@pytest.mark.parametrize(
    "case_id",
    [
        "verified_fuzzy",
        "not_found_absent_quote",
        "not_found_wrong_chunk_id",
    ],
)
@pytest.mark.asyncio
async def test_record_evidence_fixture_cases(monkeypatch, case_id):
    case = TOOL_CASES[case_id]
    chunks = chunk_map(FIXTURE)
    captured = {}

    async def _fake_get_chunk_by_id(**kwargs):
        captured.update(kwargs)
        return chunks.get(kwargs["chunk_id"])

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(**case["tool_input"])

    assert result == _expected_verified_result(case["tool_input"], case["expected_tool_result"])
    assert captured == {
        "chunk_id": case["tool_input"]["chunk_id"],
        "user_id": "user-1",
        "document_id": "doc-123",
    }


@pytest.mark.parametrize(
    "trace_case",
    ALL_292_FIXTURE["record_evidence_calls"],
    ids=lambda trace_case: trace_case["trace_id"][:8],
)
@pytest.mark.asyncio
async def test_record_evidence_resolves_section_label_chunk_id_from_trace_fixture(monkeypatch, trace_case):
    resolved_chunk_id = "1b3651f8-7745-51a0-80f3-b3eafb70a558"
    tool_input = trace_case["tool_input"]

    async def _unexpected_get_chunk_by_id(**_kwargs):
        pytest.fail("section-label chunk IDs should resolve before direct chunk-id lookup")

    def _fake_fetch_document_chunks_for_resolution(document_id, user_id):
        assert document_id == "doc-8325599"
        assert user_id == "user-1"
        return [
            {
                "id": resolved_chunk_id,
                "content": (
                    "Animals and breeding. "
                    f"{tool_input['claimed_quote']} "
                    "Mice were maintained in accordance with institutional protocols."
                ),
                "page_number": 11,
                "parent_section": "Materials and Methods",
                "section_title": "Mouse strains",
                "subsection": "Animals",
                "metadata": {},
                "doc_items": [{"page": 11}],
            }
        ]

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    monkeypatch.setattr(
        record_evidence,
        "fetch_document_chunks_for_resolution",
        _fake_fetch_document_chunks_for_resolution,
    )
    tool = record_evidence.create_record_evidence_tool("doc-8325599", "user-1")

    result = await tool(**tool_input)

    assert result["status"] == "verified"
    assert result["chunk_id"] == resolved_chunk_id
    assert result["input_chunk_id"] == tool_input["chunk_id"]
    assert result["resolution"] == "section_label_quote_match"
    assert result["verified_quote"] == tool_input["claimed_quote"]
    assert result["page"] == 11
    assert result["section"] == "Materials and Methods"
    assert result["subsection"] == "Animals"
    assert result["evidence_record_id"] == build_evidence_record_id(
        evidence_record={
            "entity": tool_input["entity"],
            "verified_quote": tool_input["claimed_quote"],
            "page": 11,
            "section": "Materials and Methods",
            "chunk_id": resolved_chunk_id,
            "subsection": "Animals",
            "figure_reference": None,
        }
    )


@pytest.mark.asyncio
async def test_record_evidence_section_label_chunk_id_returns_actionable_retry_when_unresolved(monkeypatch):
    trace_case = ALL_292_FIXTURE["record_evidence_calls"][1]

    def _no_resolution_chunks(*_args, **_kwargs):
        return []

    monkeypatch.setattr(record_evidence, "fetch_document_chunks_for_resolution", _no_resolution_chunks)
    tool = record_evidence.create_record_evidence_tool("doc-8325599", "user-1")

    result = await tool(**trace_case["tool_input"])

    assert result["status"] == "not_found"
    assert result["chunk_id"] == "Methods_1"
    assert result["invalid_chunk_id_reason"] == "section_label_not_chunk_uuid"
    assert result["retry_tool"] == "search_document"
    assert "section label" in result["message"]
    assert "search_document" in result["message"]
    assert "hit.chunk_id" in result["retry_instructions"]
    assert "evidence_record_id" not in result


@pytest.mark.asyncio
async def test_record_evidence_prefers_pdf_provenance_page_when_chunk_page_is_stale(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-live-repro",
            "text": "Actin 87E accumulated to a higher molar abundance in mutant fly eyes.",
            "page_number": 1,
            "parent_section": "Results and Discussion",
            "subsection": "2.3. The molar abundance of actins, optins, and crumbs in fly eyes",
            "doc_items": [
                {"page": 6},
                {"page": 6},
            ],
            "metadata": {},
        }

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(
        entity="Act 87E",
        chunk_id="chunk-live-repro",
        claimed_quote="Actin 87E accumulated to a higher molar abundance in mutant fly eyes.",
    )

    expected = {
        "status": "verified",
        "verified_quote": "Actin 87E accumulated to a higher molar abundance in mutant fly eyes.",
        "page": 6,
        "section": "Results and Discussion",
        "subsection": "2.3. The molar abundance of actins, optins, and crumbs in fly eyes",
    }
    assert result == _expected_verified_result(
        {
            "entity": "Act 87E",
            "chunk_id": "chunk-live-repro",
            "claimed_quote": "Actin 87E accumulated to a higher molar abundance in mutant fly eyes.",
        },
        expected,
    )


@pytest.mark.asyncio
async def test_record_evidence_prefers_pdfx_page_no_provenance_when_chunk_page_is_stale(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-pdfx-page-no-repro",
            "text": (
                "Actin 5C at 344 +/- 23 fmoles/eye is the most abundant among all actins, "
                "followed by Actin 87E (80 +/- 51 fmoles/eye)."
            ),
            "page_number": 1,
            "parent_section": "Results and Discussion",
            "subsection": "The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes",
            "doc_items": [
                {"page_no": 3},
                {"page_no": 3},
            ],
            "metadata": {},
        }

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(
        entity="Actin 87E",
        chunk_id="chunk-pdfx-page-no-repro",
        claimed_quote="followed by Actin 87E (80 +/- 51 fmoles/eye).",
    )

    expected = {
        "status": "verified",
        "verified_quote": "followed by Actin 87E (80 +/- 51 fmoles/eye).",
        "page": 3,
        "section": "Results and Discussion",
        "subsection": "The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes",
    }
    assert result == _expected_verified_result(
        {
            "entity": "Actin 87E",
            "chunk_id": "chunk-pdfx-page-no-repro",
            "claimed_quote": "followed by Actin 87E (80 +/- 51 fmoles/eye).",
        },
        expected,
    )
