"""Commit a retryable manual source without committing the caller's draft edits."""
from dataclasses import dataclass

from src.models.sql.database import SessionLocal
from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    lock_projection_artifact, write_domain_envelope_checkpoint,
)
from src.lib.validation_history.store import HistoryConflict


@dataclass(frozen=True)
class PreparedManualSource:
    envelope_id: str
    revision: int


def prepare_manual_source(*, envelope, receipt, document_id, session_id, flow_run_id, adapter_key):
    """Save only source/checkpoint/index data; the caller owns candidate linkage.

    Identity includes the immutable source hash. Repeating the exact preparation
    adopts it; a changed draft has a distinct identity. Unlinked preparations are
    excluded from workspace listing, inspection and benchmark snapshot exports.
    """
    with SessionLocal.begin() as db:
        lock_projection_artifact(db, envelope.metadata["validation_history"]["artifact_id"])
        row = db.get(DomainEnvelopeModel, envelope.envelope_id)
        if row is not None:
            saved = row.envelope_json
            if (str(row.document_id) != str(document_id) or str(row.session_id) != str(session_id)
                    or row.adapter_key != adapter_key or row.flow_run_id != flow_run_id
                    or row.execution_receipt != receipt.model_dump(mode="json")
                    or saved["metadata"].get("manual_preparation") != envelope.metadata["manual_preparation"]
                    or saved["metadata"].get("validation_history") != envelope.metadata["validation_history"]):
                raise HistoryConflict("Prepared manual source does not match the authenticated source")
            # A failed request rolls back its linkage and pending checkpoint. It
            # must not adopt a different or already-mutated source as revision 1.
            if row.revision != 1 or saved != envelope.model_dump(mode="json"):
                raise HistoryConflict("Prepared manual source changed before retry")
            return PreparedManualSource(row.envelope_id, row.revision)
        checkpoint = write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(
            project_key=envelope.domain_pack_id, envelope=envelope, expected_revision=0,
            execution_receipt=receipt, document_id=document_id, session_id=session_id,
            flow_run_id=flow_run_id, adapter_key=adapter_key,
        ))
        return PreparedManualSource(checkpoint.envelope_id, checkpoint.revision)
