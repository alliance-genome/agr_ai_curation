"""Alliance gene-expression builder tool tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from agr_ai_curation_alliance.tools import agr_curation
from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import resolver_call_ledger
from src.lib.openai_agents.tools import evidence_workspace


def _tool_fn(tool: Any, name: str):
    return agr_curation._unwrap_function_tool_callable(tool, name)


def _workspace() -> builder.ExtractionBuilderWorkspace:
    return builder.ExtractionBuilderWorkspace(
        run_id="trace-gex",
        document_id="doc-1",
        domain_pack_id=agr_curation.GENE_EXPRESSION_DOMAIN_PACK_ID,
        agent_id="gene_expression_extraction",
    )


def _resolved_output(
    *,
    field_path: str = "relation.name",
    selected_value: str = "is_expressed_in",
    selected_name: str | None = None,
    selected_curie: str | None = None,
    instruction_value: Any | None = None,
    term_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected_name = selected_name or selected_value
    instruction_value = selected_value if instruction_value is None else instruction_value
    return {
        "status": "resolved",
        "data": {
            "domain_pack_id": agr_curation.GENE_EXPRESSION_DOMAIN_PACK_ID,
            "object_type": agr_curation.GENE_EXPRESSION_OBJECT_TYPE,
            "field_path": field_path,
            "source_phrase": selected_value,
            "payload_field_instructions": {
                "set": [{"field_path": field_path, "value": instruction_value}]
            },
            "helper_selection": {
                "field_path": field_path,
                "source_tool": "resolve_domain_field_term",
                "authority": "selector_evidence",
                "lookup_status": "success",
                "source_phrase": selected_value,
                "term_source": term_source
                or {"kind": "controlled_vocabulary", "vocabulary": "Expression Relation"},
                "selected_value": selected_value,
                "selected_name": selected_name,
                **({"selected_curie": selected_curie} if selected_curie else {}),
            },
        },
    }


@pytest.fixture
def active_builder_context(monkeypatch):
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agr_curation, "write_extraction_trace_event", lambda **event: events.append(event) or event)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: events.append(event) or event)
    monkeypatch.setattr(
        resolver_call_ledger,
        "write_extraction_trace_event",
        lambda **event: events.append(event) or event,
    )
    workspace = _workspace()
    ledger = resolver_call_ledger.ResolverCallLedger(trace_id=workspace.run_id)
    evidence_records = [
        {
            "evidence_record_id": "evidence-67598e5688f123c8",
            "entity": "pef-1",
            "verified_quote": "PEF-1::GFP expression was detected in the cilium.",
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "page": 3,
            "section": "Results",
        }
    ]
    builder_token = builder.set_active_extraction_builder_workspace(workspace)
    ledger_token = resolver_call_ledger.set_active_resolver_call_ledger(ledger)
    evidence_token = evidence_workspace.set_active_evidence_records(evidence_records)
    try:
        yield workspace, ledger, events
    finally:
        evidence_workspace.reset_active_evidence_records(evidence_token)
        resolver_call_ledger.reset_active_resolver_call_ledger(ledger_token)
        builder.reset_active_extraction_builder_workspace(builder_token)


def _stage_valid_observation(ledger: resolver_call_ledger.ResolverCallLedger):
    ledger.record_tool_output(
        tool_call_id="call_relation",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(),
    )
    return _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(
        pending_ref_id="gene-expression-annotation-pef-1",
        evidence_record_ids=["evidence-67598e5688f123c8"],
        where_expressed_statement="PEF-1::GFP expression in the cilium",
        subject={
            "source_phrase": "PEF-1::GFP",
            "gene_symbol": "pef-1",
            "primary_external_id": "WB:WBGene00000001",
        },
        reference={
            "source_phrase": "PMID 39550471",
            "reference_id": "PMID:39550471",
        },
        controlled_fields=[
            {
                "field_path": "relation.name",
                "selected_value": "is_expressed_in",
            }
        ],
    )


def _stage_materializable_observation(ledger: resolver_call_ledger.ResolverCallLedger):
    resolver_outputs = {
        "call_relation": _resolved_output(),
        "call_assay": _resolved_output(
            field_path="expression_experiment.expression_assay_used",
            selected_value="MMO:0000655",
            selected_name="GFP reporter assay",
            selected_curie="MMO:0000655",
            instruction_value={"curie": "MMO:0000655", "name": "GFP reporter assay"},
            term_source={"kind": "ontology", "ontology_family": "assay"},
        ),
        "call_stage": _resolved_output(
            field_path="when_expressed_stage_name",
            selected_value="L2 larva",
            selected_name="L2 larva",
            term_source={"kind": "ontology", "ontology_family": "life_stage"},
        ),
        "call_anatomy": _resolved_output(
            field_path="expression_pattern.where_expressed.anatomical_structure",
            selected_value="WBbt:0001234",
            selected_name="cilium",
            selected_curie="WBbt:0001234",
            instruction_value={"curie": "WBbt:0001234", "name": "cilium"},
            term_source={"kind": "ontology", "ontology_family": "anatomy"},
        ),
    }
    for call_id, output in resolver_outputs.items():
        ledger.record_tool_output(
            tool_call_id=call_id,
            tool_name="resolve_domain_field_term",
            output=output,
        )
    return _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(
        pending_ref_id="gene-expression-annotation-pef-1",
        evidence_record_ids=["evidence-67598e5688f123c8"],
        where_expressed_statement="PEF-1::GFP expression in the cilium",
        subject={
            "source_phrase": "PEF-1::GFP",
            "gene_symbol": "pef-1",
            "primary_external_id": "WB:WBGene00000001",
        },
        reference={
            "source_phrase": "PMID 39550471",
            "reference_id": "PMID:39550471",
        },
        controlled_fields=[
            {
                "field_path": "relation.name",
                "selected_value": "is_expressed_in",
            },
            {
                "field_path": "expression_experiment.expression_assay_used",
                "selected_value": "MMO:0000655",
            },
            {
                "field_path": "when_expressed_stage_name",
                "selected_value": "L2 larva",
            },
            {
                "field_path": "expression_pattern.where_expressed.anatomical_structure",
                "selected_value": "WBbt:0001234",
            },
        ],
    )
def test_gene_expression_builder_tool_schemas_are_strict():
    tools = [
        agr_curation.stage_gene_expression_observation,
        agr_curation.patch_gene_expression_observation,
        agr_curation.discard_gene_expression_observation,
        agr_curation.list_staged_gene_expression_observations,
        agr_curation.finalize_gene_expression_extraction,
    ]

    for tool in tools:
        schema = getattr(tool, "params_json_schema", {}) or {}
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required") or []) == set((schema.get("properties") or {}).keys())
        _assert_closed_objects(schema)

    stage_schema = getattr(agr_curation.stage_gene_expression_observation, "params_json_schema", {})
    assert (stage_schema["properties"]["evidence_record_ids"].get("maxItems")) == 20

    patch_schema = getattr(agr_curation.patch_gene_expression_observation, "params_json_schema", {})
    update_schema = _defs_schema(patch_schema, "GeneExpressionPatchUpdateInput")
    assert "enum" in update_schema["properties"]["field_path"]
    assert "free_form.path" not in update_schema["properties"]["field_path"]["enum"]


def _assert_closed_objects(schema: Mapping[str, Any]) -> None:
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required") or []) == set((schema.get("properties") or {}).keys())
    for value in schema.values():
        if isinstance(value, Mapping):
            _assert_closed_objects(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    _assert_closed_objects(item)


def _defs_schema(schema: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    defs = schema.get("$defs") or schema.get("definitions") or {}
    return defs[name]


def test_resolver_call_ledger_records_only_valid_resolved_outputs(active_builder_context):
    _workspace, ledger, events = active_builder_context

    rejected = ledger.record_tool_output(
        tool_call_id="call_search",
        tool_name="search_domain_field_terms",
        output=_resolved_output(),
    )
    recorded = ledger.record_tool_output(
        tool_call_id="call_relation",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(),
    )

    assert rejected is None
    assert recorded is not None
    assert ledger.get("call_relation").provenance_selection()["resolver_call_id"] == "call_relation"
    assert any(event["event_type"] == "resolver_call_ledger.recorded" for event in events)


def test_stage_gene_expression_observation_copies_resolver_provenance(active_builder_context):
    workspace, ledger, events = active_builder_context

    result = _stage_valid_observation(ledger)

    assert result.status == "ok"
    candidate = workspace.candidates["gex-candidate-1"]
    assert candidate.evidence_record_ids == ["evidence-67598e5688f123c8"]
    assert candidate.resolver_selection_refs == ["call_relation"]
    assert candidate.staged_fields["relation"]["name"] == "is_expressed_in"
    selection = candidate.staged_fields["metadata"]["provenance"]["helper_selections"][0]
    assert selection["resolver_call_id"] == "call_relation"
    assert selection["source_tool"] == "resolve_domain_field_term"
    assert any(event["event_type"] == "gene_expression_builder.stage_completed" for event in events)


def test_stage_rejects_missing_resolver_provenance(active_builder_context):
    _workspace, _ledger, events = active_builder_context

    result = _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(
        pending_ref_id="gene-expression-annotation-pef-1",
        evidence_record_ids=["evidence-1"],
        where_expressed_statement="expression in cilium",
        subject={
            "source_phrase": "PEF-1::GFP",
            "gene_symbol": "pef-1",
            "primary_external_id": "WB:WBGene00000001",
        },
        reference={"source_phrase": "PMID 39550471", "reference_id": "PMID:39550471"},
        controlled_fields=[
            {
                "field_path": "relation.name",
                "selected_value": "is_expressed_in",
            }
        ],
    )

    assert result.status == "error"
    assert result.failure_classification == "validation_failed"
    assert result.data["validation_issues"][0]["reason"] == "unresolved_selected_value"
    assert any(
        event["event_type"] == "gene_expression_builder.missing_provenance_rejected"
        for event in events
    )


def test_stage_rejects_missing_evidence_ids(active_builder_context):
    _workspace, ledger, _events = active_builder_context
    ledger.record_tool_output(
        tool_call_id="call_relation",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(),
    )

    result = _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(
        pending_ref_id="gene-expression-annotation-pef-1",
        evidence_record_ids=[],
        where_expressed_statement="expression in cilium",
        subject={
            "source_phrase": "PEF-1::GFP",
            "gene_symbol": "pef-1",
            "primary_external_id": "WB:WBGene00000001",
        },
        reference={"source_phrase": "PMID 39550471", "reference_id": "PMID:39550471"},
        controlled_fields=[
            {
                "field_path": "relation.name",
                "selected_value": "is_expressed_in",
            }
        ],
    )

    assert {issue["reason"] for issue in result.data["validation_issues"]} == {"too_short"}


def test_stage_rejects_placeholder_reference(active_builder_context):
    _workspace, ledger, _events = active_builder_context
    ledger.record_tool_output(
        tool_call_id="call_relation",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(),
    )

    result = _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(
        pending_ref_id="gene-expression-annotation-pef-1",
        evidence_record_ids=["evidence-1"],
        where_expressed_statement="expression in cilium",
        subject={
            "source_phrase": "PEF-1::GFP",
            "gene_symbol": "pef-1",
            "primary_external_id": "WB:WBGene00000001",
        },
        reference={"source_phrase": "PMID placeholder", "reference_id": "PMID:..."},
        controlled_fields=[
            {
                "field_path": "relation.name",
                "selected_value": "is_expressed_in",
            }
        ],
    )

    assert {issue["reason"] for issue in result.data["validation_issues"]} == {
        "placeholder_reference"
    }


def test_patch_rejects_free_form_field_and_requires_resolver_for_controlled_patch(
    active_builder_context,
):
    _workspace, ledger, _events = active_builder_context
    _stage_valid_observation(ledger)

    result = _tool_fn(
        agr_curation.patch_gene_expression_observation,
        "patch_gene_expression_observation",
    )(
        candidate_id="gex-candidate-1",
        pending_ref_id="gene-expression-annotation-pef-1",
        updates=[
            {
                "field_path": "free_form.path",
                "string_value": "nope",
                "evidence_record_ids": None,
            },
            {
                "field_path": "relation.name",
                "string_value": None,
                "evidence_record_ids": None,
            },
        ],
    )

    reasons = {issue["reason"] for issue in result.data["validation_issues"]}
    assert "literal_error" in reasons
    assert "value_error" in reasons


def test_patch_updates_reference_and_controlled_field_from_ledger(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_valid_observation(ledger)
    ledger.record_tool_output(
        tool_call_id="call_relation_part_of",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(selected_value="is_not_expressed_in"),
    )

    result = _tool_fn(
        agr_curation.patch_gene_expression_observation,
        "patch_gene_expression_observation",
    )(
        candidate_id="gex-candidate-1",
        pending_ref_id="gene-expression-annotation-pef-1",
        updates=[
            {
                "field_path": "reference.reference_id",
                "string_value": "PMID:39550471",
                "evidence_record_ids": None,
            },
            {
                "field_path": "relation.name",
                "string_value": "is_not_expressed_in",
                "evidence_record_ids": None,
            },
        ],
    )

    assert result.status == "ok"
    candidate = workspace.candidates["gex-candidate-1"]
    assert candidate.staged_fields["single_reference"]["reference_id"] == "PMID:39550471"
    assert candidate.staged_fields["relation"]["name"] == "is_not_expressed_in"
    assert candidate.resolver_selection_refs == ["call_relation", "call_relation_part_of"]


def test_finalize_returns_compact_builder_summary(active_builder_context):
    workspace, ledger, events = active_builder_context
    _stage_materializable_observation(ledger)

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])

    assert result.status == "ok"
    assert workspace.finalization is not None
    finalization = result.data["builder_finalization"]
    assert finalization["status"] == "finalized"
    assert finalization["candidate_ids"] == ["gex-candidate-1"]
    assert finalization["source_candidate_ids"] == ["gex-candidate-1"]
    assert finalization["evidence_record_ids"] == ["evidence-67598e5688f123c8"]
    assert finalization["resolver_selection_count"] == 4
    assert "GeneExpressionEnvelope" not in result.data
    payload = workspace.finalization.payload
    assert payload["curatable_objects"][0]["object_type"] == "GeneExpressionAnnotation"
    annotation = payload["curatable_objects"][0]
    assert annotation["evidence_record_ids"] == ["evidence-67598e5688f123c8"]
    assert payload["metadata"]["evidence_records"][0]["evidence_record_id"] == (
        "evidence-67598e5688f123c8"
    )
    helper_selections = payload["metadata"]["provenance"]["helper_selections"]
    assert {selection["resolver_call_id"] for selection in helper_selections} == {
        "call_relation",
        "call_assay",
        "call_stage",
        "call_anatomy",
    }
    assert annotation["payload"]["relation"]["name"] == "is_expressed_in"
    assert annotation["payload"]["data_provider"]["abbreviation"] == "WB"
    assert annotation["payload"]["date_created"] == workspace.created_at
    assert annotation["payload"]["expression_experiment"]["unique_id"].startswith(
        "gene-expression-experiment-"
    )
    assert any(event["event_type"] == "gene_expression_builder.finalize_completed" for event in events)
    completed_event = next(
        event
        for event in events
        if event["event_type"] == "gene_expression_materializer.completed"
    )
    assert completed_event["output_summary"]["curatable_objects"] == payload["curatable_objects"]
    assert completed_event["output_summary"]["materialized_envelope"] == payload


def test_finalize_preserves_multi_observation_source_candidate_identity(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    first = workspace.get_candidate("gex-candidate-1")
    second_payload = dict(first.staged_fields)
    second_payload["pending_ref_id"] = "gene-expression-annotation-pef-2"
    workspace.upsert_candidate(
        candidate_id="gex-candidate-2",
        staged_fields=second_payload,
        pending_ref_ids=["gene-expression-annotation-pef-2"],
        evidence_record_ids=first.evidence_record_ids,
        resolver_selection_refs=first.resolver_selection_refs,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1", "gex-candidate-2"])

    assert result.status == "ok"
    finalization = workspace.finalization
    assert finalization is not None
    assert finalization.candidate_ids == ("gene-expression-envelope-818f1fdf2501",)
    assert finalization.source_candidate_ids == ("gex-candidate-1", "gex-candidate-2")
    assert finalization.summary()["source_candidate_ids"] == [
        "gex-candidate-1",
        "gex-candidate-2",
    ]
    assert finalization.payload["metadata"]["provenance"]["source_candidate_ids"] == [
        "gex-candidate-1",
        "gex-candidate-2",
    ]
    assert len(finalization.payload["curatable_objects"]) == 2


def test_duplicate_finalize_conflicts_when_source_candidates_change(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    first_candidate = workspace.get_candidate("gex-candidate-1")
    second_payload = dict(first_candidate.staged_fields)
    second_payload["pending_ref_id"] = "gene-expression-annotation-pef-2"
    workspace.upsert_candidate(
        candidate_id="gex-candidate-2",
        staged_fields=second_payload,
        pending_ref_ids=["gene-expression-annotation-pef-2"],
        evidence_record_ids=first_candidate.evidence_record_ids,
        resolver_selection_refs=first_candidate.resolver_selection_refs,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    first = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])
    duplicate = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])
    conflict = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-2"])

    assert first.status == "ok"
    assert duplicate.status == "ok"
    assert conflict.status == "error"
    assert conflict.failure_classification == "validation_failed"
    assert conflict.data["validation_issues"][0]["reason"] == "finalization_conflict"
    assert conflict.data["validation_issues"][0]["existing_candidate_ids"] == [
        "gex-candidate-1"
    ]
    assert conflict.data["validation_issues"][0]["requested_candidate_ids"] == [
        "gex-candidate-2"
    ]


def test_finalize_rejects_duplicate_candidate_ids_before_materialization(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1", "gex-candidate-1"])

    assert result.status == "error"
    assert workspace.finalization is None
    assert result.data["validation_issues"] == [
        {
            "field_path": "candidate_ids",
            "reason": "duplicate_candidate_id",
            "message": "candidate_ids must not contain duplicate candidate IDs.",
            "duplicate_candidate_ids": ["gex-candidate-1"],
        }
    ]


def test_finalize_rejects_missing_evidence_records(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=workspace.get_candidate("gex-candidate-1").staged_fields,
        pending_ref_ids=["gene-expression-annotation-pef-1"],
        evidence_record_ids=["{}"],
        resolver_selection_refs=workspace.get_candidate("gex-candidate-1").resolver_selection_refs,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])

    assert result.status == "error"
    reasons = {issue["reason"] for issue in result.data["validation_issues"]}
    assert "unknown_evidence_record_id" in reasons


def test_finalize_copies_resolver_provenance_from_ledger(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    candidate = workspace.get_candidate("gex-candidate-1")
    staged_fields = dict(candidate.staged_fields)
    staged_fields["metadata"] = {"helper_selections": [{"misplaced": True}]}
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=staged_fields,
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=candidate.evidence_record_ids,
        resolver_selection_refs=candidate.resolver_selection_refs,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])

    assert result.status == "ok"
    helper_selections = workspace.finalization.payload["metadata"]["provenance"][
        "helper_selections"
    ]
    assert all(selection.get("source_tool") == "resolve_domain_field_term" for selection in helper_selections)
    assert {selection["resolver_call_id"] for selection in helper_selections} == {
        "call_relation",
        "call_assay",
        "call_stage",
        "call_anatomy",
    }


def test_finalize_rejects_null_relation_name(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    candidate = workspace.get_candidate("gex-candidate-1")
    staged_fields = dict(candidate.staged_fields)
    staged_fields["relation"] = {"name": None}
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=staged_fields,
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=candidate.evidence_record_ids,
        resolver_selection_refs=[
            ref for ref in candidate.resolver_selection_refs if ref != "call_relation"
        ],
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])

    assert result.status == "error"
    assert "relation.name must be selected explicitly" in str(
        result.data["validation_issues"]
    )


def test_finalize_rejects_placeholder_pmid(active_builder_context):
    workspace, ledger, events = active_builder_context
    _stage_materializable_observation(ledger)
    candidate = workspace.get_candidate("gex-candidate-1")
    staged_fields = dict(candidate.staged_fields)
    staged_fields["single_reference"] = {
        **staged_fields["single_reference"],
        "reference_id": "PMID:12345678",
    }
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=staged_fields,
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=candidate.evidence_record_ids,
        resolver_selection_refs=candidate.resolver_selection_refs,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])

    assert result.status == "error"
    assert {issue["reason"] for issue in result.data["validation_issues"]} == {
        "placeholder_reference"
    }
    assert any(
        event["event_type"] == "gene_expression_materializer.placeholder_reference_rejected"
        for event in events
    )
