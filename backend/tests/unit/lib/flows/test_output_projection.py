import inspect
import json
from datetime import datetime, timezone

import pytest
from types import SimpleNamespace

import src.lib.flows.output_projection as output_projection_module
from src.lib.flows.output_projection import (
    FlowOutputColumnSpec,
    FlowOutputFilterSpec,
    FlowOutputProjectionPlan,
    FlowOutputSortSpec,
    FlowOutputTransformSpec,
    apply_projection_plan,
    build_extraction_result_artifact_bundle,
    build_flow_output_artifact_bundle,
    default_projection_plan,
    inspect_output_artifacts,
    preview_output_projection,
)
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


def _completed_domain_step():
    return {
        "step": 1,
        "extraction_result_id": "extract-gene-1",
        "agent_id": "gene_extractor",
        "agent_name": "Gene Extractor",
        "output_preview": "Extracted gene rows.",
        "candidate": SimpleNamespace(
            agent_key="gene_extractor",
            adapter_key="gene",
            candidate_count=2,
            conversation_summary="Extracted two genes.",
            payload_json={
                "domain_pack_id": "gene",
                "envelope_id": "env-gene-1",
                "objects": [
                    {
                        "object_type": "Gene",
                        "object_id": "gene-1",
                        "status": "validated",
                        "payload": {
                            "symbol": "BRCA1",
                            "primary_external_id": "TEST:GENE001",
                        },
                        "evidence": [
                            {
                                "evidence_record_id": "ev-1",
                                "verified_quote": "BRCA1 was found.",
                                "source": "Results",
                            }
                        ],
                        "validation_findings": [
                            {
                                "finding_id": "vf-1",
                                "status": "resolved",
                                "severity": "info",
                                "message": "Identifier validated.",
                            }
                        ],
                    },
                    {
                        "object_type": "Gene",
                        "object_id": "gene-2",
                        "status": "needs_review",
                        "payload": {
                            "symbol": "TP53",
                            "primary_external_id": "TEST:GENE002",
                        },
                    },
                ],
            },
        ),
    }


def _completed_generic_pdf_step():
    return {
        "step": 1,
        "extraction_result_id": "extract-generic-1",
        "agent_id": "pdf_extraction",
        "agent_name": "General PDF Extraction Agent",
        "output_preview": "Extracted generic reagent candidates.",
        "candidate": SimpleNamespace(
            agent_key="pdf_extraction",
            adapter_key="generic",
            candidate_count=2,
            conversation_summary="Extracted two generic reagents.",
            payload_json={
                "envelope_id": "env-generic-1",
                "domain_pack_id": "generic",
                "domain_pack_version": "0.1.0",
                "status": "extracted",
                "objects": [
                    {
                        "object_type": "generic_reagent_candidate",
                        "pending_ref_id": "generic-obj-1",
                        "payload": {
                            "class_key": "generic:generic_reagent_candidate",
                            "label": "Ck:GFP",
                            "source": "This study",
                            "source_identifier": "New in paper",
                            "count": 4,
                        },
                        "evidence_record_ids": ["ev-1"],
                    },
                    {
                        "object_type": "generic_reagent_candidate",
                        "pending_ref_id": "generic-obj-2",
                        "payload": {
                            "class_key": "generic:generic_reagent_candidate",
                            "label": "Actn RNAi",
                            "source": "Source not found",
                            "source_identifier": "Not found",
                            "count": 2,
                        },
                        "evidence_record_ids": ["ev-2"],
                    },
                ],
                "history": [],
                "validation_findings": [],
                "metadata": {
                    "summary": "Two genetic reagent candidates were retained.",
                    "evidence_records": [
                        {
                            "evidence_record_id": "ev-1",
                            "verified_quote": "Ck:GFP was reported.",
                        },
                        {
                            "evidence_record_id": "ev-2",
                            "verified_quote": "Actn RNAi was reported.",
                        },
                    ]
                },
            },
        ),
    }


_DEBBIE_TUMOR_COLUMNS = [
    "Organ/Cell Type of origin",
    "Tumor classification term",
    "Species",
    "Tumor type",
    "Section",
    "Extracted phrase",
]


_DEBBIE_TUMOR_ROWS = [
    {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "lymphoma incidence; B-cell lymphomas",
    },
    {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "diffuse large B-cell lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "1 DLBCL",
    },
    {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "splenic marginal zone lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "1 SMZL",
    },
    {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "follicular lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "1 FL",
    },
    {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "chronic lymphocytic leukemia",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "1 CLL/SLL-like",
    },
    {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "plasmacytoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "2 plasmacytomas",
    },
    {
        "Organ/Cell Type of origin": "lymphocyte",
        "Tumor classification term": "lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "13 lymphomas",
    },
    {
        "Organ/Cell Type of origin": "T cell",
        "Tumor classification term": "T-cell lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "5 T-cell lymphomas",
    },
    {
        "Organ/Cell Type of origin": "histiocyte",
        "Tumor classification term": "histiocytic sarcoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "2 histiocytic sarcomas",
    },
]


def _debbie_claim_text(values: dict[str, str]) -> str:
    return "; ".join(f"{column}={values[column]}" for column in _DEBBIE_TUMOR_COLUMNS)


def _debbie_tumor_extraction_result() -> CurationExtractionResultRecord:
    return CurationExtractionResultRecord(
        extraction_result_id="4170023b-8ba3-44e2-ad7c-dacaa3a3a221",
        document_id="debbie-tumor-paper",
        adapter_key="generic",
        agent_key="pdf_extraction",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="d2d3cf18-04f0-44e3-a965-09381b0f2bca",
        trace_id="360762fa9fa0f7383115e86bb9bc88d6",
        flow_run_id=None,
        user_id="debbie",
        candidate_count=len(_DEBBIE_TUMOR_ROWS),
        conversation_summary="Extracted tumor term rows.",
        payload_json={
            "summary": "Nine tumor term rows were extracted.",
            "curatable_objects": [
                {
                    "object_type": "generic_claim",
                    "pending_ref_id": f"generic-claim-{index}",
                    "payload": {
                        "label": row["Tumor classification term"],
                        "class_key": "generic:generic_claim",
                        "claim_text": _debbie_claim_text(row),
                        "claim_type": "tumor term row",
                        "confidence": "high",
                        "classification_notes": [
                            "The prompt requested a row-like tumor term claim.",
                        ],
                    },
                    "evidence_record_ids": [f"evidence-{index}"],
                }
                for index, row in enumerate(_DEBBIE_TUMOR_ROWS, start=1)
            ],
            "metadata": {
                "evidence_records": [
                    {
                        "evidence_record_id": f"evidence-{index}",
                        "verified_quote": row["Extracted phrase"],
                        "section": row["Section"],
                    }
                    for index, row in enumerate(_DEBBIE_TUMOR_ROWS, start=1)
                ],
            },
            "run_summary": {"candidate_count": len(_DEBBIE_TUMOR_ROWS)},
        },
        created_at=datetime(2026, 6, 16, 22, 7, tzinfo=timezone.utc),
        metadata={},
    )


def _completed_domain_source_step(
    *,
    step: int,
    agent_id: str,
    adapter_key: str,
    object_type: str,
    object_id: str,
    payload: dict,
    extraction_result_id: str | None = None,
    metadata: dict | None = None,
):
    source_id = extraction_result_id or f"{adapter_key}-step-{step}"
    result = {
        "step": step,
        "agent_id": agent_id,
        "agent_name": agent_id.replace("_", " ").title(),
        "output_preview": f"Extracted {object_type}.",
        "candidate": SimpleNamespace(
            agent_key=agent_id,
            adapter_key=adapter_key,
            candidate_count=1,
            metadata=metadata or {},
            payload_json={
                "domain_pack_id": adapter_key,
                "envelope_id": f"env-{source_id}",
                "objects": [
                    {
                        "object_type": object_type,
                        "object_id": object_id,
                        "status": "candidate",
                        "payload": payload,
                    }
                ],
            },
        ),
    }
    if extraction_result_id is not None:
        result["extraction_result_id"] = extraction_result_id
    return result


def test_default_tsv_projection_uses_canonical_object_rows():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
        output_format="tsv",
    )

    plan = default_projection_plan(bundle, output_format="tsv")
    result = apply_projection_plan(bundle, plan)

    assert result.row_source == "object"
    assert plan.row_strategy == "wide_union"
    assert result.total_count == 2
    assert "artifact_preview" not in [column.key for column in result.columns]
    assert "object_payload_primary_external_id" in [column.key for column in result.columns]
    assert result.rows[0]["object_payload_symbol"] == "BRCA1"
    assert result.rows[0]["object_payload_primary_external_id"] == "TEST:GENE001"
    assert result.rows[1]["object_payload_symbol"] == "TP53"


def test_prose_answer_output_cannot_become_curation_tsv_rows():
    payload = {
        "answer": (
            "Extracted rows:\n\n"
            "synonym\tsource\tsource_identifier\tcount\n"
            "Ck:GFP\tThis study\tNew in paper\t4\n"
            "Actn RNAi\tSource not found\tNot found\t2\n"
        ),
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "pdf_extraction",
                "agent_name": "General PDF Extraction Agent",
                "output": json.dumps(payload),
                "output_preview": "Extracted rows.",
                "candidate": None,
            }
        ],
        flow_name="PDF Projection Flow",
        output_format="tsv",
    )

    assert bundle.artifacts == []

    with pytest.raises(ValueError, match="Row source 'object' is not available"):
        apply_projection_plan(
            bundle,
            default_projection_plan(bundle, output_format="tsv"),
        )


def test_legacy_semantic_lists_cannot_become_curation_tsv_rows():
    step = {
        "step": 1,
        "agent_id": "pdf_extraction",
        "agent_name": "General PDF Extraction Agent",
        "candidate": SimpleNamespace(
            agent_key="pdf_extraction",
            adapter_key="generic",
            candidate_count=1,
            payload_json={
                "items": [{"label": "Ck:GFP", "status": "candidate"}],
                "raw_mentions": [{"label": "raw mention"}],
                "exclusions": [{"label": "excluded"}],
                "ambiguities": [{"label": "ambiguous"}],
                "run_summary": {"candidate_count": 1},
            },
        ),
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Legacy Projection Flow",
        output_format="tsv",
    )

    assert bundle.artifacts[0].artifact_shape == "non_structured"
    assert bundle.rows_for_source("object") == []
    assert bundle.default_row_source == "object"
    with pytest.raises(ValueError, match="Row source 'object' is not available"):
        apply_projection_plan(
            bundle,
            default_projection_plan(bundle, output_format="tsv"),
        )


def test_artifact_summary_rows_are_rejected_for_curation_tsv_exports():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "pdf_extraction",
                "candidate": SimpleNamespace(
                    agent_key="pdf_extraction",
                    adapter_key="generic",
                    candidate_count=1,
                    payload_json={"answer": "Narrative only."},
                ),
            }
        ],
        flow_name="Artifact Projection Flow",
        output_format="tsv",
    )

    with pytest.raises(ValueError, match="Artifact-summary rows cannot be used"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(format="tsv", row_source="artifact"),
        )


def test_literal_only_projection_cannot_create_curation_tsv_without_objects():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[],
        flow_name="Projection Flow",
        output_format="tsv",
    )

    with pytest.raises(ValueError, match="literal-only TSV projections are not allowed"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(
                format="tsv",
                row_source="artifact",
                columns=[
                    FlowOutputColumnSpec(
                        key="status",
                        transform=FlowOutputTransformSpec(
                            type="literal",
                            value="completed",
                        ),
                    )
                ],
            ),
        )


def test_raw_step_output_curatable_objects_cannot_become_curation_tsv_rows():
    payload = {
        "summary": "Model-written lookalike payload.",
        "curatable_objects": [
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "raw-output-1",
                "payload": {
                    "class_key": "generic:generic_reagent_candidate",
                    "label": "Ck:GFP",
                },
            }
        ],
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "pdf_extraction",
                "agent_name": "General PDF Extraction Agent",
                "output": json.dumps(payload),
                "output_preview": "Model-written JSON.",
                "candidate": None,
            }
        ],
        flow_name="Raw Output Projection Flow",
        output_format="tsv",
    )

    assert bundle.rows_for_source("object") == []
    with pytest.raises(ValueError, match="Row source 'object' is not available"):
        apply_projection_plan(
            bundle,
            default_projection_plan(bundle, output_format="tsv"),
        )


def test_trusted_candidate_curatable_objects_project_to_curation_tsv_rows():
    payload = {
        "summary": "Builder-finalized generic extraction.",
        "curatable_objects": [
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "generic-object-1",
                "payload": {
                    "class_key": "generic:generic_reagent_candidate",
                    "label": "Ck:GFP",
                    "source": "This study",
                    "count": 4,
                },
                "evidence_record_ids": ["evidence-generic-1"],
            },
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "generic-object-2",
                "payload": {
                    "class_key": "generic:generic_reagent_candidate",
                    "label": "Actn RNAi",
                    "source": "Source not found",
                    "count": 2,
                },
                "evidence_record_ids": ["evidence-generic-2"],
            },
        ],
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "pdf_extraction",
                "agent_name": "General PDF Extraction Agent",
                "output_preview": "Builder finalized generic reagents.",
                "candidate": SimpleNamespace(
                    agent_key="pdf_extraction",
                    adapter_key="generic",
                    candidate_count=2,
                    payload_json=payload,
                ),
            }
        ],
        flow_name="Trusted Candidate Projection Flow",
        output_format="tsv",
    )

    assert bundle.artifacts[0].artifact_shape == "domain_envelope_extraction"
    assert bundle.artifacts[0].object_count == 2
    assert [row["object.label"] for row in bundle.rows_for_source("object")] == [
        "Ck:GFP",
        "Actn RNAi",
    ]

    result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="tsv"),
    )

    assert result.row_source == "object"
    assert result.total_count == 2
    assert [row["object_payload_label"] for row in result.rows] == ["Ck:GFP", "Actn RNAi"]


def test_output_projection_extractor_envelope_path_is_candidate_gated():
    payload_shape_source = inspect.getsource(output_projection_module._payload_shape)
    object_items_source = inspect.getsource(output_projection_module._payload_object_items)
    candidate_items_source = inspect.getsource(
        output_projection_module._candidate_payload_object_items
    )
    build_artifact_source = inspect.getsource(output_projection_module._build_artifact_from_step)
    old_shape_name = "domain_" + "extraction_result"

    assert old_shape_name not in payload_shape_source
    assert "curatable_objects" not in payload_shape_source
    assert "curatable_objects" not in object_items_source
    assert "curatable_objects" in candidate_items_source
    assert "payload_from_candidate" in build_artifact_source


def test_mixed_domain_and_extractor_payload_cannot_become_object_rows():
    payload = {
        "envelope_id": "env-mixed-1",
        "domain_pack_id": "generic",
        "objects": [
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "object-row-1",
                "payload": {"label": "Ck:GFP"},
            }
        ],
        "curatable_objects": [
            {
                "object_type": "generic_reagent_candidate",
                "pending_ref_id": "stale-row-1",
                "payload": {"label": "stale"},
            }
        ],
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "pdf_extraction",
                "agent_name": "General PDF Extraction Agent",
                "candidate": SimpleNamespace(
                    agent_key="pdf_extraction",
                    adapter_key="generic",
                    candidate_count=1,
                    payload_json=payload,
                ),
            }
        ],
        flow_name="Mixed Shape Projection Flow",
        output_format="tsv",
    )

    assert bundle.artifacts[0].artifact_shape == "non_structured"
    assert bundle.rows_for_source("object") == []
    with pytest.raises(ValueError, match="Row source 'object' is not available"):
        apply_projection_plan(
            bundle,
            default_projection_plan(bundle, output_format="tsv"),
        )


def test_nested_raw_step_output_objects_cannot_become_curation_tsv_rows():
    payload = {
        "result": {
            "domain_pack_id": "gene",
            "envelope_id": "env-raw-gene",
            "objects": [
                {
                    "object_type": "Gene",
                    "object_id": "gene-raw-1",
                    "payload": {"symbol": "BRCA1"},
                }
            ],
        }
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "gene_extractor",
                "output": json.dumps(payload),
                "candidate": None,
            }
        ],
        flow_name="Nested Raw Output Flow",
        output_format="tsv",
    )

    assert bundle.rows_for_source("object")
    with pytest.raises(ValueError, match="model-written step output cannot be used"):
        apply_projection_plan(
            bundle,
            default_projection_plan(bundle, output_format="tsv"),
        )


def test_canonical_domain_envelope_default_tsv_exports_one_row_per_object():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_generic_pdf_step()],
        flow_name="PDF Projection Flow",
        output_format="tsv",
    )

    plan = default_projection_plan(bundle, output_format="tsv")
    result = apply_projection_plan(bundle, plan)

    assert bundle.artifacts[0].artifact_shape == "domain_envelope"
    assert result.row_source == "object"
    assert plan.row_strategy == "wide_union"
    assert result.total_count == 2
    column_keys = [column.key for column in result.columns]
    assert "object_payload_source" in column_keys
    assert "object_payload_source_identifier" in column_keys
    assert "object_payload_count" in column_keys
    assert [row["object_payload_label"] for row in result.rows] == ["Ck:GFP", "Actn RNAi"]
    assert result.rows[0]["object_payload_source"] == "This study"
    assert result.rows[0]["object_payload_source_identifier"] == "New in paper"
    assert result.rows[0]["object_payload_count"] == 4
    assert bundle.rows_for_source("evidence")[0]["evidence.evidence_record_id"] == "ev-1"


def test_extraction_result_bundle_recovers_debbie_generic_claim_rows():
    extraction_result = _debbie_tumor_extraction_result()
    payload_json = extraction_result.payload_json
    assert isinstance(payload_json, dict)
    assert all(
        "row" not in curatable_object["payload"]
        for curatable_object in payload_json["curatable_objects"]
    )

    bundle = build_extraction_result_artifact_bundle(
        extraction_results=[extraction_result],
        bundle_name="Debbie Tumor Terms",
        output_format="csv",
    )
    plan = default_projection_plan(bundle, output_format="csv")
    result = apply_projection_plan(bundle, plan)

    assert bundle.artifacts[0].artifact_shape == "domain_envelope"
    assert bundle.artifacts[0].extraction_result_id == (
        "4170023b-8ba3-44e2-ad7c-dacaa3a3a221"
    )
    assert result.total_count == 9
    assert [column.key for column in result.columns] == _DEBBIE_TUMOR_COLUMNS
    assert [column.header for column in result.columns] == _DEBBIE_TUMOR_COLUMNS
    assert result.rows == _DEBBIE_TUMOR_ROWS
    assert all("object_payload_claim_text" not in row for row in result.rows)


def test_generic_claim_table_recovery_rejects_arbitrary_assignments():
    steps = [
        _completed_domain_source_step(
            step=index,
            agent_id="pdf_extraction",
            adapter_key="generic",
            object_type="generic_claim",
            object_id=f"generic-claim-{index}",
            payload={
                "class_key": "generic:generic_claim",
                "label": f"Narrative claim {index}",
                "claim_text": f"p=0.0{index}; n={index + 10}",
            },
        )
        for index in range(1, 3)
    ]
    bundle = build_flow_output_artifact_bundle(
        completed_steps=steps,
        flow_name="Narrative Claim Flow",
        output_format="csv",
    )

    assert all(
        not any(field_ref.startswith("object.row.") for field_ref in row)
        for row in bundle.rows_for_source("object")
    )


def test_explicit_structured_row_fields_take_priority_over_claim_text_recovery():
    step = _completed_domain_source_step(
        step=1,
        agent_id="pdf_extraction",
        adapter_key="generic",
        object_type="generic_claim",
        object_id="generic-claim-1",
        payload={
            "class_key": "generic:generic_claim",
            "label": "Structured row claim",
            "claim_text": "Wrong=claim text; Row=claim text",
            "attributes": {
                "structured_row": {
                    "Organ/Cell Type of origin": "B cell",
                    "Tumor classification term": "lymphoma",
                }
            },
        },
    )
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Structured Claim Flow",
        output_format="csv",
    )
    result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="csv"),
    )

    assert result.rows == [
        {
            "Organ/Cell Type of origin": "B cell",
            "Tumor classification term": "lymphoma",
        }
    ]


def test_persisted_extraction_ids_define_tsv_source_identity_before_source_keys():
    gene_step = _completed_domain_source_step(
        step=1,
        agent_id="gene_extractor",
        adapter_key="gene",
        extraction_result_id="extract-gene-1",
        object_type="Gene",
        object_id="gene-1",
        payload={"symbol": "BRCA1", "primary_external_id": "TEST:GENE001"},
        metadata={"source_key": "shared-flow-step"},
    )
    allele_step = _completed_domain_source_step(
        step=2,
        agent_id="allele_extractor",
        adapter_key="allele",
        extraction_result_id="extract-allele-1",
        object_type="Allele",
        object_id="allele-1",
        payload={"allele_symbol": "brca1[tm1]", "primary_external_id": "TEST:ALLELE001"},
        metadata={"source_key": "shared-flow-step"},
    )
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[gene_step, allele_step],
        flow_name="Duplicate Source Key Flow",
        output_format="tsv",
    )
    plan = default_projection_plan(bundle, output_format="tsv")

    assert plan.row_strategy == "object"
    with pytest.raises(ValueError, match="Multiple canonical extraction sources"):
        apply_projection_plan(bundle, plan)


def test_multi_source_tsv_requires_explicit_selection_or_combined_plan():
    gene_step = _completed_domain_source_step(
        step=1,
        agent_id="gene_extractor",
        adapter_key="gene",
        extraction_result_id="extract-gene-1",
        object_type="Gene",
        object_id="gene-1",
        payload={"symbol": "BRCA1", "primary_external_id": "TEST:GENE001"},
    )
    allele_step = _completed_domain_source_step(
        step=2,
        agent_id="allele_extractor",
        adapter_key="allele",
        extraction_result_id="extract-allele-1",
        object_type="Allele",
        object_id="allele-1",
        payload={"allele_symbol": "brca1[tm1]", "primary_external_id": "TEST:ALLELE001"},
    )
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[gene_step, allele_step],
        flow_name="Combined Projection Flow",
        output_format="tsv",
    )

    with pytest.raises(ValueError, match="Multiple canonical extraction sources"):
        apply_projection_plan(
            bundle,
            default_projection_plan(bundle, output_format="tsv"),
        )

    single_source = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="tsv",
            row_source="object",
            source_extraction_result_ids=["extract-gene-1"],
            columns=[
                FlowOutputColumnSpec(key="symbol", field_ref="object.payload.symbol"),
            ],
        ),
    )
    assert single_source.rows == [{"symbol": "BRCA1"}]

    ledger = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="tsv",
            row_source="object",
            row_strategy="object_ledger",
            source_extraction_result_ids=["extract-gene-1", "extract-allele-1"],
        ),
    )
    assert ledger.total_count == 2
    assert [row["artifact_extraction_result_id"] for row in ledger.rows] == [
        "extract-gene-1",
        "extract-allele-1",
    ]

    wide_union = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="tsv",
            row_source="object",
            row_strategy="wide_union",
            source_extraction_result_ids=["extract-gene-1", "extract-allele-1"],
        ),
    )
    wide_columns = [column.key for column in wide_union.columns]
    assert "object_payload_symbol" in wide_columns
    assert "object_payload_allele_symbol" in wide_columns


def test_pre_persistence_source_keys_bind_multi_source_tsv_exports():
    gene_step = _completed_domain_source_step(
        step=1,
        agent_id="gene_extractor",
        adapter_key="gene",
        object_type="Gene",
        object_id="gene-1",
        payload={"symbol": "BRCA1", "primary_external_id": "TEST:GENE001"},
        metadata={"flow_id": "flow-1", "step": 1, "tool_name": "ask_gene"},
    )
    allele_step = _completed_domain_source_step(
        step=2,
        agent_id="allele_extractor",
        adapter_key="allele",
        object_type="Allele",
        object_id="allele-1",
        payload={"allele_symbol": "brca1[tm1]", "primary_external_id": "TEST:ALLELE001"},
        metadata={"flow_id": "flow-1", "step": 2, "tool_name": "ask_allele"},
    )
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[gene_step, allele_step],
        flow_name="Pre Persistence Flow",
        output_format="tsv",
    )

    assert {row["artifact.source_key"] for row in bundle.rows_for_source("object")} == {
        "flow-1:1:ask_gene:gene_extractor",
        "flow-1:2:ask_allele:allele_extractor",
    }
    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="tsv",
            row_source="object",
            source_keys=["flow-1:1:ask_gene:gene_extractor"],
            columns=[
                FlowOutputColumnSpec(key="symbol", field_ref="object.payload.symbol"),
            ],
        ),
    )

    assert result.rows == [{"symbol": "BRCA1"}]


def test_selected_source_columns_must_exist_in_selected_rows():
    gene_step = _completed_domain_source_step(
        step=1,
        agent_id="gene_extractor",
        adapter_key="gene",
        extraction_result_id="extract-gene-1",
        object_type="Gene",
        object_id="gene-1",
        payload={"symbol": "BRCA1", "primary_external_id": "TEST:GENE001"},
    )
    allele_step = _completed_domain_source_step(
        step=2,
        agent_id="allele_extractor",
        adapter_key="allele",
        extraction_result_id="extract-allele-1",
        object_type="Allele",
        object_id="allele-1",
        payload={"allele_symbol": "brca1[tm1]", "primary_external_id": "TEST:ALLELE001"},
    )
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[gene_step, allele_step],
        flow_name="Selected Source Flow",
        output_format="tsv",
    )

    with pytest.raises(ValueError, match="unknown field_ref 'object.payload.allele_symbol'"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(
                format="tsv",
                row_source="object",
                source_extraction_result_ids=["extract-gene-1"],
                columns=[
                    FlowOutputColumnSpec(
                        key="allele_symbol",
                        field_ref="object.payload.allele_symbol",
                    ),
                ],
            ),
        )


def test_plain_text_step_output_without_candidate_is_not_an_artifact():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "pdf_extraction",
                "output": "plain text only",
                "candidate": None,
            }
        ],
        flow_name="No Artifact Flow",
        output_format="tsv",
    )

    assert bundle.artifacts == []


def test_object_projection_supports_rename_omit_reorder_filter_sort_and_concat():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
        output_format="csv",
    )
    plan = FlowOutputProjectionPlan(
        format="csv",
        row_source="object",
        filters=[
            FlowOutputFilterSpec(
                field_ref="object.status",
                op="eq",
                value="validated",
            )
        ],
        sort=[
            FlowOutputSortSpec(
                field_ref="object.payload.symbol",
                direction="desc",
            )
        ],
        columns=[
            FlowOutputColumnSpec(
                key="gene_symbol",
                header="Gene Symbol",
                field_ref="object.payload.symbol",
            ),
            FlowOutputColumnSpec(
                key="gene_label",
                header="Gene Label",
                field_ref="object.label",
            ),
            FlowOutputColumnSpec(
                key="evidence_record_ids",
                header="Evidence IDs",
                field_ref="object.evidence_record_ids",
            ),
            FlowOutputColumnSpec(
                key="gene_ref",
                header="Gene Ref",
                transform=FlowOutputTransformSpec(
                    type="concat",
                    values=[
                        {"field_ref": "object.payload.primary_external_id"},
                        {"field_ref": "object.payload.symbol"},
                    ],
                    separator=" ",
                ),
            ),
        ],
    )

    result = apply_projection_plan(bundle, plan)

    assert result.rows == [
        {
            "gene_symbol": "BRCA1",
            "gene_label": "BRCA1",
            "evidence_record_ids": ["ev-1"],
            "gene_ref": "TEST:GENE001 BRCA1",
        }
    ]
    assert [column.header for column in result.columns] == [
        "Gene Symbol",
        "Gene Label",
        "Evidence IDs",
        "Gene Ref",
    ]


def test_projection_safe_derived_transforms_cover_supported_surface():
    step = _completed_domain_step()
    step["candidate"].payload_json["objects"][0]["payload"].update(
        {
            "alias": "",
            "is_primary": True,
        }
    )
    step["candidate"].payload_json["objects"][0]["evidence_record_ids"] = ["ev-1", "ev-2"]
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
    )

    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="object",
            filters=[
                FlowOutputFilterSpec(
                    field_ref="object.object_id",
                    op="eq",
                    value="gene-1",
                )
            ],
            columns=[
                FlowOutputColumnSpec(
                    key="source",
                    transform=FlowOutputTransformSpec(type="literal", value="flow"),
                ),
                FlowOutputColumnSpec(
                    key="display_name",
                    transform=FlowOutputTransformSpec(
                        type="first_non_empty",
                        field_refs=[
                            "object.payload.alias",
                            "object.payload.symbol",
                        ],
                    ),
                ),
                FlowOutputColumnSpec(
                    key="evidence_ids",
                    transform=FlowOutputTransformSpec(
                        type="join_list",
                        field_ref="object.evidence_record_ids",
                        separator=";",
                    ),
                ),
                FlowOutputColumnSpec(
                    key="evidence_count",
                    transform=FlowOutputTransformSpec(
                        type="count",
                        field_ref="object.evidence_record_ids",
                    ),
                ),
                FlowOutputColumnSpec(
                    key="status_label",
                    transform=FlowOutputTransformSpec(
                        type="map_value",
                        field_ref="object.status",
                        mapping={"validated": "Ready"},
                        default="Review",
                    ),
                ),
                FlowOutputColumnSpec(
                    key="primary",
                    transform=FlowOutputTransformSpec(
                        type="boolean_label",
                        field_ref="object.payload.is_primary",
                        true_label="Primary",
                        false_label="Secondary",
                        unknown_label="Unknown",
                    ),
                ),
            ],
        ),
    )

    assert result.json_data == [
        {
            "source": "flow",
            "display_name": "BRCA1",
            "evidence_ids": "ev-1;ev-2",
            "evidence_count": 2,
            "status_label": "Ready",
            "primary": "Primary",
        }
    ]


def test_projection_membership_emptiness_contains_filters_and_sort_are_applied():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )

    sorted_result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="object",
            filters=[
                FlowOutputFilterSpec(
                    field_ref="object.status",
                    op="in",
                    values=["validated", "needs_review"],
                ),
                FlowOutputFilterSpec(
                    field_ref="object.payload.primary_external_id",
                    op="is_not_empty",
                ),
                FlowOutputFilterSpec(
                    field_ref="object.pending_ref_id",
                    op="is_empty",
                ),
            ],
            sort=[
                FlowOutputSortSpec(
                    field_ref="object.payload.symbol",
                    direction="desc",
                )
            ],
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    field_ref="object.payload.symbol",
                )
            ],
        ),
    )
    contains_result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="object",
            filters=[
                FlowOutputFilterSpec(
                    field_ref="object.payload.symbol",
                    op="contains",
                    value="53",
                )
            ],
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    field_ref="object.payload.symbol",
                )
            ],
        ),
    )

    assert sorted_result.rows == [{"symbol": "TP53"}, {"symbol": "BRCA1"}]
    assert contains_result.rows == [{"symbol": "TP53"}]


def test_evidence_and_validation_row_sources_are_explicitly_projected():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )

    evidence = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="evidence",
            columns=[
                FlowOutputColumnSpec(
                    key="object_id",
                    field_ref="object.object_id",
                ),
                FlowOutputColumnSpec(
                    key="quote",
                    field_ref="evidence.verified_quote",
                ),
            ],
        ),
    )
    validation = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="validation_finding",
            columns=[
                FlowOutputColumnSpec(
                    key="object_id",
                    field_ref="object.object_id",
                ),
                FlowOutputColumnSpec(
                    key="status",
                    field_ref="validation.status",
                ),
                FlowOutputColumnSpec(
                    key="message",
                    field_ref="validation.message",
                ),
            ],
        ),
    )

    assert evidence.rows == [{"object_id": "gene-1", "quote": "BRCA1 was found."}]
    assert validation.json_data == [
        {
            "object_id": "gene-1",
            "status": "resolved",
            "message": "Identifier validated.",
        }
    ]


def test_json_grouped_projection_groups_by_exact_field_ref():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
        output_format="json",
    )
    plan = FlowOutputProjectionPlan(
        format="json",
        row_source="object",
        json_shape="grouped",
        group_by=["artifact.adapter_key"],
        columns=[
            FlowOutputColumnSpec(
                key="symbol",
                field_ref="object.payload.symbol",
            )
        ],
    )

    result = apply_projection_plan(bundle, plan)

    assert result.json_data == [
        {
            "group": {"artifact.adapter_key": "gene"},
            "rows": [{"symbol": "BRCA1"}, {"symbol": "TP53"}],
        }
    ]


def test_projection_preview_rejects_unknown_field_refs():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )
    preview = preview_output_projection(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="object",
            columns=[
                FlowOutputColumnSpec(
                    key="bad",
                    field_ref="object.payload.nope",
                )
            ],
        ),
    )

    assert preview.status == "invalid"
    assert "object.payload.nope" in preview.errors[0]


def test_empty_or_unsupported_row_source_fails_usefully():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {
                "step": 1,
                "agent_id": "plain",
                "candidate": SimpleNamespace(
                    agent_key="plain",
                    payload_json={"message": "not a domain envelope"},
                ),
            }
        ],
        flow_name="Projection Flow",
    )

    with pytest.raises(ValueError, match="Row source 'object' is not available"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(format="csv", row_source="object"),
        )


def test_literal_only_projection_can_create_plumbing_row_without_artifacts():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[],
        flow_name="Projection Flow",
    )

    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="artifact",
            json_shape="rows",
            columns=[
                FlowOutputColumnSpec(
                    key="check",
                    transform=FlowOutputTransformSpec(
                        type="literal",
                        value="batch_file_output",
                    ),
                ),
                FlowOutputColumnSpec(
                    key="status",
                    transform=FlowOutputTransformSpec(
                        type="literal",
                        value="completed",
                    ),
                ),
            ],
        ),
    )

    assert result.rows == [{"check": "batch_file_output", "status": "completed"}]
    assert result.json_data == [{"check": "batch_file_output", "status": "completed"}]
    assert result.total_count == 1
    assert any("literal-only row" in warning for warning in result.warnings)


def test_ordered_numeric_filters_do_not_fall_back_to_lexicographic_comparison():
    step = _completed_domain_step()
    step["candidate"].payload_json["objects"][0]["payload"]["score"] = "10"
    step["candidate"].payload_json["objects"][1]["payload"]["score"] = "2"
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
    )

    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="object",
            filters=[
                FlowOutputFilterSpec(
                    field_ref="object.payload.score",
                    op="gt",
                    value="2",
                )
            ],
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    field_ref="object.payload.symbol",
                )
            ],
        ),
    )

    assert result.rows == [{"symbol": "BRCA1"}]


def test_ordered_filter_on_non_numeric_text_fails_clearly():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )

    with pytest.raises(ValueError, match="requires numeric values"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(
                format="csv",
                row_source="object",
                filters=[
                    FlowOutputFilterSpec(
                        field_ref="object.payload.symbol",
                        op="lt",
                        value="ZZZ",
                    )
                ],
                columns=[
                    FlowOutputColumnSpec(
                        key="symbol",
                        field_ref="object.payload.symbol",
                    )
                ],
            ),
        )


def test_group_by_is_rejected_for_flat_file_formats():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )

    with pytest.raises(ValueError, match="group_by is not supported for CSV"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(
                format="csv",
                row_source="object",
                group_by=["artifact.adapter_key"],
            ),
        )


def test_chat_grouped_projection_renders_visible_sections():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )

    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="chat",
            row_source="object",
            group_by=["artifact.adapter_key"],
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    header="Symbol",
                    field_ref="object.payload.symbol",
                )
            ],
        ),
    )

    assert result.chat_output is not None
    assert "## Adapter: gene" in result.chat_output
    assert "| Symbol |" in result.chat_output
    assert "BRCA1" in result.chat_output


def test_step_level_evidence_and_validation_metadata_are_projectable_without_payload_records():
    step = _completed_domain_step()
    for obj in step["candidate"].payload_json["objects"]:
        obj.pop("evidence", None)
        obj.pop("validation_findings", None)
    step["evidence_records"] = [
        {
            "evidence_record_id": "step-ev-1",
            "verified_quote": "Runtime captured quote.",
            "source": "runtime",
        }
    ]
    step["validation_group_results"] = {
        "groups": [
            {
                "group_id": "identity",
                "validator_binding_id": "gene_identity",
                "status": "resolved",
                "curator_message": "Runtime validator resolved the gene.",
            }
        ]
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
    )

    evidence = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="evidence",
            columns=[
                FlowOutputColumnSpec(
                    key="object_id",
                    field_ref="object.object_id",
                ),
                FlowOutputColumnSpec(
                    key="quote",
                    field_ref="evidence.verified_quote",
                ),
            ],
            missing_value="",
        ),
    )
    validation = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="validation_finding",
            columns=[
                FlowOutputColumnSpec(
                    key="status",
                    field_ref="validation.status",
                ),
                FlowOutputColumnSpec(
                    key="message",
                    field_ref="validation.message",
                ),
                FlowOutputColumnSpec(
                    key="validator",
                    field_ref="validation.validator",
                ),
            ],
        ),
    )

    assert evidence.rows == [{"object_id": "", "quote": "Runtime captured quote."}]
    assert validation.rows == [
        {
            "status": "resolved",
            "message": "Runtime validator resolved the gene.",
            "validator": "gene_identity",
        }
    ]


def test_step_level_metadata_uses_explicit_object_refs_without_guessing():
    step = _completed_domain_step()
    for obj in step["candidate"].payload_json["objects"]:
        obj.pop("evidence", None)
        obj.pop("validation_findings", None)
    step["evidence_records"] = [
        {
            "evidence_record_id": "step-ev-associated",
            "object_ref": "gene-2",
            "verified_quote": "Explicitly associated quote.",
        },
        {
            "evidence_record_id": "step-ev-unassociated",
            "verified_quote": "Unassociated quote.",
        },
    ]
    step["validation_findings"] = [
        {
            "finding_id": "step-vf-associated",
            "object_id": "gene-1",
            "status": "resolved",
        },
        {
            "finding_id": "step-vf-unassociated",
            "status": "needs_review",
        },
    ]
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
    )

    evidence = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="evidence",
            sort=[FlowOutputSortSpec(field_ref="evidence.evidence_record_id")],
            columns=[
                FlowOutputColumnSpec(key="id", field_ref="evidence.evidence_record_id"),
                FlowOutputColumnSpec(key="object_id", field_ref="object.object_id"),
            ],
        ),
    )
    validation = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="validation_finding",
            sort=[FlowOutputSortSpec(field_ref="validation.finding_id")],
            columns=[
                FlowOutputColumnSpec(key="id", field_ref="validation.finding_id"),
                FlowOutputColumnSpec(key="object_id", field_ref="object.object_id"),
            ],
        ),
    )

    assert evidence.rows == [
        {"id": "step-ev-associated", "object_id": "gene-2"},
        {"id": "step-ev-unassociated", "object_id": ""},
    ]
    assert validation.rows == [
        {"id": "step-vf-associated", "object_id": "gene-1"},
        {"id": "step-vf-unassociated", "object_id": ""},
    ]
    assert "empty object refs" in " ".join(bundle.warnings)


def test_step_level_evidence_count_is_available_without_changing_default_columns():
    step = _completed_domain_step()
    step["evidence_count"] = 7
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
        output_format="tsv",
    )

    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="artifact",
            columns=[
                FlowOutputColumnSpec(
                    key="evidence_count",
                    field_ref="artifact.evidence_count",
                )
            ],
        ),
    )
    default_result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="tsv"),
    )

    assert result.rows == [{"evidence_count": 7}]
    assert "evidence_count" not in default_result.rows[0]


def test_step_level_evidence_deduplicates_embedded_records_by_id():
    step = _completed_domain_step()
    step["evidence_records"] = [
        {
            "evidence_record_id": "ev-1",
            "verified_quote": "Duplicate runtime copy.",
            "source": "runtime",
        },
        {
            "evidence_record_id": "ev-2",
            "verified_quote": "Runtime-only quote.",
            "source": "runtime",
        },
    ]
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
    )

    result = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="evidence",
            columns=[
                FlowOutputColumnSpec(
                    key="evidence_id",
                    field_ref="evidence.evidence_record_id",
                ),
                FlowOutputColumnSpec(
                    key="quote",
                    field_ref="evidence.verified_quote",
                ),
            ],
        ),
    )

    assert result.rows == [
        {"evidence_id": "ev-1", "quote": "BRCA1 was found."},
        {"evidence_id": "ev-2", "quote": "Runtime-only quote."},
    ]


def test_planner_inventory_and_preview_bound_long_values():
    long_text = "A" * 1000
    step = _completed_domain_step()
    step["candidate"].payload_json["objects"][0]["payload"]["long_note"] = long_text
    step["candidate"].payload_json["objects"][0]["evidence_record_ids"] = list(range(20))
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Projection Flow",
    )

    inventory = inspect_output_artifacts(bundle)
    encoded_inventory = str(inventory)
    assert long_text not in encoded_inventory
    assert "truncated" in encoded_inventory

    preview = preview_output_projection(
        bundle,
        FlowOutputProjectionPlan(
            format="csv",
            row_source="object",
            columns=[
                FlowOutputColumnSpec(
                    key="long_note",
                    field_ref="object.payload.long_note",
                ),
                FlowOutputColumnSpec(
                    key="large_list",
                    field_ref="object.evidence_record_ids",
                ),
            ],
        ),
        limit=1,
    )

    assert preview.status == "ok"
    assert preview.total_count == 2
    assert len(preview.preview_rows) == 1
    encoded_preview = str(preview.preview_rows)
    assert long_text not in encoded_preview
    assert "truncated" in encoded_preview
    assert len(encoded_preview) < 2500


def test_overlarge_max_rows_is_rejected():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
    )

    with pytest.raises(ValueError, match="max_rows"):
        apply_projection_plan(
            bundle,
            FlowOutputProjectionPlan(
                format="csv",
                row_source="object",
                max_rows=10_001,
            ),
        )


def test_legacy_items_payload_is_not_mapped_into_object_or_evidence_rows():
    step = {
        "step": 1,
        "agent_id": "gene",
        "agent_name": "Gene Specialist",
        "candidate": SimpleNamespace(
            agent_key="gene",
            adapter_key="gene",
            candidate_count=1,
            payload_json={
                "items": [
                    {
                        "label": "APOE",
                        "status": "candidate",
                        "entity_type": "gene",
                        "evidence": [
                            {
                                "evidence_record_id": "legacy-ev-1",
                                "verified_quote": "APOE evidence.",
                            }
                        ],
                    }
                ],
                "run_summary": {"candidate_count": 1},
            },
        ),
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[step],
        flow_name="Legacy Projection Flow",
    )

    assert bundle.artifacts[0].artifact_shape == "non_structured"
    assert bundle.rows_for_source("object") == []
    assert bundle.rows_for_source("evidence") == []
    assert any("No canonical curation object rows" in warning for warning in bundle.warnings)
