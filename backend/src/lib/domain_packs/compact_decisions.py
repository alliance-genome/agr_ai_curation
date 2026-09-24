"""Request-scoped scientific decisions and authoritative result assembly.

Provider adapters register canonical records and actual lookup attempts. The
model selects references and supplies assessments; it never supplies request
identity, lookup counts, or replacement copies of provider records.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Literal, Mapping
from uuid import uuid4

from pydantic import Field, JsonValue, StrictStr, TypeAdapter

from src.schemas.domain_validator import (
    DomainValidationRequest, DomainValidatorBaseModel, DomainValidatorResultBase,
    ValidatorCandidate, ValidatorLookupAttempt,
)


class CandidateAssessment(DomainValidatorBaseModel):
    record_ref: StrictStr
    disposition: Literal["selected", "plausible", "excluded"]
    explanation: StrictStr
    evidence_record_ids: list[StrictStr] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0, le=1)
    matched_fields: dict[str, JsonValue] = Field(default_factory=dict)


class RecordValue(DomainValidatorBaseModel):
    kind: Literal["record"]
    record_ref: StrictStr
    field: StrictStr


class ScientificValue(DomainValidatorBaseModel):
    kind: Literal["scientific"]
    value: JsonValue
    explanation: StrictStr
    evidence_record_ids: list[StrictStr] = Field(default_factory=list)


SlotValue = Annotated[RecordValue | ScientificValue, Field(discriminator="kind")]


class CompactValidatorDecision(DomainValidatorBaseModel):
    """Scientific output only; canonical result identity comes from the request."""

    request_id: StrictStr
    status: Literal["resolved", "unresolved"]
    candidates: list[CandidateAssessment] = Field(default_factory=list)
    slots: dict[str, SlotValue] = Field(default_factory=dict)
    explanation: StrictStr
    curator_message: StrictStr | None = None
    unresolved_questions: list[StrictStr] = Field(default_factory=list)


@dataclass(frozen=True)
class CanonicalValidatorRecord:
    """Package adapter output; all fields must derive from this source record."""

    candidate: ValidatorCandidate
    values: Mapping[str, Any]
    result_rows: Mapping[str, Any] = field(default_factory=dict)
    resolved_object: Mapping[str, Any] | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class DecisionContract:
    request: DomainValidationRequest
    result_schema: type[DomainValidatorResultBase] = DomainValidatorResultBase
    profile_mapped: bool = False
    # Explicitly model-owned slots, with their existing declared value types.
    # Every other slot can only copy a registered authoritative record value.
    scientific_slots: Mapping[str, TypeAdapter] = field(default_factory=dict)
    # Package-declared equivalent factual fields. Without a declaration, a
    # slot can only copy its namesake, never an arbitrary same-typed field.
    record_slot_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    selected_result_fields: frozenset[str] = frozenset()
    decision_schema: type[CompactValidatorDecision] = CompactValidatorDecision
    # Package-specific scientific contracts (component/policy decisions) remain
    # typed; their assembler may add domain fields but not silently replace the
    # runtime identity or lookup audit.
    assemble_domain: Callable[[dict[str, Any], CompactValidatorDecision, "ValidatorDecisionWorkspace"], dict[str, Any]] | None = None
    # Package-owned request-specific decision shape, not copied provider facts.
    domain_contract: Mapping[str, Any] = field(default_factory=dict)


class ValidatorDecisionWorkspace:
    """An invocation-local registry; references cannot resolve in another run."""

    def __init__(self, contracts: list[DecisionContract]):
        self._contracts = {item.request.request_id: item for item in contracts}
        if len(self._contracts) != len(contracts):
            raise ValueError("Duplicate validator request IDs")
        self._scope = uuid4().hex
        self._records: dict[str, tuple[str, str, CanonicalValidatorRecord]] = {}
        self._lookups: dict[str, list[tuple[str, ValidatorLookupAttempt]]] = {
            request_id: [] for request_id in self._contracts
        }
        self._source_payloads: dict[str, dict[str, Mapping[str, Any]]] = {
            request_id: {} for request_id in self._contracts
        }

    def record_lookup(
        self, request_id: str, *, call_id: str,
        attempt: ValidatorLookupAttempt, records: list[CanonicalValidatorRecord],
        source_payload: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Register a concrete call once, before any presentation compaction."""
        self._contract(request_id)
        if any(existing == call_id for existing, _ in self._lookups[request_id]):
            raise ValueError("Lookup call already registered for this request")
        if not call_id:
            raise ValueError("Lookup call identity is required")
        self._lookups[request_id].append((call_id, attempt.model_copy(deep=True)))
        self._source_payloads[request_id][call_id] = deepcopy(source_payload or {})
        return self.record_sources(request_id, source_id=call_id, records=records)

    def record_sources(
        self, request_id: str, *, source_id: str,
        records: list[CanonicalValidatorRecord],
    ) -> list[str]:
        """Register authoritative supplied-context records without inventing a lookup."""
        self._contract(request_id)
        if not source_id:
            raise ValueError("Source identity is required")
        references = []
        for record in records:
            reference = f"vr:{self._scope}:{len(self._records)}"
            self._records[reference] = (request_id, source_id, deepcopy(record))
            references.append(reference)
        return references

    def source_payloads(self, request_id: str) -> dict[str, Mapping[str, Any]]:
        """Return detached provider facts for a package-owned domain assembler."""
        self._contract(request_id)
        return deepcopy(self._source_payloads[request_id])

    def record(self, request_id: str, reference: str) -> CanonicalValidatorRecord:
        """Read a detached canonical record within exactly one request."""
        _, record = self._record(request_id, reference)
        return deepcopy(record)

    def records(self, request_id: str) -> list[CanonicalValidatorRecord]:
        self._contract(request_id)
        return [deepcopy(record) for owner, _call_id, record in self._records.values() if owner == request_id]

    def lookup_attempts(self, request_id: str) -> list[ValidatorLookupAttempt]:
        self._contract(request_id)
        return [attempt.model_copy(deep=True) for _, attempt in self._lookups[request_id]]

    def lookup_attempts_for(self, request_id: str, call_ids: list[str]) -> list[ValidatorLookupAttempt]:
        self._contract(request_id)
        known = dict(self._lookups[request_id])
        if len(call_ids) != len(set(call_ids)) or any(call_id not in known for call_id in call_ids):
            raise ValueError("Unknown, foreign, or duplicate lookup reference")
        return [known[call_id].model_copy(deep=True) for call_id in call_ids]

    def source_call_id(self, request_id: str, reference: str) -> str:
        call_id, _ = self._record(request_id, reference)
        return call_id

    def _contract(self, request_id: str) -> DecisionContract:
        if request_id not in self._contracts:
            raise ValueError("Unknown validator request")
        return self._contracts[request_id]

    def _record(self, request_id: str, reference: str) -> tuple[str, CanonicalValidatorRecord]:
        entry = self._records.get(reference)
        if entry is None or entry[0] != request_id:
            raise ValueError("Unknown or foreign validator record reference")
        return entry[1], entry[2]

    def _check_evidence(self, request: DomainValidationRequest, ids: list[str]) -> None:
        known = {item.get("evidence_record_id") for item in request.evidence}
        known.update(
            item.get("evidence_record_id")
            for item in request.selected_inputs.get("evidence_quotes", [])
            if isinstance(item, Mapping)
        )
        if any(identifier not in known for identifier in ids):
            raise ValueError("Evidence reference is outside this validator request")

    def assemble(self, decision: CompactValidatorDecision) -> DomainValidatorResultBase:
        contract = self._contract(decision.request_id)
        decision = contract.decision_schema.model_validate(decision.model_dump())
        request = contract.request
        seen = set()
        candidates = []
        objects = []
        rows: dict[str, list[Any]] = {}
        selected = set()
        for assessment in decision.candidates:
            if assessment.record_ref in seen:
                raise ValueError("Duplicate candidate reference")
            seen.add(assessment.record_ref)
            call_id, record = self._record(request.request_id, assessment.record_ref)
            self._check_evidence(request, assessment.evidence_record_ids)
            if assessment.disposition == "selected":
                selected.add(assessment.record_ref)
                if not contract.profile_mapped and record.resolved_object is not None:
                    objects.append(deepcopy(dict(record.resolved_object)))
            candidate = record.candidate.model_copy(deep=True)
            candidate.score = assessment.score
            candidate.matched_fields = deepcopy(assessment.matched_fields)
            candidate.details = {
                **candidate.details,
                "scientific_assessment": assessment.model_dump(exclude={"record_ref", "score", "matched_fields"}),
                "source_call_id": call_id,
                "source_record_ref": assessment.record_ref,
                "source_path": record.source_path,
            }
            candidates.append(candidate)
            for result_field, row in record.result_rows.items():
                if result_field in DomainValidatorResultBase.model_fields:
                    raise ValueError("Record adapter cannot overwrite canonical base fields")
                if result_field in contract.selected_result_fields and assessment.disposition != "selected":
                    continue
                rows.setdefault(result_field, []).append(deepcopy(row))
        values = {}
        for slot, selection in decision.slots.items():
            # Optional slots are filled only when the record confirms them; never required.
            if slot not in request.expected_result_fields and slot not in (request.optional_result_fields or {}):
                raise ValueError(f"Unexpected result slot: {slot}")
            if isinstance(selection, RecordValue):
                _, record = self._record(request.request_id, selection.record_ref)
                if selection.record_ref not in selected:
                    raise ValueError("A resolved value must reference a selected candidate")
                if selection.field not in record.values:
                    raise ValueError(f"Authoritative record does not supply field: {selection.field}")
                if selection.field not in contract.record_slot_fields.get(slot, (slot,)):
                    raise ValueError(f"Authoritative field is not a declared source for result slot: {slot}")
                values[slot] = deepcopy(record.values[selection.field])
            else:
                adapter = contract.scientific_slots.get(slot)
                if adapter is None:
                    raise ValueError(f"Result slot requires an authoritative record: {slot}")
                self._check_evidence(request, selection.evidence_record_ids)
                values[slot] = adapter.validate_python(selection.value, strict=True)
        missing = [name for name in request.expected_result_fields
                   if name not in values or values[name] is None or values[name] == ""]
        explanation = decision.explanation
        if decision.unresolved_questions:
            explanation += "\nUnresolved questions: " + "; ".join(decision.unresolved_questions)
        # Slot reasoning is scientific information, not disposable assembly metadata.
        for name, selection in decision.slots.items():
            if isinstance(selection, ScientificValue):
                explanation += f"\n{name}: {selection.explanation}"
                if selection.evidence_record_ids:
                    explanation += " [" + ", ".join(selection.evidence_record_ids) + "]"
        payload = {
            "status": decision.status,
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
            "resolved_values": values,
            "resolved_objects": objects,
            "missing_expected_fields": missing,
            "candidates": candidates,
            "lookup_attempts": [attempt.model_copy(deep=True) for _, attempt in self._lookups[request.request_id]],
            "curator_message": decision.curator_message,
            "explanation": explanation,
            **rows,
        }
        if contract.assemble_domain is not None:
            additions = contract.assemble_domain(deepcopy(payload), decision, self)
            protected = {"request_id", "validator_binding_id", "validator_agent", "target"}
            if protected.intersection(additions):
                raise ValueError("Domain assembler cannot replace runtime identity or lookup audit")
            payload.update(additions)
        # Domain assembly may deterministically supply composite fields. Check
        # completeness only after those fields exist, never from model echoes.
        # A composite result that decides each value itself (field_resolutions)
        # reports the missing fields of the values it decided; values it did
        # not decide are not written, so their expected fields are not missing.
        if not payload.get("field_resolutions"):
            payload["missing_expected_fields"] = [
                name for name in request.expected_result_fields
                if name not in payload["resolved_values"]
                or payload["resolved_values"][name] is None or payload["resolved_values"][name] == ""
            ]
        if payload["status"] == "resolved" and (payload["missing_expected_fields"] or decision.unresolved_questions):
            raise ValueError("Resolved decision still has missing fields or unresolved questions")
        result = contract.result_schema.model_validate(payload, context={"domain_validation_request": request})
        if contract.assemble_domain is not None and result.field_resolutions:
            result._assembled_field_completeness = True
        return result
