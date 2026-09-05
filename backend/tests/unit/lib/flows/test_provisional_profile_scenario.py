"""Synthetic dev fixture: real closed-profile and projection paths, no curator approval."""

from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

import pytest

from src.lib.agent_studio.profile_conformance import ProfileConformanceError, ResolvedGenericProfile
from src.lib.flows.output_projection import (
    FlowOutputProjectionPlan, apply_projection_plan, build_flow_output_artifact_bundle,
)
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.generic_extraction_profile import GenericProfileContract


@pytest.fixture
def scenario():
    fixture = Path(__file__).parents[3] / "fixtures/profiles/provisional_reagent_inventory.json"
    data = json.loads(fixture.read_text())
    contract = GenericProfileContract.model_validate(data["profile"])
    receipt = AgentExecutionReceipt.model_validate({
        "agent_id": str(UUID(int=1035)), "agent_key": "ca_provisional_dev_fixture",
        "agent_revision_id": str(UUID(int=1036)), "revision": 1, "fingerprint": "sha256:" + "a" * 64,
        "output_contract": {"output_state": "structured_extraction", "output_mode": "profile_bound_generic",
            "generic_profile_ref": {"profile_id": str(UUID(int=1037)), "profile_revision_id": str(UUID(int=1038)),
                                    "revision": 1, "fingerprint": contract.fingerprint()}},
    })
    pin = receipt.output_contract.generic_profile_ref
    assert pin is not None
    return data, receipt, ResolvedGenericProfile(pin, contract)


def test_provisional_two_column_projection_preserves_order_pairs_and_semantics(scenario):
    data, receipt, profile = scenario
    original = deepcopy(data)
    objects = []
    for index, case in enumerate(data["cases"]):
        profile.require_attributes(case["attributes"])
        objects.append({"object_type": "generic_object", "pending_ref_id": f"record-{index}",
            "payload": {"semantic_class": profile.contract.semantic_class, "attributes": case["attributes"]},
            "metadata": {"generic_profile_ref": profile.receipt,
                         "generic_extraction": {"class_key": "generic:generic_object"}}})
    payload = {"curatable_objects": objects, "metadata": {"provenance": {
        "produced_by": receipt.agent_key, "execution_receipt": receipt.model_dump(mode="json"),
        "generic_profile_ref": profile.receipt}}}
    bundle = build_flow_output_artifact_bundle(completed_steps=[{
        "step": 1, "agent_id": receipt.agent_key,
        "candidate": {"agent_key": receipt.agent_key, "adapter_key": "generic", "candidate_count": len(objects),
                      "execution_receipt": receipt, "payload_json": payload},
    }], flow_name="Provisional dev fixture", profile_resolver=lambda _: profile)
    result = apply_projection_plan(bundle, FlowOutputProjectionPlan.model_validate(data["projection"]))
    assert result.rows == [case["expected"] for case in data["cases"]]
    assert bundle.artifacts[0].execution_receipt == receipt
    assert data == original  # Display literals and joins never rewrite extraction data.
    assert profile.contract.validator_mappings == []


@pytest.mark.parametrize("patch", [
    {"synonym": "undeclared output key"},
    {"paper_labels": "not a list"},
    {"sources": [{"name": "Example", "source_identifier": "wrong nested key"}]},
])
def test_provisional_profile_rejects_trace_style_contract_mismatch_without_mutation(scenario, patch):
    data, _, profile = scenario
    attributes = deepcopy(data["cases"][0]["attributes"])
    attributes.update(patch)
    before = deepcopy(attributes)
    with pytest.raises(ProfileConformanceError):
        profile.require_attributes(attributes)
    assert attributes == before


def test_typed_candidate_mismatch_then_explicit_profile_bound_success(scenario, monkeypatch):
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.openai_agents import extraction_builder_workspace as builder

    data, receipt, profile = scenario
    case = deepcopy(data["contract_mismatch"])
    original = deepcopy(case)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_: None)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **_: None)
    arguments = {"label": case["label"], "attributes": case["attributes"],
                 "evidence_record_ids": ["synthetic-evidence-1"],
                 "classification_notes": ["Synthetic contract regression; no production content."]}
    exploratory = builder.ExtractionBuilderWorkspace(run_id="typed-mismatch", domain_pack_id="generic")
    token = builder.set_active_extraction_builder_workspace(exploratory)
    try:
        rejected = tools._stage_generic_object_impl(class_key=case["typed_class_key"], **arguments)
        assert rejected.status == "error"
        assert rejected.data["validation_issues"][0]["reason"] == case["expected_reason"]
        assert not exploratory.candidates
    finally:
        builder.reset_active_extraction_builder_workspace(token)

    # A separately configured, saved-profile workspace is the repair, not a
    # model-triggered fallback or mutation of the exploratory workspace.
    configured = builder.ExtractionBuilderWorkspace(
        run_id="profile-repair", domain_pack_id="generic", agent_id=receipt.agent_key,
        generic_profile=profile,
    )
    token = builder.set_active_extraction_builder_workspace(configured)
    try:
        accepted = tools._stage_generic_object_impl(
            class_key="generic:generic_object", semantic_class=profile.contract.semantic_class, **arguments,
        )
        assert accepted.status == "ok"
        candidate = next(iter(configured.candidates.values()))
        assert candidate.staged_fields["attributes"] == case["attributes"]
        assert candidate.evidence_record_ids == ["synthetic-evidence-1"]
        assert configured.generic_profile is profile
        assert not exploratory.candidates
        assert case == original
    finally:
        builder.reset_active_extraction_builder_workspace(token)
