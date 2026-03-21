"""Unit tests for curation prep structured input/output contracts."""

import pytest
from pydantic import ValidationError

from src.schemas.curation_prep import (
    CurationPrepAgentInput,
    CurationPrepAgentOutput,
    CurationPrepCandidate,
    CurationPrepEvidenceReference,
    CurationPrepExtractedFieldValueType,
    CurationPrepScopeConfirmation,
    CurationPrepTokenUsage,
)
from src.schemas.curation_workspace import (
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)


def make_anchor_payload() -> dict:
    """Build a representative prep evidence anchor payload."""

    return {
        "anchor_kind": EvidenceAnchorKind.SNIPPET,
        "locator_quality": EvidenceLocatorQuality.EXACT_QUOTE,
        "supports_decision": EvidenceSupportsDecision.SUPPORTS,
        "snippet_text": "APOE was associated with the reported phenotype.",
        "sentence_text": "APOE was associated with the reported phenotype.",
        "viewer_search_text": "APOE was associated with the reported phenotype.",
        "page_number": 3,
        "section_title": "Results",
        "subsection_title": "Disease association",
        "figure_reference": "Fig. 2",
        "chunk_ids": ["chunk-1"],
    }


def make_extraction_result_payload() -> dict:
    """Build a persisted extraction envelope payload for prep input tests."""

    return {
        "extraction_result_id": "extract-1",
        "document_id": "document-1",
        "adapter_key": "disease",
        "profile_key": "primary",
        "agent_key": "pdf_extraction",
        "source_kind": CurationExtractionSourceKind.CHAT,
        "candidate_count": 1,
        "conversation_summary": "Conversation focused on APOE disease relevance.",
        "payload_json": {
            "items": [{"gene_symbol": "APOE"}],
            "run_summary": {"candidate_count": 1},
        },
        "created_at": "2026-03-20T21:55:00Z",
        "metadata": {},
    }


def make_evidence_record_payload() -> dict:
    """Build a reusable evidence record payload for prep input tests."""

    return {
        "evidence_record_id": "evidence-1",
        "source": CurationEvidenceSource.EXTRACTED,
        "extraction_result_id": "extract-1",
        "field_paths": ["gene_symbol", "phenotype_label"],
        "anchor": make_anchor_payload(),
        "notes": ["Exact quote from Results section."],
    }


def make_adapter_metadata_payload() -> dict:
    """Build adapter metadata with required-field and vocabulary hints."""

    return {
        "adapter_key": "disease",
        "profile_key": "primary",
        "required_field_keys": ["gene_symbol", "phenotype_label"],
        "field_hints": [
            {
                "field_key": "gene_symbol",
                "required": True,
                "label": "Gene symbol",
                "value_type": "string",
                "controlled_vocabulary": ["APOE"],
                "normalization_hints": ["Prefer AGR gene symbols."],
            },
            {
                "field_key": "phenotype_label",
                "required": True,
                "label": "Phenotype label",
                "value_type": "string",
                "controlled_vocabulary": ["late onset phenotype"],
                "normalization_hints": ["Use the adapter-preferred display label."],
            },
        ],
        "notes": ["Populate only fields supported by the adapter-owned normalized shape."],
    }


def make_candidate_payload() -> dict:
    """Build a representative curation prep candidate payload."""

    return {
        "adapter_key": "disease",
        "profile_key": "primary",
        "extracted_fields": [
            {
                "field_path": "gene_symbol",
                "value_type": CurationPrepExtractedFieldValueType.STRING,
                "string_value": "APOE",
                "number_value": None,
                "boolean_value": None,
                "json_value": None,
            },
            {
                "field_path": "phenotype",
                "value_type": CurationPrepExtractedFieldValueType.JSON,
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "json_value": "{\"label\": \"late onset phenotype\"}",
            },
        ],
        "evidence_references": [
            {
                "field_path": "gene_symbol",
                "evidence_record_id": "evidence-1",
                "extraction_result_id": "extract-1",
                "anchor": make_anchor_payload(),
                "rationale": "The exact quote names APOE in direct association with the finding.",
            }
        ],
        "conversation_context_summary": (
            "The user asked for curation prep focused on the APOE disease association, "
            "and the extraction envelope retained APOE as a supported item."
        ),
        "confidence": 0.91,
        "unresolved_ambiguities": [
            {
                "field_path": "phenotype.normalized_id",
                "description": "Multiple phenotype identifiers remain plausible from the snippet alone.",
                "candidate_values": ["DOID:1234", "DOID:5678"],
                "evidence_record_ids": ["evidence-1"],
            }
        ],
    }


def make_agent_input_payload() -> dict:
    """Build a representative prep agent input payload."""

    return {
        "conversation_history": [
            {
                "role": "user",
                "content": "Prepare a disease curation candidate for APOE.",
                "message_id": "message-1",
                "created_at": "2026-03-20T21:50:00Z",
            }
        ],
        "extraction_results": [make_extraction_result_payload()],
        "evidence_records": [make_evidence_record_payload()],
        "scope_confirmation": {
            "confirmed": True,
            "adapter_keys": ["disease"],
            "profile_keys": ["primary"],
            "domain_keys": ["disease"],
            "notes": ["User confirmed the disease adapter scope."],
        },
        "adapter_metadata": [make_adapter_metadata_payload()],
    }


def test_curation_prep_candidate_accepts_structured_output_payload():
    """Candidates accept adapter-owned fields, evidence, and ambiguities."""

    candidate = CurationPrepCandidate(**make_candidate_payload())

    assert candidate.adapter_key == "disease"
    assert candidate.profile_key == "primary"
    assert candidate.to_extracted_fields_dict() == {
        "gene_symbol": "APOE",
        "phenotype": {"label": "late onset phenotype"},
    }
    assert candidate.evidence_references[0].field_path == "gene_symbol"
    assert candidate.unresolved_ambiguities[0].field_path == "phenotype.normalized_id"
    assert candidate.confidence == pytest.approx(0.91)


def test_curation_prep_candidate_accepts_nested_evidence_reference_for_json_field():
    """Evidence references may target nested paths that exist inside a JSON extracted field."""

    payload = make_candidate_payload()
    payload["evidence_references"] = [
        {
            "field_path": "phenotype.label",
            "evidence_record_id": "evidence-1",
            "extraction_result_id": "extract-1",
            "anchor": make_anchor_payload(),
            "rationale": "The snippet directly supports the nested phenotype label.",
        }
    ]

    candidate = CurationPrepCandidate(**payload)

    assert candidate.evidence_references[0].field_path == "phenotype.label"


def test_curation_prep_candidate_accepts_numeric_evidence_path_inside_json_field():
    """Evidence references may point into arrays carried by a JSON extracted field."""

    payload = make_candidate_payload()
    payload["extracted_fields"].append(
        {
            "field_path": "supporting_papers",
            "value_type": CurationPrepExtractedFieldValueType.JSON,
            "string_value": None,
            "number_value": None,
            "boolean_value": None,
            "json_value": '[{"title": "APOE evidence paper"}]',
        }
    )
    payload["evidence_references"] = [
        {
            "field_path": "supporting_papers.0.title",
            "evidence_record_id": "evidence-1",
            "extraction_result_id": "extract-1",
            "anchor": make_anchor_payload(),
            "rationale": "The snippet supports the first paper title captured in the JSON field.",
        }
    ]

    candidate = CurationPrepCandidate(**payload)

    assert candidate.evidence_references[0].field_path == "supporting_papers.0.title"


@pytest.mark.parametrize(
    "invalid_fields",
    [
        [],
        [
            {
                "field_path": "gene_symbol",
                "value_type": CurationPrepExtractedFieldValueType.STRING,
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "json_value": None,
            }
        ],
        [
            {
                "field_path": "phenotype",
                "value_type": CurationPrepExtractedFieldValueType.JSON,
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "json_value": "{\"label\": NaN}",
            }
        ],
        [
            {
                "field_path": "phenotype",
                "value_type": CurationPrepExtractedFieldValueType.JSON,
                "string_value": None,
                "number_value": None,
                "boolean_value": None,
                "json_value": "\"scalar values must use a dedicated value slot\"",
            }
        ],
        [
            {
                "field_path": "items.0.name",
                "value_type": CurationPrepExtractedFieldValueType.STRING,
                "string_value": "APOE",
                "number_value": None,
                "boolean_value": None,
                "json_value": None,
            }
        ],
    ],
)
def test_curation_prep_candidate_rejects_invalid_extracted_fields(invalid_fields: list[dict]):
    """Extracted fields must be non-empty and use strict compatible value carriers."""

    with pytest.raises(ValidationError):
        CurationPrepCandidate(
            **{
                **make_candidate_payload(),
                "extracted_fields": invalid_fields,
            }
        )


def test_curation_prep_candidate_rejects_conflicting_field_paths():
    """Extracted field paths must reconstruct into an unambiguous JSON object."""

    with pytest.raises(ValidationError):
        CurationPrepCandidate(
            **{
                **make_candidate_payload(),
                "extracted_fields": [
                    {
                        "field_path": "phenotype",
                        "value_type": CurationPrepExtractedFieldValueType.STRING,
                        "string_value": "late onset phenotype",
                        "number_value": None,
                        "boolean_value": None,
                        "json_value": None,
                    },
                    {
                        "field_path": "phenotype.label",
                        "value_type": CurationPrepExtractedFieldValueType.STRING,
                        "string_value": "late onset phenotype",
                        "number_value": None,
                        "boolean_value": None,
                        "json_value": None,
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    "bad_path",
    [
        "phenotype..label",
        ".phenotype",
        "phenotype.",
        "phenotype. label",
    ],
)
def test_curation_prep_candidate_rejects_malformed_field_paths(bad_path: str):
    """Malformed dot paths should be rejected before JSONB reconstruction."""

    payload = make_candidate_payload()
    payload["extracted_fields"][1]["field_path"] = bad_path

    with pytest.raises(ValidationError):
        CurationPrepCandidate(**payload)


def test_curation_prep_candidate_rejects_missing_evidence_references():
    """Every candidate must cite at least one supporting evidence reference."""

    with pytest.raises(ValidationError):
        CurationPrepCandidate(
            **{
                **make_candidate_payload(),
                "evidence_references": [],
            }
        )


def test_curation_prep_candidate_rejects_dangling_evidence_reference_path():
    """Evidence references must resolve to an extracted field path in the candidate payload."""

    with pytest.raises(ValidationError):
        CurationPrepCandidate(
            **{
                **make_candidate_payload(),
                "evidence_references": [
                    {
                        "field_path": "phenotype_label",
                        "evidence_record_id": "evidence-1",
                        "extraction_result_id": "extract-1",
                        "anchor": make_anchor_payload(),
                        "rationale": "This path does not exist in extracted_fields.",
                    }
                ],
            }
        )


def test_curation_prep_candidate_requires_adapter_key():
    """Adapter ownership is mandatory for every prep candidate."""

    payload = make_candidate_payload()
    payload.pop("adapter_key")

    with pytest.raises(ValidationError):
        CurationPrepCandidate(**payload)


def test_curation_prep_candidate_rejects_confidence_out_of_range():
    """Confidence scores stay within the downstream candidate confidence surface."""

    with pytest.raises(ValidationError):
        CurationPrepCandidate(
            **{
                **make_candidate_payload(),
                "confidence": 1.2,
            }
        )


def test_curation_prep_evidence_reference_preserves_anchor_locators():
    """Evidence references surface snippet, page, section, and figure data."""

    reference = CurationPrepEvidenceReference(
        field_path="gene_symbol",
        evidence_record_id="evidence-1",
        extraction_result_id="extract-1",
        anchor=make_anchor_payload(),
        rationale="The page and figure context supports the extracted gene symbol.",
    )

    assert reference.anchor.snippet_text == "APOE was associated with the reported phenotype."
    assert reference.anchor.page_number == 3
    assert reference.anchor.section_title == "Results"
    assert reference.anchor.figure_reference == "Fig. 2"


def test_curation_prep_evidence_reference_rejects_malformed_field_path():
    """Evidence references should reject malformed field paths."""

    with pytest.raises(ValidationError):
        CurationPrepEvidenceReference(
            field_path="phenotype..label",
            evidence_record_id="evidence-1",
            extraction_result_id="extract-1",
            anchor=make_anchor_payload(),
            rationale="Malformed paths should not validate.",
        )


def test_curation_prep_agent_input_requires_extraction_results_and_adapter_metadata():
    """Prep input requires persisted envelopes and adapter hints."""

    with pytest.raises(ValidationError):
        CurationPrepAgentInput(
            **{
                **make_agent_input_payload(),
                "extraction_results": [],
            }
        )

    with pytest.raises(ValidationError):
        CurationPrepAgentInput(
            **{
                **make_agent_input_payload(),
                "adapter_metadata": [],
            }
        )


def test_curation_prep_agent_input_reuses_existing_workspace_types():
    """Prep input reuses canonical extraction-envelope and evidence-anchor contracts."""

    agent_input = CurationPrepAgentInput(**make_agent_input_payload())

    assert agent_input.extraction_results[0].source_kind is CurationExtractionSourceKind.CHAT
    assert agent_input.evidence_records[0].source is CurationEvidenceSource.EXTRACTED
    assert agent_input.evidence_records[0].anchor.page_number == 3
    assert agent_input.adapter_metadata[0].required_field_keys == [
        "gene_symbol",
        "phenotype_label",
    ]


def test_curation_prep_scope_confirmation_requires_scoped_target_when_confirmed():
    """Confirmed scope must identify at least one adapter/profile/domain target."""

    with pytest.raises(ValidationError):
        CurationPrepScopeConfirmation(confirmed=True)


def test_curation_prep_token_usage_rejects_inconsistent_totals():
    """Run metadata should not report impossible token accounting."""

    with pytest.raises(ValidationError):
        CurationPrepTokenUsage(input_tokens=120, output_tokens=40, total_tokens=100)


def test_curation_prep_schema_is_structured_output_friendly():
    """Generated schemas stay strict for all objects used in the prep contract."""

    def collect_open_objects(node: object, *, path: str = "$") -> list[str]:
        issues: list[str] = []

        if isinstance(node, dict):
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                issues.append(path)
            for key, value in node.items():
                child_path = f"{path}.{key}"
                if isinstance(value, dict):
                    issues.extend(collect_open_objects(value, path=child_path))
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        issues.extend(collect_open_objects(item, path=f"{child_path}[{index}]"))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                issues.extend(collect_open_objects(item, path=f"{path}[{index}]"))

        return issues

    candidate_schema = CurationPrepCandidate.model_json_schema()
    output_schema = CurationPrepAgentOutput.model_json_schema()

    assert candidate_schema["additionalProperties"] is False
    assert set(candidate_schema["required"]) == set(candidate_schema["properties"].keys())
    assert candidate_schema["properties"]["extracted_fields"]["type"] == "array"
    assert candidate_schema["$defs"]["CurationPrepExtractedField"]["additionalProperties"] is False
    assert collect_open_objects(candidate_schema) == []

    assert output_schema["additionalProperties"] is False
    assert set(output_schema["required"]) == {"candidates", "run_metadata"}
    assert output_schema["$defs"]["CurationPrepRunMetadata"]["additionalProperties"] is False
    assert collect_open_objects(output_schema) == []
