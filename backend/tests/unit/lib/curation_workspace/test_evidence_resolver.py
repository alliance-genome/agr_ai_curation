"""Unit tests for deterministic evidence-anchor resolution."""

from __future__ import annotations

from src.lib.curation_workspace.evidence_resolver import DeterministicEvidenceAnchorResolver
from src.lib.curation_workspace.pipeline import EvidenceResolutionContext, NormalizedCandidate
from src.schemas.curation_prep import CurationPrepCandidate
from src.schemas.curation_workspace import EvidenceAnchor, EvidenceAnchorKind, EvidenceLocatorQuality


def _make_candidate(anchor_payload: dict, *, field_path: str = "gene_symbol") -> CurationPrepCandidate:
    return CurationPrepCandidate.model_validate(
        {
            "adapter_key": "gene",
            "payload": {
                "gene_symbol": "tinman",
                "anatomy_label": "embryonic heart",
                "is_negative": False,
            },
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-1",
                    "source": "extracted",
                    "extraction_result_id": "extract-1",
                    "field_paths": [field_path],
                    "anchor": anchor_payload,
                    "notes": [],
                }
            ],
            "conversation_context_summary": "Conversation summary.",
        }
    )


def _make_anchor_payload(**overrides: object) -> dict:
    payload = {
        "anchor_kind": "snippet",
        "locator_quality": "exact_quote",
        "supports_decision": "supports",
        "snippet_text": "Verified quote from the paper.",
        "sentence_text": "Verified quote from the paper.",
        "normalized_text": None,
        "viewer_search_text": "Verified quote from the paper.",
        "page_number": 4,
        "page_label": None,
        "section_title": "Results",
        "subsection_title": "Expression analysis",
        "figure_reference": "Figure 2A",
        "table_reference": None,
        "chunk_ids": ["chunk-1"],
    }
    payload.update(overrides)
    return payload


def _make_context(*, current_user_id: str | None = None) -> EvidenceResolutionContext:
    return EvidenceResolutionContext(
        document_id="document-1",
        adapter_key="gene",
        prep_extraction_result_id="prep-result-1",
        candidate_index=0,
        current_user_id=current_user_id,
    )


def _make_normalized_candidate(candidate: CurationPrepCandidate) -> NormalizedCandidate:
    return NormalizedCandidate(
        prep_candidate=candidate,
        normalized_payload=dict(candidate.payload),
        draft_fields=[],
    )


def _resolve_anchor(
    candidate: CurationPrepCandidate,
    *,
    chunks: list[dict],
    resolve_against_document: bool = True,
) -> tuple[EvidenceAnchor, list[str], list[str]]:
    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=lambda _prep_result_id: "user-1",
        chunk_loader=lambda _document_id, _user_id: chunks,
        resolve_against_document=resolve_against_document,
    )
    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )
    resolved_record = result[0]
    return (
        EvidenceAnchor.model_validate(resolved_record.anchor),
        resolved_record.warnings,
        resolved_record.field_keys,
    )


def test_resolver_preserves_tool_verified_anchor_payload():
    candidate = _make_candidate(_make_anchor_payload())
    resolver = DeterministicEvidenceAnchorResolver()

    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )

    assert len(result) == 1
    resolved_record = result[0]
    anchor = EvidenceAnchor.model_validate(resolved_record.anchor)

    assert resolved_record.field_keys == ["gene_symbol"]
    assert resolved_record.field_group_keys == []
    assert resolved_record.is_primary is True
    assert resolved_record.warnings == []
    assert anchor.anchor_kind is EvidenceAnchorKind.SNIPPET
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.snippet_text == "Verified quote from the paper."
    assert anchor.page_number == 4
    assert anchor.section_title == "Results"
    assert anchor.subsection_title == "Expression analysis"
    assert anchor.figure_reference == "Figure 2A"
    assert anchor.table_reference is None
    assert anchor.chunk_ids == ["chunk-1"]


def test_resolver_document_lookup_enriches_quote_from_matching_chunk():
    candidate = _make_candidate(
        _make_anchor_payload(
            page_number=None,
            section_title=None,
            subsection_title=None,
            figure_reference=None,
            chunk_ids=[],
        )
    )

    anchor, warnings, field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": "Introductory text. Verified quote from the paper. Closing text.",
                "page_number": 3,
                "section_title": "Results",
                "subsection": "Association",
                "metadata": {},
            }
        ],
    )

    assert field_keys == ["gene_symbol"]
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.anchor_kind is EvidenceAnchorKind.SNIPPET
    assert anchor.page_number == 3
    assert anchor.section_title == "Results"
    assert anchor.subsection_title == "Association"
    assert anchor.viewer_search_text == "Verified quote from the paper."
    assert anchor.chunk_ids == ["chunk-1"]
    assert warnings == []


def test_resolver_matches_fly_publication_quote_with_spaced_crb_identifier():
    quote = (
        "The c rb 8F105 mutation induces a premature stop codon, resulting in a "
        "truncated protein lacking the C-terminal 23 amino acids."
    )
    candidate = _make_candidate(
        _make_anchor_payload(
            snippet_text=quote,
            sentence_text=quote,
            viewer_search_text=quote,
            page_number=6,
            section_title="2. Results and Discussion",
            subsection_title="Linking Phenotype to Genotype through Molecular Abundance of Eye Proteins",
            chunk_ids=[],
        )
    )

    anchor, warnings, field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-crb-8f105",
                "chunk_index": 0,
                "content": (
                    "Molar abundance of Crb corroborated our expectations for the "
                    "different genetic lesions: crb 11A22 is a protein null allele and, "
                    "accordingly, the protein was not detected (Figure 5E). The c rb "
                    "8F105 mutation induces a premature stop codon, resulting in a "
                    "truncated protein lacking the C-terminal 23 amino acids. In line "
                    "with this, we detected, on average, 0.4 ± 0.1 fmoles Crb protein/eye "
                    "in c rb 8F105 as compared to 1.0 ± 0.1 fmoles/eye in control flies."
                ),
                "page_number": 6,
                "section_title": "2. Results and Discussion",
                "subsection": "Linking Phenotype to Genotype through Molecular Abundance of Eye Proteins",
                "metadata": {},
            }
        ],
    )

    assert field_keys == ["gene_symbol"]
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.page_number == 6
    assert (
        anchor.subsection_title
        == "Linking Phenotype to Genotype through Molecular Abundance of Eye Proteins"
    )
    assert anchor.viewer_search_text == quote
    assert anchor.chunk_ids == ["chunk-crb-8f105"]
    assert warnings == []


def test_resolver_uses_context_current_user_id_when_prep_result_has_no_user_id():
    candidate = _make_candidate(_make_anchor_payload(page_number=None, section_title=None, chunk_ids=[]))
    observed_users: list[str | None] = []

    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=lambda _prep_result_id: None,
        chunk_loader=lambda _document_id, user_id: (
            observed_users.append(user_id)
            or [
                {
                    "id": "chunk-1",
                    "chunk_index": 0,
                    "content": "Verified quote from the paper.",
                    "page_number": 3,
                    "section_title": "Results",
                    "subsection": "Association",
                    "metadata": {},
                }
            ]
        ),
        resolve_against_document=True,
    )

    resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(current_user_id="user-from-request"),
    )

    assert observed_users == ["user-from-request"]


def test_resolver_caches_document_chunks_for_same_document_and_user():
    candidate = _make_candidate(_make_anchor_payload(page_number=None, section_title=None, chunk_ids=[]))
    load_calls: list[tuple[str, str | None]] = []

    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=lambda _prep_result_id: "user-1",
        chunk_loader=lambda document_id, user_id: (
            load_calls.append((document_id, user_id))
            or [
                {
                    "id": "chunk-1",
                    "chunk_index": 0,
                    "content": "Verified quote from the paper.",
                    "page_number": 3,
                    "section_title": "Results",
                    "subsection": "Association",
                    "metadata": {},
                }
            ]
        ),
        resolve_against_document=True,
    )

    resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )
    resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )

    assert load_calls == [("document-1", "user-1")]


def test_resolver_moves_table_literal_into_table_reference():
    candidate = _make_candidate(
        _make_anchor_payload(
            figure_reference="Table 3",
            table_reference=None,
        ),
        field_path="anatomy_label",
    )
    resolver = DeterministicEvidenceAnchorResolver()

    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )

    anchor = EvidenceAnchor.model_validate(result[0].anchor)

    assert result[0].field_keys == ["anatomy_label"]
    assert anchor.figure_reference is None
    assert anchor.table_reference == "Table 3"
