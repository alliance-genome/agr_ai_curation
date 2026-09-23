"""Contract tests for the allele builder-pattern migration (Phase 4).

Mirrors ``test_gene_domain_pack.py`` / ``test_phenotype_builder_domain_pack.py`` for the allele
extractor's envelope -> builder migration: the per-domain materializer
(``materialize_allele_builder_state``), RELATIVE metadata_refs, the golden pending fixture, and the
``builder_finalization`` / ``builder_run_state`` tool-binding detection flags.

POSTURE: the migration changes the EXTRACTION MECHANISM, not the curation target. The builder
materializer emits the same 4-object pending paper/evidence association graph (one shared Reference,
per-candidate AlleleMention + EvidenceQuote(s) + AllelePaperEvidenceAssociation curatable_unit) and
the same BLOCKED write/export posture the existing envelope pack declares. The extractor is
mention-only: it NEVER materializes an Allele object or an allele identifier; the active
allele_mention_reference_validation binding owns allele identity.

The pre-existing ``test_allele_domain_pack.py`` covers the envelope-pattern conversion and
export/submission adapters and is intentionally left untouched (envelope legacy stays until Phase 6).
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import field_path_exists

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.allele import (  # noqa: E402
    ALLELE_ASSOCIATION_KIND,
    ALLELE_ASSOCIATION_OBJECT_ROLE,
    ALLELE_ASSOCIATION_OBJECT_TYPE,
    ALLELE_DOMAIN_PACK_ID,
    ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
    ALLELE_MATERIALIZER_ID,
    ALLELE_MENTION_OBJECT_TYPE,
    ALLELE_REFERENCE_OBJECT_TYPE,
    materialize_allele_builder_state,
)
from agr_ai_curation_alliance.domain_packs.allele.conversion import (  # noqa: E402
    AlleleBuilderExtractionOutput,
    validate_allele_builder_objects,
)

ALLELE_PACK_DIR = ALLIANCE_PYTHON_SRC.parent.parent / "domain_packs" / "allele"
BUILDER_FIXTURE_PATH = ALLELE_PACK_DIR / "fixtures" / "allele_builder_pending.yaml"
BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"


def _staged_fields() -> dict[str, Any]:
    return {
        "domain_pack_id": ALLELE_DOMAIN_PACK_ID,
        "object_type": ALLELE_ASSOCIATION_OBJECT_TYPE,
        "pending_ref_id": "allele-mention-1",
        "mention": "unc-54(e190)",
        "source_mentions": ["unc-54(e190)"],
        "rationale": "The paper's own paralysis assay characterizes e190, so this allele is curatable here.",
        "associated_gene": "unc-54",
        "taxon": "NCBITaxon:6239",
        "reference_title": "Myosin assembly in C. elegans body-wall muscle",
        "reference_filename": "unc54_paper.pdf",
    }


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-unc54-1",
            "entity": "unc-54(e190)",
            "verified_quote": "unc-54(e190) animals were paralyzed and arrested at the L4 stage.",
            "page": 5,
            "section": "Results",
            "subsection": "Muscle phenotypes",
            "chunk_id": "chunk-unc54-1",
        }
    ]


def _materialize_one_candidate() -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-test-run",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )


def test_h2_ab1_guidance_survives_stage_materialization_request_and_review(monkeypatch):
    """Historical mention/source facts; quote and guidance are synthetic fixture text."""
    from agr_ai_curation_alliance.tools import allele_builder_tools as tools
    from src.lib.openai_agents import extraction_builder_workspace as builder
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry, ValidationBindingState
    from src.lib.domain_packs.validator_dispatch import validator_request_payload_for_agent
    from src.lib.domain_packs.materialization import _validation_request_finding_payload
    from src.schemas.domain_envelope import DomainEnvelope
    events = []
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: events.append(event))
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **event: events.append(event))
    workspace = ExtractionBuilderWorkspace(run_id="guidance-fixture", domain_pack_id=ALLELE_DOMAIN_PACK_ID)
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    guidance = "The paper describes H2-Ab1 f/f mice from Cyagen; check conditional allele and source compatibility, not gene name alone."
    staged = tools._stage_allele_observation_impl(
        pending_ref_id="h2-ab1", mention="H2-Ab1 f/f", evidence_record_ids=["evidence-unc54-1"],
        source_mentions=["H2-Ab1 f/f", "Cyagen"], associated_gene="H2-Ab1",
        rationale="The paper uses H2-Ab1 f/f mice for its conditional knockout experiments.",
        taxon="Mus musculus", validation_guidance=guidance,
    )
    assert staged.status == "ok"
    assert any((event.get("output_summary") or {}).get("candidate", {}).get("staged_fields", {}).get("validation_guidance") == guidance for event in events)
    records = _evidence_records()
    records[0]["verified_quote"] = "Fixture: H2-Ab1 f/f mice were obtained from Cyagen."
    result = materialize_allele_builder_state(workspace=workspace, candidate_ids=[staged.data["candidate_id"]], evidence_records=records, resolver_entry_lookup=None)
    assert result.ok, result.summary()
    objects = result.payload["curatable_objects"]
    assert [obj["object_type"] for obj in objects if obj.get("validation_guidance")] == [ALLELE_MENTION_OBJECT_TYPE]
    envelope = DomainEnvelope(envelope_id="guidance", domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        extracted_objects=objects, metadata=result.payload["metadata"])
    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    matches = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
    requests = [build_domain_validation_request(match).request for match in matches if match.object_envelope.object_type == ALLELE_MENTION_OBJECT_TYPE]
    assert requests and all(request is not None for request in requests)
    for request in requests:
        assert request.validation_guidance == guidance
        assert validator_request_payload_for_agent(request)["validation_guidance"] == guidance
        assert _validation_request_finding_payload(request)["validation_guidance"] == guidance
        assert "MGI:7584221" not in str(request.selected_inputs)


def test_allele_pack_loads_with_builder_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(
        ALLELE_DOMAIN_PACK_ID, "allele_builder_pending"
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/allele_builder_pending.yaml"
    assert ALLELE_ASSOCIATION_OBJECT_TYPE in fixture_ref.object_types


def test_allele_builder_materializer_produces_clean_extraction_output():
    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    payload = result.payload
    assert payload is not None

    objects = payload["curatable_objects"]
    by_type = {obj["object_type"] for obj in objects}
    assert by_type == {
        ALLELE_REFERENCE_OBJECT_TYPE,
        ALLELE_MENTION_OBJECT_TYPE,
        ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
        ALLELE_ASSOCIATION_OBJECT_TYPE,
    }
    # Mention-only: the extractor never emits an Allele object.
    assert "Allele" not in by_type

    association = next(
        obj for obj in objects if obj["object_type"] == ALLELE_ASSOCIATION_OBJECT_TYPE
    )
    assert association["object_role"] == ALLELE_ASSOCIATION_OBJECT_ROLE
    assert association["pending_ref_id"] == "allele-paper-evidence-association-1"
    assert association["payload"]["association_kind"] == ALLELE_ASSOCIATION_KIND
    # The association's allele is staged unresolved with its paper wording.
    assert association["payload"]["mention"] == "unc-54(e190)"
    assert association["payload"]["resolution_state"] == "unresolved"
    assert association["payload"]["lookup_outcome"] == "not_validated"
    assert association["payload"].get("allele_identifier") is None
    assert association["payload"].get("allele_label") is None
    assert association["evidence_record_ids"] == ["evidence-unc54-1"]
    assert association["payload"]["evidence_record_ids"] == ["evidence-unc54-1"]
    assert association["payload"]["rationale"] == _staged_fields()["rationale"]
    # Existing-pack posture preserved: write/export remain blocked (the write_blocked BLOCKER is a
    # domain finding surfaced downstream, NOT a structural code).
    assert association["metadata"]["write_behavior"]["status"] == "blocked"
    assert association["metadata"]["export_behavior"]["status"] == "blocked"

    mention = next(
        obj for obj in objects if obj["object_type"] == ALLELE_MENTION_OBJECT_TYPE
    )
    # Exact paper notation preserved as the validator-binding selector anchor.
    assert mention["payload"]["mention"]["text"] == "unc-54(e190)"
    assert mention["payload"]["associated_gene"]["symbol"] == "unc-54"
    assert mention["payload"]["taxon"]["curie"] == "NCBITaxon:6239"
    assert mention["evidence_record_ids"] == ["evidence-unc54-1"]

    assert payload["metadata"]["provenance"]["source"] == ALLELE_MATERIALIZER_ID
    # No resolver/helper machinery: the allele validator owns identity.
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert result.evidence_record_ids == ("evidence-unc54-1",)


def test_allele_builder_shares_one_reference_across_candidates():
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-two-candidate",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    second = {
        **_staged_fields(),
        "pending_ref_id": "allele-mention-2",
        "mention": "daf-2(m41)",
        "source_mentions": ["daf-2(m41)"],
        "associated_gene": "daf-2",
    }
    workspace.upsert_candidate(
        candidate_id="allele-candidate-2",
        staged_fields=second,
        pending_ref_ids=["allele-mention-2"],
        evidence_record_ids=["evidence-daf2-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    evidence = _evidence_records() + [
        {
            "evidence_record_id": "evidence-daf2-1",
            "entity": "daf-2(m41)",
            "verified_quote": "Sequencing identified a G-to-A substitution in daf-2(m41).",
            "page": 7,
            "section": "Results",
            "subsection": "Molecular lesions",
            "chunk_id": "chunk-daf2-1",
        }
    ]
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1", "allele-candidate-2"],
        evidence_records=evidence,
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    objects = result.payload["curatable_objects"]
    references = [o for o in objects if o["object_type"] == ALLELE_REFERENCE_OBJECT_TYPE]
    associations = [o for o in objects if o["object_type"] == ALLELE_ASSOCIATION_OBJECT_TYPE]
    # Exactly one shared Reference; two associations both reference it.
    assert len(references) == 1
    assert len(associations) == 2
    shared_ref_id = references[0]["pending_ref_id"]
    for association in associations:
        ref_ids = {
            ref["pending_ref_id"]
            for ref in association["object_refs"]
            if ref["object_type"] == ALLELE_REFERENCE_OBJECT_TYPE
        }
        assert ref_ids == {shared_ref_id}


def test_allele_builder_metadata_refs_are_relative_and_resolve():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    association = next(
        obj
        for obj in payload["curatable_objects"]
        if obj["object_type"] == ALLELE_ASSOCIATION_OBJECT_TYPE
    )

    metadata_paths = {ref["metadata_path"] for ref in association["metadata_refs"]}
    assert metadata_paths == {"raw_mentions[0]", "evidence_records[0]"}
    metadata_root = payload["metadata"]
    for ref in association["metadata_refs"]:
        assert not ref["metadata_path"].startswith("extraction_metadata")
        assert field_path_exists(metadata_root, ref["metadata_path"])


def test_allele_builder_output_validates_against_object_contract():
    result = _materialize_one_candidate()
    assert result.payload is not None
    output = AlleleBuilderExtractionOutput.model_validate(result.payload)
    assert validate_allele_builder_objects(output) == ()


def test_allele_builder_rejects_evidence_record_not_in_metadata():
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-bad-evidence",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-MISSING"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_allele_builder_rejects_missing_mention():
    staged = _staged_fields()
    staged["mention"] = "   "
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-no-mention",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] in {"missing_allele_mention", "no_retained_candidates"}
        for issue in result.issues
    )


def test_allele_builder_golden_fixture_loads_with_relative_refs():
    fixture_pack = load_domain_fixture_pack(BUILDER_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    assert envelope.domain_pack_id == ALLELE_DOMAIN_PACK_ID

    association = next(
        obj
        for obj in envelope.extracted_objects
        if obj.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE
    )
    assert association.pending_ref_id == "allele-paper-evidence-association-1"

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.extracted_objects:
        for ref in obj.metadata_refs:
            assert not ref.metadata_path.startswith("extraction_metadata")
            assert field_path_exists(extraction_metadata, ref.metadata_path)


def test_finalize_allele_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_allele_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.allele_builder_tools:finalize_allele_extraction"
    )
    for tool_id in (
        "stage_allele_observation",
        "patch_allele_observation",
        "discard_allele_observation",
        "list_staged_allele_observations",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_allele_extractor_agent_has_no_output_schema_and_builder_tools():
    agent_path = (
        REPO_ROOT / "packages" / "alliance" / "agents" / "allele_extractor" / "agent.yaml"
    )
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert agent["output_schema"] is None
    tools = set(agent["tools"])
    assert "stage_allele_observation" in tools
    assert "finalize_allele_extraction" in tools
    assert "AlleleExtractionResultEnvelope" not in str(agent.get("output_schema"))


def test_attached_field_only_evidence_survives_allele_materialization():
    from src.lib.openai_agents.tools.evidence_workspace import _sync_target_fields

    records = _evidence_records()
    _sync_target_fields(records[0], [
        {'field_path': 'mention'},
        {'pending_ref_id': 'allele-mention-1', 'field_path': 'mention'},
    ])
    workspace = ExtractionBuilderWorkspace(
        run_id='field-only-evidence', domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id='allele_extractor',
    )
    workspace.upsert_candidate(
        candidate_id='allele-candidate-1', staged_fields=_staged_fields(),
        pending_ref_ids=['allele-mention-1'], evidence_record_ids=['evidence-unc54-1'],
        resolver_selection_refs=[], status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_allele_builder_state(
        workspace=workspace, candidate_ids=['allele-candidate-1'],
        evidence_records=records, resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    assert result.payload is not None
    assert result.payload["metadata"]["evidence_records"][0]["evidence_record_id"] == "evidence-unc54-1"


def _rationale_tools(monkeypatch):
    from agr_ai_curation_alliance.tools import allele_builder_tools as tools

    workspace = ExtractionBuilderWorkspace(
        run_id="allele-rationale", domain_pack_id=ALLELE_DOMAIN_PACK_ID
    )
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_event: None)
    return tools, workspace


def _stage_unc54(tools, rationale):
    return tools._stage_allele_observation_impl(
        pending_ref_id="allele-mention-1",
        mention="unc-54(e190)",
        evidence_record_ids=["evidence-unc54-1"],
        source_mentions=["unc-54(e190)"],
        rationale=rationale,
    )


def test_stage_allele_tool_requires_rationale_with_shared_description():
    from agr_ai_curation_alliance.tools import allele_builder_tools as tools
    from agr_ai_curation_alliance.tools.builder_rationale import RATIONALE_ARG_DESCRIPTION

    schema = tools.stage_allele_observation.params_json_schema
    assert "rationale" in schema["required"]
    assert schema["properties"]["rationale"]["description"] == RATIONALE_ARG_DESCRIPTION
    patch_schema = tools.patch_allele_observation.params_json_schema
    assert "rationale" not in patch_schema["properties"]
    assert (
        "A `rationale` update must be non-empty; it cannot be cleared."
        in patch_schema["properties"]["updates"]["description"]
    )


def test_stage_allele_rationale_is_stripped_and_staged(monkeypatch):
    tools, workspace = _rationale_tools(monkeypatch)
    staged = _stage_unc54(tools, "  e190 is the allele this paper assays.  ")
    assert staged.status == "ok"
    candidate = workspace.get_candidate(staged.data["candidate_id"])
    assert candidate.staged_fields["rationale"] == "e190 is the allele this paper assays."


def test_stage_allele_rejects_blank_rationale(monkeypatch):
    tools, workspace = _rationale_tools(monkeypatch)
    for bad_value, expected in (("   ", "non-empty"), ("", "non-empty")):
        result = _stage_unc54(tools, bad_value)
        assert result.status == "error"
        issues = result.data["validation_issues"]
        assert [issue["field_path"] for issue in issues] == ["rationale"]
        assert expected in issues[0]["message"]
    assert not workspace.candidates


def test_patch_allele_rationale_replaces_and_rejects_clearing(monkeypatch):
    tools, workspace = _rationale_tools(monkeypatch)
    candidate_id = _stage_unc54(tools, "Original reason.").data["candidate_id"]

    patched = tools._patch_allele_observation_impl(
        candidate_id=candidate_id,
        pending_ref_id="allele-mention-1",
        updates=[{"field_path": "rationale", "string_value": " Better reason. "}],
    )
    assert patched.status == "ok"
    assert workspace.get_candidate(candidate_id).staged_fields["rationale"] == "Better reason."

    for cleared in ("", "   ", None):
        rejected = tools._patch_allele_observation_impl(
            candidate_id=candidate_id,
            pending_ref_id="allele-mention-1",
            updates=[{"field_path": "rationale", "string_value": cleared}],
        )
        assert rejected.status == "error"
        assert rejected.data["validation_issues"][0]["reason"] == "invalid_rationale"
    assert workspace.get_candidate(candidate_id).staged_fields["rationale"] == "Better reason."


def test_allele_builder_rejects_missing_rationale():
    staged = _staged_fields()
    del staged["rationale"]
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-no-rationale",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
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


def test_allele_pack_declares_optional_rationale_in_rationale_group():
    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    definition = next(
        obj
        for obj in pack.metadata.object_definitions
        if obj.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE
    )
    field = next(item for item in definition.fields if item.field_path == "rationale")
    assert field.required is False
    assert field.display_name == "Rationale"
    groups = definition.metadata["workspace_display"]["groups"]
    rationale_group = next(group for group in groups if group["id"] == "rationale")
    assert rationale_group["label"] == "Rationale"
    assert rationale_group["fields"] == ["rationale"]


def test_stored_allele_association_without_rationale_gets_no_new_findings():
    from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks

    envelope = load_domain_fixture_pack(BUILDER_FIXTURE_PATH).fixtures[0].envelope
    association = next(
        obj for obj in envelope.extracted_objects if obj.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE
    )
    assert association.payload["rationale"]
    stored = envelope.model_copy(deep=True)
    for obj in stored.extracted_objects:
        obj.payload.pop("rationale", None)
    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)

    with_rationale = run_domain_envelope_structural_checks(envelope, pack).appended_findings
    without_rationale = run_domain_envelope_structural_checks(stored, pack).appended_findings
    assert [finding.code for finding in without_rationale] == [
        finding.code for finding in with_rationale
    ]
    assert not any("rationale" in str(finding.field_ref) for finding in without_rationale)


# --- Extracted vs validated allele (ALL-1283) ----------------------------------------------


def _staged_allele_envelope():
    from src.schemas.domain_envelope import DomainEnvelope

    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    return DomainEnvelope(
        envelope_id="allele-resolution",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata=result.payload["metadata"],
    )


def _validate_allele_mention(envelope, **result_fields):
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.materialization import (
        ValidatorResultMaterializationInput,
        materialize_validator_results_into_envelope,
    )
    from src.lib.domain_packs.validation_registry import (
        DomainPackValidationRegistry,
        ValidationBindingState,
    )
    from src.schemas.domain_validator import DomainValidatorResultBase

    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    match = next(
        match
        for match in DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(
            envelope, states=[ValidationBindingState.ACTIVE]
        )
        if match.binding.binding_id == "allele_mention_reference_validation"
    )
    request = build_domain_validation_request(match).request
    assert request is not None
    assert request.selected_inputs["mention"] == "unc-54(e190)"
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
    )


def _object(envelope, object_type):
    return next(obj for obj in envelope.extracted_objects if obj.object_type == object_type)


def test_allele_builder_stages_the_allele_unresolved_with_its_paper_wording():
    mention = next(
        obj
        for obj in _materialize_one_candidate().payload["curatable_objects"]
        if obj["object_type"] == ALLELE_MENTION_OBJECT_TYPE
    )
    allele = mention["payload"]["allele"]

    assert allele["mention"] == "unc-54(e190)"
    assert allele["resolution_state"] == "unresolved"
    assert allele["lookup_outcome"] == "not_validated"
    assert allele["validator_explanation"] == "Not validated yet."
    for key in ("primary_external_id", "allele_symbol", "taxon"):
        assert allele.get(key) is None


def test_pinned_allele_binding_resolves_the_staged_allele_value():
    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    binding = next(
        item
        for item in pack.metadata.metadata["validator_bindings"]["active"]
        if item["binding_id"] == "allele_mention_reference_validation"
    )
    # The hash-pinned binding is unchanged; its parent value just carries the mention.
    assert binding["expected_result_fields"] == {
        "curie": "allele.primary_external_id",
        "symbol": "allele.allele_symbol",
        "taxon": "allele.taxon",
    }

    result = _validate_allele_mention(
        _staged_allele_envelope(),
        status="resolved",
        resolved_values={
            "curie": "WB:WBVar00000190",
            "symbol": "e190",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Exact symbol match in WB.",
        curator_message="Resolved unc-54(e190).",
    )
    allele = _object(result.envelope, ALLELE_MENTION_OBJECT_TYPE).payload["allele"]

    assert allele == {
        "mention": "unc-54(e190)",
        "primary_external_id": "WB:WBVar00000190",
        "allele_symbol": "e190",
        "taxon": "NCBITaxon:6239",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": "Exact symbol match in WB.",
        "validator_curator_message": "Resolved unc-54(e190).",
    }
    assert [obj.payload for obj in result.materialized_objects if obj.object_type == "Allele"] == [
        {
            "primary_external_id": "WB:WBVar00000190",
            "allele_symbol": "e190",
            "taxon": "NCBITaxon:6239",
        }
    ]


def test_unresolved_allele_keeps_its_paper_wording_and_no_identity():
    result = _validate_allele_mention(
        _staged_allele_envelope(),
        status="unresolved",
        resolved_values={},
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_alleles",
                "query": {"allele_symbol": "unc-54(e190)"},
                "result_count": 3,
                "outcome": "ambiguous",
            }
        ],
        explanation="Three WB alleles share this designation.",
        curator_message="Pick the allele in review.",
    )
    allele = _object(result.envelope, ALLELE_MENTION_OBJECT_TYPE).payload["allele"]

    assert allele["mention"] == "unc-54(e190)"
    assert allele["resolution_state"] == "unresolved"
    assert allele["lookup_outcome"] == "ambiguous"
    assert allele["validator_explanation"] == "Three WB alleles share this designation."
    for key in ("primary_external_id", "allele_symbol", "taxon"):
        assert allele.get(key) is None
    assert not [obj for obj in result.materialized_objects if obj.object_type == "Allele"]


def test_allele_builder_requires_source_mentions_without_falling_back_to_the_mention():
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-no-source-mentions",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields={**_staged_fields(), "source_mentions": []},
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )

    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
    )

    assert not result.ok
    assert [issue["reason"] for issue in result.issues] == ["missing_source_mentions"]


def _association(envelope):
    return _object(envelope, ALLELE_ASSOCIATION_OBJECT_TYPE)


def _association_label(envelope) -> str:
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer

    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    rows = DomainPackMetadataReviewRowMaterializer(pack.metadata).materialize(
        envelope, envelope_revision=1
    )
    return next(
        row.display_label for row in rows if row.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE
    )


def _blocker_codes(envelope) -> set[str]:
    from agr_ai_curation_alliance.domain_packs.allele import (
        build_allele_association_submission_plan,
    )

    plan = build_allele_association_submission_plan(envelope)
    return {blocker["code"] for blocker in plan["blockers"]}


def test_validated_allele_is_written_back_onto_the_association():
    staged = _staged_allele_envelope()
    assert _association_label(staged) == "unc-54(e190) (paper wording)"
    assert "alliance.allele.allele_unresolved" in _blocker_codes(staged)

    result = _validate_allele_mention(
        staged,
        status="resolved",
        resolved_values={
            "curie": "WB:WBVar00000190",
            "symbol": "e190",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Exact symbol match in WB.",
        curator_message="Resolved unc-54(e190).",
    )
    association = _association(result.envelope)
    allele = next(obj for obj in result.envelope.extracted_objects if obj.object_type == "Allele")

    assert association.payload["mention"] == "unc-54(e190)"
    assert association.payload["allele_identifier"] == "WB:WBVar00000190"
    assert association.payload["allele_label"] == "e190"
    assert association.payload["resolution_state"] == "resolved"
    assert association.payload["lookup_outcome"] == "matched"
    assert association.payload["validator_explanation"] == "Exact symbol match in WB."
    assert allele.to_object_ref() in association.object_refs
    assert _association_label(result.envelope) == "e190"
    # The association now references its validated Allele, so that blocker is gone; the
    # pack's by-design write blockers (blocked write behavior, durable DB ids) remain.
    codes = _blocker_codes(result.envelope)
    assert "alliance.allele.association_refs_missing" not in codes
    assert "alliance.allele.allele_unresolved" not in codes
    assert "alliance.allele.write_behavior_blocked" in codes

    from agr_ai_curation_alliance.domain_packs.allele import validate_pending_allele_envelope

    assert not [
        finding
        for finding in validate_pending_allele_envelope(result.envelope)
        if finding.code == "alliance.allele.extractor_owned_identity_present"
    ]


def test_unresolved_allele_leaves_the_association_unresolved_with_its_paper_wording():
    result = _validate_allele_mention(
        _staged_allele_envelope(),
        status="unresolved",
        resolved_values={},
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_alleles",
                "query": {"allele_symbol": "unc-54(e190)"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        explanation="No WB allele matched.",
        curator_message="Check the allele designation.",
    )
    association = _association(result.envelope)

    assert association.payload["resolution_state"] == "unresolved"
    assert association.payload["lookup_outcome"] == "not_found"
    assert association.payload["validator_explanation"] == "No WB allele matched."
    assert association.payload.get("allele_identifier") is None
    assert association.payload.get("allele_label") is None
    assert not [ref for ref in association.object_refs if ref.object_type == "Allele"]
    assert _association_label(result.envelope) == "unc-54(e190) (paper wording)"
    assert {
        "alliance.allele.allele_unresolved",
        "alliance.allele.association_refs_missing",
    } <= _blocker_codes(result.envelope)


def test_stored_association_before_the_contract_reads_as_legacy_unverified():
    from src.schemas.domain_envelope import DomainEnvelope

    staged = _staged_allele_envelope()
    objects = []
    for obj in staged.extracted_objects:
        if obj.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE:
            # The pre-ALL-1283 shape: the paper wording sat in allele_label, no state.
            payload = {
                key: value
                for key, value in obj.payload.items()
                if key
                not in {
                    "mention",
                    "allele_identifier",
                    "allele_label",
                    "resolution_state",
                    "lookup_outcome",
                    "validator_explanation",
                }
            }
            obj = obj.model_copy(update={"payload": {**payload, "allele_label": "unc-54(e190)"}})
        objects.append(obj)
    legacy = DomainEnvelope(
        envelope_id="allele-legacy",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        extracted_objects=objects,
    )

    from src.lib.flows.export_fields import PackagedExportSource

    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    association = _association(legacy)
    effective = PackagedExportSource(pack).effective_item(
        {
            "object_type": ALLELE_ASSOCIATION_OBJECT_TYPE,
            "payload": association.payload,
            "metadata": association.metadata,
        }
    )["payload"]

    # The stored label is never read as a validated allele: it becomes marked paper wording.
    assert effective["resolution_state"] == "unresolved"
    assert effective["lookup_outcome"] == "legacy_unverified"
    assert effective["allele_label"] is None
    assert effective["mention"] == "unc-54(e190) (legacy, unverified)"
    assert "alliance.allele.allele_unresolved" in _blocker_codes(legacy)
    # The review row never shows the stored label as a validated allele.
    assert _association_label(legacy) == "unc-54(e190) (legacy, unverified)"


def test_allele_leaf_columns_match_the_shared_resolvable_headers():
    from src.lib.flows.export_fields import _pack_export_fields

    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    entries = [
        entry
        for entry in _pack_export_fields(pack)
        if entry["object_type"] in {ALLELE_ASSOCIATION_OBJECT_TYPE, ALLELE_MENTION_OBJECT_TYPE}
        and "payload_path" in entry
    ]
    display_names = {
        (obj.object_type, field.field_path): field.display_name
        for obj in pack.metadata.object_definitions
        for field in obj.fields
    }
    labels = {(entry["object_type"], entry["payload_path"]): entry["label"] for entry in entries}

    for object_type, prefix, parent in (
        (ALLELE_ASSOCIATION_OBJECT_TYPE, "", "Allele paper/evidence association"),
        (ALLELE_MENTION_OBJECT_TYPE, "allele.", "Allele"),
    ):
        for key, suffix in (
            ("mention", "(paper wording)"),
            ("resolution_state", "(status)"),
            ("lookup_outcome", "(lookup result)"),
            ("validator_explanation", "(validator explanation)"),
            ("validator_curator_message", "(validator message)"),
        ):
            header = f"{parent} {suffix}"
            assert labels[(object_type, f"{prefix}{key}")] == header
            assert display_names[(object_type, f"{prefix}{key}")] == header


def test_allele_taxon_is_part_of_the_validated_identity():
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields, effective_value

    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    spec = declared_resolvable_fields(pack.metadata, ALLELE_MENTION_OBJECT_TYPE)["allele"]
    assert spec.identity_keys == ("primary_external_id", "allele_symbol", "taxon")

    legacy = {"allele_symbol": "e190", "primary_external_id": "WB:WBVar00000190", "taxon": "NCBITaxon:6239"}
    assert effective_value(legacy, spec, covered_by_validator=False)["taxon"] is None


def test_a_later_unresolved_allele_drops_the_association_allele_link():
    resolved = _validate_allele_mention(
        _staged_allele_envelope(),
        status="resolved",
        resolved_values={
            "curie": "WB:WBVar00000190",
            "symbol": "e190",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Exact symbol match in WB.",
        curator_message="Resolved unc-54(e190).",
    ).envelope
    assert [ref for ref in _association(resolved).object_refs if ref.object_type == "Allele"]

    result = _validate_allele_mention(
        resolved,
        status="unresolved",
        resolved_values={},
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_alleles",
                "query": {"allele_symbol": "unc-54(e190)"},
                "result_count": 2,
                "outcome": "ambiguous",
            }
        ],
        explanation="Two WB alleles now share this designation.",
        curator_message="Pick the allele in review.",
    )
    association = _association(result.envelope)

    assert association.payload["resolution_state"] == "unresolved"
    assert association.payload["lookup_outcome"] == "ambiguous"
    assert association.payload["allele_identifier"] is None
    assert association.payload["allele_label"] is None
    assert not [ref for ref in association.object_refs if ref.object_type == "Allele"]
    assert _association_label(result.envelope) == "unc-54(e190) (paper wording)"
    assert "alliance.allele.allele_unresolved" in _blocker_codes(result.envelope)


def test_validated_allele_rows_read_the_allele_symbol():
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer

    result = _validate_allele_mention(
        _staged_allele_envelope(),
        status="resolved",
        resolved_values={
            "curie": "WB:WBVar00000190",
            "symbol": "e190",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Exact symbol match in WB.",
        curator_message="Resolved unc-54(e190).",
    )
    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    rows = DomainPackMetadataReviewRowMaterializer(pack.metadata).materialize(
        result.envelope, envelope_revision=1
    )
    allele_rows = [row for row in rows if row.object_type == "Allele"]

    assert [row.display_label for row in allele_rows] == ["e190"]


def _with_curated(envelope, object_type, path, edits):
    from src.lib.domain_packs.resolvable_values import apply_curator_identity

    objects = []
    for obj in envelope.extracted_objects:
        if obj.object_type == object_type:
            payload = copy.deepcopy(obj.payload)
            value = payload[path] if path else payload
            keys = (
                ("primary_external_id", "allele_symbol", "taxon")
                if path
                else ("allele_identifier", "allele_label")
            )
            apply_curator_identity(value, edits, identity_keys=keys, actor_id="curator-1", at="2026-09-23T20:00:00Z")
            obj = obj.model_copy(update={"payload": payload})
        objects.append(obj)
    return envelope.model_copy(update={"extracted_objects": objects})


_OVERRIDE = {
    "primary_external_id": "WB:WBVar00000999",
    "allele_symbol": "e999",
    "taxon": "NCBITaxon:6239",
}


def test_a_curator_overridden_allele_drives_the_association_like_a_validated_one():
    staged = _with_curated(_staged_allele_envelope(), ALLELE_MENTION_OBJECT_TYPE, "allele", _OVERRIDE)

    # The validator disagrees, but the override stands and the association follows it.
    result = _validate_allele_mention(
        staged,
        status="resolved",
        resolved_values={
            "curie": "WB:WBVar00000190",
            "symbol": "e190",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Exact symbol match in WB.",
        curator_message="Resolved unc-54(e190).",
    )
    association = _association(result.envelope)

    assert association.payload["allele_identifier"] == "WB:WBVar00000999"
    assert association.payload["allele_label"] == "e999"
    assert association.payload["resolution_state"] == "resolved"
    assert association.payload["lookup_outcome"] == "curator_override"
    # The validator's Allele (a different identity) is not linked to the association.
    linked = [ref for ref in association.object_refs if ref.object_type == "Allele"]
    assert linked == []
    assert _association_label(result.envelope) == "e999"


def test_a_curator_overridden_allele_survives_an_unresolved_result_on_the_association():
    staged = _with_curated(_staged_allele_envelope(), ALLELE_MENTION_OBJECT_TYPE, "allele", _OVERRIDE)

    result = _validate_allele_mention(
        staged,
        status="unresolved",
        resolved_values={},
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_alleles",
                "query": {"allele_symbol": "unc-54(e190)"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        explanation="No WB allele matched.",
        curator_message="Check the allele designation.",
    )
    association = _association(result.envelope)

    assert association.payload["allele_identifier"] == "WB:WBVar00000999"
    assert association.payload["lookup_outcome"] == "curator_override"
    assert "alliance.allele.allele_unresolved" not in _blocker_codes(result.envelope)


def test_a_curator_override_on_the_association_is_never_changed_by_the_validator():
    staged = _with_curated(
        _staged_allele_envelope(),
        ALLELE_ASSOCIATION_OBJECT_TYPE,
        "",
        {"allele_identifier": "WB:WBVar00000999", "allele_label": "e999"},
    )
    before = _association(staged)

    result = _validate_allele_mention(
        staged,
        status="resolved",
        resolved_values={
            "curie": "WB:WBVar00000190",
            "symbol": "e190",
            "taxon": "NCBITaxon:6239",
        },
        explanation="Exact symbol match in WB.",
        curator_message="Resolved unc-54(e190).",
    )
    association = _association(result.envelope)

    assert association.payload == before.payload
    assert association.object_refs == before.object_refs


def test_every_staged_contract_value_is_a_declared_resolvable_value():
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    pack = load_alliance_domain_pack_registry().get_pack(ALLELE_DOMAIN_PACK_ID)
    envelope = _staged_allele_envelope()
    fixture_envelopes = [
        load_domain_fixture_pack(BUILDER_FIXTURE_PATH).fixtures[0].envelope,
    ]

    def contract_paths(node, path=""):
        if isinstance(node, dict):
            if "resolution_state" in node or "lookup_outcome" in node:
                yield path
            for key, value in node.items():
                yield from contract_paths(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for value in node:
                yield from contract_paths(value, path)

    seen: set[tuple[str, str]] = set()
    for env in (envelope, *fixture_envelopes):
        for obj in env.extracted_objects:
            declared = declared_resolvable_fields(pack.metadata, obj.object_type)
            for path in contract_paths(obj.payload):
                seen.add((obj.object_type, path))
                assert path in declared, (obj.object_type, path)
    assert {(ALLELE_ASSOCIATION_OBJECT_TYPE, ""), (ALLELE_MENTION_OBJECT_TYPE, "allele")} <= seen
