"""A binding may route by one target value to a per-value validator and mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidationRegistryError,
    validate_active_validator_agent_references,
)
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFindingSeverity,
)


_ROUTES_YAML = """
        route_by:
          source: payload
          path: subject.kind
        routes:
          widget:
            validator_agent:
              package_id: fixture.validators
              agent_id: widget_validator
            max_tool_calls: 5
            input_fields:
              name:
                source: payload
                path: subject.wording
              proposed_id:
                source: payload
                path: subject.proposed_id
                required: false
            expected_result_fields:
              widget_id: subject.identifier
              widget_name: subject.label
          gadget:
            validator_agent:
              package_id: fixture.validators
              agent_id: gadget_validator
            input_fields:
              label:
                source: payload
                path: subject.wording
            expected_result_fields:
              gadget_id: subject.identifier
"""


def _pack_text(binding_body: str = _ROUTES_YAML) -> str:
    return f"""
pack_id: fixture.routed
display_name: Fixture Routed Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: ClaimPayload
    display_name: Claim payload
object_definitions:
  - object_type: Claim
    display_name: Claim
    model_ref: ClaimPayload
    fields:
      - field_path: subject.kind
        field_type: string
      - field_path: subject.wording
        field_type: string
      - field_path: subject.proposed_id
        field_type: string
      - field_path: subject.identifier
        field_type: string
      - field_path: subject.label
        field_type: string
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.subject_lookup
        display_name: Subject lookup
        applies_to:
          domain_pack_id: fixture.routed
          object_types:
            - Claim
          field_paths:
            - subject.identifier
{binding_body}
        required: true
        blocking: true
        allow_opt_out: true
        curator_override:
          allowed: true
""".strip()


def _loaded_pack(tmp_path: Path, binding_body: str = _ROUTES_YAML) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.routed"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(_pack_text(binding_body), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _envelope(*subjects: dict[str, Any]) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="routed-env",
        domain_pack_id="fixture.routed",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Claim",
                pending_ref_id=f"claim-{index}",
                payload={"subject": subject},
            )
            for index, subject in enumerate(subjects, start=1)
        ],
    )


def _match(tmp_path: Path, subject: dict[str, Any]):
    registry = DomainPackValidationRegistry.from_domain_pack(_loaded_pack(tmp_path))
    (match,) = registry.match_bindings(_envelope(subject), states=[ValidationBindingState.ACTIVE])
    return match


def test_route_value_selects_its_validator_inputs_and_results(tmp_path: Path):
    match = _match(tmp_path, {"kind": "widget", "wording": "blue widget"})

    assert match.binding.binding_id == "fixture.subject_lookup"
    assert match.binding.route_value == "widget"
    assert match.binding.max_tool_calls == 5
    assert match.binding.identity_details()["route"] == {
        "route_by": {"source": "payload", "path": "subject.kind"},
        "value": "widget",
    }
    result = build_domain_validation_request(match)

    assert result.findings == ()
    request = result.request
    assert request.validator_binding_id == "fixture.subject_lookup"
    assert request.validator_agent.agent_id == "widget_validator"
    assert request.selected_inputs == {"name": "blue widget"}
    assert request.expected_result_fields == {
        "widget_id": "subject.identifier",
        "widget_name": "subject.label",
    }
    assert request.target.expected_fields == ["widget_id", "widget_name"]

    (tmp_path / "gadget").mkdir()
    gadget = build_domain_validation_request(
        _match(tmp_path / "gadget", {"kind": "gadget", "wording": "gizmo"})
    )
    assert gadget.request.validator_agent.agent_id == "gadget_validator"
    assert gadget.request.selected_inputs == {"label": "gizmo"}
    assert gadget.request.expected_result_fields == {"gadget_id": "subject.identifier"}


@pytest.mark.parametrize(
    ("subject", "code"),
    [
        ({"wording": "blue widget"}, "selector_missing"),
        ({"kind": "", "wording": "blue widget"}, "selector_missing"),
        ({"kind": "unknown", "wording": "blue widget"}, "selector_unrouted"),
        ({"kind": "Widget", "wording": "blue widget"}, "selector_unrouted"),
    ],
)
def test_a_target_without_a_route_is_reported_never_sent_elsewhere(
    tmp_path: Path, subject: dict[str, Any], code: str
):
    match = _match(tmp_path, subject)

    assert match.binding.unrouted
    result = build_domain_validation_request(match)

    assert result.request is None
    (finding,) = result.findings
    assert finding.code == code
    # A blocking binding's unrouted target gates readiness like any selector failure.
    assert finding.severity is ValidationFindingSeverity.BLOCKER
    assert finding.field_ref.field_path == "subject.kind"
    problem = finding.details["selector_problem"]
    assert problem["input_name"] == "route_by"
    assert problem["routes"] == ["gadget", "widget"]
    assert finding.details["validation_metadata"]["curator_override"] == {"allowed": True}


def test_dispatch_runs_only_routed_targets_and_never_writes_the_routing_value(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    calls = []

    def runner(request, *, binding):
        calls.append((request.validator_agent.agent_id, binding.route_value, binding.max_tool_calls))
        return {
            "status": "resolved",
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": request.target.model_dump(mode="json"),
            "resolved_values": {"widget_id": "W:1", "widget_name": "Blue widget"},
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "fixture_lookup",
                    "method": "search",
                    "query": {"name": "blue widget"},
                    "result_count": 1,
                    "outcome": "success",
                }
            ],
            "curator_message": None,
            "explanation": "Fixture validator result.",
        }

    result = dispatch_active_validator_bindings(
        _envelope(
            {"kind": "widget", "wording": "blue widget"},
            {"kind": "unknown", "wording": "thing"},
        ),
        pack,
        runner=runner,
    )

    assert calls == [("widget_validator", "widget", 5)]
    routed, unrouted = result.envelope.extracted_objects
    assert routed.payload["subject"]["kind"] == "widget"
    assert routed.payload["subject"]["identifier"] == "W:1"
    assert routed.payload["subject"]["label"] == "Blue widget"
    assert unrouted.payload["subject"] == {"kind": "unknown", "wording": "thing"}
    assert [
        finding.code
        for finding in result.envelope.validation_findings
        if finding.code == "selector_unrouted"
    ] == ["selector_unrouted"]


def test_route_agents_are_checked_like_binding_agents(tmp_path: Path):
    registry = DomainPackValidationRegistry.from_domain_pack(_loaded_pack(tmp_path))

    class _Packages:
        def get_package(self, package_id):
            return object()

        def package_declares_dependency(self, owner, dependency):
            return True

    with pytest.raises(ValidationRegistryError, match="gadget_validator"):
        validate_active_validator_agent_references(
            [registry],
            _Packages(),
            agent_resolver=lambda package_id, agent_id: (
                None if agent_id == "gadget_validator" else object()
            ),
            output_schema_resolver=lambda key: None,
        )


def test_for_payload_selects_the_route_a_stored_object_names(tmp_path: Path):
    registry = DomainPackValidationRegistry.from_domain_pack(_loaded_pack(tmp_path))
    (binding,) = registry.bindings

    assert binding.unrouted
    assert [ref.agent_id for ref in binding.validator_agents()] == [
        "widget_validator",
        "gadget_validator",
    ]
    assert binding.for_payload({"subject": {"kind": "gadget"}}).expected_result_fields == {
        "gadget_id": "subject.identifier"
    }
    assert binding.for_payload({"subject": {"kind": "unknown"}}) is binding


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            _ROUTES_YAML
            + """        validator_agent:
          package_id: fixture.validators
          agent_id: widget_validator
""",
            "declare validator_agent on each route",
        ),
        (
            """
        route_by:
          source: payload
          path: subject.kind
""",
            "route_by and routes together",
        ),
        (
            """
        route_by:
          source: payload
          path: subject.kind
        routes:
          widget:
            validator_agent:
              package_id: fixture.validators
              agent_id: widget_validator
            input_fields:
              name:
                source: payload
                path: subject.wording
""",
            "must declare input_fields and expected_result_fields",
        ),
        (
            """
        route_by:
          source: literal
          path: subject.kind
        routes: {}
""",
            "route_by",
        ),
    ],
)
def test_invalid_route_declarations_fail_at_load(tmp_path: Path, body: str, message: str):
    with pytest.raises(Exception, match=message):
        _loaded_pack(tmp_path, body)


@pytest.mark.parametrize(
    ("body", "location"),
    [
        (
            _ROUTES_YAML.replace("gadget_id: subject.identifier", "gadget_id: subject.kind", 1),
            "routes.gadget.expected_result_fields.gadget_id",
        ),
        (
            _ROUTES_YAML.replace("widget_name: subject.label", "widget_name: subject.kind.label", 1),
            "routes.widget.expected_result_fields.widget_name",
        ),
        (
            _ROUTES_YAML + """        optional_result_fields:
          kind: subject.kind
""",
            "optional_result_fields.kind",
        ),
    ],
)
def test_a_route_never_writes_its_routing_value(tmp_path: Path, body: str, location: str):
    with pytest.raises(Exception, match=f"{location}.*routing value is never a validator result"):
        _loaded_pack(tmp_path, body)


def test_route_selectors_are_checked_against_the_target_object(tmp_path: Path):
    pack = _loaded_pack(tmp_path, _ROUTES_YAML.replace("path: subject.wording\n            expected", "path: subject.missing\n            expected", 1))

    with pytest.raises(ValidationRegistryError, match="routes.gadget.label"):
        DomainPackValidationRegistry.from_domain_pack(pack)


# --- A curator override on a routed value -----------------------------------------------

_OVERRIDE_KEYS = ("curie", "name")


def _override_pack() -> LoadedDomainPack:
    from src.lib.domain_packs.resolvable_values import LOOKUP_OUTCOMES
    from src.schemas.domain_pack_metadata import (
        DomainPackEnumDefinition,
        DomainPackFieldDefinition,
        DomainPackFieldType,
        DomainPackMetadata,
        DomainPackObjectDefinition,
    )

    def route(agent_id: str, prefix: str) -> dict[str, Any]:
        return {
            "validator_agent": {"package_id": "fixture.validators", "agent_id": agent_id},
            "input_fields": {"mention": {"source": "payload", "path": "site.mention"}},
            "expected_result_fields": {f"{prefix}_id": "site.curie", f"{prefix}_name": "site.name"},
        }

    metadata = DomainPackMetadata(
        pack_id="fixture.routed_override",
        display_name="Fixture Routed Override",
        version="0.1.0",
        metadata_api_version="1.0.0",
        enum_definitions=[DomainPackEnumDefinition(
            enum_id="LookupOutcome", display_name="Lookup outcome",
            values=[{"value": value} for value in LOOKUP_OUTCOMES],
        )],
        metadata={"validator_bindings": {"active": [{
            "binding_id": "fixture.site_lookup",
            "display_name": "Site lookup",
            "applies_to": {"domain_pack_id": "fixture.routed_override", "object_types": ["Observation"]},
            "route_by": {"source": "payload", "path": "site.kind"},
            "routes": {"widget": route("widget_validator", "widget"), "gadget": route("gadget_validator", "gadget")},
            "required": True,
            "blocking": True,
            "curator_override": {"allowed": True},
        }], "under_development": []}},
        object_definitions=[DomainPackObjectDefinition(
            object_type="Observation", display_name="Observation", metadata={"object_role": "curatable_unit"},
            fields=[
                DomainPackFieldDefinition(field_path="site", field_type=DomainPackFieldType.OBJECT,
                                          metadata={"display": {"label": "name", "id": "curie", "mention": "mention"}}),
                DomainPackFieldDefinition(field_path="site.curie", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="site.name", field_type=DomainPackFieldType.STRING,
                                          metadata={"editable": True}),
                DomainPackFieldDefinition(field_path="site.mention", field_type=DomainPackFieldType.STRING),
                DomainPackFieldDefinition(field_path="site.kind", field_type=DomainPackFieldType.STRING),
                DomainPackFieldDefinition(field_path="site.lookup_outcome", field_type=DomainPackFieldType.ENUM,
                                          enum_ref="LookupOutcome"),
            ],
        )],
    )
    return LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    )


def _overridden_site_envelope(kind: str) -> DomainEnvelope:
    from src.lib.domain_packs.resolvable_values import apply_curator_identity, unresolved_value

    site = unresolved_value("blue thing", identity_keys=_OVERRIDE_KEYS, kind="widget")
    apply_curator_identity(site, {"curie": "W:1", "name": "Blue widget"}, identity_keys=_OVERRIDE_KEYS,
                           id_key="curie", label_key="name", actor_id="curator-7",
                           actor_display_name="curator-7", at="2026-09-24T12:00:00+00:00")
    # The routing value changes after the override (e.g. a re-extraction reads it differently).
    site["kind"] = kind
    return DomainEnvelope(
        envelope_id="routed-override-env", domain_pack_id="fixture.routed_override",
        extracted_objects=[CuratableObjectEnvelope(object_type="Observation", object_id="obs-1",
                                                   payload={"site": site})],
    )


@pytest.mark.parametrize(
    ("values", "status", "outcome", "disagrees"),
    [
        ({"gadget_id": "G:9", "gadget_name": "Gizmo"}, "resolved", "success", True),
        ({}, "unresolved", "not_found", True),
        ({"gadget_id": "W:1", "gadget_name": "Blue widget"}, "resolved", "success", False),
    ],
)
def test_an_override_on_a_routed_value_sticks_when_a_later_run_takes_another_route(
    values: dict[str, Any], status: str, outcome: str, disagrees: bool
):
    from src.lib.domain_packs.materialization import (
        ValidatorResultMaterializationInput,
        materialize_validator_results_into_envelope,
    )
    from src.lib.domain_packs.resolvable_values import is_curator_override
    from src.schemas.domain_validator import DomainValidatorResultBase

    pack = _override_pack()
    envelope = _overridden_site_envelope("gadget")
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    (match,) = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
    request = build_domain_validation_request(match).request
    assert request.validator_agent.agent_id == "gadget_validator"
    result = DomainValidatorResultBase.model_validate({
        "status": status, "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id, "validator_agent": request.validator_agent,
        "target": request.target, "resolved_values": values, "resolved_objects": [],
        "missing_expected_fields": [], "candidates": [],
        "lookup_attempts": [{"provider": "f", "method": "m", "query": {}, "result_count": 1, "outcome": outcome}],
        "curator_message": None, "explanation": "Validator words.",
    })

    materialized = materialize_validator_results_into_envelope(
        envelope, pack.metadata,
        [ValidatorResultMaterializationInput(match=match, request=request, result=result)],
    )

    site = materialized.envelope.extracted_objects[0].payload["site"]
    assert site == envelope.extracted_objects[0].payload["site"]
    assert is_curator_override(site)
    assert (site["curie"], site["name"], site["kind"]) == ("W:1", "Blue widget", "gadget")
    disagreements = [
        finding for finding in materialized.appended_findings
        if finding.code == "domain_pack.validator_disagrees_with_curator_override"
    ]
    assert bool(disagreements) is disagrees


def test_an_override_on_a_value_that_no_longer_routes_stays_and_runs_nothing():
    pack = _override_pack()
    envelope = _overridden_site_envelope("unknown")
    calls = []

    result = dispatch_active_validator_bindings(
        envelope, pack, runner=lambda request, *, binding: calls.append(request),
    )

    assert calls == []
    assert result.envelope.extracted_objects[0].payload["site"] == envelope.extracted_objects[0].payload["site"]
    assert [finding.code for finding in result.appended_findings] == ["selector_unrouted"]
