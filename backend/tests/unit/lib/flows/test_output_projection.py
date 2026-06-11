import json

import pytest
from types import SimpleNamespace

from src.lib.flows.output_projection import (
    FlowOutputColumnSpec,
    FlowOutputFilterSpec,
    FlowOutputProjectionPlan,
    FlowOutputSortSpec,
    FlowOutputTransformSpec,
    apply_projection_plan,
    build_flow_output_artifact_bundle,
    default_projection_plan,
    inspect_output_artifacts,
    preview_output_projection,
)


def _completed_domain_step():
    return {
        "step": 1,
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


def test_default_tsv_artifact_projection_matches_compatibility_columns():
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[_completed_domain_step()],
        flow_name="Projection Flow",
        output_format="tsv",
    )

    result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="tsv"),
    )

    assert result.row_source == "artifact"
    assert result.rows == [
        {
            "step": 1,
            "agent_id": "gene_extractor",
            "agent_name": "Gene Extractor",
            "adapter_key": "gene",
            "domain_pack_id": "gene",
            "envelope_id": "env-gene-1",
            "object_count": 2,
            "candidate_count": 2,
            "artifact_preview": "Extracted gene rows.",
        }
    ]


def test_generic_pdf_step_output_projects_answer_table_rows_without_candidate():
    payload = {
        "answer": (
            "Extracted rows:\n\n"
            "synonym\tsource\tsource_identifier\tcount\n"
            "Ck:GFP\tThis study\tNew in paper\t4\n"
            "Actn RNAi\tSource not found\tNot found\t2\n"
        ),
        "items": [
            {
                "label": "group-level audit item",
                "entity_type": "genetic reagent group",
                "evidence_record_ids": ["ev-1"],
            }
        ],
        "evidence_records": [
            {
                "evidence_record_id": "ev-1",
                "verified_quote": "Server verified quote.",
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
                "output_preview": "Extracted rows.",
                "candidate": None,
            }
        ],
        flow_name="PDF Projection Flow",
        output_format="tsv",
    )

    object_rows = bundle.rows_for_source("object")
    assert len(bundle.artifacts) == 1
    assert bundle.artifacts[0].artifact_shape == "generic_pdf_answer_table"
    assert bundle.default_row_source == "object"
    assert len(object_rows) == 2
    assert object_rows[0]["object.payload.synonym"] == "Ck:GFP"
    assert object_rows[0]["object.payload.source_identifier"] == "New in paper"
    assert object_rows[1]["object.payload.count"] == "2"
    assert bundle.rows_for_source("evidence")[0]["evidence.evidence_record_id"] == "ev-1"

    result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="tsv"),
    )

    assert result.row_source == "object"
    assert [column.key for column in result.columns] == [
        "synonym",
        "source",
        "source_identifier",
        "count",
    ]
    assert result.rows == [
        {
            "synonym": "Ck:GFP",
            "source": "This study",
            "source_identifier": "New in paper",
            "count": "4",
        },
        {
            "synonym": "Actn RNAi",
            "source": "Source not found",
            "source_identifier": "Not found",
            "count": "2",
        },
    ]


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
            format="tsv",
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


def test_legacy_items_payload_is_explicitly_mapped_with_warning():
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

    objects = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="object",
            columns=[
                FlowOutputColumnSpec(key="label", field_ref="object.label"),
                FlowOutputColumnSpec(key="status", field_ref="object.status"),
            ],
        ),
    )
    evidence = apply_projection_plan(
        bundle,
        FlowOutputProjectionPlan(
            format="json",
            row_source="evidence",
            columns=[
                FlowOutputColumnSpec(key="object_id", field_ref="object.object_id"),
                FlowOutputColumnSpec(key="quote", field_ref="evidence.verified_quote"),
            ],
        ),
    )

    assert objects.rows == [{"label": "APOE", "status": "candidate"}]
    assert evidence.rows == [{"object_id": "1", "quote": "APOE evidence."}]
    assert any("Legacy extraction envelope" in warning for warning in bundle.warnings)
