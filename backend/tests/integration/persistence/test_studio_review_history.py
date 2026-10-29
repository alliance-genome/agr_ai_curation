"""Studio reads the same immutable ledger without creating workspace candidates."""
import json

from sqlalchemy import func, select

from src.lib.agent_studio.review_history_tools import inspect_history
from src.lib.curation_workspace.models import CurationCandidate
from src.lib.validation_history import store as h
from src.models.sql.validation_history import ValidationFact
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated
from tests.integration.persistence.test_curator_selected_authority import scenario as scenario


def test_studio_history_without_candidate_is_owned_exact_and_read_only(scope, scenario, monkeypatch):
    monkeypatch.setenv("VALIDATION_HISTORY_PAGE_SIZE", "2")
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "512")
    args = dict(session_factory=h.SessionLocal, user_auth_sub=scope.owner_id,
                envelope_id=scenario.env.envelope_id)
    with h.SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(ValidationFact))
        candidates = db.scalar(select(func.count()).select_from(CurationCandidate))
    summary = inspect_history(**args)
    assert summary["success"] and summary["kind_counts"]
    denied = inspect_history(**{**args, "user_auth_sub": "another-user"})
    assert denied["code"] == "history_access_denied"
    assert "kind_counts" not in denied
    cursor = dict(action="list", through_sequence=summary["through_sequence"])
    found = []
    while True:
        page = inspect_history(**args, **cursor)
        assert page["success"] and len(page["items"]) <= 2
        found.extend(page["items"])
        if page["complete"]:
            assert page["next_call"] is None
            break
        cursor = {k:v for k,v in page["next_call"]["arguments"].items() if k != "envelope_id"}
    assert len(found) == sum(summary["kind_counts"].values())
    assert len({f["sequence"] for f in found}) == len(found)
    assert all(f["sequence"] <= summary["through_sequence"] for f in found)
    for ref in [summary["baseline"], found[0]]:
        detail = inspect_history(**args, action="detail", fact_id=ref["fact_id"], sha256=ref["sha256"])
        assert detail["success"]
        chunks = [detail["content"]]
        if ref["fact_id"] == "baseline":
            assert detail["next_call"] is not None
        while not detail["complete"]:
            call = detail["next_call"]["arguments"]
            detail = inspect_history(session_factory=h.SessionLocal, user_auth_sub=scope.owner_id, **call)
            chunks.append(detail["content"])
        decoded = json.loads("".join(chunks))
        assert h.canonical(decoded)[0] == ref["sha256"]
        assert inspect_history(**args, action="detail", fact_id=ref["fact_id"], sha256="wrong")["success"] is False
    assert inspect_history(**args, action="detail", fact_id="unknown", sha256="wrong")["success"] is False
    assert inspect_history(**args, action="list")["code"] == "invalid_history_request"
    with h.SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ValidationFact)) == before
        assert db.scalar(select(func.count()).select_from(CurationCandidate)) == candidates


def test_studio_history_filter_and_empty_page(scope, scenario):
    args = dict(session_factory=h.SessionLocal, user_auth_sub=scope.owner_id, envelope_id=scenario.env.envelope_id)
    summary = inspect_history(**args)
    kind = next(iter(summary["kind_counts"]))
    page = inspect_history(**args, action="list", kind=kind, through_sequence=summary["through_sequence"])
    assert page["success"] and all(f["kind"] == kind for f in page["items"])
    empty = inspect_history(**args, action="list", through_sequence=summary["through_sequence"], after=summary["through_sequence"])
    assert empty["success"] and empty["complete"] and empty["items"] == []


def test_imported_baseline_is_not_claimed_as_original(scope):
    from uuid import uuid4
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, write_domain_envelope_checkpoint
    from src.lib.validation_history.migration import import_historical_envelope
    envelope = DomainEnvelope(envelope_id="studio-historical-" + uuid4().hex, domain_pack_id="fixture",
        extracted_objects=[CuratableObjectEnvelope(object_id="one", object_type="gene",
                                                   payload={"name": "already validated"})])
    with h.SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(
            project_key="fixture", envelope=envelope, expected_revision=0,
            document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=str(uuid4())))
    assert import_historical_envelope(envelope.envelope_id)
    summary = inspect_history(session_factory=h.SessionLocal, user_auth_sub=scope.owner_id,
                              envelope_id=envelope.envelope_id)
    assert summary["success"]
    assert summary["baseline"]["subject_kind"] == "historical"
    assert summary["baseline"]["original_available"] is False
