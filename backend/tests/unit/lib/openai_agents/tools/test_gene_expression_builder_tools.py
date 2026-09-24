"""Alliance gene-expression builder tool tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from agr_ai_curation_alliance.tools import agr_curation
from src.lib.openai_agents import extraction_builder_workspace as builder
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


def _no_database_lookup(**kwargs: Any):
    raise AssertionError(f"extraction must never search a database; called with {kwargs}")


_SUBJECT = {"mention": "pef-1", "proposed_primary_external_id": None}
_REFERENCE = {"mention": "PMID:39550471"}
_NOT_VALIDATED = {
    "resolution_state": "unresolved",
    "lookup_outcome": "not_validated",
    "validator_explanation": "Not validated yet.",
}


def _relation_field(mention: str = "is_expressed_in") -> dict[str, Any]:
    return {"field_path": "relation.name", "mention": mention, "proposed_curie": None}


def _controlled_field(field_path: str, mention: str, proposed_curie: str | None = None) -> dict[str, Any]:
    return {"field_path": field_path, "mention": mention, "proposed_curie": proposed_curie}


@pytest.fixture
def active_builder_context(monkeypatch):
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agr_curation, "write_extraction_trace_event", lambda **event: events.append(event) or event)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: events.append(event) or event)
    # Extraction reads the paper; any database lookup fails the test.
    monkeypatch.setattr(agr_curation, "_AGR_QUERY_CALLABLE", _no_database_lookup)
    workspace = _workspace()
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
    evidence_token = evidence_workspace.set_active_evidence_records(evidence_records)
    try:
        yield workspace, events
    finally:
        evidence_workspace.reset_active_evidence_records(evidence_token)
        builder.reset_active_extraction_builder_workspace(builder_token)


def _stage(**overrides: Any):
    arguments = {
        "pending_ref_id": "gene-expression-annotation-pef-1",
        "evidence_record_ids": ["evidence-67598e5688f123c8"],
        "where_expressed_statement": "PEF-1::GFP expression in the cilium",
        "rationale": "Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        "data_provider": "WB",
        "subject": _SUBJECT,
        "reference": _REFERENCE,
        "controlled_fields": [_relation_field()],
    }
    arguments.update(overrides)
    return _tool_fn(
        agr_curation.stage_gene_expression_observation,
        "stage_gene_expression_observation",
    )(**arguments)


def _stage_valid_observation():
    return _stage()


def _stage_materializable_observation(
    *extra_controlled_fields: dict[str, Any],
    anatomy: dict[str, Any] | None = None,
):
    return _stage(
        controlled_fields=[
            _relation_field(),
            _controlled_field("expression_experiment.expression_assay_used", "GFP reporter"),
            _controlled_field("expression_pattern.when_expressed.developmental_stage_start", "L2 larvae"),
            anatomy
            or _controlled_field("expression_pattern.where_expressed.anatomical_structure", "cilia"),
            *extra_controlled_fields,
        ],
    )


def _finalize(candidate_ids: list[str]):
    return _tool_fn(
        agr_curation.finalize_gene_expression_extraction,
        "finalize_gene_expression_extraction",
    )(candidate_ids=candidate_ids)


def _patch(updates: list[dict[str, Any]]):
    return _tool_fn(
        agr_curation.patch_gene_expression_observation,
        "patch_gene_expression_observation",
    )(
        candidate_id="gex-candidate-1",
        pending_ref_id="gene-expression-annotation-pef-1",
        updates=[
            {
                "string_value": None,
                "mention": None,
                "proposed_curie": None,
                "evidence_record_ids": None,
                **update,
            }
            for update in updates
        ],
    )


def _restage(workspace: builder.ExtractionBuilderWorkspace, staged_fields: dict[str, Any], **changes: Any) -> None:
    candidate = workspace.get_candidate("gex-candidate-1")
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=staged_fields,
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=changes.get("evidence_record_ids", candidate.evidence_record_ids),
        status=builder.CANDIDATE_STATUS_VALID,
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
    controlled_schema = _defs_schema(stage_schema, "GeneExpressionControlledFieldInput")
    # Extraction stages paper wording (and a paper-stated ID); it never takes a resolved value.
    assert set(controlled_schema["properties"]) == {"field_path", "mention", "proposed_curie"}
    assert "never the validated value" in controlled_schema["properties"]["proposed_curie"]["description"]

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


# ---------------------------------------------------------------------------------------
# Extraction never searches: every value is staged in the paper's wording, not validated.
# ---------------------------------------------------------------------------------------


def test_stage_records_every_value_as_paper_wording_not_yet_validated(active_builder_context):
    workspace, events = active_builder_context

    result = _stage_materializable_observation()

    assert result.status == "ok"
    candidate = workspace.candidates["gex-candidate-1"]
    assert candidate.evidence_record_ids == ["evidence-67598e5688f123c8"]
    staged = candidate.staged_fields
    assert "metadata" not in staged
    assert staged["relation"] == {
        "name": None,
        "vocabulary": None,
        "id": None,
        "mention": "is_expressed_in",
        **_NOT_VALIDATED,
    }
    assert staged["data_provider"] == {"abbreviation": None, "mention": "WB", **_NOT_VALIDATED}
    anatomy = staged["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert anatomy == {"curie": None, "name": None, "mention": "cilia", **_NOT_VALIDATED}
    stage = staged["expression_pattern"]["when_expressed"]["developmental_stage_start"]
    assert (stage["curie"], stage["name"], stage["mention"]) == (None, None, "L2 larvae")
    assert stage["lookup_outcome"] == "not_validated"
    # The paper's stage wording is the stage term's mention; validation fills the stage name.
    assert "when_expressed_stage_name" not in staged
    assert any(event["event_type"] == "gene_expression_builder.stage_completed" for event in events)


def test_stage_keeps_a_curie_the_paper_states_only_as_a_proposal(active_builder_context):
    workspace, _events = active_builder_context

    result = _stage_materializable_observation(
        anatomy=_controlled_field(
            "expression_pattern.where_expressed.anatomical_structure", "cilia", "WBbt:0005733"
        ),
    )

    assert result.status == "ok"
    anatomy = workspace.candidates["gex-candidate-1"].staged_fields["expression_pattern"][
        "where_expressed"
    ]["anatomical_structure"]
    assert anatomy == {
        "curie": None,
        "name": None,
        "mention": "cilia",
        "proposed_curie": "WBbt:0005733",
        **_NOT_VALIDATED,
    }


def test_stage_keeps_a_gene_id_the_paper_states_only_as_a_proposal(active_builder_context):
    workspace, _events = active_builder_context

    result = _stage(subject={"mention": "pef-1", "proposed_primary_external_id": "WBGene00003914"})

    assert result.status == "ok"
    assert workspace.candidates["gex-candidate-1"].staged_fields["expression_annotation_subject"] == {
        "primary_external_id": None,
        "gene_symbol": None,
        "mention": "pef-1",
        "proposed_primary_external_id": "WBGene00003914",
        **_NOT_VALIDATED,
    }


@pytest.mark.parametrize(
    "field_path",
    ["relation.name", "expression_pattern.when_expressed.stage_uberon_slim_terms"],
)
def test_stage_rejects_a_curie_on_a_fixed_choice_field(active_builder_context, field_path):
    result = _stage(
        controlled_fields=[
            _relation_field(),
            _controlled_field(field_path, "UBERON:0000068", "UBERON:0000068"),
        ]
    )

    assert result.status == "error"
    [issue] = result.data["validation_issues"]
    assert "fixed choice named by its term name; pass proposed_curie null" in issue["message"]


def test_stage_rejects_missing_evidence_ids(active_builder_context):
    result = _stage(evidence_record_ids=[])

    assert {issue["reason"] for issue in result.data["validation_issues"]} == {"too_short"}


def test_stage_rejects_placeholder_reference(active_builder_context):
    result = _stage(reference={"mention": "PMID:..."})

    assert {issue["reason"] for issue in result.data["validation_issues"]} == {
        "placeholder_reference"
    }


def test_patch_rejects_free_form_field_and_requires_wording_for_controlled_patch(
    active_builder_context,
):
    _stage_valid_observation()

    result = _patch(
        [
            {"field_path": "free_form.path", "string_value": "nope"},
            {"field_path": "relation.name"},
        ]
    )

    reasons = {issue["reason"] for issue in result.data["validation_issues"]}
    assert "literal_error" in reasons
    assert "value_error" in reasons


def test_patch_takes_no_resolved_value_for_a_controlled_field(active_builder_context):
    _stage_valid_observation()

    result = _patch(
        [{"field_path": "relation.name", "mention": "is_expressed_in", "string_value": "is_expressed_in"}]
    )

    assert result.status == "error"
    assert "pass string_value null" in str(result.data["validation_issues"])


def test_patch_updates_reference_and_controlled_field_as_paper_wording(active_builder_context):
    workspace, _events = active_builder_context
    _stage_materializable_observation()

    result = _patch(
        [
            {"field_path": "reference", "string_value": "PMID 39550472"},
            {
                "field_path": "expression_pattern.where_expressed.anatomical_structure",
                "mention": "cilium base",
                "proposed_curie": "WBbt:0005733",
            },
        ]
    )

    assert result.status == "ok"
    candidate = workspace.candidates["gex-candidate-1"]
    reference = candidate.staged_fields["single_reference"]
    assert reference["mention"] == "PMID 39550472"
    assert reference["pmid"] == "PMID:39550472"
    assert reference["reference_id"] is None
    assert reference["resolution_state"] == "unresolved"
    anatomy = candidate.staged_fields["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert (anatomy["mention"], anatomy["proposed_curie"], anatomy["curie"]) == (
        "cilium base",
        "WBbt:0005733",
        None,
    )
    assert anatomy["lookup_outcome"] == "not_validated"


def test_patch_restages_a_controlled_field_unresolved_with_its_wording(active_builder_context):
    workspace, _events = active_builder_context
    _stage_materializable_observation()

    result = _patch(
        [{"field_path": "expression_pattern.where_expressed.anatomical_structure", "mention": _RESIDUAL_BODY}]
    )

    assert result.status == "ok"
    anatomy = workspace.candidates["gex-candidate-1"].staged_fields["expression_pattern"][
        "where_expressed"
    ]["anatomical_structure"]
    assert anatomy == {"curie": None, "name": None, "mention": _RESIDUAL_BODY, **_NOT_VALIDATED}


def test_finalize_returns_compact_builder_summary(active_builder_context):
    workspace, events = active_builder_context
    _stage_materializable_observation()

    result = _finalize(["gex-candidate-1"])

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
    assert "GeneExpressionEnvelope" not in result.data
    payload = workspace.finalization.payload
    assert payload["curatable_objects"][0]["object_type"] == "GeneExpressionAnnotation"
    annotation = payload["curatable_objects"][0]
    assert annotation["evidence_record_ids"] == ["evidence-67598e5688f123c8"]
    assert payload["metadata"]["evidence_records"][0]["evidence_record_id"] == (
        "evidence-67598e5688f123c8"
    )
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert annotation["payload"]["relation"]["mention"] == "is_expressed_in"
    assert annotation["payload"]["relation"]["lookup_outcome"] == "not_validated"
    assert annotation["payload"]["data_provider"]["mention"] == "WB"
    assert annotation["payload"]["data_provider"]["abbreviation"] is None
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
    workspace, _events = active_builder_context
    _stage_materializable_observation()
    first = workspace.get_candidate("gex-candidate-1")
    second_payload = dict(first.staged_fields)
    second_payload["pending_ref_id"] = "gene-expression-annotation-pef-2"
    workspace.upsert_candidate(
        candidate_id="gex-candidate-2",
        staged_fields=second_payload,
        pending_ref_ids=["gene-expression-annotation-pef-2"],
        evidence_record_ids=first.evidence_record_ids,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = _finalize(["gex-candidate-1", "gex-candidate-2"])

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
    workspace, _events = active_builder_context
    _stage_materializable_observation()
    first_candidate = workspace.get_candidate("gex-candidate-1")
    second_payload = dict(first_candidate.staged_fields)
    second_payload["pending_ref_id"] = "gene-expression-annotation-pef-2"
    workspace.upsert_candidate(
        candidate_id="gex-candidate-2",
        staged_fields=second_payload,
        pending_ref_ids=["gene-expression-annotation-pef-2"],
        evidence_record_ids=first_candidate.evidence_record_ids,
        status=builder.CANDIDATE_STATUS_VALID,
    )

    first = _finalize(["gex-candidate-1"])
    duplicate = _finalize(["gex-candidate-1"])
    conflict = _finalize(["gex-candidate-2"])

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
    workspace, _events = active_builder_context
    _stage_materializable_observation()

    result = _finalize(["gex-candidate-1", "gex-candidate-1"])

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
    workspace, _events = active_builder_context
    _stage_materializable_observation()
    _restage(
        workspace,
        workspace.get_candidate("gex-candidate-1").staged_fields,
        evidence_record_ids=["{}"],
    )

    result = _finalize(["gex-candidate-1"])

    assert result.status == "error"
    reasons = {issue["reason"] for issue in result.data["validation_issues"]}
    assert "unknown_evidence_record_id" in reasons


def test_finalize_rejects_a_value_staged_as_validated(active_builder_context):
    """No path lets extraction supply an identity: a resolved value never finalizes."""

    workspace, _events = active_builder_context
    _stage_materializable_observation()
    staged_fields = dict(workspace.get_candidate("gex-candidate-1").staged_fields)
    staged_fields["relation"] = {
        "name": "is_expressed_in",
        "vocabulary": "Expression Relation",
        "id": 1,
        "mention": "is_expressed_in",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }
    _restage(workspace, staged_fields)

    result = _finalize(["gex-candidate-1"])

    assert result.status == "error"
    assert "relation must be staged not yet validated" in str(result.data["validation_issues"])


def test_finalize_rejects_relation_without_contract_state(active_builder_context):
    workspace, _events = active_builder_context
    _stage_materializable_observation()
    staged_fields = dict(workspace.get_candidate("gex-candidate-1").staged_fields)
    staged_fields["relation"] = {"name": "is_expressed_in"}
    _restage(workspace, staged_fields)

    result = _finalize(["gex-candidate-1"])

    assert result.status == "error"
    assert "relation: resolution_state must be one of" in str(
        result.data["validation_issues"]
    )


def test_finalize_rejects_placeholder_pmid(active_builder_context):
    workspace, events = active_builder_context
    _stage_materializable_observation()
    staged_fields = dict(workspace.get_candidate("gex-candidate-1").staged_fields)
    staged_fields["single_reference"] = {
        **staged_fields["single_reference"],
        "mention": "PMID:12345678",
    }
    _restage(workspace, staged_fields)

    result = _finalize(["gex-candidate-1"])

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
    assert "A `rationale` update must be non-empty; it cannot be" in patch_schema["properties"]["updates"]["description"]


@pytest.mark.parametrize(
    ("rationale", "message"),
    [("   ", "rationale must be non-empty"), ("", "rationale must be non-empty")],
)
def test_stage_rejects_blank_rationale(active_builder_context, rationale, message):
    result = _stage(rationale=rationale)

    assert result.status == "error"
    issues = result.data["validation_issues"]
    assert [issue["field_path"] for issue in issues] == ["rationale"]
    assert message in issues[0]["message"]


def test_stage_stores_stripped_rationale(active_builder_context):
    workspace, _events = active_builder_context
    _stage_valid_observation()

    assert workspace.candidates["gex-candidate-1"].staged_fields["rationale"] == (
        "Anti-GFP staining localizes the reporter to the cilium, not the cell body."
    )


def _patch_rationale(value: Any):
    return _patch([{"field_path": "rationale", "string_value": value}])


def test_patch_rewrites_rationale(active_builder_context):
    workspace, _events = active_builder_context
    _stage_valid_observation()

    result = _patch_rationale("  Reporter signal is ciliary in every imaged neuron.  ")

    assert result.status == "ok"
    assert workspace.candidates["gex-candidate-1"].staged_fields["rationale"] == (
        "Reporter signal is ciliary in every imaged neuron."
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_patch_cannot_clear_rationale(active_builder_context, value):
    workspace, _events = active_builder_context
    _stage_valid_observation()

    result = _patch_rationale(value)

    assert result.status == "error"
    assert [issue["reason"] for issue in result.data["validation_issues"]] == ["invalid_rationale"]
    assert workspace.candidates["gex-candidate-1"].staged_fields["rationale"] == (
        "Anti-GFP staining localizes the reporter to the cilium, not the cell body."
    )


def test_finalize_carries_rationale_into_the_annotation_payload(active_builder_context):
    workspace, _events = active_builder_context
    _stage_materializable_observation()

    result = _finalize(["gex-candidate-1"])

    assert result.status == "ok"
    annotation = workspace.finalization.payload["curatable_objects"][0]
    assert annotation["payload"]["rationale"] == (
        "Anti-GFP staining localizes the reporter to the cilium, not the cell body."
    )


def test_finalize_rejects_new_candidate_without_rationale(active_builder_context):
    workspace, _events = active_builder_context
    _stage_materializable_observation()
    staged_fields = dict(workspace.get_candidate("gex-candidate-1").staged_fields)
    staged_fields.pop("rationale")
    _restage(workspace, staged_fields)

    result = _finalize(["gex-candidate-1"])

    assert result.status == "error"
    assert {issue["reason"] for issue in result.data["validation_issues"]} == {
        "missing_rationale"
    }
    assert result.data["validation_issues"][0]["message"].endswith(
        "patch the candidate with a rationale saying why you selected it."
    )


# ---------------------------------------------------------------------------------------
# ALL-1283: every staged value keeps the paper's wording; validators supply the identity.
# ---------------------------------------------------------------------------------------

_RESIDUAL_BODY = "structures associated with the residual body"


def test_stage_keeps_wording_no_term_may_match_unresolved(active_builder_context):
    """Daniela's case: an anatomy phrase no term may match is staged, never rejected or dropped."""

    workspace, _events = active_builder_context

    result = _stage_materializable_observation(
        anatomy=_controlled_field(
            "expression_pattern.where_expressed.anatomical_structure", _RESIDUAL_BODY
        ),
    )

    assert result.status == "ok"
    staged = workspace.candidates["gex-candidate-1"].staged_fields
    assert staged["expression_pattern"]["where_expressed"]["anatomical_structure"] == {
        "curie": None,
        "name": None,
        "mention": _RESIDUAL_BODY,
        **_NOT_VALIDATED,
    }
    finalized = _finalize(["gex-candidate-1"])
    assert finalized.status == "ok"
    payload = workspace.finalization.payload["curatable_objects"][0]["payload"]
    anatomy = payload["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert anatomy["mention"] == _RESIDUAL_BODY
    assert anatomy["resolution_state"] == "unresolved"


def test_stage_writes_subject_and_reference_as_paper_wording_only(active_builder_context):
    workspace, _events = active_builder_context

    _stage_valid_observation()

    staged = workspace.candidates["gex-candidate-1"].staged_fields
    assert staged["expression_annotation_subject"] == {
        "primary_external_id": None,
        "gene_symbol": None,
        "mention": "pef-1",
        **_NOT_VALIDATED,
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


def test_stage_records_the_data_provider_as_named_without_a_lookup(active_builder_context):
    """The data provider is paper/species context; its validator confirms it."""

    workspace, _events = active_builder_context

    for pending_ref_id, provider in (
        ("gene-expression-annotation-pef-1", "wb"),
        ("gene-expression-annotation-pef-2", "C. elegans"),
    ):
        result = _stage(pending_ref_id=pending_ref_id, data_provider=provider)
        assert result.status == "ok"

    for candidate_id, provider in (("gex-candidate-1", "wb"), ("gex-candidate-2", "C. elegans")):
        assert workspace.candidates[candidate_id].staged_fields["data_provider"] == {
            "abbreviation": None,
            "mention": provider,
            **_NOT_VALIDATED,
        }


def test_stage_keeps_every_slim_term_as_its_own_value(active_builder_context):
    """Stage slims are Stage Uberon Slim Terms vocabulary terms, each its own value."""

    workspace, _events = active_builder_context

    result = _stage_materializable_observation(
        _controlled_field(
            "expression_pattern.when_expressed.stage_uberon_slim_terms", "post embryonic, pre-adult"
        ),
        _controlled_field("expression_pattern.when_expressed.stage_uberon_slim_terms", "late larval"),
    )

    assert result.status == "ok"
    slims = workspace.candidates["gex-candidate-1"].staged_fields["expression_pattern"][
        "when_expressed"
    ]["stage_uberon_slim_terms"]
    assert [
        (term["mention"], term["name"], term["vocabulary"], term["id"], term["lookup_outcome"])
        for term in slims
    ] == [
        ("post embryonic, pre-adult", None, None, None, "not_validated"),
        ("late larval", None, None, None, "not_validated"),
    ]


def test_stage_rejects_a_single_valued_field_staged_twice(active_builder_context):
    result = _stage_materializable_observation(
        _controlled_field("expression_pattern.where_expressed.anatomical_structure", "hypodermis"),
    )

    assert result.status == "error"
    assert [issue["reason"] for issue in result.data["validation_issues"]] == [
        "duplicate_controlled_field"
    ]


def test_stage_requires_paper_wording_for_every_controlled_field(active_builder_context):
    result = _stage(controlled_fields=[_relation_field("  ")])

    assert result.status == "error"
    assert result.data["validation_issues"][0]["field_path"].endswith("mention")


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


def test_inspect_ontology_term_carries_the_paper_wording_not_the_term_name(monkeypatch):
    """Regression (ALL-1283 S4): the suggested resolve call uses the staged paper wording."""

    def _lookup(*, method: str, **kwargs: Any) -> agr_curation.AgrQueryResult:
        if method == "get_ontology_term":
            return agr_curation.AgrQueryResult(
                status="ok",
                data={"curie": kwargs["term"], "name": "cilium", "ontology_type": kwargs.get("ontology_term_type")},
            )
        return agr_curation.AgrQueryResult(status="ok", data=[])

    monkeypatch.setattr(agr_curation, "_AGR_QUERY_CALLABLE", _lookup)
    inspect = _tool_fn(agr_curation.inspect_ontology_term, "inspect_ontology_term")
    arguments = {
        "domain_pack_id": agr_curation.GENE_EXPRESSION_DOMAIN_PACK_ID,
        "object_type": "GeneExpressionAnnotation",
        "field_path": "expression_pattern.where_expressed.anatomical_structure",
        "curie": "WBbt:0001234",
        "include_parents": False,
        "include_children": False,
    }

    result = inspect(**arguments, source_phrase="cilia")
    assert result.status == "ok", result
    assert result.data["next_tool_call"]["arguments"]["source_phrase"] == "cilia"
    assert "cilium" not in str(result.data["next_tool_call"])
    assert "cilia" in result.data["diagnostic_summary"]

    missing = inspect(**arguments, source_phrase="  ")
    assert missing.status == "error"
    assert "requires source_phrase: the paper's wording" in str(missing.message)
