"""Contract tests for the gene builder-pattern migration (Phase 1).

Mirrors ``test_gene_expression_domain_pack.py`` for the gene extractor's envelope -> builder
migration: the per-domain materializer (``materialize_gene_builder_state``), RELATIVE metadata_refs,
the golden pending fixture, and the ``builder_finalization`` tool-binding detection flag.

The pre-existing ``test_alliance_gene_domain_pack.py`` covers the envelope-pattern conversion and
export/submission adapters and is intentionally left untouched (envelope legacy stays until Phase 6).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
    project_validation_summary_projections,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    field_path_exists,
)
from src.schemas.domain_validator import DomainValidatorResultBase

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.gene import (  # noqa: E402
    GENE_DOMAIN_PACK_ID,
    GENE_MATERIALIZER_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    GENE_OBJECT_ROLE,
    GENE_REFERENCE_VALIDATOR_BINDING_ID,
    materialize_gene_builder_state,
)
from agr_ai_curation_alliance.domain_packs.gene.conversion import (  # noqa: E402
    GeneBuilderExtractionOutput,
    validate_gene_builder_objects,
)

GENE_PACK_DIR = ALLIANCE_PYTHON_SRC.parent.parent / "domain_packs" / "gene"
BUILDER_FIXTURE_PATH = GENE_PACK_DIR / "fixtures" / "daf16_builder_pending.yaml"
BINDINGS_PATH = (
    REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"
)


def _staged_fields() -> dict[str, Any]:
    return {
        "domain_pack_id": GENE_DOMAIN_PACK_ID,
        "object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
        "pending_ref_id": "gene-mention-evidence-1",
        "mention": "daf-16",
        "confidence": "high",
        "identity_resolution_notes": [
            "The paper reports a daf-16 nuclear translocation phenotype in C. elegans."
        ],
        "rationale": "This paper's heat-shock assay shows DAF-16 nuclear translocation as a new result.",
        "species": "Caenorhabditis elegans",
        "taxon_hint": "NCBITaxon:6239",
        "data_provider_hint": "WB",
        "proposed_gene_symbol": "daf-16",
        "proposed_taxon": "NCBITaxon:6239",
    }


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-daf16-1",
            "entity": "daf-16",
            "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
            "page": 4,
            "section": "Results",
            "subsection": "Stress response assay",
            "chunk_id": "chunk-daf16-1",
            "figure_reference": "Figure 2A",
        }
    ]


def _materialize_one_candidate(validation_guidance=None) -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-test-run",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields={**_staged_fields(), "validation_guidance": validation_guidance},
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-daf16-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )


def test_gene_pack_loads_with_builder_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(GENE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(
        GENE_DOMAIN_PACK_ID, "daf16_builder_pending"
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/daf16_builder_pending.yaml"
    assert fixture_ref.object_types == [GENE_MENTION_EVIDENCE_OBJECT_TYPE]


def test_gene_guidance_survives_materialization_and_shared_binding():
    guidance = "Use the paper's C. elegans context to disambiguate the gene mention."
    result = _materialize_one_candidate(guidance)
    assert result.ok, result.summary()
    envelope = DomainEnvelope(envelope_id="gene-guidance", domain_pack_id=GENE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"], metadata=result.payload["metadata"])
    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    matches = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
    request = build_domain_validation_request(next(match for match in matches if match.binding.binding_id == GENE_REFERENCE_VALIDATOR_BINDING_ID)).request
    assert request is not None
    assert request.validation_guidance == guidance


def test_gene_builder_materializer_produces_clean_extraction_output():
    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    payload = result.payload
    assert payload is not None

    objects = payload["curatable_objects"]
    assert len(objects) == 1
    obj = objects[0]
    assert obj["object_type"] == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert obj["object_role"] == GENE_OBJECT_ROLE
    assert obj["pending_ref_id"] == "gene-mention-evidence-1"
    assert obj["evidence_record_ids"] == ["evidence-daf16-1"]
    assert obj["payload"]["mention"] == "daf-16"
    assert obj["payload"]["evidence_record_id"] == "evidence-daf16-1"
    assert obj["payload"]["verified_quote"] == (
        "DAF-16 translocated to nuclei after heat shock."
    )
    assert obj["payload"]["rationale"] == _staged_fields()["rationale"]
    assert obj["payload"]["identity_resolution_notes"] == _staged_fields()["identity_resolution_notes"]
    # No resolver/helper machinery: the gene validator owns identity.
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert payload["metadata"]["provenance"]["source"] == GENE_MATERIALIZER_ID
    assert result.evidence_record_ids == ("evidence-daf16-1",)


def test_gene_builder_metadata_refs_are_relative_and_resolve():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    obj = payload["curatable_objects"][0]

    metadata_paths = {ref["metadata_path"] for ref in obj["metadata_refs"]}
    assert metadata_paths == {"raw_mentions[0]", "evidence_records[0]"}
    # RELATIVE refs resolve against the extraction metadata namespace, never absolute.
    metadata_root = payload["metadata"]
    for ref in obj["metadata_refs"]:
        assert not ref["metadata_path"].startswith("extraction_metadata")
        assert field_path_exists(metadata_root, ref["metadata_path"])


def test_gene_builder_output_validates_against_object_contract():
    result = _materialize_one_candidate()
    assert result.payload is not None
    # Re-validating the materialized payload through the output model must succeed.
    output = GeneBuilderExtractionOutput.model_validate(result.payload)
    assert validate_gene_builder_objects(output) == ()


def test_gene_validator_resolution_projects_materialized_fields():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(GENE_DOMAIN_PACK_ID)
    assert pack is not None
    envelope = DomainEnvelope(
        envelope_id="gene-validation-projection-fixture",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                pending_ref_id="gene-mention-evidence-1",
                object_role=GENE_OBJECT_ROLE,
                status=CuratableObjectStatus.PENDING,
                payload={
                    "mention": "daf-16",
                    "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
                    "identity_resolution_notes": [
                        "The paper reports daf-16 in C. elegans."
                    ],
                    "species": "Caenorhabditis elegans",
                    "taxon_hint": "NCBITaxon:6239",
                    "data_provider_hint": "WB",
                },
            )
        ],
    )
    validation_registry = DomainPackValidationRegistry.from_domain_pack(pack)
    matches = [
        match
        for match in validation_registry.match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
        if match.binding.binding_id == GENE_REFERENCE_VALIDATOR_BINDING_ID
    ]
    assert len(matches) == 1
    request = build_domain_validation_request(matches[0]).request
    assert request is not None

    result = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [
            ValidatorResultMaterializationInput(
                match=matches[0],
                request=request,
                result=DomainValidatorResultBase(
                    status="resolved",
                    request_id=request.request_id,
                    validator_binding_id=request.validator_binding_id,
                    validator_agent=request.validator_agent,
                    target=request.target,
                    resolved_values={
                        "curie": "WB:WBGene00000912",
                        "symbol": "daf-16",
                        "taxon": "NCBITaxon:6239",
                    },
                    resolved_objects=[
                        {
                            "object_type": "Gene",
                            "resolved_id": "WB:WBGene00000912",
                            "projection_type": "gene_reference",
                            "projection_status": "resolved",
                        }
                    ],
                    missing_expected_fields=[],
                    candidates=[],
                    lookup_attempts=[
                        {
                            "provider": "agr_curation_query",
                            "method": "search_genes",
                            "query": {"symbol": "daf-16", "taxon": "NCBITaxon:6239"},
                            "result_count": 1,
                            "outcome": "success",
                        }
                    ],
                    curator_message="Resolved daf-16.",
                    explanation="Resolved by gene validation fixture.",
                ),
            )
        ],
    )

    assert result.materialized_objects == ()
    assert result.envelope.extracted_objects[0].payload["primary_external_id"] == (
        "WB:WBGene00000912"
    )
    assert result.envelope.extracted_objects[0].payload["gene_symbol"] == "daf-16"
    assert result.envelope.extracted_objects[0].payload["taxon"] == "NCBITaxon:6239"
    summaries = project_validation_summary_projections(
        result.envelope,
        envelope_revision=1,
        object_id="gene-mention-evidence-1",
    )
    assert {
        summary.field_path: summary.status.value
        for summary in summaries
        if summary.field_path is not None
    } == {
        "primary_external_id": "resolved",
        "gene_symbol": "resolved",
        "taxon": "resolved",
    }


def test_gene_builder_rejects_evidence_record_not_in_metadata():
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-bad-evidence",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-MISSING"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_gene_builder_golden_fixture_loads_with_relative_refs():
    fixture_pack = load_domain_fixture_pack(BUILDER_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    assert envelope.domain_pack_id == GENE_DOMAIN_PACK_ID
    assert envelope.extracted_objects[0].object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert envelope.extracted_objects[0].pending_ref_id == "gene-mention-evidence-1"

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.extracted_objects:
        for ref in obj.metadata_refs:
            assert not ref.metadata_path.startswith("extraction_metadata")
            assert field_path_exists(extraction_metadata, ref.metadata_path)


def test_finalize_gene_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_gene_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.gene_builder_tools:finalize_gene_extraction"
    )
    # The four staging tools are run-state builder tools.
    for tool_id in (
        "stage_gene_mention_evidence",
        "patch_gene_mention_evidence",
        "discard_gene_mention_evidence",
        "list_staged_gene_mention_evidence",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_gene_extractor_agent_has_no_output_schema_and_builder_tools():
    agent_path = (
        REPO_ROOT / "packages" / "alliance" / "agents" / "gene_extractor" / "agent.yaml"
    )
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert agent["output_schema"] is None
    tools = set(agent["tools"])
    assert "stage_gene_mention_evidence" in tools
    assert "finalize_gene_extraction" in tools
    # Builder agents must not carry an output schema (forbidden by the platform guard).
    assert "GeneExtractionResultEnvelope" not in str(agent.get("output_schema"))


def test_gene_rationale_is_copied_onto_every_evidence_object():
    records = _evidence_records() + [
        {
            **_evidence_records()[0],
            "evidence_record_id": "evidence-daf16-2",
            "verified_quote": "daf-16 mutants failed to extend lifespan.",
            "chunk_id": "chunk-daf16-2",
        }
    ]
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-fanout",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-daf16-1", "evidence-daf16-2"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=records,
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    objects = result.payload["curatable_objects"]
    assert len(objects) == 2
    assert {obj["payload"]["rationale"] for obj in objects} == {_staged_fields()["rationale"]}


def test_gene_builder_rejects_missing_rationale():
    staged = _staged_fields()
    staged["rationale"] = "  "
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-no-rationale",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-daf16-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "missing_rationale"
        and issue["field_path"] == "rationale"
        and issue["message"].endswith("patch the candidate with a rationale saying why you selected it.")
        for issue in result.issues
    )


def _gene_rationale_tools(monkeypatch):
    from agr_ai_curation_alliance.tools import gene_builder_tools as tools

    workspace = ExtractionBuilderWorkspace(run_id="gene-rationale", domain_pack_id=GENE_DOMAIN_PACK_ID)
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_event: None)
    return tools, workspace


def _stage_daf16(tools, rationale):
    return tools._stage_gene_mention_evidence_impl(
        pending_ref_id="gene-mention-evidence-1",
        mention="daf-16",
        evidence_record_ids=["evidence-daf16-1"],
        identity_resolution_notes=["C. elegans paper; WB provider context."],
        confidence="high",
        rationale=rationale,
    )


def test_stage_gene_tool_requires_rationale_with_shared_description():
    from agr_ai_curation_alliance.tools import gene_builder_tools as tools
    from agr_ai_curation_alliance.tools.builder_rationale import RATIONALE_ARG_DESCRIPTION

    schema = tools.stage_gene_mention_evidence.params_json_schema
    assert {"rationale", "identity_resolution_notes"} <= set(schema["required"])
    assert schema["properties"]["rationale"]["description"] == RATIONALE_ARG_DESCRIPTION
    patch_schema = tools.patch_gene_mention_evidence.params_json_schema
    assert "rationale" not in patch_schema["properties"]
    assert (
        "A `rationale` update must be non-empty; it cannot be cleared."
        in patch_schema["properties"]["updates"]["description"]
    )


def test_stage_gene_rationale_is_staged_separately_from_identity_notes(monkeypatch):
    tools, workspace = _gene_rationale_tools(monkeypatch)
    staged = _stage_daf16(tools, " DAF-16 translocation is this paper's own finding. ")
    assert staged.status == "ok"
    fields = workspace.get_candidate(staged.data["candidate_id"]).staged_fields
    assert fields["rationale"] == "DAF-16 translocation is this paper's own finding."
    assert fields["identity_resolution_notes"] == ["C. elegans paper; WB provider context."]


def test_stage_gene_rejects_blank_rationale(monkeypatch):
    tools, workspace = _gene_rationale_tools(monkeypatch)
    for bad_value, expected in (("", "non-empty"), ("   ", "non-empty")):
        result = _stage_daf16(tools, bad_value)
        assert result.status == "error"
        issues = result.data["validation_issues"]
        assert [issue["field_path"] for issue in issues] == ["rationale"]
        assert expected in issues[0]["message"]
    assert not workspace.candidates


def test_patch_gene_rationale_replaces_and_rejects_clearing(monkeypatch):
    tools, workspace = _gene_rationale_tools(monkeypatch)
    candidate_id = _stage_daf16(tools, "Original reason.").data["candidate_id"]

    patched = tools._patch_gene_mention_evidence_impl(
        candidate_id=candidate_id,
        pending_ref_id="gene-mention-evidence-1",
        updates=[{"field_path": "rationale", "string_value": "Better reason."}],
    )
    assert patched.status == "ok"
    assert workspace.get_candidate(candidate_id).staged_fields["rationale"] == "Better reason."

    for cleared in ("", "   ", None):
        rejected = tools._patch_gene_mention_evidence_impl(
            candidate_id=candidate_id,
            pending_ref_id="gene-mention-evidence-1",
            updates=[{"field_path": "rationale", "string_value": cleared}],
        )
        assert rejected.status == "error"
        assert rejected.data["validation_issues"][0]["reason"] == "invalid_rationale"
    assert workspace.get_candidate(candidate_id).staged_fields["rationale"] == "Better reason."


def test_gene_pack_rationale_group_is_separate_from_identity_notes():
    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    definition = next(
        obj
        for obj in pack.metadata.object_definitions
        if obj.object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    )
    field = next(item for item in definition.fields if item.field_path == "rationale")
    assert field.required is False
    assert field.display_name == "Rationale"
    groups = {group["id"]: group for group in definition.metadata["workspace_display"]["groups"]}
    assert groups["rationale"]["label"] == "Rationale"
    assert groups["rationale"]["fields"] == ["rationale"]
    assert "identity_resolution_notes" in groups["provenance"]["fields"]
    assert "rationale" not in groups["provenance"]["fields"]


def test_stored_gene_evidence_without_rationale_gets_no_new_findings():
    from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks

    envelope = load_domain_fixture_pack(BUILDER_FIXTURE_PATH).fixtures[0].envelope
    assert envelope.extracted_objects[0].payload["rationale"]
    stored = envelope.model_copy(deep=True)
    for obj in stored.extracted_objects:
        obj.payload.pop("rationale", None)
    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)

    with_rationale = run_domain_envelope_structural_checks(envelope, pack).appended_findings
    without_rationale = run_domain_envelope_structural_checks(stored, pack).appended_findings
    assert [finding.code for finding in without_rationale] == [
        finding.code for finding in with_rationale
    ]
    assert not any("rationale" in str(finding.field_ref) for finding in without_rationale)


# --- Extracted vs validated gene (ALL-1283) ------------------------------------------------


def _staged_gene_envelope() -> DomainEnvelope:
    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    return DomainEnvelope(
        envelope_id="gene-resolution",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=result.payload["metadata"],
    )


def _gene_validator_result(envelope: DomainEnvelope, **result_fields: Any):
    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    match = next(
        match
        for match in DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(
            envelope, states=[ValidationBindingState.ACTIVE]
        )
        if match.binding.binding_id == GENE_REFERENCE_VALIDATOR_BINDING_ID
    )
    request = build_domain_validation_request(match).request
    assert request is not None
    result = DomainValidatorResultBase(
        request_id=request.request_id,
        validator_binding_id=request.validator_binding_id,
        validator_agent=request.validator_agent,
        target=request.target,
        **{
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [],
            **result_fields,
        },
    )
    return materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [ValidatorResultMaterializationInput(match=match, request=request, result=result)],
    ).envelope.extracted_objects[0]


def test_gene_builder_stages_the_paper_wording_unresolved():
    payload = _materialize_one_candidate().payload["curatable_objects"][0]["payload"]

    assert payload["mention"] == "daf-16"
    assert payload["resolution_state"] == "unresolved"
    assert payload["lookup_outcome"] == "not_validated"
    assert payload["validator_explanation"] == "Not validated yet."
    # The extractor's proposal stays a proposal; no validated identity is staged.
    assert payload["proposed_gene_symbol"] == "daf-16"
    for key in ("primary_external_id", "gene_symbol", "taxon"):
        assert payload.get(key) is None


def test_gene_validator_resolution_writes_identity_and_state_keeping_the_mention():
    obj = _gene_validator_result(
        _staged_gene_envelope(),
        status="resolved",
        resolved_values={
            "curie": "WB:WBGene00000912",
            "symbol": "daf-16",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Matched by symbol in WB.",
        curator_message="Resolved daf-16.",
    )

    assert obj.payload["mention"] == "daf-16"
    assert obj.payload["primary_external_id"] == "WB:WBGene00000912"
    assert obj.payload["gene_symbol"] == "daf-16"
    assert obj.payload["taxon"] == "NCBITaxon:6239"
    assert obj.payload["resolution_state"] == "resolved"
    assert obj.payload["lookup_outcome"] == "matched"
    assert obj.payload["validator_explanation"] == "Matched by symbol in WB."
    assert obj.payload["validator_curator_message"] == "Resolved daf-16."


def test_gene_validator_failure_records_the_outcome_and_never_fills_identity():
    obj = _gene_validator_result(
        _staged_gene_envelope(),
        status="unresolved",
        resolved_values={},
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_genes",
                "query": {"symbol": "daf-16"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        explanation="No WB gene matched daf-16.",
        curator_message="Check the gene name in the paper.",
    )

    assert obj.payload["mention"] == "daf-16"
    assert obj.payload["resolution_state"] == "unresolved"
    assert obj.payload["lookup_outcome"] == "not_found"
    assert obj.payload["validator_explanation"] == "No WB gene matched daf-16."
    assert obj.payload["validator_curator_message"] == "Check the gene name in the paper."
    for key in ("primary_external_id", "gene_symbol", "taxon"):
        assert obj.payload.get(key) is None


def test_gene_builder_requires_the_paper_wording_without_fallback():
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-no-mention",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    staged = {**_staged_fields(), "mention": "  "}
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-daf16-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )

    result = materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=_evidence_records(),
    )

    assert not result.ok
    assert [issue["reason"] for issue in result.issues] == ["missing_gene_mention"]


def test_gene_builder_output_rejects_an_identity_without_validation():
    payload = _materialize_one_candidate().payload
    payload["curatable_objects"][0]["payload"]["primary_external_id"] = "WB:WBGene00000912"

    with pytest.raises(ValidationError, match="never carries an identity"):
        GeneBuilderExtractionOutput.model_validate(payload)


def _review_label(payload: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer

    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    envelope = DomainEnvelope(
        envelope_id="gene-label",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                object_id="gene-object-1",
                object_role=GENE_OBJECT_ROLE,
                payload=payload,
                metadata=metadata or {},
            )
        ],
    )
    rows = DomainPackMetadataReviewRowMaterializer(pack.metadata).materialize(
        envelope, envelope_revision=1
    )
    return rows[0].display_label


def test_gene_row_label_is_the_validated_symbol_or_marked_paper_wording():
    resolved = {
        "mention": "DAF-16",
        "primary_external_id": "WB:WBGene00000912",
        "gene_symbol": "daf-16",
        "taxon": "NCBITaxon:6239",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
    }
    unresolved = {
        "mention": "DAF-16",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_found",
    }

    assert _review_label(resolved) == "daf-16"
    assert _review_label(unresolved) == "DAF-16 (paper wording)"
    # A record stored before ALL-1283 never shows its stored text as the validated gene.
    legacy = {"mention": "DAF-16", "gene_symbol": "daf-16", "primary_external_id": "WB:WBGene00000912"}
    assert _review_label(legacy) == "DAF-16 (legacy, unverified)"
    covered = {
        "validator_resolved_value_materialization": [
            {"materialized_field_paths": ["primary_external_id", "gene_symbol", "taxon"]}
        ]
    }
    assert _review_label(legacy, covered) == "daf-16"


def test_gene_pack_declares_the_shared_resolution_vocabularies():
    from src.lib.domain_packs.resolvable_values import LOOKUP_OUTCOMES, RESOLUTION_STATES

    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    enums = {enum.enum_id: [value.value for value in enum.values] for enum in pack.metadata.enum_definitions}
    definition = next(
        obj
        for obj in pack.metadata.object_definitions
        if obj.object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    )
    fields = {field.field_path: field for field in definition.fields}

    assert enums[fields["resolution_state"].enum_ref] == list(RESOLUTION_STATES)
    assert enums[fields["lookup_outcome"].enum_ref] == list(LOOKUP_OUTCOMES)
    assert fields["validator_explanation"].field_type.value == "string"
    assert fields["validator_curator_message"].field_type.value == "string"
    model = next(
        model for model in pack.metadata.model_definitions if model.model_id == definition.model_ref
    )
    assert model.metadata["display"] == {
        "label": "gene_symbol",
        "id": "primary_external_id",
        "mention": "mention",
        "validated": ["taxon"],
    }
    assert definition.metadata["workspace_display"]["primary_label_field"] == "gene_symbol"
    assert definition.metadata["supervisor_manifest"]["primary_label_field"] == "gene_symbol"


def test_gene_taxon_is_part_of_the_validated_identity():
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields, effective_value
    from src.lib.flows.export_fields import PackagedExportSource

    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    spec = declared_resolvable_fields(pack.metadata, GENE_MENTION_EVIDENCE_OBJECT_TYPE)[""]
    assert spec.identity_keys == ("primary_external_id", "gene_symbol", "taxon")

    # A staged (unresolved) gene carries no taxon; the extractor's stays a proposal.
    staged = _materialize_one_candidate().payload["curatable_objects"][0]["payload"]
    assert staged.get("taxon") is None
    assert staged["proposed_taxon"] == "NCBITaxon:6239"

    # A gene stored before ALL-1283 without a covering validator event: its taxon
    # is not shown as validated anywhere.
    legacy = {
        "mention": "daf-16",
        "gene_symbol": "daf-16",
        "primary_external_id": "WB:WBGene00000912",
        "taxon": "NCBITaxon:6239",
    }
    assert effective_value(legacy, spec, covered_by_validator=False)["taxon"] is None
    exported = PackagedExportSource(pack).effective_item(
        {"object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE, "payload": legacy, "metadata": {}}
    )["payload"]
    assert exported["taxon"] is None
    assert exported["lookup_outcome"] == "legacy_unverified"
    # Covered by a validator write-back, the stored taxon reads as validated.
    assert effective_value(legacy, spec, covered_by_validator=True)["taxon"] == "NCBITaxon:6239"


def test_a_demoted_gene_keeps_the_extractor_proposals_apart_from_the_overruled_identity():
    resolved = _gene_validator_result(
        _staged_gene_envelope(),
        status="resolved",
        resolved_values={
            "curie": "WB:WBGene00000912",
            "symbol": "daf-16",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Matched by symbol in WB.",
        curator_message="Resolved daf-16.",
    )
    envelope = _staged_gene_envelope().model_copy(update={"extracted_objects": [resolved]})

    demoted = _gene_validator_result(
        envelope,
        status="unresolved",
        resolved_values={},
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_genes",
                "query": {"symbol": "daf-16"},
                "result_count": 2,
                "outcome": "ambiguous",
            }
        ],
        explanation="Two WB genes share this symbol.",
        curator_message="Pick the gene in review.",
    )
    payload = demoted.payload

    assert payload["resolution_state"] == "unresolved"
    assert payload["lookup_outcome"] == "ambiguous"
    for key in ("primary_external_id", "gene_symbol", "taxon"):
        assert payload[key] is None
    # The extractor's proposals are untouched; the overruled identity sits apart.
    assert payload["proposed_gene_symbol"] == "daf-16"
    assert payload["proposed_taxon"] == "NCBITaxon:6239"
    assert "proposed_primary_external_id" not in payload
    assert payload["overruled_primary_external_id"] == "WB:WBGene00000912"
    assert payload["overruled_gene_symbol"] == "daf-16"
    assert payload["overruled_taxon"] == "NCBITaxon:6239"


def test_every_staged_gene_contract_value_is_a_declared_resolvable_value():
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    pack = load_alliance_domain_pack_registry().get_pack(GENE_DOMAIN_PACK_ID)
    declared = declared_resolvable_fields(pack.metadata, GENE_MENTION_EVIDENCE_OBJECT_TYPE)
    staged = _materialize_one_candidate().payload["curatable_objects"][0]["payload"]
    fixture = load_domain_fixture_pack(BUILDER_FIXTURE_PATH).fixtures[0].envelope

    # The object root is the gene value; nothing below it carries contract state.
    for payload in (staged, fixture.extracted_objects[0].payload):
        assert "resolution_state" in payload
        assert "" in declared
        assert not [
            key for key, value in payload.items()
            if isinstance(value, dict) and ("resolution_state" in value or "lookup_outcome" in value)
        ]
