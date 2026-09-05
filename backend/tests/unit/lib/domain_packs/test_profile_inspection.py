"""Inspection authorizes and pages the exact saved profile, never its template."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.lib.agent_studio import domain_envelope_tools as inspection
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from .test_profile_validation import example as example, resolve


@pytest.fixture
def inspected_profile(example, monkeypatch):
    raw, capability, pack = example
    receipt, profile = resolve(raw)
    receipt = receipt.model_copy(update={"agent_key": f"ca_{receipt.agent_id}"})
    db = SimpleNamespace(close=Mock())
    monkeypatch.setattr(inspection, "domain_pack_validation_registries",
                        lambda: {"generic": DomainPackValidationRegistry.from_domain_pack(pack)})
    authorization_calls = []

    def authorized(session, agent_id, revision_id, user_id, *, active_group_ids):
        assert session is db
        assert agent_id == receipt.agent_id and revision_id == receipt.agent_revision_id
        authorization_calls.append((user_id, active_group_ids))
        return (SimpleNamespace(id=receipt.agent_revision_id, revision=receipt.revision, fingerprint=receipt.fingerprint),
                SimpleNamespace(output_contract=receipt.output_contract, curation={"domain_pack_id": "generic"}))

    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.get_execution_revision", authorized)
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.current_execution_receipt",
                        lambda *args, **kwargs: pytest.fail("Pinned inspection read mutable head"))
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile", lambda *args: profile)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: [capability])
    arguments = dict(agent_id=receipt.agent_key, agent_revision_id=str(receipt.agent_revision_id),
                     session_factory=lambda: db, user_id=7, active_group_ids=["FB"])
    return receipt, profile, arguments, db, authorization_calls


def test_profile_plan_summary_and_pages_keep_exact_revision(inspected_profile):
    receipt, profile, arguments, db, authorization_calls = inspected_profile
    summary = inspection.get_domain_pack_validation_plan(**arguments)
    assert summary["success"], summary
    assert summary["execution_receipt"] == receipt.model_dump(mode="json")
    assert summary["generic_profile_ref"] == profile.receipt
    assert summary["unavailable_mapping_count"] == 0
    for request in summary["detail_requests"]:
        assert request["example_input"]["agent_revision_id"] == str(receipt.agent_revision_id)
        assert "domain_pack_id" not in request["example_input"]
    page = inspection.get_domain_pack_validation_plan(**arguments, section="fields", limit=1)
    assert page["next_request"]["agent_revision_id"] == str(receipt.agent_revision_id)
    assert page["next_request"]["agent_id"] == receipt.agent_key
    binding = inspection.get_domain_pack_validation_plan(**arguments, section="validator_bindings")["items"][0]
    assert binding["available"]
    assert binding["profile_validator_mapping"] == profile.contract.validator_mappings[0].model_dump(mode="json")
    assert authorization_calls == [(7, ["FB"])] * 3
    assert db.close.call_count == 3


def test_profile_plan_shows_unavailable_mapping_with_policy(inspected_profile, monkeypatch):
    _, profile, arguments, _, _ = inspected_profile
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: [])
    plan = inspection.get_domain_pack_validation_plan(**arguments, section="validator_bindings", state="unavailable")
    assert plan["success"], plan
    assert plan["unavailable_mapping_count"] == 1
    mapping, = plan["items"]
    assert not mapping["available"] and mapping["unavailable_reasons"]
    assert mapping["profile_validator_mapping"]["policy"] == profile.contract.validator_mappings[0].policy.model_dump()
    summary = inspection.get_domain_pack_validation_plan(**arguments)
    assert summary["validation_dispatch_summary"]["active_automatic"] == 0
    assert summary["validation_attachment_summary"]["by_state"]["unavailable"] == 1


def test_profile_plan_rejects_other_pack_and_denied_revision(inspected_profile, monkeypatch):
    from src.lib.agent_studio.execution_revision_service import ExecutionRevisionNotFoundError
    _, _, arguments, db, _ = inspected_profile
    wrong_pack = inspection.get_domain_pack_validation_plan(**arguments, domain_pack_id="other")
    assert not wrong_pack["success"]
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.get_execution_revision",
                        Mock(side_effect=ExecutionRevisionNotFoundError("Revision unavailable")))
    denied = inspection.get_domain_pack_validation_plan(**arguments)
    assert not denied["success"] and denied["error"] == "Revision unavailable"
    assert db.close.call_count == 2


def test_custom_inspection_requires_authenticated_revision_access(inspected_profile):
    receipt, _, _, _, _ = inspected_profile
    result = inspection.get_domain_pack_validation_plan(agent_id=receipt.agent_key)
    assert not result["success"] and "Authenticated" in result["error"]


@pytest.mark.parametrize("available", [True, False])
def test_custom_catalog_projects_saved_profile_and_current_capability_access(inspected_profile, monkeypatch, available):
    from src.lib.agent_studio import domain_envelope_metadata as metadata
    receipt, profile, _, db, calls = inspected_profile
    monkeypatch.setattr(metadata, "domain_pack_validation_registries", inspection.domain_pack_validation_registries)
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.current_execution_receipt",
                        lambda *args, **kwargs: receipt)
    if not available:
        monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: [])
    pin, result = metadata.custom_agent_revision_metadata(db, receipt.agent_key, 7, active_group_ids=["FB"])
    assert pin == receipt and calls == [(7, ["FB"])]
    assert result["execution_receipt"] == receipt.model_dump(mode="json")
    assert result["generic_profile_ref"] == profile.receipt
    assert result["schema_refs"] == [] and result["model_definitions"] == []
    obj, = result["object_definitions"]
    assert obj["object_type"] == "generic_object"
    assert "attributes.paper_name" in {field["field_path"] for field in obj["fields"]}
    option, = result["validation_attachments"]
    assert option["available"] is available
    assert bool(option["unavailable_reasons"]) is not available
    assert result["validation_summary"]["by_state"]["unavailable"] == (0 if available else 1)
    assert obj["capabilities"]["validate"]["state"] == ("active" if available else "unavailable")
    assert obj["capabilities"]["validate"]["active_bindings"] == (1 if available else 0)


def test_custom_catalog_without_structured_output_does_not_consult_template(inspected_profile, monkeypatch):
    from src.lib.agent_studio.domain_envelope_metadata import custom_agent_revision_metadata
    from src.schemas.agent_execution_revision import AgentOutputContract
    receipt, _, _, db, calls = inspected_profile
    receipt = receipt.model_copy(update={"output_contract": AgentOutputContract(output_state="none")})
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.current_execution_receipt",
                        lambda *args, **kwargs: receipt)
    assert custom_agent_revision_metadata(db, receipt.agent_key, 7, active_group_ids=["FB"]) == (receipt, None)
    assert calls == []
