"""Run-scoped workspace for staged extraction builder candidates."""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .evidence_summary import (
    canonicalize_structured_result_payload,
    extract_evidence_records_from_structured_result,
)
from .event_types import INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
from .extraction_trace_events import write_extraction_trace_event

BUILDER_STATE_ACTIVE = "active"
BUILDER_STATE_FINALIZED = "finalized"
BUILDER_STATE_VALIDATION_FAILED = "validation_failed"
BUILDER_STATE_CANCELLED = "cancelled"
BUILDER_STATE_ABORTED = "aborted"

CANDIDATE_STATUS_DRAFT = "draft"
CANDIDATE_STATUS_VALID = "valid"
CANDIDATE_STATUS_NEEDS_PATCH = "needs_patch"
CANDIDATE_STATUS_DISCARDED = "discarded"
CANDIDATE_STATUS_FINALIZED = "finalized"


class ExtractionBuilderError(RuntimeError):
    """Base class for extraction builder workspace failures."""


class ExtractionBuilderFinalizedError(ExtractionBuilderError):
    """Raised when a finalized workspace receives a mutation."""


class ExtractionBuilderFinalizationConflict(ExtractionBuilderError):
    """Raised when duplicate finalization changes candidate membership."""


class ExtractionBuilderValidationError(ExtractionBuilderError):
    """Raised when finalization is attempted with validation errors."""


_ACTIVE_EXTRACTION_BUILDER_WORKSPACE: ContextVar[
    "ExtractionBuilderWorkspace | None"
] = ContextVar(
    "active_extraction_builder_workspace",
    default=None,
)


@dataclass
class ExtractionBuilderCandidate:
    """One staged extraction candidate owned by the backend builder."""

    candidate_id: str
    staged_fields: dict[str, Any] = field(default_factory=dict)
    pending_ref_ids: list[str] = field(default_factory=list)
    evidence_record_ids: list[str] = field(default_factory=list)
    resolver_selection_refs: list[str] = field(default_factory=list)
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    status: str = CANDIDATE_STATUS_DRAFT
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())

    def snapshot(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "pending_ref_ids": list(self.pending_ref_ids),
            "evidence_record_ids": list(self.evidence_record_ids),
            "resolver_selection_refs": list(self.resolver_selection_refs),
            "validation_errors": deepcopy(self.validation_errors),
            "staged_fields": deepcopy(self.staged_fields),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ExtractionBuilderFinalization:
    """Canonical payload and summary produced by builder finalization."""

    status: str
    payload: dict[str, Any]
    candidate_ids: tuple[str, ...]
    finalized_candidate_count: int
    validation_errors: tuple[dict[str, Any], ...]
    evidence_record_ids: tuple[str, ...]
    resolver_selection_count: int
    builder_run_id: str

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "finalized_candidate_count": self.finalized_candidate_count,
            "validation_errors": [dict(error) for error in self.validation_errors],
            "evidence_record_ids": list(self.evidence_record_ids),
            "resolver_selection_count": self.resolver_selection_count,
            "builder_run_id": self.builder_run_id,
            "candidate_ids": list(self.candidate_ids),
        }


class ExtractionBuilderWorkspace:
    """Run-scoped, domain-pack-agnostic owner for extraction assembly state."""

    def __init__(
        self,
        *,
        run_id: str,
        document_id: str | None = None,
        domain_pack_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.run_id = _optional_string(run_id) or _new_builder_run_id()
        self.document_id = _optional_string(document_id)
        self.domain_pack_id = _optional_string(domain_pack_id)
        self.agent_id = _optional_string(agent_id)
        self.state = BUILDER_STATE_ACTIVE
        self.candidates: dict[str, ExtractionBuilderCandidate] = {}
        self.validation_errors: list[dict[str, Any]] = []
        self.finalization: ExtractionBuilderFinalization | None = None
        self.finalized_candidate_ids: tuple[str, ...] = ()
        self.created_at = _now_iso()
        self.updated_at = self.created_at

    def upsert_candidate(
        self,
        *,
        candidate_id: str,
        staged_fields: Mapping[str, Any],
        pending_ref_ids: Iterable[Any] | None = None,
        evidence_record_ids: Iterable[Any] | None = None,
        resolver_selection_refs: Iterable[Any] | None = None,
        status: str = CANDIDATE_STATUS_DRAFT,
    ) -> ExtractionBuilderCandidate:
        self._ensure_mutable()
        normalized_candidate_id = _required_string(candidate_id, "candidate_id")
        candidate = self.candidates.get(normalized_candidate_id)
        now = _now_iso()
        if candidate is None:
            candidate = ExtractionBuilderCandidate(candidate_id=normalized_candidate_id)
            self.candidates[normalized_candidate_id] = candidate
        candidate.staged_fields = deepcopy(dict(staged_fields))
        candidate.pending_ref_ids = _string_list(pending_ref_ids)
        candidate.evidence_record_ids = _string_list(evidence_record_ids)
        candidate.resolver_selection_refs = _string_list(resolver_selection_refs)
        candidate.status = status
        candidate.updated_at = now
        self.updated_at = now
        self._emit(
            "extraction_builder.candidate_mutation",
            output_summary={
                "action": "upsert",
                "candidate": candidate.snapshot(),
            },
        )
        return candidate

    def apply_scope_metadata(
        self,
        *,
        document_id: str | None = None,
        domain_pack_id: str | None = None,
    ) -> None:
        self._ensure_mutable()
        self._ensure_scope_value("document_id", document_id)
        self._ensure_scope_value("domain_pack_id", domain_pack_id)

    def discard_candidate(self, candidate_id: str, *, reason: str | None = None) -> None:
        self._ensure_mutable()
        candidate = self._candidate(candidate_id)
        candidate.status = CANDIDATE_STATUS_DISCARDED
        candidate.updated_at = _now_iso()
        self.updated_at = candidate.updated_at
        self._emit(
            "extraction_builder.candidate_mutation",
            output_summary={
                "action": "discard",
                "candidate_id": candidate.candidate_id,
                "reason": reason,
            },
        )

    def record_validation_failure(
        self,
        *,
        errors: Iterable[Mapping[str, Any]],
        candidate_ids: Iterable[str] | None = None,
    ) -> None:
        self._ensure_mutable()
        normalized_errors = [dict(error) for error in errors]
        if not normalized_errors:
            return
        target_ids = tuple(candidate_ids or self.candidates.keys())
        for candidate_id in target_ids:
            candidate = self.candidates.get(candidate_id)
            if candidate is None:
                continue
            candidate.status = CANDIDATE_STATUS_NEEDS_PATCH
            candidate.validation_errors = deepcopy(normalized_errors)
            candidate.updated_at = _now_iso()
        self.validation_errors = deepcopy(normalized_errors)
        self.state = BUILDER_STATE_VALIDATION_FAILED
        self.updated_at = _now_iso()
        self._emit(
            "extraction_builder.validation_failure",
            validation={"status": "failed", "errors": normalized_errors},
            output_summary=self.snapshot(redact_payload=False),
        )

    def mark_cancelled(self, *, reason: str | None = None) -> None:
        self._mark_terminal(BUILDER_STATE_CANCELLED, reason=reason)

    def mark_aborted(self, *, reason: str | None = None) -> None:
        self._mark_terminal(BUILDER_STATE_ABORTED, reason=reason)

    def finalize(
        self,
        *,
        candidate_ids: Iterable[str],
        validation_errors: Iterable[Mapping[str, Any]] | None = None,
    ) -> ExtractionBuilderFinalization:
        normalized_candidate_ids = tuple(
            _unique_strings(_required_string(value, "candidate_id") for value in candidate_ids)
        )
        if not normalized_candidate_ids:
            raise ValueError("At least one candidate_id is required for builder finalization")

        if self.finalization is not None:
            if set(normalized_candidate_ids) == set(self.finalized_candidate_ids):
                self._emit(
                    "extraction_builder.finalization_decision",
                    output_summary={
                        "decision": "duplicate_idempotent",
                        "finalization": self.finalization.summary(),
                    },
                )
                return self.finalization
            raise ExtractionBuilderFinalizationConflict(
                "Builder run already finalized with different candidate membership "
                f"(existing={list(self.finalized_candidate_ids)}, requested={list(normalized_candidate_ids)})."
            )

        if self.state in {BUILDER_STATE_CANCELLED, BUILDER_STATE_ABORTED}:
            raise ExtractionBuilderError(f"Cannot finalize builder workspace after {self.state}.")

        normalized_errors = [dict(error) for error in validation_errors or []]
        if normalized_errors:
            self.record_validation_failure(
                errors=normalized_errors,
                candidate_ids=normalized_candidate_ids,
            )
            raise ExtractionBuilderValidationError("Builder finalization failed validation.")

        selected = [self._candidate(candidate_id) for candidate_id in normalized_candidate_ids]
        payload = _assemble_payload(selected)
        evidence_record_ids = _unique_strings(
            evidence_id
            for candidate in selected
            for evidence_id in candidate.evidence_record_ids
        )
        resolver_selection_refs = _unique_strings(
            ref
            for candidate in selected
            for ref in candidate.resolver_selection_refs
        )
        for candidate in selected:
            candidate.status = CANDIDATE_STATUS_FINALIZED
            candidate.updated_at = _now_iso()

        self.state = BUILDER_STATE_FINALIZED
        self.finalized_candidate_ids = normalized_candidate_ids
        self.updated_at = _now_iso()
        self.finalization = ExtractionBuilderFinalization(
            status=BUILDER_STATE_FINALIZED,
            payload=payload,
            candidate_ids=normalized_candidate_ids,
            finalized_candidate_count=len(selected),
            validation_errors=(),
            evidence_record_ids=tuple(evidence_record_ids),
            resolver_selection_count=len(resolver_selection_refs),
            builder_run_id=self.run_id,
        )
        self._emit(
            "extraction_builder.finalization_decision",
            output_summary={
                "decision": "finalized",
                "finalization": self.finalization.summary(),
                "payload": self.finalization.payload,
            },
        )
        return self.finalization

    def snapshot(self, *, redact_payload: bool = True) -> dict[str, Any]:
        candidates = [candidate.snapshot() for candidate in self.candidates.values()]
        if redact_payload:
            for candidate in candidates:
                candidate["staged_fields"] = {
                    "keys": sorted(candidate.get("staged_fields", {}).keys()),
                    "field_count": len(candidate.get("staged_fields", {})),
                }
        return {
            "run_id": self.run_id,
            "document_id": self.document_id,
            "domain_pack_id": self.domain_pack_id,
            "agent_id": self.agent_id,
            "state": self.state,
            "candidate_count": len(self.candidates),
            "candidate_ids": list(self.candidates.keys()),
            "pending_ref_ids": _unique_strings(
                ref
                for candidate in self.candidates.values()
                for ref in candidate.pending_ref_ids
            ),
            "evidence_record_ids": _unique_strings(
                evidence_id
                for candidate in self.candidates.values()
                for evidence_id in candidate.evidence_record_ids
            ),
            "resolver_selection_refs": _unique_strings(
                ref
                for candidate in self.candidates.values()
                for ref in candidate.resolver_selection_refs
            ),
            "validation_errors": deepcopy(self.validation_errors),
            "candidates": candidates,
            "finalization": self.finalization.summary() if self.finalization else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def _candidate(self, candidate_id: str) -> ExtractionBuilderCandidate:
        normalized_candidate_id = _required_string(candidate_id, "candidate_id")
        try:
            return self.candidates[normalized_candidate_id]
        except KeyError as exc:
            raise KeyError(f"Unknown extraction builder candidate: {normalized_candidate_id}") from exc

    def _ensure_mutable(self) -> None:
        if self.finalization is not None or self.state == BUILDER_STATE_FINALIZED:
            raise ExtractionBuilderFinalizedError(
                "Extraction builder workspace is finalized; candidate mutations are rejected."
            )
        if self.state in {BUILDER_STATE_CANCELLED, BUILDER_STATE_ABORTED}:
            raise ExtractionBuilderError(
                f"Extraction builder workspace is {self.state}; candidate mutations are rejected."
            )

    def _ensure_scope_value(self, field_name: str, value: str | None) -> None:
        normalized = _optional_string(value)
        if normalized is None:
            return
        current = getattr(self, field_name)
        if current is None:
            setattr(self, field_name, normalized)
            self.updated_at = _now_iso()
            return
        if current != normalized:
            raise ExtractionBuilderError(
                f"Extraction builder workspace {field_name} conflict "
                f"(existing={current!r}, requested={normalized!r})."
            )

    def _mark_terminal(self, state: str, *, reason: str | None = None) -> None:
        if self.state == BUILDER_STATE_FINALIZED:
            raise ExtractionBuilderFinalizedError(
                "Extraction builder workspace is finalized; cancellation/abort is rejected."
            )
        self.state = state
        self.updated_at = _now_iso()
        self._emit(
            f"extraction_builder.{state}",
            output_summary={
                "state": state,
                "reason": reason,
                "workspace": self.snapshot(),
            },
        )

    def _emit(
        self,
        event_type: str,
        *,
        output_summary: Any = None,
        validation: Mapping[str, Any] | None = None,
    ) -> None:
        write_extraction_trace_event(
            event_type=event_type,
            trace_id=self.run_id,
            domain_pack_id=self.domain_pack_id,
            output_summary=output_summary if output_summary is not None else self.snapshot(),
            validation=validation,
            metadata={
                "builder_run_id": self.run_id,
                "document_id": self.document_id,
                "agent_id": self.agent_id,
                "builder_state": self.state,
            },
        )


def set_active_extraction_builder_workspace(
    workspace: ExtractionBuilderWorkspace | None,
) -> Token[ExtractionBuilderWorkspace | None]:
    return _ACTIVE_EXTRACTION_BUILDER_WORKSPACE.set(workspace)


def reset_active_extraction_builder_workspace(
    token: Token[ExtractionBuilderWorkspace | None],
) -> None:
    _ACTIVE_EXTRACTION_BUILDER_WORKSPACE.reset(token)


def get_active_extraction_builder_workspace() -> ExtractionBuilderWorkspace:
    workspace = _ACTIVE_EXTRACTION_BUILDER_WORKSPACE.get()
    if workspace is None:
        raise RuntimeError("No active extraction builder workspace is bound to this run")
    return workspace


def finalize_extraction_payload(
    payload: Mapping[str, Any],
    *,
    workspace: ExtractionBuilderWorkspace,
    candidate_id: str,
    evidence_records: Iterable[Mapping[str, Any]] | None = None,
    resolver_selection_refs: Iterable[Any] | None = None,
    validation_errors: Iterable[Mapping[str, Any]] | None = None,
) -> ExtractionBuilderFinalization:
    """Stage and finalize one payload through the builder handoff contract."""

    if workspace.finalization is None:
        stage_extraction_payload(
            payload,
            workspace=workspace,
            candidate_id=candidate_id,
            evidence_records=evidence_records,
            resolver_selection_refs=resolver_selection_refs,
        )
    return workspace.finalize(
        candidate_ids=[candidate_id],
        validation_errors=validation_errors,
    )


def stage_extraction_payload(
    payload: Mapping[str, Any],
    *,
    workspace: ExtractionBuilderWorkspace,
    candidate_id: str,
    evidence_records: Iterable[Mapping[str, Any]] | None = None,
    resolver_selection_refs: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Stage one canonical payload candidate without finalizing the workspace."""

    preferred_evidence_records = list(evidence_records or [])
    canonical_payload = canonicalize_structured_result_payload(
        dict(payload),
        preferred_evidence_records=preferred_evidence_records,
    )
    workspace.apply_scope_metadata(
        document_id=_scope_value_from_payload(
            canonical_payload,
            "document_id",
            extra_payloads=preferred_evidence_records,
        ),
        domain_pack_id=_scope_value_from_payload(canonical_payload, "domain_pack_id"),
    )
    evidence_record_ids = _evidence_record_ids(canonical_payload, preferred_evidence_records)
    workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=canonical_payload,
        evidence_record_ids=evidence_record_ids,
        resolver_selection_refs=resolver_selection_refs,
        status=CANDIDATE_STATUS_VALID,
    )
    return canonical_payload


def build_internal_extraction_result_event(
    *,
    tool_name: str,
    specialist_name: str,
    finalization: ExtractionBuilderFinalization,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build the backend-only chat/persistence event from a finalized payload."""

    canonical_output = json.dumps(finalization.payload)
    return {
        "type": INTERNAL_EXTRACTION_RESULT_EVENT_TYPE,
        "timestamp": timestamp or _now_iso(),
        "details": {
            "toolName": tool_name,
            "friendlyName": f"{specialist_name}: Internal Extraction Result",
            "success": True,
            "isSpecialistInternal": True,
            "builderFinalization": finalization.summary(),
        },
        "internal": {
            "tool_output": canonical_output,
            "canonical_payload": deepcopy(finalization.payload),
            "builder_finalization": finalization.summary(),
            "output_length": len(canonical_output),
        },
    }


def _assemble_payload(candidates: list[ExtractionBuilderCandidate]) -> dict[str, Any]:
    if len(candidates) == 1:
        return deepcopy(candidates[0].staged_fields)
    return {
        "candidates": [deepcopy(candidate.staged_fields) for candidate in candidates],
        "run_summary": {
            "candidate_count": len(candidates),
            "kept_count": len(candidates),
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
    }


def _evidence_record_ids(
    payload: Mapping[str, Any],
    evidence_records: Iterable[Mapping[str, Any]] | None,
) -> list[str]:
    ids = [
        record.get("evidence_record_id")
        for record in extract_evidence_records_from_structured_result(dict(payload))
        if isinstance(record, Mapping)
    ]
    if evidence_records:
        ids.extend(record.get("evidence_record_id") for record in evidence_records)
    return _unique_strings(ids)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_builder_run_id() -> str:
    return f"builder-{uuid4()}"


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _scope_value_from_payload(
    payload: Any,
    key: str,
    *,
    extra_payloads: Iterable[Any] | None = None,
) -> str | None:
    values: list[str] = []

    def visit(value: Any) -> None:
        if hasattr(value, "model_dump"):
            try:
                value = value.model_dump(mode="json")
            except Exception:
                return
        if isinstance(value, Mapping):
            direct = _optional_string(value.get(key))
            if direct:
                values.append(direct)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                visit(item)

    visit(payload)
    if extra_payloads is not None:
        visit(extra_payloads)
    unique_values = _unique_strings(values)
    if len(unique_values) == 1:
        return unique_values[0]
    return None


def _required_string(value: Any, field_name: str) -> str:
    text = _optional_string(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _string_list(values: Iterable[Any] | None) -> list[str]:
    return _unique_strings(values or [])


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _optional_string(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


__all__ = [
    "BUILDER_STATE_ABORTED",
    "BUILDER_STATE_ACTIVE",
    "BUILDER_STATE_CANCELLED",
    "BUILDER_STATE_FINALIZED",
    "BUILDER_STATE_VALIDATION_FAILED",
    "CANDIDATE_STATUS_DISCARDED",
    "CANDIDATE_STATUS_DRAFT",
    "CANDIDATE_STATUS_FINALIZED",
    "CANDIDATE_STATUS_NEEDS_PATCH",
    "CANDIDATE_STATUS_VALID",
    "ExtractionBuilderCandidate",
    "ExtractionBuilderError",
    "ExtractionBuilderFinalization",
    "ExtractionBuilderFinalizationConflict",
    "ExtractionBuilderFinalizedError",
    "ExtractionBuilderValidationError",
    "ExtractionBuilderWorkspace",
    "build_internal_extraction_result_event",
    "finalize_extraction_payload",
    "get_active_extraction_builder_workspace",
    "reset_active_extraction_builder_workspace",
    "set_active_extraction_builder_workspace",
    "stage_extraction_payload",
]
