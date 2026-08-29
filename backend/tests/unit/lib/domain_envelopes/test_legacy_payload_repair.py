from __future__ import annotations

from copy import deepcopy
import traceback

import pytest

from src.lib.domain_envelopes.legacy_payload_repair import (
    LegacyDomainEnvelopeRepairError,
    transform_legacy_domain_envelope_payload,
)


def _legacy_payload() -> dict:
    return {
        "envelope_id": "legacy-envelope-1",
        "domain_pack_id": "generic",
        "domain_pack_version": "0.8.0",
        "status": "extracted",
        "objects": [],
        "validation_findings": [],
        "history": [],
        "metadata": {"nested": {"preserved": [1, 2, 3]}},
    }


def test_transform_renames_only_legacy_objects_key() -> None:
    original = _legacy_payload()
    before = deepcopy(original)

    transformed = transform_legacy_domain_envelope_payload(original)

    assert original == before
    assert "objects" not in transformed
    assert transformed["extracted_objects"] == before["objects"]
    assert {
        key: value for key, value in transformed.items() if key != "extracted_objects"
    } == {key: value for key, value in before.items() if key != "objects"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.pop("objects"), "does not contain legacy key"),
        (
            lambda payload: payload.update({"extracted_objects": []}),
            "contains both legacy",
        ),
        (lambda payload: payload.update({"objects": {}}), "is not a list"),
        (
            lambda payload: payload.update({"unexpected": True}),
            "does not satisfy the current DomainEnvelope contract",
        ),
    ],
)
def test_transform_rejects_ambiguous_or_invalid_payloads(mutation, message) -> None:
    payload = _legacy_payload()
    mutation(payload)

    with pytest.raises(LegacyDomainEnvelopeRepairError, match=message):
        transform_legacy_domain_envelope_payload(payload)


def test_transform_validation_error_does_not_echo_payload_values() -> None:
    payload = _legacy_payload()
    payload["unexpected"] = "private-curation-value"

    with pytest.raises(LegacyDomainEnvelopeRepairError) as exc_info:
        transform_legacy_domain_envelope_payload(payload)

    assert "unexpected: extra_forbidden" in str(exc_info.value)
    rendered_traceback = "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )
    assert "private-curation-value" not in rendered_traceback
