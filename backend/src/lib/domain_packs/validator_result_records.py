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
    ref, original = validator_result_snapshot(
        result.model_dump(mode="json"), request=request.model_dump(mode="json"),
        source_envelope_revision=source_envelope_revision,
    )
    if ref not in envelope.validator_results:
        original["recorded_order"] = len(envelope.validator_results)
        envelope = envelope.model_copy(update={
            "validator_results": {**envelope.validator_results, ref: original},
        })
    return envelope, ref
