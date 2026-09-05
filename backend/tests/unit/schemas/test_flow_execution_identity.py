"""Flow pins reuse the established execution receipt without a second profile choice."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas.flows import FlowNodeData


def node_with_receipt(output_contract):
    revision_id = str(uuid4())
    return {
        "agent_id": "ca_fixture", "agent_display_name": "Fixture", "output_key": "result",
        "agent_revision_id": revision_id,
        "execution_receipt": {
            "agent_id": str(uuid4()), "agent_key": "ca_fixture",
            "agent_revision_id": revision_id, "revision": 4,
            "fingerprint": "sha256:" + "a" * 64, "output_contract": output_contract,
        },
    }


@pytest.mark.parametrize("contract", [
    {"output_state": "none"},
    {"output_state": "structured_extraction", "output_mode": "domain",
     "output_schema_key": "GeneExpressionEnvelope"},
    {"output_state": "structured_extraction", "output_mode": "unprofiled_generic"},
    {"output_state": "structured_extraction", "output_mode": "profile_bound_generic",
     "generic_profile_ref": {
         "profile_id": str(uuid4()), "profile_revision_id": str(uuid4()),
         "revision": 3, "fingerprint": "sha256:" + "b" * 64,
     }},
])
def test_all_output_states_preserve_exact_receipt_through_json(contract):
    node = FlowNodeData.model_validate(node_with_receipt(contract))
    restored = FlowNodeData.model_validate_json(node.model_dump_json())
    assert restored == node
    assert restored.execution_receipt.output_contract.output_state == contract["output_state"]
    assert restored.execution_receipt.output_contract.output_mode == contract.get("output_mode")


@pytest.mark.parametrize("change", ["agent", "revision", "missing_revision", "system"])
def test_conflicting_identity_rejected(change):
    node = node_with_receipt({"output_state": "none"})
    if change == "agent":
        node["execution_receipt"]["agent_key"] = "ca_other"
    elif change == "revision":
        node["agent_revision_id"] = str(uuid4())
    elif change == "missing_revision":
        node.pop("agent_revision_id")
    else:
        node["agent_id"] = "pdf_extraction"
    with pytest.raises(ValidationError):
        FlowNodeData.model_validate(node)


def test_legacy_prompt_version_does_not_invent_execution_identity():
    node = FlowNodeData.model_validate({
        "agent_id": "ca_legacy", "agent_display_name": "Legacy",
        "output_key": "result", "prompt_version": 7,
    })
    assert node.agent_revision_id is None
    assert node.execution_receipt is None


def test_no_independent_profile_choice():
    node = node_with_receipt({"output_state": "none"})
    node["generic_profile_ref"] = {"revision": 3}
    with pytest.raises(ValidationError, match="Extra inputs"):
        FlowNodeData.model_validate(node)
