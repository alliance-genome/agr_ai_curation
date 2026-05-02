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
async def test_record_evidence_rejects_section_label_chunk_id_from_trace_fixture(monkeypatch, trace_case):
    tool_input = trace_case["tool_input"]

    async def _unexpected_get_chunk_by_id(**_kwargs):
        pytest.fail("section-label chunk IDs should be rejected before direct chunk-id lookup")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-8325599", "user-1")

    result = await tool(**tool_input)

    assert result["status"] == "not_found"
    assert result["chunk_id"] == tool_input["chunk_id"]
    assert result["invalid_chunk_id"] == tool_input["chunk_id"]
    assert result["invalid_chunk_id_reason"] == "not_a_tool_returned_chunk_id"
    assert result["retry_tool"] == "search_document"
    assert "search_document" in result["message"]
    assert "section.source_chunks[].chunk_id" in result["message"]
    assert "hit.chunk_id" in result["retry_instructions"]
    assert "section.source_chunks[].chunk_id" in result["retry_instructions"]
    assert "evidence_record_id" not in result


@pytest.mark.asyncio
async def test_record_evidence_rejects_uncommon_section_label_without_auto_resolution(monkeypatch):
    tool_input = {
        "entity": "example allele",
        "chunk_id": "Experimental_Procedures_2",
        "claimed_quote": "The allele was generated with a two-step targeting protocol.",
    }

    async def _unexpected_get_chunk_by_id(**_kwargs):
        pytest.fail("non-tool-returned section labels should be rejected before direct chunk-id lookup")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-uncommon-section", "user-1")

    result = await tool(**tool_input)

    assert result["status"] == "not_found"
    assert result["chunk_id"] == "Experimental_Procedures_2"
    assert result["invalid_chunk_id_reason"] == "not_a_tool_returned_chunk_id"
    assert result["retry_tool"] == "search_document"
    assert "search_document" in result["message"]
    assert "evidence_record_id" not in result


@pytest.mark.parametrize("bad_chunk_id", ["chunk_1", "chunk_id_placeholder"])
@pytest.mark.asyncio
async def test_record_evidence_rejects_model_generated_chunk_placeholders(monkeypatch, bad_chunk_id):
    async def _unexpected_get_chunk_by_id(**_kwargs):
        pytest.fail("model-generated placeholder chunk IDs should be rejected before lookup")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-8325599", "user-1")

    result = await tool(
        entity="example allele",
        chunk_id=bad_chunk_id,
        claimed_quote="The allele was generated with a two-step targeting protocol.",
    )

    assert result["status"] == "not_found"
    assert result["chunk_id"] == bad_chunk_id
    assert result["invalid_chunk_id_reason"] == "not_a_tool_returned_chunk_id"
    assert result["retry_tool"] == "search_document"
    assert "hit.chunk_id" in result["retry_instructions"]
    assert "evidence_record_id" not in result


@pytest.mark.parametrize(
    ("entity", "claimed_quote", "closest_quote", "mismatch_reason"),
    [
        (
            "LSL-DTA",
            (
                "LSL-DTA (Strain NO. 009669) mice were kindly provided by Dr. Ming O Li, "
                "Memorial Sloan Kettering Cancer Center."
            ),
            (
                "LSL-DTR (Strain NO. 007900) mice were kindly provided by Dr. Ming O Li, "
                "Memorial Sloan Kettering Cancer Center."
            ),
            "strain_or_stock_identifier_mismatch",
        ),
        (
            "CD8a-/-",
            "CD8a-/- (Strain NO. S-KO-01440) mice were purchased from Cyagen.",
            "CD4-/- (Strain NO. S-KO-01417) mice were purchased from Cyagen.",
            "allele_or_entity_identifier_mismatch",
        ),
    ],
)
@pytest.mark.asyncio
async def test_record_evidence_rejects_adjacent_allele_quote_mismatch(
    monkeypatch,
    entity,
    claimed_quote,
    closest_quote,
    mismatch_reason,
):
    chunk_text = (
        "DBHCre (Strain NO. 033951) mice were kindly provided by Dr. Patricia Jensen, "
        "National Institute of Health and Dr. Ming O Li, Memorial Sloan Kettering Cancer Center. "
        "LSL-DTR (Strain NO. 007900) mice were kindly provided by Dr. Ming O Li, "
        "Memorial Sloan Kettering Cancer Center. "
        "CD4-/- (Strain NO. S-KO-01417) mice were purchased from Cyagen."
    )

    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "b247a1a2-a6fa-2176-46ff-b814431e61c8",
            "text": chunk_text,
            "page_number": 22,
            "parent_section": "Methods",
            "subsection": "Mice",
            "metadata": {},
        }

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-8323314", "user-1")

    result = await tool(
        entity=entity,
        chunk_id="b247a1a2-a6fa-2176-46ff-b814431e61c8",
        claimed_quote=claimed_quote,
    )

    assert result["status"] == "quote_mismatch"
    assert result["needs_retry"] is True
    assert result["closest_quote"] == closest_quote
    assert mismatch_reason in result["mismatch_reasons"]
    assert result["candidate_neighboring_quotes"]
    assert closest_quote in result["candidate_neighboring_quotes"]
    assert result["page"] == 22
    assert result["section"] == "Methods"
    assert result["subsection"] == "Mice"
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
