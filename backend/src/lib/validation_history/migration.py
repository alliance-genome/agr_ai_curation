"""Explicit, resumable maintenance import before enabling history-aware workers.

Run only after draining workers. This records observed historical projections;
it does not infer prevalidation inputs, executions, or supervisor acceptance.
"""

from sqlalchemy import and_, or_, select

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
        from src.models.sql.validation_history import ValidationCheckpoint
        from .store import _body

        checkpoint = db.get(ValidationCheckpoint, (row.envelope_id, row.revision))
        observed_checkpoint = checkpoint is None
        if observed_checkpoint:
            db.add(
                ValidationCheckpoint(
                    envelope_id=row.envelope_id,
                    revision=row.revision,
                    document_id=str(row.document_id),
                    source_extraction_result_id=row.source_extraction_result_id,
                    body_sha=_body(db, row.envelope_json),
                    provenance="historically_observed_current_revision",
                )
            )
            db.flush()
        envelope = DomainEnvelope.model_validate(row.envelope_json)
        if "validation_history" in envelope.metadata:
            return observed_checkpoint
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


def _require_source_owner(db, row):
    document = db.get(PDFDocument, row.document_id)
    owner = db.get(User, document.user_id) if document is not None and document.user_id is not None else None
    if owner is None or row.user_id != owner.auth_sub:
        raise ValueError("Historical source has no matching authorized owner; manual reconciliation required")


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
        _require_source_owner(db, row)
        # Import the authoritative saved projection when one already exists.
        # Re-normalizing its old source against today's converters can change
        # metadata/hash contracts and must not replace the observed checkpoint.
        source_match = DomainEnvelopeModel.source_extraction_result_id == str(row.id)
        payload = row.payload_json
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        alias = metadata.get("source_extraction_result_id") if isinstance(metadata, dict) else None
        if isinstance(alias, str) and alias and isinstance(payload.get("envelope_id"), str):
            # Older inline checkpoints explicitly named their chat-runtime source
            # before the durable extraction UUID existed. Preserve that exact link.
            source_match = or_(source_match, and_(
                DomainEnvelopeModel.envelope_id == payload["envelope_id"],
                or_(DomainEnvelopeModel.source_extraction_result_id == alias,
                    and_(DomainEnvelopeModel.source_extraction_result_id.is_(None),
                         DomainEnvelopeModel.envelope_json["metadata"]["source_extraction_result_id"].as_string() == alias))))
        saved = list(db.scalars(select(DomainEnvelopeModel).where(source_match)))
        if any(item.document_id != row.document_id or item.adapter_key != row.adapter_key for item in saved):
            raise ValueError("Historical source and saved envelope scope differ")
        saved_ids = [item.envelope_id for item in saved]
        source = _record_to_schema(row) if not saved_ids else None
    if saved_ids:
        changed = False
        for envelope_id in saved_ids:
            changed = import_historical_envelope(envelope_id) or changed
        return changed
    ref = ensure_domain_envelope_materialization(source, persist=True)
    return import_historical_envelope(ref.envelope_id)


def retained_source_reason(agent_key, payload):
    """Recognize known old wire shapes, never infer or convert scientific data.

    Current-shaped and unknown/malformed sources are not exclusions: normal
    validation must still reject them rather than hiding a broken import.
    """
    from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
    from src.schemas.curation_prep import CurationPrepAgentOutput
    from src.schemas.models.domain_envelope_extraction import LEGACY_SEMANTIC_LIST_FIELDS

    if not isinstance(payload, dict) or {"extracted_objects", "curatable_objects"}.intersection(payload):
        return None
    if agent_key == CURATION_PREP_AGENT_ID:
        CurationPrepAgentOutput.model_validate(payload)
        return "curation_preparation_output"
    if (isinstance(payload.get("objects"), list)
            and all(isinstance(payload.get(key), str) for key in ("envelope_id", "domain_pack_id"))):
        return "legacy_objects_envelope"
    if (isinstance(payload.get("run_summary"), dict)
            and isinstance(payload.get("items"), list)
            and any(isinstance(payload.get(key), list) for key in LEGACY_SEMANTIC_LIST_FIELDS - {"items"})):
        return "legacy_specialized_output"
    return None


def retained_extraction_reason(extraction_result_id):
    from uuid import UUID
    from src.lib.curation_workspace.models import CurationExtractionResultRecord
    with SessionLocal() as db:
        row = db.get(CurationExtractionResultRecord, UUID(extraction_result_id))
        if row is None:
            raise ValueError("Historical extraction no longer exists")
        _require_source_owner(db, row)
        reason = retained_source_reason(row.agent_key, row.payload_json)
        if reason is not None:
            return reason
        # Some old preparation runs owned the checkpoint rather than its source
        # extraction. Preserve both records, without guessing/reassigning a link;
        # --all imports the authoritative saved envelope independently below.
        payload = row.payload_json
        envelope_id = payload.get("envelope_id") if isinstance(payload, dict) else None
        saved = db.get(DomainEnvelopeModel, envelope_id) if isinstance(envelope_id, str) else None
        if saved is None or saved.document_id != row.document_id or saved.adapter_key != row.adapter_key:
            return None
        if saved.source_extraction_result_id in {None, str(row.id)}:
            return None
        try:
            preparation_id = UUID(saved.source_extraction_result_id)
        except ValueError:
            return None
        preparation = db.get(CurationExtractionResultRecord, preparation_id)
        if (preparation is None or preparation.document_id != row.document_id
                or preparation.user_id != row.user_id or preparation.adapter_key != row.adapter_key):
            return None
        from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
        from src.schemas.curation_prep import CurationPrepAgentOutput
        if preparation.agent_key != CURATION_PREP_AGENT_ID:
            return None
        prepared = CurationPrepAgentOutput.model_validate(preparation.payload_json)
        source_refs = {str(row.id)}
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("source_extraction_result_id"), str):
            source_refs.add(metadata["source_extraction_result_id"])
        if any(ref.envelope_id == saved.envelope_id and ref.domain_pack_id == saved.domain_pack_key
               and ref.source_extraction_result_id in source_refs for ref in prepared.envelope_refs):
            DomainEnvelope.model_validate(payload)
            return "legacy_preparation_owned_checkpoint"
        return None


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
    parser.add_argument("--retain-legacy-results", action="store_true",
        help="With explicit operator approval, preserve known legacy outputs/preparation summaries unchanged and report every exclusion")
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
            if kind == "extraction" and args.retain_legacy_results:
                reason = retained_extraction_reason(identity)
                if reason is not None:
                    print(json.dumps({"kind": kind, "id": identity, "imported": False,
                        "disposition": "retained_unchanged", "reason": reason}), flush=True)
                    continue
            changed = importer(identity)
            print(
                json.dumps({"kind": kind, "id": identity, "imported": changed}),
                flush=True,
            )


if __name__ == "__main__":
    main()
