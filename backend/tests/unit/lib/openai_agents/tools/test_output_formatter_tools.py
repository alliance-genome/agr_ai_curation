import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.lib.flows.output_projection import (
    FlowOutputArtifactBundle,
    build_flow_output_artifact_bundle,
)
from src.lib.openai_agents.tools.output_formatter_tools import (
    build_output_formatter_tools,
)


def _completed_gene_step() -> dict:
    return {
        "step": 1,
        "extraction_result_id": "extract-gene-1",
        "agent_id": "gene_extractor",
        "agent_name": "Gene Extractor",
        "output_preview": "Extracted gene rows.",
        "candidate": SimpleNamespace(
            agent_key="gene_extractor",
            adapter_key="gene",
            candidate_count=3,
            conversation_summary="Extracted three genes.",
            payload_json={
                "domain_pack_id": "gene",
                "envelope_id": "env-gene-1",
                "extracted_objects": [
                    {
                        "object_type": "Gene",
                        "object_id": "gene-1",
                        "status": "validated",
                        "payload": {
                            "symbol": "BRCA1",
                            "primary_external_id": "TEST:GENE001",
                            "score": 2,
                            "aliases": ["breast cancer 1", "brca-1"],
                            "approved": True,
                        },
                        "evidence_record_ids": ["ev-1", "ev-2"],
                    },
                    {
                        "object_type": "Gene",
                        "object_id": "gene-2",
                        "status": "needs_review",
                        "payload": {
                            "symbol": "TP53",
                            "primary_external_id": "TEST:GENE002",
                            "score": 5,
                            "aliases": [],
                            "approved": False,
                        },
                    },
                    {
                        "object_type": "Gene",
                        "object_id": "gene-3",
                        "status": "validated",
                        "payload": {
                            "symbol": "MAPK",
                            "primary_external_id": "TEST:GENE003",
                            "score": 3,
                            "aliases": ["erk"],
                            "approved": True,
                        },
                        "evidence_record_ids": ["ev-3"],
                    },
                ],
            },
        ),
    }


def _completed_generic_attribute_step() -> dict:
    return {
        "step": 1,
        "extraction_result_id": "extract-generic-1",
        "agent_id": "pdf_extraction",
        "agent_name": "General PDF Extraction Agent",
        "output_preview": "Extracted generic observations.",
        "candidate": SimpleNamespace(
            agent_key="pdf_extraction",
            adapter_key="generic",
            candidate_count=2,
            conversation_summary="Extracted two observations.",
            payload_json={
                "domain_pack_id": "generic",
                "envelope_id": "env-generic-1",
                "extracted_objects": [
                    {
                        "object_type": "generic_object",
                        "object_id": "generic-1",
                        "payload": {
                            "class_key": "generic:generic_object",
                            "label": "B cell lymphoma",
                            "semantic_class": "tumor_classification_occurrence",
                            "attributes": {
                                "Cell Type": "B cell",
                                "Tumor Classification Term": "lymphoma",
                                "Species": "Mouse",
                            },
                        },
                    },
                    {
                        "object_type": "generic_object",
                        "object_id": "generic-2",
                        "payload": {
                            "class_key": "generic:generic_object",
                            "label": "T cell lymphoma",
                            "semantic_class": "tumor_classification_occurrence",
                            "attributes": {
                                "Cell Type": "T cell",
                                "Tumor Classification Term": "T-cell lymphoma",
                                "Section": "Results",
                            },
                        },
                    },
                ],
            },
        ),
    }


def _completed_generic_attribute_step_for_result(
    extraction_result_id: str,
    *,
    label: str,
    tumor_term: str,
) -> dict:
    step = deepcopy(_completed_generic_attribute_step())
    step["extraction_result_id"] = extraction_result_id
    step["candidate"].payload_json["envelope_id"] = f"env-{extraction_result_id}"
    step["candidate"].payload_json["extracted_objects"] = [
        {
            "object_type": "generic_object",
            "object_id": f"{extraction_result_id}-object",
            "payload": {
                "class_key": "generic:generic_object",
                "label": label,
                "semantic_class": "tumor_classification_occurrence",
                "attributes": {
                    "Tumor Classification Term": tumor_term,
                    "Section": "Results",
                },
            },
        }
    ]
    return step


def _completed_generic_claim_text_only_step() -> dict:
    return {
        "step": 1,
        "extraction_result_id": "extract-generic-claim-1",
        "agent_id": "pdf_extraction",
        "agent_name": "General PDF Extraction Agent",
        "output_preview": "Extracted generic claims.",
        "candidate": SimpleNamespace(
            agent_key="pdf_extraction",
            adapter_key="generic",
            candidate_count=1,
            payload_json={
                "domain_pack_id": "generic",
                "envelope_id": "env-generic-claim-1",
                "extracted_objects": [
                    {
                        "object_type": "generic_claim",
                        "object_id": "claim-1",
                        "payload": {
                            "class_key": "generic:generic_claim",
                            "label": "Narrative claim",
                            "claim_text": "cell_type=B cell; tumor_type=lymphoma",
                        },
                    }
                ],
            },
        ),
    }


def _completed_mixed_semantic_generic_attribute_step() -> dict:
    step = _completed_generic_attribute_step()
    objects = step["candidate"].payload_json["extracted_objects"]
    objects[1]["payload"]["semantic_class"] = "experimental_condition"
    objects[1]["payload"]["attributes"] = {
        "Condition": "irradiated",
        "Section": "Methods",
    }
    return step


def _bundle() -> FlowOutputArtifactBundle:
    return build_flow_output_artifact_bundle(
        completed_steps=[_completed_gene_step()],
        flow_name="Formatter Tool Flow",
        flow_run_id="flow-run-1",
        document_id="doc-1",
        output_format="csv",
    )


def _tool_by_name(tools, name: str):
    return next(tool for tool in tools if getattr(tool, "name", "") == name)


async def _invoke(tool, payload: dict | None = None) -> dict:
    tool_ctx = SimpleNamespace(tool_name=getattr(tool, "name", "tool"))
    raw = await tool.on_invoke_tool(tool_ctx, json.dumps(payload or {}))
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _tool_param_names(tool) -> set[str]:
    schema = getattr(tool, "params_json_schema", None)
    if schema is None:
        schema = getattr(tool, "parameters_json_schema", None)
    if not isinstance(schema, dict):
        return set()
    return set((schema.get("properties") or {}).keys())


@pytest.mark.asyncio
async def test_formatter_tool_suite_is_plan_only_and_structure_bound():
    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )

    names = {getattr(tool, "name", "") for tool in tools}
    assert names == {
        "explain_formatter_capabilities",
        "inspect_output_artifacts",
        "inspect_output_rows",
        "inspect_field_values",
        "build_default_projection_plan",
        "validate_output_projection",
        "preview_output_projection",
        "finalize_and_save",
        "formatter_cannot_complete",
    }
    assert not {"save_csv_file", "save_tsv_file", "save_json_file"} & names

    forbidden_params = {
        "content",
        "csv",
        "data",
        "data_json",
        "file_content",
        "json",
        "raw_rows",
        "records",
        "rows",
        "tsv",
    }
    for tool in tools:
        assert not (forbidden_params & _tool_param_names(tool))

    capabilities = await _invoke(_tool_by_name(tools, "explain_formatter_capabilities"))
    assert capabilities["status"] == "ok"
    assert "raw row arrays" in capabilities["invariant"]
    assert capabilities["format"] == "csv"


@pytest.mark.asyncio
async def test_inspection_reports_generic_attribute_inventory_and_claim_text_notice():
    attribute_tools = build_output_formatter_tools(
        bundle=build_flow_output_artifact_bundle(
            completed_steps=[_completed_generic_attribute_step()],
            flow_name="Generic Formatter Flow",
            output_format="csv",
        ),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )

    inventory = await _invoke(
        _tool_by_name(attribute_tools, "inspect_output_artifacts"),
        {"example_limit": 1},
    )
    summary = inventory["inventory"]["generic_source_summary"]
    assert summary["generic_source_count"] == 1
    source = summary["sources"][0]
    assert source["source_ref"] == "extract-generic-1"
    assert source["semantic_classes"] == ["tumor_classification_occurrence"]
    assert source["all_attribute_keys"] == [
        "cell_type",
        "tumor_classification_term",
        "species",
        "section",
    ]
    assert source["shared_attribute_keys"] == [
        "cell_type",
        "tumor_classification_term",
    ]
    assert source["semantic_class_attribute_groups"] == [
        {
            "semantic_class": "tumor_classification_occurrence",
            "row_count": 2,
            "all_attribute_keys": [
                "cell_type",
                "tumor_classification_term",
                "species",
                "section",
            ],
            "shared_attribute_keys": [
                "cell_type",
                "tumor_classification_term",
            ],
            "keys_missing_from_some_objects": ["species", "section"],
        }
    ]
    assert source["notices"][0]["code"] == "generic_attribute_key_drift"

    mixed_tools = build_output_formatter_tools(
        bundle=build_flow_output_artifact_bundle(
            completed_steps=[_completed_mixed_semantic_generic_attribute_step()],
            flow_name="Mixed Semantic Generic Formatter Flow",
            output_format="csv",
        ),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )
    mixed_inventory = await _invoke(
        _tool_by_name(mixed_tools, "inspect_output_artifacts"),
        {"example_limit": 1},
    )
    mixed_source = mixed_inventory["inventory"]["generic_source_summary"]["sources"][0]
    assert mixed_source["semantic_classes"] == [
        "tumor_classification_occurrence",
        "experimental_condition",
    ]
    assert mixed_source["semantic_class_attribute_groups"] == [
        {
            "semantic_class": "tumor_classification_occurrence",
            "row_count": 1,
            "all_attribute_keys": [
                "cell_type",
                "tumor_classification_term",
                "species",
            ],
            "shared_attribute_keys": [
                "cell_type",
                "tumor_classification_term",
                "species",
            ],
            "keys_missing_from_some_objects": [],
        },
        {
            "semantic_class": "experimental_condition",
            "row_count": 1,
            "all_attribute_keys": ["condition", "section"],
            "shared_attribute_keys": ["condition", "section"],
            "keys_missing_from_some_objects": [],
        },
    ]
    assert mixed_source["notices"] == []

    claim_tools = build_output_formatter_tools(
        bundle=build_flow_output_artifact_bundle(
            completed_steps=[_completed_generic_claim_text_only_step()],
            flow_name="Generic Claim Formatter Flow",
            output_format="csv",
        ),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )
    claim_inventory = await _invoke(
        _tool_by_name(claim_tools, "inspect_output_artifacts"),
        {"example_limit": 1},
    )
    claim_source = claim_inventory["inventory"]["generic_source_summary"]["sources"][0]
    assert claim_source["all_attribute_keys"] == []
    assert claim_source["notices"][0]["code"] == "generic_claim_text_only_unstructured"


@pytest.mark.asyncio
async def test_default_plan_source_ref_prefers_selected_generic_attribute_columns():
    tools = build_output_formatter_tools(
        bundle=build_flow_output_artifact_bundle(
            completed_steps=[_completed_gene_step(), _completed_generic_attribute_step()],
            flow_name="Mixed Source Formatter Flow",
            output_format="csv",
        ),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )

    default_plan = await _invoke(
        _tool_by_name(tools, "build_default_projection_plan"),
        {
            "row_source": "object",
            "source_ref": "extract-generic-1",
        },
    )

    assert default_plan["status"] == "ok"
    assert default_plan["plan"]["source_extraction_result_ids"] == ["extract-generic-1"]
    assert default_plan["plan"]["row_strategy"] == "wide_union"
    assert [
        column["field_ref"]
        for column in default_plan["plan"]["columns"][:4]
    ] == [
        "object.attribute.cell_type",
        "object.attribute.tumor_classification_term",
        "object.attribute.species",
        "object.attribute.section",
    ]


@pytest.mark.asyncio
async def test_default_plan_preserves_selected_older_result_source_ref():
    older_step = _completed_generic_attribute_step_for_result(
        "extract-older-1",
        label="Older endogenous tumor observation",
        tumor_term="endogenous tumor",
    )
    newer_step = _completed_generic_attribute_step_for_result(
        "extract-newer-1",
        label="Newer unrelated observation",
        tumor_term="control tissue",
    )
    tools = build_output_formatter_tools(
        bundle=build_flow_output_artifact_bundle(
            completed_steps=[newer_step, older_step],
            flow_name="Prior Result Formatter Flow",
            output_format="csv",
        ),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )

    default_plan = await _invoke(
        _tool_by_name(tools, "build_default_projection_plan"),
        {
            "row_source": "object",
            "source_ref": "extraction-result:extract-older-1",
        },
    )
    preview = await _invoke(
        _tool_by_name(tools, "preview_output_projection"),
        {"plan_json": json.dumps(default_plan["plan"])},
    )

    assert default_plan["status"] == "ok"
    assert default_plan["plan"]["source_extraction_result_ids"] == ["extract-older-1"]
    assert preview["status"] == "ok"
    serialized_rows = json.dumps(preview["preview"]["preview_rows"])
    assert "Older endogenous tumor observation" in serialized_rows
    assert "Newer unrelated observation" not in serialized_rows


@pytest.mark.asyncio
async def test_inspection_tools_read_bounded_saved_bundle_rows_and_values():
    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=lambda *_args: None,  # type: ignore[arg-type]
    )

    inventory = await _invoke(
        _tool_by_name(tools, "inspect_output_artifacts"),
        {"example_limit": 2},
    )
    assert inventory["status"] == "ok"
    assert inventory["inventory"]["row_sources"]["object"]["row_count"] == 3
    assert inventory["inventory"]["source_refs"]["source_extraction_result_ids"] == [
        "extract-gene-1"
    ]

    row_page = await _invoke(
        _tool_by_name(tools, "inspect_output_rows"),
        {
            "row_source": "object",
            "field_refs_json": json.dumps(
                ["object.payload.symbol", "object.payload.score", "object.status"]
            ),
            "filters_json": json.dumps(
                [{"field_ref": "object.status", "op": "eq", "value": "validated"}]
            ),
            "sort_json": json.dumps(
                [{"field_ref": "object.payload.score", "direction": "desc"}]
            ),
            "limit": 1,
        },
    )
    assert row_page["status"] == "ok"
    assert row_page["total_count"] == 2
    assert row_page["rows"] == [
        {
            "object_payload_symbol": "MAPK",
            "object_payload_score": 3,
            "object_status": "validated",
        }
    ]
    assert row_page["next_cursor"] == "1"

    field_values = await _invoke(
        _tool_by_name(tools, "inspect_field_values"),
        {
            "row_source": "object",
            "field_ref": "object.status",
        },
    )
    assert field_values["status"] == "ok"
    assert field_values["distinct_count"] == 2
    assert {"value": "validated", "count": 2} in field_values["values"]


@pytest.mark.asyncio
async def test_default_plan_and_finalize_save_projected_rows_only():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(
            {
                "output_format": output_format,
                "projection": projection,
                "filename_hint": filename_hint,
                "formatter_agent_id": formatter_agent_id,
            }
        )
        return {
            "file_id": "file-1",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 123,
            "download_url": "/download/file-1",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )

    default_plan = await _invoke(
        _tool_by_name(tools, "build_default_projection_plan"),
        {
            "row_source": "object",
            "row_strategy": "object_ledger",
            "source_ref": "extract-gene-1",
        },
    )
    assert default_plan["status"] == "ok"
    assert default_plan["plan"]["format"] == "csv"
    assert default_plan["plan"]["row_strategy"] == "object_ledger"
    assert default_plan["plan"]["source_extraction_result_ids"] == ["extract-gene-1"]

    result = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {
            "plan_json": "",
            "filename_hint": "gene-export",
        },
    )
    assert result["status"] == "ok"
    assert result["file_id"] == "file-1"
    assert result["projection_summary"]["total_count"] == 3
    assert len(saved) == 1
    assert saved[0]["formatter_agent_id"] == "csv_formatter"
    assert saved[0]["filename_hint"] == "gene-export"
    assert saved[0]["projection"].rows


@pytest.mark.asyncio
async def test_finalize_and_save_saves_once_per_formatter_tool_suite():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        file_id = f"file-{len(saved) + 1}"
        saved.append(
            {
                "output_format": output_format,
                "projection": projection,
                "filename_hint": filename_hint,
                "formatter_agent_id": formatter_agent_id,
                "file_id": file_id,
            }
        )
        return {
            "file_id": file_id,
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 123,
            "download_url": f"/download/{file_id}",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    finalize = _tool_by_name(tools, "finalize_and_save")

    first = await _invoke(
        finalize,
        {
            "plan_json": "",
            "filename_hint": "gene-export",
        },
    )
    second = await _invoke(
        finalize,
        {
            "plan_json": "",
            "filename_hint": "gene-export-recreated",
        },
    )

    assert first["status"] == "ok"
    assert first["file_id"] == "file-1"
    assert second["status"] == "invalid"
    assert second["code"] == "already_finalized"
    assert second["format"] == "csv"
    assert second["formatter_agent_id"] == "csv_formatter"
    assert second["saved_file"] is True
    assert second["finalized_file"]["file_id"] == "file-1"
    assert "already finalized and saved one file" in second["errors"][0]
    assert len(saved) == 1
    assert saved[0]["filename_hint"] == "gene-export"

    later_tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    later = await _invoke(
        _tool_by_name(later_tools, "finalize_and_save"),
        {
            "plan_json": "",
            "filename_hint": "gene-export-recreated",
        },
    )

    assert later["status"] == "ok"
    assert later["file_id"] == "file-2"
    assert len(saved) == 2
    assert saved[1]["filename_hint"] == "gene-export-recreated"


@pytest.mark.asyncio
async def test_validate_preview_and_finalize_support_full_csv_shaping_surface():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "file-shaped",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 456,
            "download_url": "/download/file-shaped",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    plan = {
        "format": "csv",
        "row_source": "object",
        "source_extraction_result_ids": ["extract-gene-1"],
        "columns": [
            {
                "key": "symbol",
                "header": "Gene Symbol",
                "field_ref": "object.payload.symbol",
            },
            {
                "key": "score",
                "header": "Score",
                "field_ref": "object.payload.score",
            },
            {
                "key": "species",
                "header": "Species",
                "transform": {"type": "literal", "value": "Drosophila melanogaster"},
            },
            {
                "key": "best_label",
                "transform": {
                    "type": "first_non_empty",
                    "field_refs": ["object.label", "object.payload.symbol"],
                },
            },
            {
                "key": "display",
                "transform": {
                    "type": "concat",
                    "values": [
                        {"field_ref": "object.payload.symbol"},
                        " (",
                        {"field_ref": "object.payload.primary_external_id"},
                        ")",
                    ],
                    "separator": "",
                },
            },
            {
                "key": "evidence_ids",
                "transform": {
                    "type": "join_list",
                    "field_ref": "object.evidence_record_ids",
                    "separator": "; ",
                },
            },
            {
                "key": "evidence_count",
                "transform": {
                    "type": "count",
                    "field_ref": "object.evidence_record_ids",
                },
            },
            {
                "key": "status_label",
                "transform": {
                    "type": "map_value",
                    "field_ref": "object.status",
                    "mapping": {
                        "validated": "Ready",
                        "needs_review": "Review",
                    },
                },
            },
            {
                "key": "approval",
                "transform": {
                    "type": "boolean_label",
                    "field_ref": "object.payload.approved",
                    "true_label": "Approved",
                    "false_label": "Not approved",
                },
            },
        ],
        "filters": [{"field_ref": "object.payload.score", "op": "gte", "value": 3}],
        "sort": [{"field_ref": "object.payload.symbol", "direction": "asc"}],
        "missing_value": "",
        "max_rows": 2,
    }

    validation = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(plan)},
    )
    assert validation["status"] == "ok"
    assert [column["key"] for column in validation["columns"]] == [
        "symbol",
        "score",
        "species",
        "best_label",
        "display",
        "evidence_ids",
        "evidence_count",
        "status_label",
        "approval",
    ]

    preview = await _invoke(
        _tool_by_name(tools, "preview_output_projection"),
        {"plan_json": json.dumps(plan), "limit": 2},
    )
    assert preview["status"] == "ok"
    assert [row["symbol"] for row in preview["preview"]["preview_rows"]] == [
        "MAPK",
        "TP53",
    ]

    result = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {"plan_json": json.dumps(plan), "filename_hint": "gene-shaped"},
    )
    assert result["status"] == "ok"
    assert len(saved) == 1
    assert saved[0].rows == [
        {
            "symbol": "MAPK",
            "score": 3,
            "species": "Drosophila melanogaster",
            "best_label": "MAPK",
            "display": "MAPK (TEST:GENE003)",
            "evidence_ids": "ev-3",
            "evidence_count": 1,
            "status_label": "Ready",
            "approval": "Approved",
        },
        {
            "symbol": "TP53",
            "score": 5,
            "species": "Drosophila melanogaster",
            "best_label": "TP53",
            "display": "TP53 (TEST:GENE002)",
            "evidence_ids": "",
            "evidence_count": 0,
            "status_label": "Review",
            "approval": "Not approved",
        },
    ]


@pytest.mark.asyncio
async def test_json_formatter_supports_grouped_and_bundle_shapes():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "file-json",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 789,
            "download_url": "/download/file-json",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="json",
        formatter_agent_id="json_formatter",
        save_projected_output=_fake_save,
    )
    grouped_plan = {
        "format": "json",
        "row_source": "object",
        "columns": [
            {"key": "symbol", "field_ref": "object.payload.symbol"},
            {"key": "status", "field_ref": "object.status"},
        ],
        "group_by": ["object.status"],
        "json_shape": "grouped",
    }

    grouped = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {"plan_json": json.dumps(grouped_plan), "filename_hint": "gene-grouped"},
    )
    assert grouped["status"] == "ok"
    assert saved[0].json_data is not None
    assert {
        tuple(group["group"].items())[0]
        for group in saved[0].json_data
    } == {
        ("object.status", "validated"),
        ("object.status", "needs_review"),
    }

    bundle_plan = {
        "format": "json",
        "row_source": "object",
        "columns": [{"key": "symbol", "field_ref": "object.payload.symbol"}],
        "json_shape": "bundle",
    }
    bundle_result = await _invoke(
        _tool_by_name(tools, "preview_output_projection"),
        {"plan_json": json.dumps(bundle_plan), "limit": 2},
    )
    assert bundle_result["status"] == "ok"


@pytest.mark.asyncio
async def test_invalid_raw_content_plans_and_impossible_requests_do_not_save():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "unexpected",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 1,
            "download_url": "/download/unexpected",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )

    raw_rows_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [{"key": "symbol", "field_ref": "object.payload.symbol"}],
        "rows": [{"symbol": "model-authored"}],
    }
    invalid = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(raw_rows_plan)},
    )
    assert invalid["status"] == "invalid"
    assert "model-authored content key 'rows'" in invalid["errors"][0]

    grouped_csv_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [{"key": "symbol", "field_ref": "object.payload.symbol"}],
        "group_by": ["object.status"],
    }
    grouped_csv = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(grouped_csv_plan)},
    )
    assert grouped_csv["status"] == "invalid"
    assert "group_by is not supported for CSV projections" in grouped_csv["errors"][0]

    missing_field = await _invoke(
        _tool_by_name(tools, "inspect_field_values"),
        {
            "row_source": "object",
            "field_ref": "object.payload.not_real",
        },
    )
    assert missing_field["status"] == "invalid"

    cannot_complete = await _invoke(
        _tool_by_name(tools, "formatter_cannot_complete"),
        {
            "reason": "The saved bundle has no validated disease rows.",
            "missing_data": "validated disease rows",
            "suggested_next_step": "Run a disease extraction first.",
        },
    )
    assert cannot_complete == {
        "format": "csv",
        "formatter_agent_id": "csv_formatter",
        "missing_data": "validated disease rows",
        "reason": "The saved bundle has no validated disease rows.",
        "saved_file": False,
        "status": "cannot_complete",
        "suggested_next_step": "Run a disease extraction first.",
    }
    assert saved == []


@pytest.mark.asyncio
async def test_literal_only_plans_are_rejected_even_with_saved_rows():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "unexpected",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 1,
            "download_url": "/download/unexpected",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    literal_only_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [
            {
                "key": "species",
                "transform": {
                    "type": "literal",
                    "value": "Drosophila melanogaster",
                },
            },
            {
                "key": "note",
                "transform": {
                    "type": "literal",
                    "value": "constant note",
                },
            },
        ],
    }

    validation = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(literal_only_plan)},
    )
    assert validation["status"] == "invalid"
    assert "literal-only files are not allowed" in validation["errors"][0]

    result = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {
            "plan_json": json.dumps(literal_only_plan),
            "filename_hint": "literal-only",
        },
    )
    assert result["status"] == "invalid"
    assert "literal-only files are not allowed" in result["errors"][0]
    assert saved == []


@pytest.mark.asyncio
async def test_transform_literals_cannot_smuggle_structured_replacement_content():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "unexpected",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 1,
            "download_url": "/download/unexpected",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    structured_literal_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [
            {"key": "symbol", "field_ref": "object.payload.symbol"},
            {
                "key": "replacement",
                "transform": {
                    "type": "literal",
                    "value": {"rows": [{"symbol": "model-authored"}]},
                },
            },
        ],
    }
    encoded_json_literal_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [
            {"key": "symbol", "field_ref": "object.payload.symbol"},
            {
                "key": "replacement",
                "transform": {
                    "type": "concat",
                    "values": [
                        {"field_ref": "object.payload.symbol"},
                        "[{\"symbol\":\"model-authored\"}]",
                    ],
                },
            },
        ],
    }

    structured = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(structured_literal_plan)},
    )
    assert structured["status"] == "invalid"
    assert "structured replacement data" in structured["errors"][0]

    encoded = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {
            "plan_json": json.dumps(encoded_json_literal_plan),
            "filename_hint": "encoded",
        },
    )
    assert encoded["status"] == "invalid"
    assert "encoded JSON objects or arrays" in encoded["errors"][0]
    assert saved == []


@pytest.mark.asyncio
async def test_output_labels_separators_and_missing_values_cannot_smuggle_file_content():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "unexpected",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 1,
            "download_url": "/download/unexpected",
        }

    tools = build_output_formatter_tools(
        bundle=_bundle(),
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    newline_header_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [
            {
                "key": "symbol",
                "header": "Symbol\nforged_header,forged_value",
                "field_ref": "object.payload.symbol",
            }
        ],
    }
    encoded_separator_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [
            {
                "key": "symbol",
                "field_ref": "object.payload.symbol",
            },
            {
                "key": "display",
                "transform": {
                    "type": "concat",
                    "values": [
                        {"field_ref": "object.payload.symbol"},
                        {"field_ref": "object.payload.primary_external_id"},
                    ],
                    "separator": "[{\"symbol\":\"model-authored\"}]",
                },
            },
        ],
    }
    missing_value_plan = {
        "format": "csv",
        "row_source": "object",
        "columns": [
            {
                "key": "symbol",
                "field_ref": "object.payload.symbol",
            },
        ],
        "missing_value": "{\"rows\":[{\"symbol\":\"model-authored\"}]}",
    }

    newline_header = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(newline_header_plan)},
    )
    assert newline_header["status"] == "invalid"
    assert "newline-delimited file content" in newline_header["errors"][0]

    encoded_separator = await _invoke(
        _tool_by_name(tools, "validate_output_projection"),
        {"plan_json": json.dumps(encoded_separator_plan)},
    )
    assert encoded_separator["status"] == "invalid"
    assert "encoded JSON objects or arrays" in encoded_separator["errors"][0]

    missing_value = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {
            "plan_json": json.dumps(missing_value_plan),
            "filename_hint": "missing-value",
        },
    )
    assert missing_value["status"] == "invalid"
    assert "encoded JSON objects or arrays" in missing_value["errors"][0]
    assert saved == []


@pytest.mark.asyncio
async def test_finalize_rejects_literal_only_files_without_saved_rows():
    saved = []

    async def _fake_save(output_format, projection, filename_hint, formatter_agent_id):
        saved.append(projection)
        return {
            "file_id": "unexpected",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 1,
            "download_url": "/download/unexpected",
        }

    empty_bundle = FlowOutputArtifactBundle(flow_name="Empty Export")
    tools = build_output_formatter_tools(
        bundle=empty_bundle,
        output_format="csv",
        formatter_agent_id="csv_formatter",
        save_projected_output=_fake_save,
    )
    literal_plan = {
        "format": "csv",
        "row_source": "artifact",
        "columns": [
            {
                "key": "note",
                "transform": {
                    "type": "literal",
                    "value": "model-authored standalone row",
                },
            }
        ],
    }

    result = await _invoke(
        _tool_by_name(tools, "finalize_and_save"),
        {
            "plan_json": json.dumps(literal_plan),
            "filename_hint": "standalone",
        },
    )
    assert result["status"] == "invalid"
    assert "literal-only files" in result["errors"][0]
    assert saved == []
