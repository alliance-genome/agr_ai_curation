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


def test_find_verified_quote_requires_exact_source_substring():
    chunk_text = "Alpha beta gamma delta."

    quote, match = record_evidence._find_verified_quote(" beta gamma ", chunk_text)

    assert quote == "beta gamma"
    assert match is not None
    assert match.raw_start == 6
    assert match.raw_end == 16


@pytest.mark.parametrize(
    "claimed_quote",
    [
        "Alpha beta delta.",
        "Alpha beta gamma.",
        "Alpha beta gamma inserted delta.",
        "alpha beta gamma delta.",
    ],
)
def test_find_verified_quote_rejects_omitted_inserted_or_changed_text(claimed_quote):
    quote, match = record_evidence._find_verified_quote(
        claimed_quote,
        "Alpha beta gamma delta.",
    )

    assert quote is None
    assert match is None


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
        "not_found_changed_quote",
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


@pytest.mark.asyncio
async def test_record_evidence_returns_terminal_unverified_after_repeated_entity_chunk_attempts(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-retry",
            "text": "Exact source text names the retained allele.",
            "page_number": 3,
            "parent_section": "Methods",
            "metadata": {},
        }

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    first = await tool(
        entity="retained allele",
        chunk_id="chunk-retry",
        claimed_quote="Approximate source text names the retained allele.",
    )
    second = await tool(
        entity="retained allele",
        chunk_id="chunk-retry",
        claimed_quote="Approximate source text names the retained allele.",
    )
    third = await tool(
        entity="retained allele",
        chunk_id="chunk-retry",
        claimed_quote="Approximate source text names the retained allele.",
    )

    assert first["status"] == "not_found"
    assert first["retry_exhausted"] is False
    assert first["terminal"] is False
    assert first["unverified_attempts"] == 1
    assert second["unverified_attempts"] == 2
    assert third["status"] == "not_found"
    assert third["retry_exhausted"] is True
    assert third["terminal"] is True
    assert third["unverified_attempts"] == 3
    assert "Stop retrying" in third["message"]
    assert "evidence_record_id" not in third


@pytest.mark.asyncio
async def test_record_evidence_regression_rejects_all_341_neighboring_allele_quotes(monkeypatch):
    chunk_id = "b247a1a2-a6fa-2176-46ff-b814431e61c8"
    chunk_text = (
        "DBHCre (Strain NO. 033951) mice were kindly provided by Dr. Patricia Jensen, "
        "National Institute of Health and Dr. Ming O Li, Memorial Sloan Kettering Cancer Center. "
        "LSL-DTR (Strain NO. 007900) mice were kindly provided by Dr. Ming O Li, "
        "Memorial Sloan Kettering Cancer Center. "
        "CD4-/- (Strain NO. S-KO-01417) mice were purchased from Cyagen."
    )

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return {
            "id": chunk_id,
            "text": chunk_text,
            "page_number": 22,
            "parent_section": "Methods",
            "subsection": "Mice",
            "metadata": {},
        }

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool(
        "c86ebc60-ae69-4591-baa0-071fc5dee5af",
        "user-1",
    )

    lsl_dta_result = await tool(
        entity="LSL-DTA",
        chunk_id=chunk_id,
        claimed_quote=(
            "LSL-DTA (Strain NO. 009669) mice were kindly provided by Dr. Ming O Li, "
            "Memorial Sloan Kettering Cancer Center."
        ),
    )
    cd8_result = await tool(
        entity="CD8a-/-",
        chunk_id=chunk_id,
        claimed_quote="CD8a-/- (Strain NO. S-KO-01440) mice were purchased from Cyagen.",
    )
    cd4_result = await tool(
        entity="CD4-/-",
        chunk_id=chunk_id,
        claimed_quote="CD4-/- (Strain NO. S-KO-01417) mice were purchased from Cyagen.",
    )

    assert lsl_dta_result["status"] == "not_found"
    assert lsl_dta_result["entity"] == "LSL-DTA"
    assert "evidence_record_id" not in lsl_dta_result
    assert "verified_quote" not in lsl_dta_result
    assert "LSL-DTR (Strain NO. 007900)" in lsl_dta_result["chunk_content_preview"]

    assert cd8_result["status"] == "not_found"
    assert cd8_result["entity"] == "CD8a-/-"
    assert "evidence_record_id" not in cd8_result
    assert "verified_quote" not in cd8_result
    assert "CD4-/-" in cd8_result["chunk_content_preview"]

    assert cd4_result["status"] == "verified"
    assert cd4_result["verified_quote"] == (
        "CD4-/- (Strain NO. S-KO-01417) mice were purchased from Cyagen."
    )


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
