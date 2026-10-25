"""Exact, content-addressed validator originals, separate from finding previews.

An original is created from the typed runtime output before any presentation
projection. Historical finding projections are never promoted to originals.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from src.schemas.domain_envelope import DomainEnvelope
from src.schemas.domain_validator import DomainValidationRequest, DomainValidatorResultBase


def validator_result_snapshot(
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    source_envelope_revision: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return an immutable identity and lossless JSON snapshot, including nulls."""
    snapshot = {
        "schema_version": 1,
        "source_envelope_revision": source_envelope_revision,
        "request": deepcopy(dict(request)) if request is not None else None,
        "result": deepcopy(dict(result)),
    }
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return "validator-result:" + sha256(encoded.encode("utf-8")).hexdigest(), snapshot


def retain_validator_original(
    envelope: DomainEnvelope, *, result: DomainValidatorResultBase,
    request: DomainValidationRequest, source_envelope_revision: int | None,
) -> tuple[DomainEnvelope, str]:
    """Shared boundary for packaged and custom-profile materialization."""
    history = result._history_ref
    if history is None:
        raise ValueError("Materialization requires a durable validation history reference")
    ref = "validation-fact:" + history["fact_id"]
    original = {
        "schema_version": 2,
        "source_envelope_revision": source_envelope_revision,
        "history_ref": deepcopy(history),
        "recorded_order": history["sequence"],
        "original_available": history["execution_state"] in {"completed", "incomplete"},
        "result": {name: value for name, value in result.model_dump(mode="json").items()
                   if name in {"status", "request_id", "validator_binding_id", "target"}},
        "output_complete": not bool(result.output_issues),
        "follow_up_question_count": len(result.unresolved_questions),
    }
    original["result"]["target"] = {name: value for name, value in original["result"]["target"].items()
                                    if name in {"object_type", "object_id", "field_path", "domain_pack_id"}}
    if not original["original_available"]:
        original["result"]["status"] = history["execution_state"]
        original["output_complete"] = False
    previous = envelope.validator_results.get(ref)
    if previous is not None and previous != original:
        raise ValueError("Validation completion reference was reused with different metadata")
    return envelope.model_copy(update={"validator_results": {**envelope.validator_results, ref: original}}), ref


def read_validator_original(snapshot: Mapping[str, Any], *, user_id: str, document_id: str) -> dict | None:
    """Resolve only after the caller authorized the containing extraction."""
    if snapshot["schema_version"] == 1:
        # A known historical body, not a reconstructed answer or review decision.
        return deepcopy(dict(snapshot))
    from src.lib.validation_history import store
    reference = snapshot["history_ref"]
    scope = store.HistoryScope(user_id, document_id, reference["session_id"])
    completion = store.read_fact(scope, reference["artifact_id"], reference["fact_id"])
    if completion["sha256"] != reference["body_sha256"]:
        raise store.HistoryConflict("Validator body reference does not match its immutable fact")
    if completion["kind"] == "scheduling":
        return None
    if completion["kind"] != "completion":
        raise store.HistoryConflict("Validator reference is not a completion or scheduling fact")
    if completion["content"]["result"] is None:
        return None
    attempt = store.read_fact(scope, reference["artifact_id"], completion["parent_id"])
    return {"schema_version": 2, "source_envelope_revision": snapshot.get("source_envelope_revision"),
            "request": attempt["content"]["input"]["request"], "result": completion["content"]["result"],
            "history_ref": deepcopy(reference)}
