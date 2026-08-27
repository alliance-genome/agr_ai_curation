"""API validation for mutually exclusive chat route targets."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas.chat_route_preferences import ChatRoutePreferenceUpdate


@pytest.mark.parametrize(
    ("payload", "mode"),
    [
        ({"mode": "automatic"}, "automatic"),
        ({"mode": "agent", "agent_id": "gene_validation"}, "agent"),
        ({"mode": "flow", "flow_id": str(uuid4())}, "flow"),
    ],
)
def test_valid_preference_shapes(payload, mode):
    assert ChatRoutePreferenceUpdate.model_validate(payload).mode.value == mode


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "automatic", "agent_id": "gene_validation"},
        {"mode": "automatic", "flow_id": str(uuid4())},
        {"mode": "agent"},
        {"mode": "agent", "agent_id": "gene_validation", "flow_id": str(uuid4())},
        {"mode": "flow"},
        {"mode": "flow", "agent_id": "gene_validation", "flow_id": str(uuid4())},
        {"mode": "unknown"},
    ],
)
def test_invalid_or_mixed_preference_shapes_are_rejected(payload):
    with pytest.raises(ValidationError):
        ChatRoutePreferenceUpdate.model_validate(payload)


def test_request_contract_does_not_accept_identity_or_access_claims():
    fields = ChatRoutePreferenceUpdate.model_fields

    assert set(fields) == {"mode", "agent_id", "flow_id"}
    with pytest.raises(ValidationError):
        ChatRoutePreferenceUpdate.model_validate(
            {"mode": "automatic", "user_id": 12, "groups": ["RGD"]}
        )
