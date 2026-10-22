"""Explicit, resumable maintenance import before enabling history-aware workers.

Run only after draining workers. This records observed historical projections;
it does not infer prevalidation inputs, executions, or supervisor acceptance.
"""

from sqlalchemy import select

from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    write_domain_envelope_checkpoint,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.domain_envelope import DomainEnvelope

from .runtime import capture_extraction
from .store import HistoryScope


def import_historical_envelope(envelope_id: str) -> bool:
    """One explicit idempotent import; incomplete previous import can be retried."""
    with SessionLocal.begin() as db:
        row = db.scalar(
            select(DomainEnvelopeModel)
            .where(DomainEnvelopeModel.envelope_id == envelope_id)
            .with_for_update()
        )
        if row is None:
            raise ValueError("Historical envelope no longer exists")
        envelope = DomainEnvelope.model_validate(row.envelope_json)
        if "validation_history" in envelope.metadata:
            return False
        document = db.get(PDFDocument, row.document_id)
        owner = (
            db.get(User, document.user_id)
            if document is not None and document.user_id is not None
            else None
        )
        if owner is None:
            raise ValueError(
                "Historical source has no authorized owner; manual reconciliation required"
            )
        scope_id = str(
            row.session_id or row.flow_run_id or row.source_extraction_result_id
        )
        if scope_id == "None":
            raise ValueError("Historical envelope has no source scope")
        envelope = capture_extraction(
            scope=HistoryScope(owner.auth_sub, str(row.document_id), scope_id),
            producer_id="historical:" + row.envelope_id,
            typed=None,
            envelope=envelope,
            provenance={
                "source_revision": row.revision,
                "review_status": "historical_unknown",
                "original_extraction_available": False,
            },
            subject_kind="historical",
        )
        # Known old result references remain intact in the checkpoint and baseline.
        # No result is expanded into an invented number of logical attempts.
        write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key=row.project_key,
                envelope=envelope,
                expected_revision=row.revision,
                document_id=row.document_id,
                session_id=row.session_id,
                flow_run_id=row.flow_run_id,
                adapter_key=row.adapter_key,
                source_extraction_result_id=row.source_extraction_result_id,
                source_payload_hash=row.source_payload_hash,
                execution_receipt=AgentExecutionReceipt.model_validate(
                    row.execution_receipt
                )
                if row.execution_receipt
                else None,
                object_model_ref_json=row.object_model_ref_json,
                model_field_ref_json=row.model_field_ref_json,
            ),
        )
        return True


def import_historical_extraction(extraction_result_id: str) -> bool:
    """Materialize a known stored source, then import its observed checkpoint.

    Never rewrite the source payload: its existing IDs and result refs remain
    usable. Unsupported/ownerless sources stop maintenance for reconciliation.
    """
    from uuid import UUID
    from src.lib.curation_workspace.models import CurationExtractionResultRecord
    from src.lib.curation_workspace.extraction_results import _record_to_schema
    from src.lib.curation_workspace.curation_prep_service import (
        ensure_domain_envelope_materialization,
    )

    with SessionLocal() as db:
        row = db.get(CurationExtractionResultRecord, UUID(extraction_result_id))
        if row is None:
            raise ValueError("Historical extraction no longer exists")
        source = _record_to_schema(row)
    ref = ensure_domain_envelope_materialization(source, persist=True)
    return import_historical_envelope(ref.envelope_id)


def main() -> None:
    import argparse
    import json
    from src.lib.curation_workspace.models import CurationExtractionResultRecord

    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--envelope", action="append")
    targets.add_argument("--extraction", action="append")
    targets.add_argument("--all", action="store_true")
    parser.add_argument("--workers-drained", action="store_true", required=True)
    args = parser.parse_args()
    if args.all:
        # Take a finite ID inventory after the operator drains writers. Processing
        # and retries are per source; no transaction spans the entire inventory.
        with SessionLocal() as db:
            extractions = [
                str(key)
                for key in db.scalars(select(CurationExtractionResultRecord.id))
            ]
            envelopes = list(db.scalars(select(DomainEnvelopeModel.envelope_id)))
    else:
        extractions, envelopes = args.extraction or [], args.envelope or []
    for kind, ids, importer in (
        ("extraction", extractions, import_historical_extraction),
        ("envelope", envelopes, import_historical_envelope),
    ):
        for identity in ids:
            changed = importer(identity)
            print(
                json.dumps({"kind": kind, "id": identity, "imported": changed}),
                flush=True,
            )


if __name__ == "__main__":
    main()
