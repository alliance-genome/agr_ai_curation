"""Workspace and prep use the same exact profile registry and transaction."""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.lib.curation_workspace import pipeline, session_validation_service
from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks
from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
from .test_profile_validation import example as example
from .test_profile_materialization import prepared, results


@pytest.mark.parametrize("inline_complete", [False, True])
def test_prep_checks_live_profile_access_without_open_generic_fallback(example, monkeypatch, inline_complete):
    source, context = prepared(example)
    source.metadata["inline_validator_dispatch_complete"] = inline_complete
    row = SimpleNamespace(envelope_json=source.model_dump(mode="json"), revision=1,
        project_key="fixture", document_id=None, session_id=None, flow_run_id=None,
        object_model_ref_json={}, model_field_ref_json={})
    db = SimpleNamespace(get=lambda *args: row)
    revoked = replace(example[1], available=False, unavailable_reason="Access revoked")
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile",
                        lambda session, receipt: context.profile)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog",
                        lambda **kwargs: [revoked])
    monkeypatch.setattr(pipeline, "resolve_curation_domain_pack_by_id", lambda _key: example[2])
    monkeypatch.setattr(pipeline, "resolve_curation_domain_envelope_validator_by_id",
                        lambda _key: lambda *_args: pytest.fail("Open generic validator ran"))
    checkpoints = []
    monkeypatch.setattr(pipeline, "write_domain_envelope_checkpoint",
                        lambda _db, request: (checkpoints.append(request), SimpleNamespace(revision=2))[1])
    if inline_complete:
        monkeypatch.setattr(pipeline, "dispatch_active_validator_bindings",
                            lambda *args, **kwargs: pytest.fail("Prep reran inline validators"))
    revision = pipeline._refresh_domain_envelope_validation_for_ref(
        db, SimpleNamespace(envelope_id=source.envelope_id, envelope_revision=1),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("TEAM_A",)),
    )
    assert revision == 2
    saved = checkpoints[0].envelope
    assert saved.extracted_objects == source.extracted_objects
    finding, = saved.validation_findings
    assert finding.code == "generic_profile.validator_unavailable"
    assert finding.details["generic_profile_ref"] == context.profile.receipt


def test_workspace_revalidation_uses_profile_registry_and_atomic_writer(example, monkeypatch):
    monkeypatch.setattr("src.lib.curation_workspace.session_mutation_service.load_envelope_candidates_for_patch",
                        lambda *args, **kwargs: [])
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    source, context = prepared(example)
    item, = results(source, context, [{"identifier": "EX:1"}])
    row = SimpleNamespace(revision=1, project_key="fixture", document_id=None,
        session_id=None, flow_run_id=None, object_model_ref_json={}, model_field_ref_json={})
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile",
                        lambda session, receipt: context.profile)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog",
                        lambda **kwargs: [example[1]])
    monkeypatch.setattr(session_validation_service, "resolve_curation_domain_pack_by_id", lambda _key: example[2])
    monkeypatch.setattr(session_validation_service, "resolve_curation_domain_envelope_validator_by_id",
                        lambda _key: lambda *_args: pytest.fail("Open generic validator ran"))
    monkeypatch.setattr(session_validation_service, "dispatch_active_validator_bindings",
        lambda *args, **kwargs: dispatch_active_validator_bindings(*args, **kwargs,
            runner=lambda request, **run_kwargs: item.result.model_dump(mode="json")))
    checkpoints = []
    monkeypatch.setattr(session_validation_service, "write_domain_envelope_checkpoint",
                        lambda _db, request: (checkpoints.append(request), SimpleNamespace(revision=2))[1])
    candidate = SimpleNamespace(id="candidate", session_id="session", object_id="one", envelope_revision=1, draft=None)
    updated, revision, warnings = session_validation_service._dispatch_workspace_envelope_validation(
        object(), candidate, envelope_row=row, envelope=source, field_paths=(),
        validated_at=datetime.now(timezone.utc),
    )
    assert revision == candidate.envelope_revision == 2
    assert not warnings
    assert updated.extracted_objects[0].payload["attributes"]["resolved_id"] == "EX:1"
    assert checkpoints[0].envelope == updated
    assert source.extracted_objects[0].payload["attributes"] == {"paper_name": "A"}


@pytest.mark.parametrize("available", [True, False])
def test_manual_profile_candidate_uses_durable_envelope_and_mapped_validation(example, monkeypatch, available):
    monkeypatch.setattr("src.lib.curation_workspace.session_mutation_service.load_envelope_candidates_for_patch",
                        lambda *args, **kwargs: [])
    from uuid import uuid4
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    from src.lib.curation_workspace.execution_contracts import require_extraction_conformance
    from src.schemas.curation_workspace import CurationCandidateSource, CurationDraftField
    from src.schemas.domain_envelope import DomainEnvelope
    source, context = prepared(example)
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile",
                        lambda *args: context.profile)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog",
                        lambda **kwargs: [example[1]] if available else [])
    monkeypatch.setattr(session_validation_service, "resolve_curation_domain_pack_by_id", lambda key: example[2])
    monkeypatch.setattr(session_validation_service, "resolve_curation_domain_envelope_validator_by_id",
                        lambda key: lambda *args: pytest.fail("Manual profile used open generic validation"))
    candidate = SimpleNamespace(id=uuid4(), session_id=uuid4(), agent_revision_id=context.receipt.agent_revision_id,
        execution_receipt=context.receipt.model_dump(mode="json"), source=CurationCandidateSource.MANUAL,
        envelope_id=None, object_id=None, envelope_revision=None, domain_envelope=None,
        adapter_key="generic", validation_snapshots=[], candidate_metadata={},
        normalized_payload={"object_type": "generic_object", "class_key": "generic:generic_object",
                            "semantic_class": "record", "attributes": {"paper_name": "A"}},
        session=SimpleNamespace(document_id=uuid4(), flow_run_id=None),
        draft=SimpleNamespace(version=1, fields=[CurationDraftField(field_key="attributes", label="Record",
            value={"paper_name": "A"}, seed_value={"paper_name": "A"}, field_type="object").model_dump(mode="json")]))
    rows, requests = {}, []
    db = SimpleNamespace(get=lambda model, key: rows.get(key), flush=lambda: None)

    def checkpoint(session, request):
        assert session is db
        require_extraction_conformance(db, context.receipt, request.envelope.model_dump(mode="json"),
                                      agent_key=context.receipt.agent_key)
        requests.append(request)
        row = rows.setdefault(request.envelope.envelope_id, SimpleNamespace(
            project_key=request.project_key, document_id=request.document_id, session_id=request.session_id,
            flow_run_id=request.flow_run_id, object_model_ref_json={}, model_field_ref_json={},
            execution_receipt=context.receipt.model_dump(mode="json")))
        row.revision = request.expected_revision + 1
        row.envelope_json = request.envelope.model_dump(mode="json")
        return SimpleNamespace(envelope_id=request.envelope.envelope_id, revision=row.revision)

    monkeypatch.setattr(session_validation_service, "write_domain_envelope_checkpoint", checkpoint)

    def dispatch(envelope, pack, **kwargs):
        values = results(envelope, kwargs["profile_context"], [{"identifier": "EX:1"}]) if available else []
        return dispatch_active_validator_bindings(envelope, pack, **kwargs,
            runner=lambda *args, **kw: values[0].result.model_dump(mode="json"))

    monkeypatch.setattr(session_validation_service, "dispatch_active_validator_bindings", dispatch)
    computation = session_validation_service._compute_candidate_validation(
        db, candidate, force=True, validated_at=datetime.now(timezone.utc))
    assert len(requests) == 2 and requests[0].expected_revision == 0
    assert candidate.envelope_id == f"manual-profile:{candidate.id}"
    assert candidate.normalized_payload == {}
    final = DomainEnvelope.model_validate(rows[candidate.envelope_id].envelope_json)
    assert final.metadata["source"] == "manual"
    assert final.metadata["extraction_metadata"]["provenance"]["record_source"] == "curator_manual"
    attributes = final.extracted_objects[0].payload["attributes"]
    assert attributes == ({"paper_name": "A", "resolved_id": "EX:1"} if available else {"paper_name": "A"})
    assert computation.updated_fields[0]["value"] == attributes
    assert computation.snapshot.envelope_id == candidate.envelope_id
    assert computation.snapshot.envelope_revision == 2
    if not available:
        assert final.validation_findings[0].code == "generic_profile.validator_unavailable"


def test_profile_structural_check_handles_empty_array_of_required_fields(example):
    source, context = prepared(example, per_element=True, attributes={"records": []})
    result = run_domain_envelope_structural_checks(source, example[2], profile_context=context)
    assert result.registry is context.registry
    assert not result.appended_findings


def test_profile_structural_check_rejects_invalid_nested_record(example):
    from src.lib.agent_studio.profile_conformance import ProfileConformanceError
    source, context = prepared(example, per_element=True, attributes={"records": [{}]})
    with pytest.raises(ProfileConformanceError):
        run_domain_envelope_structural_checks(source, example[2], profile_context=context)


def test_profile_cache_rechecks_live_capability_and_restored_access(example, monkeypatch):
    from src.lib.domain_packs.profile_validation import profile_policy_finding
    source, context = prepared(example)
    row = SimpleNamespace(envelope_json=source.model_dump(mode="json"))
    db = SimpleNamespace(get=lambda *args: row)
    candidate = SimpleNamespace(execution_receipt=context.receipt.model_dump(mode="json"),
                                envelope_id=source.envelope_id)
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile",
                        lambda session, receipt: context.profile)
    catalog = [example[1]]
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: catalog)
    monkeypatch.setattr(session_validation_service, "resolve_curation_domain_pack_by_id", lambda _key: example[2])
    assert session_validation_service._profile_validation_cache_is_current(db, candidate, None)
    catalog[:] = [replace(example[1], available=False, unavailable_reason="Revoked")]
    assert not session_validation_service._profile_validation_cache_is_current(db, candidate, None)
    catalog[:] = [example[1]]
    source.validation_findings.append(profile_policy_finding(context, context.profile.contract.validator_mappings[0],
        code="generic_profile.validator_unavailable", message="Previous revocation"))
    row.envelope_json = source.model_dump(mode="json")
    assert not session_validation_service._profile_validation_cache_is_current(db, candidate, None)


def test_persisted_profile_review_rows_use_closed_editable_fields_and_unavailable_mapping(example, monkeypatch):
    from src.lib.domain_packs.materialization import materialize_persisted_envelope_review_rows
    raw, cap, pack = example
    raw["fields"][0]["display_name"] = "Published name"
    source, context = prepared(example)
    source.extracted_objects[0].metadata["workspace_display"] = {
        "groups": [{"id": "injected", "label": "Injected", "fields": ["attributes.undeclared"]}],
    }
    source.extracted_objects[0].object_role = "metadata_only"
    source.extracted_objects[0].metadata["object_model_ref"] = {"provider": "unapproved"}
    row = SimpleNamespace(envelope_json=source.model_dump(mode="json"), revision=3)
    db = SimpleNamespace(get=lambda *args: row)
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda key: pack)
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile", lambda *args: context.profile)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: [])
    response = materialize_persisted_envelope_review_rows(db, source.envelope_id, revision=3,
        materializer=SimpleNamespace(materialize=lambda *a, **kw: pytest.fail("Profile used supplied open materializer")))
    review_row, = response.rows
    assert review_row.object_role is None
    assert review_row.object_model_ref == {} and review_row.model_field_ref == {}
    fields = review_row.metadata["workspace_fields"]
    assert [field["field_path"] for field in fields] == ["attributes.paper_name", "attributes.resolved_id"]
    assert fields[0]["label"] == "Published name"
    assert fields[0]["value"] == "A" and fields[0]["metadata"]["editable"]
    assert fields[1]["value"] is None
    assert review_row.metadata["generic_profile_ref"] == context.profile.receipt
    assert review_row.metadata["linkml_alignment"] == "not_assessed"
    unavailable, = review_row.metadata["unavailable_validator_capabilities"]
    assert unavailable["state"] == "unavailable"
    assert unavailable["profile_validator_mapping"]["mapping_id"] == "lookup"
