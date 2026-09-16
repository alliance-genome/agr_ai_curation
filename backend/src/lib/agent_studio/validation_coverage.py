"""Explicit mapping coverage and auditable extraction-only acknowledgments.

Shape compatibility is not evidence that an arbitrary text field needs database
validation. Applicable fields come from declared mappings and the profile's
immutable mapping history. No species, provider or field-name heuristics.
"""
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.generic_extraction_profile import GenericExtractionProfileRevision
from src.models.sql.validation_acknowledgment import ValidationAcknowledgment
from src.schemas.generic_extraction_profile import GenericProfileContract, canonical_json


class UnvalidatedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    label: str


class ValidationCoverageScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    data_type: str
    unvalidated_fields: list[UnvalidatedField] = Field(min_length=1)
    disabled_checks: list[str] = Field(default_factory=list)
    status: Literal["not_database_validated"] = "not_database_validated"

    def fingerprint(self) -> str:
        return "sha256:" + sha256(canonical_json(self.model_dump(mode="json")).encode()).hexdigest()


class ValidationAcknowledgmentRequired(Exception):
    def __init__(self, scopes: list[ValidationCoverageScope]):
        self.scopes = scopes
        super().__init__("Review fields without database validation and explicitly acknowledge extraction-only operation.")

    def detail(self):
        return {"code": "validation_acknowledgment_required", "message": str(self),
                "scopes": [scope.model_dump(mode="json") for scope in self.scopes]}


def _mapping_fields(mapping) -> set[str]:
    return set(mapping.outputs.values()) | {
        item.field_path for item in mapping.inputs.values()
        if item.source == "field" and item.field_path
    }


def profile_coverage_scope(
    db: Session, contract: GenericProfileContract, *, profile_id: UUID | None,
    disabled_mapping_ids: frozenset[str] = frozenset(), agent_id: UUID | None = None,
) -> ValidationCoverageScope | None:
    """Missing coverage is explicit; unavailable configured mappings are separate."""
    labels = {}

    def fields(items, prefix="attributes"):
        for item in items:
            path = prefix + "." + item.key
            labels[path] = item.display_name or item.key
            schema = item.value_schema
            if schema.kind == "array":
                path += "[]"
                labels[path] = item.display_name or item.key
                schema = schema.items
            if schema.kind == "object":
                fields(schema.fields, path)
    fields(contract.fields)
    expected = set()
    profile_ids = {profile_id} if profile_id is not None else set()
    if agent_id is not None:
        from src.models.sql.agent_execution_revision import AgentExecutionRevision
        profile_ids.update(db.scalars(select(GenericExtractionProfileRevision.profile_id).join(
            AgentExecutionRevision, AgentExecutionRevision.profile_revision_id == GenericExtractionProfileRevision.id,
        ).where(AgentExecutionRevision.agent_id == agent_id).distinct()))
    if profile_ids:
        for raw in db.scalars(select(GenericExtractionProfileRevision.contract).where(
            GenericExtractionProfileRevision.profile_id.in_(profile_ids),
        )):
            previous = GenericProfileContract.model_validate(raw)
            for mapping in previous.validator_mappings:
                expected.update(_mapping_fields(mapping))
    covered = set()
    for mapping in contract.validator_mappings:
        expected.update(_mapping_fields(mapping))
        if mapping.mapping_id not in disabled_mapping_ids:
            covered.update(_mapping_fields(mapping))
    missing = sorted((expected & labels.keys()) - covered)
    if not missing:
        return None
    return ValidationCoverageScope(
        configuration_fingerprint=contract.fingerprint(), data_type=contract.name,
        unvalidated_fields=[UnvalidatedField(path=path, label=labels[path]) for path in missing],
        disabled_checks=sorted(disabled_mapping_ids),
    )


def require_acknowledgments(db: Session, user_id: int, scopes: list[ValidationCoverageScope]) -> None:
    missing = [scope for scope in scopes if db.get(ValidationAcknowledgment, (user_id, scope.fingerprint())) is None]
    if missing:
        raise ValidationAcknowledgmentRequired(missing)


def flow_coverage_scopes(db: Session, definition, *, user_id: int, active_group_ids: list[str]) -> list[ValidationCoverageScope]:
    from src.lib.agent_studio.generic_profile_service import get_profile_revision
    from src.lib.agent_studio.profile_mapping_service import validate_profile_mappings
    from src.lib.flows.validation_attachments import validation_schedule_from_node_data
    scopes = []
    for node in definition.nodes:
        receipt = node.data.execution_receipt
        if receipt is not None and receipt.output_contract.generic_profile_ref is not None:
            pin = receipt.output_contract.generic_profile_ref
            row = get_profile_revision(db, pin.profile_id, pin.revision, user_id, include_archived=True)
            contract = GenericProfileContract.model_validate(row.contract)
            # A selected-but-unavailable validator cannot be acknowledged away.
            validate_profile_mappings(contract, active_group_ids=active_group_ids, user_id=user_id)
            replaced = {group.attachment_id for group in node.data.validation_groups if group.state == "replaced"}
            disabled = frozenset(mapping.mapping_id for mapping in contract.validator_mappings
                if any(not choice.enabled and choice.attachment_id not in replaced and choice.validator_binding_id ==
                       f"profile-{pin.fingerprint.removeprefix('sha256:')}-{mapping.mapping_id}"
                       for choice in node.data.validation_attachments))
            scope = profile_coverage_scope(db, contract, profile_id=pin.profile_id, disabled_mapping_ids=disabled, agent_id=receipt.agent_id)
            if scope is not None:
                scopes.append(scope)
        else:
            schedule = validation_schedule_from_node_data(node.data.model_dump(mode="json"))
            for choice in schedule["opt_outs"]:
                paths = choice.get("affected_fields") or [choice.get("field_path") or choice.get("object_type") or "extracted data"]
                scopes.append(ValidationCoverageScope(
                    configuration_fingerprint="sha256:" + sha256(canonical_json(node.data.model_dump(mode="json")).encode()).hexdigest(),
                    data_type=node.data.agent_display_name,
                    unvalidated_fields=[UnvalidatedField(path=path, label=path) for path in sorted(set(paths))],
                    disabled_checks=[choice["attachment_id"]],
                ))
    return list({scope.fingerprint(): scope for scope in scopes}.values())


def runtime_coverage_metadata(db: Session, definition, *, user_id: int, active_group_ids: list[str]) -> dict[str, list[dict]]:
    """Capture actual configuration and acknowledgment separately from validation."""
    by_node = {}
    for node in definition.nodes:
        scopes = flow_coverage_scopes(db, definition.model_copy(update={"nodes": [node]}),
                                     user_id=user_id, active_group_ids=active_group_ids)
        require_acknowledgments(db, user_id, scopes)
        if scopes:
            by_node[node.id] = [{**scope.model_dump(mode="json"),
                "acknowledgment": {"user_id": user_id, "fingerprint": scope.fingerprint(),
                    "acknowledged_at": db.get(ValidationAcknowledgment, (user_id, scope.fingerprint())).acknowledged_at.isoformat()}}
                for scope in scopes]
    return by_node


def annotate_unvalidated_candidate(candidate, scopes: list[dict]) -> None:
    from src.schemas.domain_envelope import DomainEnvelope, ValidationFinding, ValidationFindingSeverity
    if candidate is None or not scopes:
        return
    candidate.metadata["database_validation_coverage"] = scopes
    payload = candidate.payload_json
    if not isinstance(payload, dict) or "extracted_objects" not in payload:
        return
    envelope = DomainEnvelope.model_validate(payload)
    envelope.metadata["database_validation_coverage"] = scopes
    for obj in envelope.extracted_objects:
        for scope in scopes:
            labels = ", ".join(field["label"] for field in scope["unvalidated_fields"])
            envelope.validation_findings.append(ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                code="not_database_validated",
                message=f"Not database-validated: {labels}. Extraction-only operation was acknowledged; these values remain unverified.",
                object_ref=obj.to_object_ref(), details={"database_validation_coverage": scope},
            ))
    payload.update(envelope.model_dump(mode="json"))
