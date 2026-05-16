"""End-to-end fixture coverage for package-scoped validator dispatch."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.schemas.domain_envelope import DomainEnvelope
from src.schemas.domain_validator import DomainValidatorResultBase


FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "domain_packs"
    / "validator_dispatch"
    / "end_to_end_cases.yaml"
)


@pytest.fixture(scope="module")
def dispatch_fixture() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


def _loaded_pack(
    tmp_path: Path,
    dispatch_fixture: dict[str, Any],
    *,
    active_binding_ids: list[str],
) -> LoadedDomainPack:
    pack_payload = deepcopy(dispatch_fixture["domain_pack"])
    active_bindings = pack_payload["metadata"]["validator_bindings"]["active"]
    pack_payload["metadata"]["validator_bindings"]["active"] = [
        binding
        for binding in active_bindings
        if binding["binding_id"] in set(active_binding_ids)
    ]
    pack_dir = tmp_path / "fixture.validator_dispatch"
    pack_dir.mkdir()
    metadata_path = pack_dir / "domain_pack.yaml"
    metadata_path.write_text(yaml.safe_dump(pack_payload), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_dir,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _result_payload(case: dict[str, Any], request) -> dict[str, Any]:
    payload = deepcopy(case["validator_result"])
    payload.update(
        {
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": request.target.model_dump(mode="json"),
        }
    )
    return payload


def test_dispatch_fixture_declares_forward_only_contract(dispatch_fixture):
    contract = dispatch_fixture["fixture_contract"]

    assert contract["metadata_shape"] == "object_selector_validator_bindings"
    assert contract["request_shape"] == "DomainValidationRequest"
    assert contract["result_shape"] == "DomainValidatorResultBase"
    assert contract["finding_statuses"] == ["open", "resolved", "waived"]
    assert set(contract["selector_failure_codes"]) == {
        "selector_missing",
        "selector_ambiguous",
        "selector_unresolved_ref",
        "selector_missing_field",
    }
    assert contract["forbidden_runtime_result_statuses"] == ["under_development"]
    with pytest.raises(ValidationError):
        DomainValidatorResultBase.model_validate(
            {
                "status": "under_development",
                "request_id": "request-1",
                "validator_binding_id": "fixture.future_reference_lookup",
                "validator_agent": {
                    "package_id": "fixture.validators",
                    "agent_id": "reference_validator",
                },
                "target": {"domain_pack_id": "fixture.validator_dispatch"},
                "resolved_values": {},
                "resolved_objects": [],
                "missing_expected_fields": [],
                "candidates": [],
                "lookup_attempts": [],
                "curator_message": None,
                "explanation": "Metadata-only state must not become a result.",
            }
        )


@pytest.mark.parametrize(
    "case",
    yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"],
    ids=lambda case: case["case_id"],
)
def test_validator_dispatch_end_to_end_fixture_cases(tmp_path, dispatch_fixture, case):
    pack = _loaded_pack(
        tmp_path,
        dispatch_fixture,
        active_binding_ids=case["active_binding_ids"],
    )
    envelope = DomainEnvelope.model_validate(case["envelope"])
    captured_requests = []

    def _runner(request, *, binding):
        captured_requests.append(request)
        return _result_payload(case, request)

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=_runner,
        source_envelope_revision=3,
    )

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.validator_binding_id == case["expected_request"][
        "validator_binding_id"
    ]
    assert request.selected_inputs == case["expected_request"]["selected_inputs"]
    expected_target = case["expected_request"].get("target", {})
    for key, expected_value in expected_target.items():
        assert getattr(request.target, key) == expected_value

    expected_projection = case["expected_projection"]
    finding = result.appended_findings[0]
    assert finding.code == expected_projection["finding_code"]
    assert finding.status.value == expected_projection["finding_status"]
    assert finding.details["validation_metadata"]["binding_state"] == "active"
    assert finding.details["validation_metadata"]["source_envelope_revision"] == 3
    assert finding.details["validation_result"]["status"] in {"resolved", "unresolved"}
    assert finding.details["validation_result"]["status"] not in (
        dispatch_fixture["fixture_contract"]["forbidden_runtime_result_statuses"]
    )
    assert "repair_action" not in finding.details["validation_result"]

    if "failure_classification" in expected_projection:
        assert finding.details["failure_classification"] == (
            expected_projection["failure_classification"]
        )
    if "missing_expected_fields" in expected_projection:
        assert result.validator_results[0].missing_expected_fields == (
            expected_projection["missing_expected_fields"]
        )
    if "candidate_values" in expected_projection:
        assert [
            candidate["value"]
            for candidate in finding.details["candidate_matches"]
        ] == expected_projection["candidate_values"]
    if "materialized_object_type" in expected_projection:
        materialized = next(
            domain_object
            for domain_object in result.envelope.objects
            if domain_object.object_type
            == expected_projection["materialized_object_type"]
        )
        assert materialized.status.value == expected_projection[
            "materialized_object_status"
        ]
        assert materialized.payload == expected_projection["materialized_payload"]
        assert result.envelope.objects[0].object_refs == [materialized.to_object_ref()]


@pytest.mark.parametrize(
    "case",
    yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["selector_failure_cases"],
    ids=lambda case: case["case_id"],
)
def test_validator_dispatch_selector_failure_fixture_cases(
    tmp_path,
    dispatch_fixture,
    case,
):
    pack = _loaded_pack(
        tmp_path,
        dispatch_fixture,
        active_binding_ids=case["active_binding_ids"],
    )

    def _runner(_request, *, binding):  # pragma: no cover - selector must stop dispatch
        raise AssertionError(f"selector failure should not run {binding.binding_id}")

    result = dispatch_active_validator_bindings(
        DomainEnvelope.model_validate(case["envelope"]),
        pack,
        runner=_runner,
    )

    assert result.validator_results == ()
    assert [finding.code for finding in result.appended_findings] == [
        case["expected_selector_code"]
    ]
    finding = result.appended_findings[0]
    assert finding.status.value == "open"
    assert finding.details["validation_metadata"]["binding_state"] == "active"
    assert finding.details["selector_problem"]["code"] == case["expected_selector_code"]
