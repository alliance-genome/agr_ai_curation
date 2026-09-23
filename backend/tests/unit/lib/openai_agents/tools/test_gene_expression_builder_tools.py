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
    source_phrase: str = "expressed in",
) -> dict[str, Any]:
    """A recorded resolve call; source_phrase is the wording it searched (the staged mention)."""

    selected_name = selected_name or selected_value
    instruction_value = selected_value if instruction_value is None else instruction_value
    return {
        "status": "resolved",
        "data": {
            "domain_pack_id": agr_curation.GENE_EXPRESSION_DOMAIN_PACK_ID,
            "object_type": agr_curation.GENE_EXPRESSION_OBJECT_TYPE,
            "field_path": field_path,
            "source_phrase": source_phrase,
            "payload_field_instructions": {
                "set": [{"field_path": field_path, "value": instruction_value}]
            },
            "helper_selection": {
                "field_path": field_path,
                "source_tool": "resolve_domain_field_term",
                "authority": "selector_evidence",
                "lookup_status": "success",
                "source_phrase": source_phrase,
                "term_source": term_source
                or {"kind": "controlled_vocabulary", "vocabulary": "Expression Relation"},
                "selected_value": selected_value,
                "selected_name": selected_name,
                **({"selected_curie": selected_curie} if selected_curie else {}),
            },
        },
    }


_ALLIANCE_PROVIDERS = {"WB", "ZFIN", "MGI", "FB", "RGD", "SGD", "XB"}


def _provider_lookup(*, method: str, abbreviation: str | None = None, **_kwargs: Any):
    """The exact provider-list lookup the builder runs for the staged data provider."""

    assert method == "get_data_provider"
    normalized = (abbreviation or "").strip().upper()
    matches = [{"abbreviation": normalized}] if normalized in _ALLIANCE_PROVIDERS else []
    return agr_curation.AgrQueryResult(
        status="ok",
        data={"matches": matches, "candidates": matches},
        count=len(matches),
    )


_SUBJECT = {"mention": "pef-1"}
_REFERENCE = {"mention": "PMID:39550471"}


def _relation_field(selected_value: str | None = "is_expressed_in") -> dict[str, Any]:
    return {"field_path": "relation.name", "mention": "expressed in", "selected_value": selected_value}


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
    monkeypatch.setattr(agr_curation, "_AGR_QUERY_CALLABLE", _provider_lookup)
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
            "pending_ref_id": "gene-expression-annotation-pef-1",
            "field_path": "where_expressed_statement",
            "field_paths": [
                "where_expressed_statement",
                "expression_pattern.where_expressed.anatomical_structure",
            ],
            "envelope_target": {
                "pending_ref_id": "gene-expression-annotation-pef-1",
                "field_path": "where_expressed_statement",
            },
            "envelope_targets": [
                {
                    "pending_ref_id": "gene-expression-annotation-pef-1",
                    "field_path": "where_expressed_statement",
                },
                {
                    "pending_ref_id": "gene-expression-annotation-pef-1",
                    "field_path": "expression_pattern.where_expressed.anatomical_structure",
                },
                {
                    "pending_ref_id": "gene-expression-annotation-pef-2",
                    "field_path": "where_expressed_statement",
                },
                {
                    "pending_ref_id": "gene-expression-annotation-pef-2",
                    "field_path": "expression_pattern.where_expressed.anatomical_structure",
                },
            ],
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
        rationale="Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[_relation_field()],
    )


def _stage_materializable_observation(
    ledger: resolver_call_ledger.ResolverCallLedger,
    *extra_controlled_fields: dict[str, Any],
    anatomy: dict[str, Any] | None = None,
):
    resolver_outputs = {
        "call_relation": _resolved_output(),
        "call_assay": _resolved_output(
            field_path="expression_experiment.expression_assay_used",
            selected_value="MMO:0000655",
            selected_name="GFP reporter assay",
            selected_curie="MMO:0000655",
            instruction_value={"curie": "MMO:0000655", "name": "GFP reporter assay"},
            term_source={"kind": "ontology", "ontology_family": "assay"},
            source_phrase="GFP reporter",
        ),
        "call_stage": _resolved_output(
            field_path="expression_pattern.when_expressed.developmental_stage_start",
            selected_value="WBls:0000024",
            selected_name="L2 larva",
            selected_curie="WBls:0000024",
            instruction_value={"curie": "WBls:0000024", "name": "L2 larva"},
            term_source={"kind": "ontology", "ontology_family": "life_stage"},
            source_phrase="L2 larvae",
        ),
        "call_anatomy": _resolved_output(
            field_path="expression_pattern.where_expressed.anatomical_structure",
            selected_value="WBbt:0001234",
            selected_name="cilium",
            selected_curie="WBbt:0001234",
            instruction_value={"curie": "WBbt:0001234", "name": "cilium"},
            term_source={"kind": "ontology", "ontology_family": "anatomy"},
            source_phrase="cilia",
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
        rationale="Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[
            _relation_field(),
            {
                "field_path": "expression_experiment.expression_assay_used",
                "mention": "GFP reporter",
                "selected_value": "MMO:0000655",
            },
            {
                "field_path": "expression_pattern.when_expressed.developmental_stage_start",
                "mention": "L2 larvae",
                "selected_value": "WBls:0000024",
            },
            anatomy
            or {
                "field_path": "expression_pattern.where_expressed.anatomical_structure",
                "mention": "cilia",
                "selected_value": "WBbt:0001234",
            },
            *extra_controlled_fields,
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


def test_resolver_call_ledger_retains_structured_authoritative_tool_outputs(
    active_builder_context,
):
    _workspace, ledger, _events = active_builder_context
    ledger.record_tool_output(
        tool_call_id="call-quickgo",
        tool_name="quickgo_api_call",
        output={"results": [{"id": "GO:0005515", "name": "protein binding"}]},
    )

    entry = ledger.find_tool_output_containing(
        tool_names={"quickgo_api_call"},
        value={"id": "GO:0005515", "name": "protein binding"},
    )

    assert entry is not None
    assert entry.tool_call_id == "call-quickgo"
    assert ledger.get_tool_output("call-quickgo").contains("GO:0005515")


def test_resolver_call_ledger_limits_missing_id_rejections_to_resolver_outputs(
    active_builder_context,
):
    _workspace, ledger, events = active_builder_context
    generic_output = {"content": "full document text must not enter rejection traces"}

    assert (
        ledger.record_tool_output(
            tool_call_id=None,
            tool_name="read_section",
            output=generic_output,
        )
        is None
    )
    assert events == []

    assert (
        ledger.record_tool_output(
            tool_call_id=None,
            tool_name="resolve_domain_field_term",
            output=_resolved_output(),
        )
        is None
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "resolver_call_ledger.rejected"
    assert events[0]["validation"]["reason"] == "missing_tool_call_id"


def test_stage_gene_expression_observation_copies_resolver_provenance(active_builder_context):
    workspace, ledger, events = active_builder_context

    result = _stage_valid_observation(ledger)

    assert result.status == "ok"
    candidate = workspace.candidates["gex-candidate-1"]
    assert candidate.evidence_record_ids == ["evidence-67598e5688f123c8"]
    assert candidate.resolver_selection_refs == ["call_relation"]
    assert candidate.staged_fields["relation"] == {
        "name": "is_expressed_in",
        "vocabulary": None,
        "id": None,
        "mention": "expressed in",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }
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
        rationale="Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[_relation_field()],
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
        rationale="Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[_relation_field()],
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
        rationale="Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        data_provider="WB",
        subject=_SUBJECT,
        reference={"mention": "PMID:..."},
        controlled_fields=[_relation_field()],
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
                "mention": None,
                "evidence_record_ids": None,
            },
            {
                "field_path": "relation.name",
                "string_value": None,
                "mention": None,
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
        output=_resolved_output(selected_value="is_not_expressed_in", source_phrase="not expressed in"),
    )

    result = _tool_fn(
        agr_curation.patch_gene_expression_observation,
        "patch_gene_expression_observation",
    )(
        candidate_id="gex-candidate-1",
        pending_ref_id="gene-expression-annotation-pef-1",
        updates=[
            {
                "field_path": "reference",
                "string_value": "PMID 39550472",
                "mention": None,
                "evidence_record_ids": None,
            },
            {
                "field_path": "relation.name",
                "string_value": "is_not_expressed_in",
                "mention": "not expressed in",
                "evidence_record_ids": None,
            },
        ],
    )

    assert result.status == "ok"
    candidate = workspace.candidates["gex-candidate-1"]
    reference = candidate.staged_fields["single_reference"]
    assert reference["mention"] == "PMID 39550472"
    assert reference["pmid"] == "PMID:39550472"
    assert reference["reference_id"] is None
    assert reference["resolution_state"] == "unresolved"
    assert candidate.staged_fields["relation"]["name"] == "is_not_expressed_in"
    assert candidate.staged_fields["relation"]["mention"] == "not expressed in"
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
    # ALL-1278: the model-facing receipt carries counts; the full id lists stay
    # on the workspace finalization.
    assert finalization["source_candidate_count"] == 1
    assert finalization["evidence_record_count"] == 1
    assert "source_candidate_ids" not in finalization
    assert "evidence_record_ids" not in finalization
    assert workspace.finalization.source_candidate_ids == ("gex-candidate-1",)
    assert workspace.finalization.evidence_record_ids == ("evidence-67598e5688f123c8",)
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


def test_finalize_rejects_relation_without_contract_state(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    candidate = workspace.get_candidate("gex-candidate-1")
    staged_fields = dict(candidate.staged_fields)
    staged_fields["relation"] = {"name": "is_expressed_in"}
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
    assert "relation: resolution_state must be one of" in str(
        result.data["validation_issues"]
    )


def test_finalize_rejects_placeholder_pmid(active_builder_context):
    workspace, ledger, events = active_builder_context
    _stage_materializable_observation(ledger)
    candidate = workspace.get_candidate("gex-candidate-1")
    staged_fields = dict(candidate.staged_fields)
    staged_fields["single_reference"] = {
        **staged_fields["single_reference"],
        "mention": "PMID:12345678",
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


def test_stage_schema_requires_rationale_with_shared_description():
    from agr_ai_curation_alliance.tools.builder_rationale import RATIONALE_ARG_DESCRIPTION

    stage_schema = agr_curation.stage_gene_expression_observation.params_json_schema
    assert "rationale" in stage_schema["required"]
    assert stage_schema["properties"]["rationale"]["type"] == "string"
    assert stage_schema["properties"]["rationale"]["description"] == RATIONALE_ARG_DESCRIPTION

    patch_schema = agr_curation.patch_gene_expression_observation.params_json_schema
    update_schema = _defs_schema(patch_schema, "GeneExpressionPatchUpdateInput")
    assert "rationale" in update_schema["properties"]["field_path"]["enum"]
    assert "A `rationale` update must be non-empty; it cannot be cleared." in patch_schema["properties"]["updates"]["description"]


@pytest.mark.parametrize(
    ("rationale", "message"),
    [("   ", "rationale must be non-empty"), ("", "rationale must be non-empty")],
)
def test_stage_rejects_blank_rationale(active_builder_context, rationale, message):
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
        evidence_record_ids=["evidence-67598e5688f123c8"],
        where_expressed_statement="PEF-1::GFP expression in the cilium",
        rationale=rationale,
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[_relation_field()],
    )

    assert result.status == "error"
    issues = result.data["validation_issues"]
    assert [issue["field_path"] for issue in issues] == ["rationale"]
    assert message in issues[0]["message"]


def test_stage_stores_stripped_rationale(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_valid_observation(ledger)

    assert workspace.candidates["gex-candidate-1"].staged_fields["rationale"] == (
        "Anti-GFP staining localizes the reporter to the cilium, not the cell body."
    )


def _patch_rationale(value: Any):
    return _tool_fn(
        agr_curation.patch_gene_expression_observation,
        "patch_gene_expression_observation",
    )(
        candidate_id="gex-candidate-1",
        pending_ref_id="gene-expression-annotation-pef-1",
        updates=[
            {
                "field_path": "rationale",
                "string_value": value,
                "mention": None,
                "evidence_record_ids": None,
            }
        ],
    )


def test_patch_rewrites_rationale(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_valid_observation(ledger)

    result = _patch_rationale("  Reporter signal is ciliary in every imaged neuron.  ")

    assert result.status == "ok"
    assert workspace.candidates["gex-candidate-1"].staged_fields["rationale"] == (
        "Reporter signal is ciliary in every imaged neuron."
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_patch_cannot_clear_rationale(active_builder_context, value):
    workspace, ledger, _events = active_builder_context
    _stage_valid_observation(ledger)

    result = _patch_rationale(value)

    assert result.status == "error"
    assert [issue["reason"] for issue in result.data["validation_issues"]] == ["invalid_rationale"]
    assert workspace.candidates["gex-candidate-1"].staged_fields["rationale"] == (
        "Anti-GFP staining localizes the reporter to the cilium, not the cell body."
    )


def test_finalize_carries_rationale_into_the_annotation_payload(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)

    result = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])

    assert result.status == "ok"
    annotation = workspace.finalization.payload["curatable_objects"][0]
    assert annotation["payload"]["rationale"] == (
        "Anti-GFP staining localizes the reporter to the cilium, not the cell body."
    )


def test_finalize_rejects_new_candidate_without_rationale(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)
    candidate = workspace.get_candidate("gex-candidate-1")
    staged_fields = dict(candidate.staged_fields)
    staged_fields.pop("rationale")
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
        "missing_rationale"
    }
    assert result.data["validation_issues"][0]["message"].endswith(
        "patch the candidate with a rationale saying why you selected it."
    )


# ---------------------------------------------------------------------------------------
# ALL-1283: every staged value keeps the paper's wording beside its validated identity.
# ---------------------------------------------------------------------------------------

_RESIDUAL_BODY = "structures associated with the residual body"


def test_stage_keeps_unmatched_anatomy_unresolved_with_its_paper_wording(active_builder_context):
    """Daniela's case: an anatomy term with no match is staged, never rejected or dropped."""

    workspace, ledger, _events = active_builder_context

    result = _stage_materializable_observation(
        ledger,
        anatomy={
            "field_path": "expression_pattern.where_expressed.anatomical_structure",
            "mention": _RESIDUAL_BODY,
            "selected_value": None,
        },
    )

    assert result.status == "ok"
    staged = workspace.candidates["gex-candidate-1"].staged_fields
    assert staged["expression_pattern"]["where_expressed"]["anatomical_structure"] == {
        "curie": None,
        "name": None,
        "mention": _RESIDUAL_BODY,
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    finalized = _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=["gex-candidate-1"])
    assert finalized.status == "ok"
    payload = workspace.finalization.payload["curatable_objects"][0]["payload"]
    anatomy = payload["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert anatomy["mention"] == _RESIDUAL_BODY
    assert anatomy["resolution_state"] == "unresolved"


def test_stage_writes_resolver_identity_and_keeps_the_mention_apart(active_builder_context):
    workspace, ledger, _events = active_builder_context

    _stage_materializable_observation(ledger)

    staged = workspace.candidates["gex-candidate-1"].staged_fields
    anatomy = staged["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert anatomy == {
        "curie": "WBbt:0001234",
        "name": "cilium",
        "mention": "cilia",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }
    stage = staged["expression_pattern"]["when_expressed"]["developmental_stage_start"]
    assert (stage["curie"], stage["name"], stage["mention"]) == ("WBls:0000024", "L2 larva", "L2 larvae")
    # The paper's stage wording is the stage term's mention; no separate stage-name field.
    assert "when_expressed_stage_name" not in staged


def test_stage_writes_subject_and_reference_as_paper_wording_only(active_builder_context):
    workspace, ledger, _events = active_builder_context

    _stage_valid_observation(ledger)

    staged = workspace.candidates["gex-candidate-1"].staged_fields
    assert staged["expression_annotation_subject"] == {
        "primary_external_id": None,
        "gene_symbol": None,
        "mention": "pef-1",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    reference = staged["single_reference"]
    assert (reference["mention"], reference["pmid"]) == ("PMID:39550471", "PMID:39550471")
    assert (reference["reference_id"], reference["curie"], reference["title"]) == (None, None, None)
    assert reference["resolution_state"] == "unresolved"


@pytest.mark.parametrize(
    ("mention", "lookup_inputs"),
    [
        ("PMID 39550471", {"pmid": "PMID:39550471"}),
        ("doi:10.1242/dev.201234", {"doi": "10.1242/dev.201234"}),
        ("WB:WBPaper00065432", {}),
    ],
)
def test_reference_mention_becomes_lookup_input_never_identity(mention, lookup_inputs):
    reference = agr_curation._gene_expression_reference_value(mention)

    assert reference["mention"] == mention
    assert {key: reference[key] for key in ("pmid", "doi") if key in reference} == lookup_inputs
    assert (reference["reference_id"], reference["curie"], reference["title"]) == (None, None, None)


def test_stage_resolves_data_provider_only_by_exact_provider_match(active_builder_context):
    workspace, ledger, _events = active_builder_context
    ledger.record_tool_output(
        tool_call_id="call_relation",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(),
    )
    stage = _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )

    for pending_ref_id, provider in (
        ("gene-expression-annotation-pef-1", "wb"),
        ("gene-expression-annotation-pef-2", "C. elegans"),
    ):
        result = stage(
            pending_ref_id=pending_ref_id,
            evidence_record_ids=["evidence-67598e5688f123c8"],
            where_expressed_statement="expression in cilium",
            rationale="Anti-GFP staining localizes the reporter to the cilium.",
            data_provider=provider,
            subject=_SUBJECT,
            reference=_REFERENCE,
            controlled_fields=[_relation_field()],
        )
        assert result.status == "ok"

    matched = workspace.candidates["gex-candidate-1"].staged_fields["data_provider"]
    assert (matched["abbreviation"], matched["mention"], matched["resolution_state"]) == (
        "WB",
        "wb",
        "resolved",
    )
    unmatched = workspace.candidates["gex-candidate-2"].staged_fields["data_provider"]
    assert unmatched == {
        "abbreviation": None,
        "mention": "C. elegans",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }


def test_stage_keeps_every_slim_term_as_its_own_value(active_builder_context):
    """Stage slims are Stage Uberon Slim Terms vocabulary terms, each its own value."""

    workspace, ledger, _events = active_builder_context
    output = _resolved_output(
        field_path="expression_pattern.when_expressed.stage_uberon_slim_terms",
        selected_value="post embryonic, pre-adult",
        term_source={"kind": "controlled_vocabulary", "vocabulary": "Stage Uberon Slim Terms"},
        source_phrase="post embryonic, pre-adult",
    )
    output["data"]["helper_selection"].update(
        {"vocabulary": "Stage Uberon Slim Terms", "selected_internal_id": 200008800}
    )
    ledger.record_tool_output(
        tool_call_id="call_slim",
        tool_name="resolve_domain_field_term",
        output=output,
    )

    result = _stage_materializable_observation(
        ledger,
        {
            "field_path": "expression_pattern.when_expressed.stage_uberon_slim_terms",
            "mention": "post embryonic, pre-adult",
            "selected_value": "post embryonic, pre-adult",
        },
        {
            "field_path": "expression_pattern.when_expressed.stage_uberon_slim_terms",
            "mention": "late larval",
            "selected_value": None,
        },
    )

    assert result.status == "ok"
    slims = workspace.candidates["gex-candidate-1"].staged_fields["expression_pattern"][
        "when_expressed"
    ]["stage_uberon_slim_terms"]
    assert [
        (term["mention"], term["name"], term["vocabulary"], term["id"], term["resolution_state"])
        for term in slims
    ] == [
        ("post embryonic, pre-adult", "post embryonic, pre-adult", "Stage Uberon Slim Terms", 200008800, "resolved"),
        ("late larval", None, None, None, "unresolved"),
    ]


def test_stage_rejects_a_single_valued_field_staged_twice(active_builder_context):
    _workspace, ledger, _events = active_builder_context

    result = _stage_materializable_observation(
        ledger,
        {
            "field_path": "expression_pattern.where_expressed.anatomical_structure",
            "mention": "hypodermis",
            "selected_value": None,
        },
    )

    assert result.status == "error"
    assert [issue["reason"] for issue in result.data["validation_issues"]] == [
        "duplicate_controlled_field"
    ]


def test_stage_requires_paper_wording_for_every_controlled_field(active_builder_context):
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
        evidence_record_ids=["evidence-67598e5688f123c8"],
        where_expressed_statement="expression in cilium",
        rationale="Anti-GFP staining localizes the reporter to the cilium.",
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[{**_relation_field(), "mention": "  "}],
    )

    assert result.status == "error"
    assert result.data["validation_issues"][0]["field_path"].endswith("mention")


def test_patch_restages_a_controlled_field_unresolved_with_its_wording(active_builder_context):
    workspace, ledger, _events = active_builder_context
    _stage_materializable_observation(ledger)

    result = _tool_fn(
        agr_curation.patch_gene_expression_observation,
        "patch_gene_expression_observation",
    )(
        candidate_id="gex-candidate-1",
        pending_ref_id="gene-expression-annotation-pef-1",
        updates=[
            {
                "field_path": "expression_pattern.where_expressed.anatomical_structure",
                "string_value": None,
                "mention": _RESIDUAL_BODY,
                "evidence_record_ids": None,
            }
        ],
    )

    assert result.status == "ok"
    anatomy = workspace.candidates["gex-candidate-1"].staged_fields["expression_pattern"][
        "where_expressed"
    ]["anatomical_structure"]
    assert (anatomy["mention"], anatomy["curie"], anatomy["resolution_state"]) == (
        _RESIDUAL_BODY,
        None,
        "unresolved",
    )


@pytest.mark.parametrize(
    ("mention", "accepted"),
    [("  Expressed   IN ", True), ("was detected in", False)],
)
def test_stage_pairs_a_resolution_only_with_the_wording_it_was_resolved_from(
    active_builder_context, mention, accepted
):
    """A resolved value only counts for the paper wording its resolve call searched."""

    workspace, ledger, _events = active_builder_context
    ledger.record_tool_output(
        tool_call_id="call_relation",
        tool_name="resolve_domain_field_term",
        output=_resolved_output(source_phrase="expressed in"),
    )

    result = _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(
        pending_ref_id="gene-expression-annotation-pef-1",
        evidence_record_ids=["evidence-67598e5688f123c8"],
        where_expressed_statement="expression in cilium",
        rationale="Anti-GFP staining localizes the reporter to the cilium.",
        data_provider="WB",
        subject=_SUBJECT,
        reference=_REFERENCE,
        controlled_fields=[{**_relation_field(), "mention": mention}],
    )

    if accepted:
        assert result.status == "ok"
        relation = workspace.candidates["gex-candidate-1"].staged_fields["relation"]
        assert (relation["name"], relation["mention"]) == ("is_expressed_in", mention.strip())
    else:
        assert result.status == "error"
        [issue] = result.data["validation_issues"]
        assert issue["reason"] == "unresolved_selected_value"
        assert "selected_value null" in issue["message"]


@pytest.mark.parametrize("status", ["unresolved", "ambiguous", "blocked"])
def test_resolver_instructions_tell_the_model_to_stage_unmatched_wording(status):
    """Regression (ALL-1283): no "preserve it in unresolved metadata" instruction remains."""

    lines = agr_curation._resolver_instruction(
        resolution_status=status,
        field_path="expression_pattern.where_expressed.anatomical_structure",
        source_phrase=_RESIDUAL_BODY,
        resolver={},
    )
    text = " ".join(lines)
    assert "metadata" not in text
    # Shared with every builder: no builder-specific parameter names.
    assert "selected_value" not in text
    assert f"{_RESIDUAL_BODY!r} as the paper's wording (its mention), leaving the identifier empty" in text
    assert "the validator will check it" in text


def test_resolver_selection_reads_only_the_selected_terms_own_keys():
    """Regression (ALL-1283): no name/curie/value fallback chains in resolver output."""

    ontology_source = {"kind": "ontology", "ontology_family": "anatomy"}
    no_curie = {"name": "cilium", "term_name": "cilium", "value": "cilium"}
    selection = agr_curation._resolver_helper_selection(
        field_path="expression_pattern.where_expressed.anatomical_structure",
        source_phrase="cilia",
        candidate=no_curie,
        term_source=ontology_source,
        policy={},
        evidence_context={},
        resolved_at="2026-09-23T00:00:00Z",
    )
    assert "selected_value" not in selection
    assert selection["selected_name"] == "cilium"

    vocabulary = {"kind": "controlled_vocabulary", "vocabulary": "Expression Relation"}
    assert agr_curation._payload_field_instructions(
        field_path="relation.name",
        candidate={"name": "expressed in", "value": "expressed in"},
        term_source=vocabulary,
    ) == {"set": [{"field_path": "relation.name", "value": None}]}
    # A stage-name path no longer takes the CURIE as its name.
    assert agr_curation._payload_field_instructions(
        field_path="when_expressed_stage_name",
        candidate={"curie": "WBls:0000024"},
        term_source=ontology_source,
    ) == {
        "set": [
            {"field_path": "when_expressed_stage_name.curie", "value": "WBls:0000024"},
            {"field_path": "when_expressed_stage_name.name", "value": None},
        ]
    }


# ---------------------------------------------------------------------------------------
# Shared builder list/search helpers (_builder_candidate_list / _search_builder_candidates).
# These back every domain's list_staged_*/find_staged_* tools, so they are exercised once here
# against a plain workspace built directly through upsert_candidate.
# ---------------------------------------------------------------------------------------


def _seed_search_workspace() -> builder.ExtractionBuilderWorkspace:
    workspace = _workspace()
    workspace.upsert_candidate(
        candidate_id="cand-alpha",
        staged_fields={"where_expressed_statement": "GFP detected in the cilium"},
        pending_ref_ids=["pending-alpha"],
        evidence_record_ids=["evidence-alpha"],
    )
    workspace.upsert_candidate(
        candidate_id="cand-beta",
        staged_fields={"where_expressed_statement": "mCherry in the gut"},
        pending_ref_ids=["pending-beta"],
        evidence_record_ids=["evidence-beta", "evidence-shared"],
    )
    third = workspace.upsert_candidate(
        candidate_id="cand-gamma",
        staged_fields={"where_expressed_statement": "GFP in the pharynx"},
        pending_ref_ids=["pending-gamma"],
        evidence_record_ids=["evidence-shared"],
    )
    third.validation_errors = [{"field_path": "relation.name", "reason": "unresolved"}]
    return workspace


def test_builder_candidate_list_pages_with_offset_and_next_offset():
    workspace = _seed_search_workspace()

    first = agr_curation._builder_candidate_list(workspace, limit=2, offset=0)
    assert first["returned_candidate_count"] == 2
    assert first["total_listed_candidate_count"] == 3
    assert first["offset"] == 0
    assert first["next_offset"] == 2
    assert first["truncated"] is True
    assert [c["candidate_id"] for c in first["candidates"]] == ["cand-alpha", "cand-beta"]

    second = agr_curation._builder_candidate_list(workspace, limit=2, offset=2)
    assert second["returned_candidate_count"] == 1
    assert second["offset"] == 2
    assert second["next_offset"] is None
    assert second["truncated"] is False
    assert [c["candidate_id"] for c in second["candidates"]] == ["cand-gamma"]


def test_builder_candidate_list_redacts_staged_field_values():
    workspace = _seed_search_workspace()

    result = agr_curation._builder_candidate_list(workspace, limit=10, offset=0)

    first_candidate = result["candidates"][0]
    assert first_candidate["staged_fields"] == {
        "keys": ["where_expressed_statement"],
        "field_count": 1,
    }


def test_search_builder_candidates_filters_by_field_value_contains():
    workspace = _seed_search_workspace()

    result = agr_curation._search_builder_candidates(
        workspace, field_value_contains="gfp"
    )

    assert result["matched_candidate_count"] == 2
    assert {c["candidate_id"] for c in result["candidates"]} == {"cand-alpha", "cand-gamma"}
    # RETURN must be redacted: raw staged-field text is never echoed back.
    assert result["candidates"][0]["staged_fields"] == {
        "keys": ["where_expressed_statement"],
        "field_count": 1,
    }


def test_search_builder_candidates_filters_by_pending_ref_and_evidence_and_candidate_id():
    workspace = _seed_search_workspace()

    by_pending = agr_curation._search_builder_candidates(
        workspace, pending_ref_id="pending-beta"
    )
    assert [c["candidate_id"] for c in by_pending["candidates"]] == ["cand-beta"]

    by_evidence = agr_curation._search_builder_candidates(
        workspace, evidence_record_id="evidence-shared"
    )
    assert {c["candidate_id"] for c in by_evidence["candidates"]} == {
        "cand-beta",
        "cand-gamma",
    }

    by_candidate_id = agr_curation._search_builder_candidates(
        workspace, candidate_id="cand-alpha"
    )
    assert [c["candidate_id"] for c in by_candidate_id["candidates"]] == ["cand-alpha"]


def test_search_builder_candidates_filters_by_has_validation_errors():
    workspace = _seed_search_workspace()

    with_errors = agr_curation._search_builder_candidates(
        workspace, has_validation_errors=True
    )
    assert [c["candidate_id"] for c in with_errors["candidates"]] == ["cand-gamma"]

    without_errors = agr_curation._search_builder_candidates(
        workspace, has_validation_errors=False
    )
    assert {c["candidate_id"] for c in without_errors["candidates"]} == {
        "cand-alpha",
        "cand-beta",
    }


def test_search_builder_candidates_combines_filters_with_and_semantics():
    workspace = _seed_search_workspace()

    result = agr_curation._search_builder_candidates(
        workspace,
        field_value_contains="gfp",
        has_validation_errors=True,
    )

    assert [c["candidate_id"] for c in result["candidates"]] == ["cand-gamma"]


def test_search_builder_candidates_respects_include_discarded():
    workspace = _seed_search_workspace()
    workspace.discard_candidate("cand-alpha", reason="duplicate")

    default_search = agr_curation._search_builder_candidates(
        workspace, field_value_contains="gfp"
    )
    assert [c["candidate_id"] for c in default_search["candidates"]] == ["cand-gamma"]

    with_discarded = agr_curation._search_builder_candidates(
        workspace, field_value_contains="gfp", include_discarded=True
    )
    assert {c["candidate_id"] for c in with_discarded["candidates"]} == {
        "cand-alpha",
        "cand-gamma",
    }


def test_search_builder_candidates_pages_matches():
    workspace = _seed_search_workspace()

    first = agr_curation._search_builder_candidates(workspace, limit=2, offset=0)
    assert first["matched_candidate_count"] == 3
    assert first["returned_candidate_count"] == 2
    assert first["offset"] == 0
    assert first["next_offset"] == 2
    assert first["truncated"] is True

    second = agr_curation._search_builder_candidates(workspace, limit=2, offset=2)
    assert second["matched_candidate_count"] == 3
    assert second["returned_candidate_count"] == 1
    assert second["next_offset"] is None
    assert second["truncated"] is False
