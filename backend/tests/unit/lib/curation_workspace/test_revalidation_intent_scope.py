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
        domain_envelope=envelope, session=session) for n in range(2)]
    monkeypatch.setattr(v, "_load_session_for_validation", lambda *a, **k: session)
    monkeypatch.setattr(v, "require_candidate_conformance", lambda *a: None)
    calls = []
    def apply(*a, **kwargs):
        calls.append(kwargs)
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
