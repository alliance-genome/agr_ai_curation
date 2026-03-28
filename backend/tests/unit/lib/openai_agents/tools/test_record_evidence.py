"""Unit tests for the record_evidence document tool."""

import pytest

import src.lib.openai_agents.tools.record_evidence as record_evidence


class _Tracker:
    def __init__(self):
        self.calls = []

    def record_call(self, name: str):
        self.calls.append(name)


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)


def test_find_verified_quote_handles_fuzzy_quote_variants():
    quote, match = record_evidence._find_verified_quote(
        "Crb is required for epithelial polarity during embryogenesis.",
        "Crb is required for epithelial polarity during early embryogenesis.",
    )

    assert quote == "Crb is required for epithelial polarity during early embryogenesis."
    assert match is not None
    assert match.score >= 0.87


@pytest.mark.asyncio
async def test_record_evidence_returns_verified_payload(monkeypatch):
    captured = {}

    async def _fake_get_chunk_by_id(**kwargs):
        captured.update(kwargs)
        return {
            "id": "chunk-1",
            "text": "Figure 2A. Crumb is essential for maintaining epithelial polarity in the embryo.",
            "page_number": 4,
            "parent_section": "Results",
            "subsection": "Gene Expression Analysis",
            "metadata": {},
        }

    tracker = _Tracker()
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-12345678", "user-1", tracker=tracker)

    result = await tool(
        entity="crumb",
        chunk_id="chunk-1",
        claimed_quote="Crumb is essential for maintaining epithelial polarity in the embryo.",
    )

    assert result == {
        "status": "verified",
        "verified_quote": "Crumb is essential for maintaining epithelial polarity in the embryo.",
        "page": 4,
        "section": "Results",
        "subsection": "Gene Expression Analysis",
        "figure_reference": "Figure 2A",
    }
    assert captured == {
        "chunk_id": "chunk-1",
        "user_id": "user-1",
        "document_id": "doc-12345678",
    }
    assert tracker.calls == ["record_evidence"]


@pytest.mark.asyncio
async def test_record_evidence_returns_not_found_payload(monkeypatch):
    async def _fake_get_chunk_by_id(**_kwargs):
        return {
            "id": "chunk-2",
            "text": "Crb is required for epithelial polarity during embryogenesis and localizes to the apical membrane.",
            "page_number": 4,
            "parent_section": "Results",
            "subsection": "Gene Expression Analysis",
            "metadata": {},
        }

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(
        entity="crumb",
        chunk_id="chunk-2",
        claimed_quote="Crumb is essential for maintaining epithelial polarity in the embryo.",
    )

    assert result["status"] == "not_found"
    assert result["page"] == 4
    assert result["section"] == "Results"
    assert result["subsection"] == "Gene Expression Analysis"
    assert result["message"] == (
        "Quote not found in this chunk. Retry with text from the chunk or drop this evidence."
    )
    assert result["chunk_content_preview"].startswith(
        "Crb is required for epithelial polarity during embryogenesis"
    )
