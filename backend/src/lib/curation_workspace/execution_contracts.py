"""Closed output contracts at persistence and curation mutation boundaries.

Callers own access authorization. These checks use only the explicit immutable
profile revision in the execution receipt; they never select a current head.
The producing agent/revision pair is also enforced by the database receipt FK.
"""
from copy import deepcopy
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from src.lib.agent_studio.profile_conformance import ProfileIdentityError, ResolvedGenericProfile
from src.models.sql.generic_extraction_profile import GenericExtractionProfileRevision
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.domain_envelope import DomainEnvelope, parse_field_path
from src.schemas.generic_extraction_profile import normalize_profile_contract
from src.lib.curation_workspace.models import CurationReviewSession, CurationCandidate, DomainEnvelopeModel
from src.lib.curation_workspace.session_types import PreparedDraftFieldInput
from src.schemas.curation_workspace import CurationDraftField


def profiled_draft_payload(
    db: Session,
    receipt: AgentExecutionReceipt,
    fields: Sequence[PreparedDraftFieldInput] | Sequence[CurationDraftField],
    *,
    base_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Validate a canonical record assembled from noncoercing draft values.

    Partial envelope projections retain nonprojected source fields. Manual
    records start from the saved fixed identity, never another candidate's data.
    """
    # Domain-envelope package initialization also imports persistence, whose
    # write gate calls this module. Load the path helper only after startup.
    from src.lib.domain_envelopes.patches import set_payload_value

    profile = resolve_receipt_profile(db, receipt)
    if profile is None:
        return None
    payload: dict[str, Any] = deepcopy(base_payload) if base_payload is not None else {
        "class_key": "generic:generic_object",
        "object_type": "generic_object",
        "semantic_class": profile.contract.semantic_class,
    }
    paths: list[tuple[str | int, ...]] = []
    for field in sorted(fields, key=lambda item: (item.order, item.field_key)):
        path = field.metadata.get("source_field_path", field.field_key)
        if not isinstance(path, str):
            raise ProfileIdentityError("Candidate fields require canonical field paths")
        try:
            parts = tuple(parse_field_path(path))
            if any(parts[:len(old)] == old or old[:len(parts)] == parts for old in paths):
                raise ProfileIdentityError("Candidate fields cannot duplicate or overlap another field path")
            paths.append(parts)
            set_payload_value(payload, path, deepcopy(field.value))
        except ValueError as exc:
            raise ProfileIdentityError(str(exc)) from exc
    profile.require_candidate(payload)
    return payload


def require_candidate_conformance(db: Session, candidate: CurationCandidate) -> None:
    """Check source and current draft before validation-cache reuse or export."""
    if candidate.execution_receipt is None:
        return  # Historical/system candidates have no invented execution identity.
    receipt = AgentExecutionReceipt.model_validate(candidate.execution_receipt)
    if receipt.agent_revision_id != candidate.agent_revision_id:
        raise ProfileIdentityError("Candidate revision does not match its execution receipt")
    if receipt.output_contract.output_state != "structured_extraction":
        raise ProfileIdentityError("Candidate source revision does not produce structured extraction")
    profile = resolve_receipt_profile(db, receipt)
    if profile is None:
        return
    base_payload = None
    if candidate.envelope_id is not None:
        row = db.get(DomainEnvelopeModel, candidate.envelope_id)
        if row is None or row.session_id != candidate.session_id or row.execution_receipt != candidate.execution_receipt:
            raise ProfileIdentityError("Candidate source envelope does not match its saved execution receipt")
        require_extraction_conformance(db, receipt, row.envelope_json, agent_key=receipt.agent_key)
        envelope = DomainEnvelope.model_validate(row.envelope_json)
        obj = next((item for item in envelope.extracted_objects
                    if candidate.object_id is not None and candidate.object_id in (item.object_id, item.pending_ref_id)), None)
        if obj is None:
            raise ProfileIdentityError("Candidate object is missing from its source envelope")
        base_payload = {
            **obj.payload,
            "object_type": obj.object_type,
            "class_key": obj.payload.get("class_key", "generic:generic_object"),
        }
    else:
        profile.require_candidate(candidate.normalized_payload or {}, candidate_id=str(candidate.id))
    if candidate.draft is None:
        raise ProfileIdentityError("Profile-bound candidate is missing its draft")
    profiled_draft_payload(
        db, receipt, [CurationDraftField.model_validate(field) for field in candidate.draft.fields or []],
        base_payload=base_payload,
    )


def resolve_manual_candidate_receipt(
    session: CurationReviewSession, selected_revision_id: UUID | None,
) -> AgentExecutionReceipt | None:
    """Select only an existing session source, never a mutable agent head."""
    sources = session.execution_revisions
    if selected_revision_id is None:
        if not sources:
            return None
        if len(sources) != 1:
            raise ProfileIdentityError("Select the saved source revision for this manual candidate")
        source = sources[0]
    else:
        source = next((row for row in sources if row.agent_revision_id == selected_revision_id), None)
        if source is None:
            raise ProfileIdentityError("The selected revision is not a saved source of this session")
    receipt = AgentExecutionReceipt.model_validate(source.execution_receipt)
    if receipt.agent_revision_id != source.agent_revision_id:
        raise ProfileIdentityError("Session source revision does not match its execution receipt")
    if receipt.output_contract.output_state != "structured_extraction":
        raise ProfileIdentityError("The selected revision does not produce structured extraction")
    return receipt


def resolve_receipt_profile(db: Session, receipt: AgentExecutionReceipt) -> ResolvedGenericProfile | None:
    pin = receipt.output_contract.generic_profile_ref
    if pin is None:
        return None
    row = db.get(GenericExtractionProfileRevision, pin.profile_revision_id)
    if row is None or (row.profile_id, row.revision, row.fingerprint) != (pin.profile_id, pin.revision, pin.fingerprint):
        raise ProfileIdentityError("The exact saved output structure is unavailable or does not match its receipt")
    return ResolvedGenericProfile(pin, normalize_profile_contract(row.contract))


def require_extraction_conformance(
    db: Session, receipt: AgentExecutionReceipt | None, payload: Any, *, agent_key: str,
) -> None:
    if receipt is None:
        if agent_key.startswith("ca_"):
            raise ProfileIdentityError("New custom-agent extraction requires its exact execution receipt")
        return
    if receipt.agent_key != agent_key or not agent_key.startswith("ca_"):
        raise ProfileIdentityError("Extraction producer does not match its execution receipt")
    if receipt.output_contract.output_state != "structured_extraction":
        raise ProfileIdentityError("This saved agent revision does not produce structured extraction")
    profile = resolve_receipt_profile(db, receipt)
    if profile is None:
        return
    require_resolved_profile_conformance(profile, receipt, payload)


def load_receipt_profile(receipt: AgentExecutionReceipt) -> ResolvedGenericProfile | None:
    """Load an exact contract for authorized callers without retaining a DB session."""
    from src.models.sql.database import SessionLocal
    with SessionLocal() as db:
        return resolve_receipt_profile(db, receipt)


def require_resolved_profile_conformance(
    profile: ResolvedGenericProfile, receipt: AgentExecutionReceipt, payload: Any,
) -> None:
    pin = receipt.output_contract.generic_profile_ref
    if pin is None:
        raise ProfileIdentityError("A profile-bound projection requires its saved profile reference")
    profile.require_receipt(pin.model_dump(mode="json"))
    if not isinstance(payload, dict):
        raise ProfileIdentityError("Profile-bound extraction requires an envelope")
    if "extracted_objects" in payload:
        # Read-only view of the canonical post-conversion representation. Keep
        # the original semantic dictionaries so validation cannot coerce values.
        DomainEnvelope.model_validate(payload)
        if "curatable_objects" in payload:
            raise ProfileIdentityError("Extraction cannot contain two semantic object lists")
        view = {
            "curatable_objects": payload["extracted_objects"],
            "metadata": payload.get("metadata", {}).get("extraction_metadata", {}),
        }
    else:
        view = payload
    profile.require_envelope(view, execution_receipt=receipt.model_dump(mode="json"), agent_key=receipt.agent_key)
