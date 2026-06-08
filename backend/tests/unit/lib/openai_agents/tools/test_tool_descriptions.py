"""Model-visible tool description contracts for span-backed PDF evidence."""

import inspect

import src.lib.openai_agents.tools.evidence_workspace as evidence_workspace
import src.lib.openai_agents.tools.record_evidence as record_evidence
import src.lib.openai_agents.tools.weaviate_search as weaviate_search


STALE_TOOL_DESCRIPTION_PHRASES = [
    "claimed_quote",
    "verbatim or lightly trimmed",
    "fuzzy quote",
    "exact contiguous source text copied from that chunk",
]


def _identity_tool(fn):
    return fn


def _tool_doc(tool) -> str:
    return inspect.getdoc(tool) or ""


def _assert_clean_doc(tool_name: str, doc: str) -> None:
    doc_lower = doc.lower()
    stale_hits = [
        phrase
        for phrase in STALE_TOOL_DESCRIPTION_PHRASES
        if phrase.lower() in doc_lower
    ]
    assert stale_hits == [], f"{tool_name} has stale description phrases: {stale_hits}"


def test_document_discovery_tools_point_to_read_chunk_span_selection(monkeypatch):
    monkeypatch.setattr(weaviate_search, "function_tool", _identity_tool)

    docs = {
        "search_document": _tool_doc(weaviate_search.create_search_tool("doc-1", "user-1")),
        "read_chunk": _tool_doc(weaviate_search.create_read_chunk_tool("doc-1", "user-1")),
        "read_section": _tool_doc(weaviate_search.create_read_section_tool("doc-1", "user-1")),
        "read_subsection": _tool_doc(weaviate_search.create_read_subsection_tool("doc-1", "user-1")),
    }

    assert "Discovery tool" in docs["search_document"]
    assert "read_chunk" in docs["search_document"]
    assert "evidence_spans[].span_id" in docs["read_chunk"]
    assert "record_evidence(span_ids=[...])" in docs["read_chunk"]
    assert "Survey" in docs["read_section"]
    assert "read_chunk" in docs["read_section"]
    assert "Survey" in docs["read_subsection"]
    assert "evidence_spans[].span_id" in docs["read_subsection"]

    # Document-search guidance relocated from the gene_expression prompt's
    # <search_infrastructure> block must now live in the tool docstrings so a
    # later task can delete that prompt block without losing the guidance.
    # These tokens are UNIQUE to the relocated prose (not the base docstring's
    # pre-existing search_mode/section_keywords mentions), so they genuinely
    # guard the enrichment: reverting it would drop them and fail this test.
    search_doc = docs["search_document"]
    assert "BM25" in search_doc, (
        "search_document must explain hybrid search blends semantic + BM25 keyword matching"
    )
    assert "cross-encoder" in search_doc, (
        "search_document must explain cross-encoder reranking"
    )
    assert "MMR" in search_doc, (
        "search_document must explain MMR diversification"
    )

    # read_section / read_subsection must convey FULL/survey coverage of a named
    # section via the semantic hierarchy (not page/positional order).
    for read_tool in ("read_section", "read_subsection"):
        doc_lower = docs[read_tool].lower()
        assert "all" in doc_lower, (
            f"{read_tool} must convey it returns ALL chunks of the named section"
        )
        assert "hierarchy" in doc_lower, (
            f"{read_tool} must convey it uses the LLM-resolved semantic hierarchy"
        )
        assert "page" in doc_lower, (
            f"{read_tool} must contrast with linear page order"
        )

    # read_chunk is the evidence-selection step: full text + span ids.
    assert "evidence_spans" in docs["read_chunk"]
    assert "span_id" in docs["read_chunk"]

    for tool_name, doc in docs.items():
        _assert_clean_doc(tool_name, doc)


def test_record_evidence_description_exposes_span_ids_not_quote_text(monkeypatch):
    monkeypatch.setattr(record_evidence, "function_tool", _identity_tool)

    tool = record_evidence.create_record_evidence_tool("doc-1", "user-1")
    doc = _tool_doc(tool)
    signature = inspect.signature(tool)

    assert "span_ids" in signature.parameters
    assert "claimed_quote" not in signature.parameters
    assert "read_chunk evidence span IDs" in doc
    assert "span_ids" in doc
    assert "verified_quote" in doc
    assert "one evidence record" in doc
    _assert_clean_doc("record_evidence", doc)


def test_evidence_workspace_descriptions_cover_review_attach_detach_discard_metadata(monkeypatch):
    monkeypatch.setattr(evidence_workspace, "function_tool", _identity_tool)

    attach_tool = evidence_workspace.create_attach_evidence_to_object_tool("doc-1", "user-1")
    attach_signature = inspect.signature(attach_tool)
    assert attach_signature.parameters["field_path"].default is inspect.Parameter.empty

    docs = {
        "list_recorded_evidence": _tool_doc(
            evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")
        ),
        "get_recorded_evidence": _tool_doc(
            evidence_workspace.create_get_recorded_evidence_tool("doc-1", "user-1")
        ),
        "attach_evidence_to_object": _tool_doc(attach_tool),
        "detach_evidence_from_object": _tool_doc(
            evidence_workspace.create_detach_evidence_from_object_tool("doc-1", "user-1")
        ),
        "discard_recorded_evidence": _tool_doc(
            evidence_workspace.create_discard_recorded_evidence_tool("doc-1", "user-1")
        ),
        "update_recorded_evidence_metadata": _tool_doc(
            evidence_workspace.create_update_recorded_evidence_metadata_tool("doc-1", "user-1")
        ),
    }

    assert "Review queued active-run evidence" in docs["list_recorded_evidence"]
    assert "detailed review" in docs["get_recorded_evidence"]
    assert "Attach active evidence to the intended object" in docs["attach_evidence_to_object"]
    assert "Detach evidence from a wrong object" in docs["detach_evidence_from_object"]
    assert "Discard wrong or weak evidence" in docs["discard_recorded_evidence"]
    assert "Update only editable agent-owned evidence metadata" in docs["update_recorded_evidence_metadata"]
    assert "Correct source" in docs["update_recorded_evidence_metadata"]
    assert "record_evidence with the existing evidence_record_id" in docs["update_recorded_evidence_metadata"]

    for tool_name, doc in docs.items():
        _assert_clean_doc(tool_name, doc)
