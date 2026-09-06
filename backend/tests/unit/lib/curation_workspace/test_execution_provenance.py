"""Server-owned execution identity is retained independently of model output."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.lib.domain_envelopes.persistence import (
    DomainEnvelopePersistenceError,
    _checkpoint_execution_context,
    domain_envelope_payload_hash,
)
from src.schemas.domain_envelope import DomainEnvelope
from src.schemas.execution_provenance import (
    ExtractionExecutionContext,
    SourceDocumentProvenance,
)


def _context():
    return ExtractionExecutionContext(
        captured_at=datetime.now(timezone.utc), source_kind="flow", flow_id="flow-1",
        step_id="extractor", agent_key="gene", executed_query="  Curator query\r\nβ\n",
        document=SourceDocumentProvenance(document_id=uuid4()),
    )


def test_context_preserves_exact_query_and_missing_source_identity():
    context = _context()
    assert ExtractionExecutionContext.model_validate_json(context.model_dump_json()) == context
    assert context.executed_query == "  Curator query\r\nβ\n"
    assert context.document.reference_curie is None
    assert context.document.converted_artifact_sha256 is None


def test_digest_requires_original_artifact_identity():
    with pytest.raises(ValidationError, match="requires provider and artifact"):
        SourceDocumentProvenance(document_id=uuid4(), converted_artifact_sha256="a" * 64)


def test_chat_context_does_not_invent_flow_identity():
    payload = _context().model_dump()
    payload.update(source_kind="chat", flow_id=None, step_id="ask_gene_specialist")
    assert ExtractionExecutionContext.model_validate(payload).flow_id is None
    payload["flow_id"] = "invented-flow"
    with pytest.raises(ValidationError, match="cannot claim flow identity"):
        ExtractionExecutionContext.model_validate(payload)


def test_flow_context_requires_real_flow_and_step_identifiers():
    payload = _context().model_dump()
    payload["step_id"] = None
    with pytest.raises(ValidationError, match="requires flow and step identity"):
        ExtractionExecutionContext.model_validate(payload)


def test_initial_checkpoint_uses_only_explicit_server_capture():
    context = _context()
    row = SimpleNamespace(envelope_json={"execution_context": context.model_dump(mode="json")})
    assert _checkpoint_execution_context(envelope_row=row, supplied=None, initial=True) is None
    assert _checkpoint_execution_context(envelope_row=row, supplied=context, initial=True) == context


def test_later_checkpoint_preserves_capture_and_rejects_replacement():
    context = _context()
    row = SimpleNamespace(envelope_json={"execution_context": context.model_dump(mode="json")})
    assert _checkpoint_execution_context(envelope_row=row, supplied=None, initial=False) == context
    changed = context.model_copy(update={"executed_query": "new mutable flow instructions"})
    with pytest.raises(DomainEnvelopePersistenceError, match="cannot be replaced"):
        _checkpoint_execution_context(envelope_row=row, supplied=changed, initial=False)


def test_historical_context_is_not_backfilled():
    row = SimpleNamespace(envelope_json={})
    assert _checkpoint_execution_context(envelope_row=row, supplied=None, initial=False) is None
    with pytest.raises(DomainEnvelopePersistenceError, match="cannot be replaced"):
        _checkpoint_execution_context(envelope_row=row, supplied=_context(), initial=False)


def test_output_hash_does_not_mix_execution_identity_with_model_output():
    envelope = DomainEnvelope(envelope_id="env-1", domain_pack_id="generic")
    assert domain_envelope_payload_hash(envelope) == domain_envelope_payload_hash(
        envelope.model_copy(update={"execution_context": _context()})
    )
