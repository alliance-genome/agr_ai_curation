"""Explicit request scope never inherits display aliases or submission authority."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from src.lib.curation_workspace import session_validation_service as v
from src.schemas.curation_workspace import CurationCandidateValidationRequest, CurationSessionValidationRequest


@pytest.mark.parametrize("endpoint", ["candidate", "session"])
def test_missing_runtime_context_rejects_before_execution(monkeypatch, endpoint):
    session = NS(id=uuid4(), document_id=None)
    candidate = NS(id=uuid4(), domain_envelope=None, envelope_id=None, object_id=None,
        session=session, draft=NS(fields=[]))
    session.candidates = [candidate]
    monkeypatch.setattr(v, "_load_candidate_for_write", lambda *a, **k: candidate)
    monkeypatch.setattr(v, "_load_session_for_validation", lambda *a, **k: session)
    monkeypatch.setattr(v, "require_candidate_conformance", lambda *a: None)
    monkeypatch.setattr(v, "_apply_candidate_validation", lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(HTTPException) as exc:
        if endpoint == "candidate":
            v.validate_candidate(None, candidate.id, CurationCandidateValidationRequest(
                session_id=str(session.id), candidate_id=str(candidate.id), replace_curator_override=True), user_id="owner", explicit_request=True)
        else:
            v.validate_session(None, session.id, CurationSessionValidationRequest(
                session_id=str(session.id), replace_curator_override=True), user_id="owner", explicit_request=True)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("explicit,replacing", [(False, False), (True, True)])
def test_session_dispatches_once_per_envelope_with_all_requested_intents(monkeypatch, explicit, replacing):
    session = NS(id=uuid4(), document_id=uuid4())
    envelope = NS(document_id=session.document_id, envelope_json={"metadata": {
        "validation_history": {"artifact_id": "artifact", "session_id": "history"}}})
    eid = uuid4()
    session.candidates = [NS(id=uuid4(), object_id=f"obj-{n}", envelope_id=eid,
        domain_envelope=envelope, session=session, envelope_revision=1, draft=NS(fields=[])) for n in range(2)]
    monkeypatch.setattr(v, "_load_session_for_validation", lambda *a, **k: session)
    monkeypatch.setattr(v, "require_candidate_conformance", lambda *a: None)
    calls = []
    def apply(*a, **kwargs):
        calls.append(kwargs)
        if kwargs["force"] or kwargs["runtime_context"].explicit_validation_requested:
            kwargs["runtime_context"].dispatch_receipt["executed"] = True
        return None, False
    monkeypatch.setattr(v, "_apply_candidate_validation", apply)
    class EndOfValidation(Exception):
        pass
    def stop(**kwargs):
        raise EndOfValidation
    monkeypatch.setattr(v, "_aggregate_session_validation_snapshot", stop)
    with pytest.raises(EndOfValidation):
        v.validate_session(None, session.id, CurationSessionValidationRequest(
            session_id=str(session.id), replace_curator_override=replacing), user_id="owner", active_groups=(), explicit_request=explicit)
    assert [call["force"] for call in calls] == [replacing, False]
    assert [call["runtime_context"].explicit_validation_requested for call in calls] == [explicit, False]
    intents = calls[0]["runtime_context"].curator_revalidation
    assert ([i["object_id"] for i in intents] if intents else []) == (["obj-0", "obj-1"] if replacing else [])
    assert calls[1]["runtime_context"].curator_revalidation is None


def test_revalidation_authority_does_not_expand_name_to_unrelated_siblings():
    fields = [v.CurationDraftFieldSchema(field_key="attributes.term.name", label="Name")]
    assert "attributes.term" in v._candidate_validation_field_paths(fields)
    assert v._curator_revalidation_field_paths(fields) == ("attributes.term.name",)


def test_group_dispatch_resolves_stale_findings_for_every_requested_object(monkeypatch):
    from datetime import datetime, timezone
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope, ValidationFinding, FieldRef, ObjectRef
    objects = [CuratableObjectEnvelope(object_id=key, object_type="Record", payload={"title": "Present"}) for key in ("one", "two")]
    findings = [ValidationFinding(code="old.missing", message="Old missing title", severity="error", status="open",
        field_ref=FieldRef(object_ref=ObjectRef(object_id=key, object_type="Record"), field_path="title")) for key in ("one", "two")]
    envelope = DomainEnvelope(envelope_id="grouped", domain_pack_id="fixture", extracted_objects=objects, validation_findings=findings)
    pack = NS(pack_id="fixture")
    monkeypatch.setattr(v, "resolve_curation_domain_pack_by_id", lambda _: pack)
    monkeypatch.setattr(v, "resolve_curation_domain_envelope_validator_by_id", lambda _: None)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.resolve_envelope_profile_validation", lambda *a, **k: None)
    monkeypatch.setattr("src.lib.validation_history.runtime.bind_artifact", lambda *a: None)
    monkeypatch.setattr("src.lib.validation_history.projection.read_dispatch_source", lambda binding, source: source)
    monkeypatch.setattr(v, "run_domain_envelope_structural_checks", lambda source, *a, **k: NS(envelope=source, registry=None, appended_findings=()))
    def dispatch(source, *a, **k):
        assert not source.validation_findings
        return NS(envelope=source, appended_findings=(), binding_audit=())
    monkeypatch.setattr(v, "dispatch_active_validator_bindings", dispatch)
    # This test isolates grouped finding scopes; PostgreSQL tests cover locking.
    monkeypatch.setattr(v, "_lock_unchanged_validation_draft", lambda *args: None)
    monkeypatch.setattr(v, "write_domain_envelope_checkpoint", lambda *a: NS(revision=2))
    monkeypatch.setattr("src.lib.curation_workspace.session_mutation_service.load_envelope_candidates_for_patch", lambda *a, **k: [])
    row = NS(revision=1, project_key="fixture", document_id=uuid4(), session_id=uuid4(), flow_run_id=None,
        object_model_ref_json={}, model_field_ref_json={})
    candidate = NS(object_id="one", session_id=row.session_id, draft=NS(version=1, fields=[]))
    receipt = {}
    result, _, _ = v._dispatch_workspace_envelope_validation(None, candidate, envelope_row=row, envelope=envelope,
        field_paths=("title",), validated_at=datetime.now(timezone.utc), runtime_context=v.ValidatorRuntimeContext(
            document_id=str(row.document_id), user_id="owner", refresh_scopes=[("one", ("title",)), ("two", ("title",))], dispatch_receipt=receipt))
    assert receipt["executed"]
    assert [(finding.field_ref.object_ref.object_id, finding.status.value) for finding in result.validation_findings] == [("one", "resolved"), ("two", "resolved")]
