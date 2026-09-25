"""Scoped, bounded agent contract discovery (ALL-1277 / KANBAN-1834).

Production trace 0d4b22a6d6ac1c31b4e8d10441969c89 recorded two
``get_agent_contract`` calls for ``ontology_term_validation`` with
``topic=validator_bindings``, ``field_path=when_expressed_stage_name``,
``detail_level=detail`` and a page size of 50 then 20. Each returned 389,039
characters of pack-wide validators, field policies and validation attachments,
and the page size had no effect. These tests rebuild an equivalent wide,
multi-pack registry and pin the scoped, paged contract instead.
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents import Agent, RunConfig, Runner
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from pydantic import BaseModel, Field, create_model

from src.lib import agent_contracts
from src.lib.agent_contracts import AGENT_CONTRACT_TOPICS, get_agent_contract
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.schemas.domain_pack_metadata import DomainPackMetadata

VALIDATOR_AGENT = "fixture_ontology_validator"
VALIDATOR_PACKAGE = "org.validators"
STAGE_FIELD = "when_expressed_stage_name"
RECORDED_CALL = {
    "agent_id": VALIDATOR_AGENT,
    "topic": "validator_bindings",
    "field_path": STAGE_FIELD,
    "detail_level": "detail",
}


def _provider_refs(pack_id: str, object_type: str, field_path: str) -> dict:
    return {
        "fixture_linkml": {
            "schema_ref": "fixture.linkml",
            "commit": "0" * 40,
            "source_file": f"model/schema/{pack_id.replace('.', '_')}.yaml",
            "class": object_type,
            "slot": field_path.replace(".", "_"),
            "range": "string",
            "db_table": object_type.lower(),
            "db_column": field_path.replace(".", "").replace("_", ""),
        }
    }


def _wide_pack(
    pack_id: str,
    object_type: str,
    *,
    field_count: int,
    stage_field: bool,
    stage_bindings: int,
    other_bindings: int,
    validator_agent_id: str | None = VALIDATOR_AGENT,
    custom_stage_binding: bool = False,
    oversized_prefixes: int = 0,
    description_chars: int = 0,
) -> DomainPackValidationRegistry:
    field_paths = [f"attribute_{index:03d}_term_name" for index in range(field_count)]
    if stage_field:
        field_paths.insert(field_count // 2, STAGE_FIELD)
    fields = [
        {
            "field_path": field_path,
            "display_name": field_path.replace("_", " ").capitalize(),
            "description": (
                "D" * description_chars
                if description_chars and field_path == STAGE_FIELD
                else (
                    f"Synthetic {object_type} slot {field_path}. "
                    "Mirrors provider slot documentation carried by packaged domain packs. "
                    * 3
                )
            ),
            "field_type": "string",
            "required": index % 3 == 0 or field_path == STAGE_FIELD,
            "metadata": {
                "source_of_truth": "fixture_linkml",
                "provider_refs": _provider_refs(pack_id, object_type, field_path),
            },
        }
        for index, field_path in enumerate(field_paths)
    ]
    fields.extend(
        [
            {"field_path": "stage_kind", "field_type": "enum", "enum_ref": "StageKind"},
            {
                "field_path": "stage_term",
                "field_type": "object",
                "model_ref": f"{object_type}Payload",
            },
        ]
    )
    bindings = []
    for index in range(stage_bindings if stage_field else 0):
        binding = {
            "binding_id": f"stage_term_lookup_{index:02d}",
            "display_name": f"Stage term lookup {index}",
            "description": "Checks the developmental stage against the species stage ontology.",
            "applies_to": {
                "domain_pack_id": pack_id,
                "object_types": [object_type],
                "field_paths": [STAGE_FIELD],
            },
            "input_fields": {"label": {"source": "payload", "path": STAGE_FIELD}},
            "expected_result_fields": {"curie": STAGE_FIELD},

            "required": True,
            "allow_opt_out": True,
        }
        if validator_agent_id:
            binding["validator_agent"] = {
                "package_id": VALIDATOR_PACKAGE,
                "agent_id": validator_agent_id,
            }
        if oversized_prefixes and index == 0:
            binding["input_fields"]["accepted_prefixes"] = {
                "source": "literal",
                "value": [f"STAGE{number:05d}" for number in range(oversized_prefixes)],
            }
        bindings.append(binding)
    for index in range(other_bindings):
        target = field_paths[(index * 7) % len(field_paths)]
        if target == STAGE_FIELD:
            target = field_paths[0]
        binding = {
            "binding_id": f"term_lookup_{index:03d}",
            "display_name": f"Term lookup {index}",
            "description": "Checks an ontology-backed attribute against its ontology.",
            "applies_to": {
                "domain_pack_id": pack_id,
                "object_types": [object_type],
                "field_paths": [target],
            },
            "input_fields": {"label": {"source": "payload", "path": target}},
            "expected_result_fields": {"curie": target},
            "required": True,
            "allow_opt_out": True,
        }
        if validator_agent_id:
            binding["validator_agent"] = {
                "package_id": VALIDATOR_PACKAGE,
                "agent_id": validator_agent_id,
            }
        bindings.append(binding)
    metadata = DomainPackMetadata.model_validate(
        {
            "pack_id": pack_id,
            "display_name": pack_id.title(),
            "version": "0.1.0",
            "metadata_api_version": "1.0.0",
            "schema_refs": [
                {"schema_id": "fixture.linkml", "provider": "fixture", "name": "Fixture"}
            ],
            "enum_definitions": [
                {
                    "enum_id": "StageKind",
                    "display_name": "Stage kind",
                    "values": [{"value": "embryonic"}, {"value": "larval"}],
                }
            ],
            "model_definitions": [
                {"model_id": f"{object_type}Payload", "display_name": f"{object_type} payload"}
            ],
            "object_definitions": [
                {
                    "object_type": object_type,
                    "display_name": object_type,
                    "model_ref": f"{object_type}Payload",
                    "metadata": {"object_role": "curatable_unit"},
                    "fields": fields,
                }
            ],
            "metadata": {
                "validators": {
                    "active": [
                        {
                            "validator_id": f"{pack_id}.declared_validator",
                            "display_name": "Declared validator",
                            "description": "Pack-level declarative validator entry.",
                        }
                    ]
                },
                "validator_bindings": {"active": bindings},
            },
        }
    )
    loaded = LoadedDomainPack(
        pack_id=pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=Path("/nonexistent") / pack_id,
        metadata_path=Path("/nonexistent") / pack_id / "domain_pack.yaml",
        metadata=metadata,
        package_id="org.extractors",
    )
    registry = DomainPackValidationRegistry.from_domain_pack(loaded)
    if not custom_stage_binding:
        return registry
    # Saved Workshop revisions reuse a packaged binding at runtime with an exact
    # custom pin (see custom_profile_validators); mirror that shape here.
    source = next(b for b in registry.bindings if STAGE_FIELD in b.field_paths)
    custom = replace(
        source,
        binding_id=f"{source.binding_id}--custom--rev-1",
        display_name="Curator stage lookup",
        raw={**source.raw, "custom_validator": {"agent_id": "ca_stage", "revision_id": "rev-1"}},
    )
    return replace(registry, bindings=(*registry.bindings, custom))


def _base_wide_registries() -> dict[str, DomainPackValidationRegistry]:
    return {
        "fixture.expression": _wide_pack(
            "fixture.expression",
            "ExpressionAnnotation",
            field_count=90,
            stage_field=True,
            stage_bindings=16,
            other_bindings=30,
            custom_stage_binding=True,
        ),
        "fixture.disease": _wide_pack(
            "fixture.disease",
            "DiseaseAnnotation",
            field_count=140,
            stage_field=False,
            stage_bindings=0,
            other_bindings=40,
        ),
        "fixture.phenotype": _wide_pack(
            "fixture.phenotype",
            "PhenotypeAnnotation",
            field_count=90,
            stage_field=True,
            stage_bindings=14,
            other_bindings=25,
        ),
        # Same field name, never bound to the validator: must never be merged in.
        "fixture.unrelated": _wide_pack(
            "fixture.unrelated",
            "UnrelatedAnnotation",
            field_count=40,
            stage_field=True,
            stage_bindings=3,
            other_bindings=0,
            validator_agent_id="some_other_validator",
        ),
    }




def _wide_registries(**expression_overrides) -> dict[str, DomainPackValidationRegistry]:
    registries = _base_wide_registries()
    if expression_overrides:
        registries["fixture.expression"] = _wide_pack(
            "fixture.expression",
            "ExpressionAnnotation",
            field_count=90,
            stage_field=True,
            stage_bindings=16,
            other_bindings=30,
            custom_stage_binding=True,
            **expression_overrides,
        )
    return registries


class WideEnvelope(BaseModel):
    """Synthetic wide output schema."""


def _wide_schema() -> type[BaseModel]:
    stage = create_model(
        "StageTerm",
        curie=(str, Field(description="Stage CURIE")),
        label=(str | None, None),
    )
    fields = {
        f"slot_{index:03d}": (str | None, Field(None, description=f"Slot {index} " * 4))
        for index in range(80)
    }
    fields["stage"] = (stage, Field(description="Resolved stage term"))
    return create_model("WideEnvelope", __base__=WideEnvelope, **fields)


def _agent_registry() -> dict[str, dict]:
    return {
        VALIDATOR_AGENT: {
            "name": "Fixture Ontology Validator",
            "category": "Validation",
            "package_id": VALIDATOR_PACKAGE,
            "tools": [f"tool_{index:02d}" for index in range(30)],
            "output_schema": "WideEnvelope",
            "curation": {"domain_pack_id": None},
        },
        "fixture_expression_extractor": {
            "name": "Fixture Expression Extractor",
            "category": "Extraction",
            "package_id": "org.extractors",
            "tools": ["tool_00"],
            "output_schema": "WideEnvelope",
            "curation": {"domain_pack_id": "fixture.expression"},
        },
    }


def _tool_details(_agent_id: str, tool_id: str) -> dict:
    return {
        "name": tool_id,
        "description": f"Synthetic tool {tool_id}.",
        "category": "Lookup",
        "required_context": [],
        "documentation": {"summary": "Synthetic documentation. " * 20},
    }


def _call(**kwargs):
    kwargs.setdefault("agent_registry", _agent_registry())
    kwargs.setdefault("registries", _wide_registries())
    kwargs.setdefault("tool_details_resolver", _tool_details)
    kwargs.setdefault("output_schema_resolver", lambda _name: _wide_schema())
    return get_agent_contract(**kwargs)


def _all_pages(**kwargs) -> tuple[list[dict], list[dict]]:
    responses: list[dict] = []
    items: list[dict] = []
    cursor = None
    while True:
        response = _call(cursor=cursor, **kwargs)
        assert response["success"] is True, response
        responses.append(response)
        items.extend(response["items"])
        cursor = response["page"]["next_cursor"]
        if cursor is None:
            return responses, items


def _chars(value) -> int:
    return len(json.dumps(value))


def _default_budget() -> int:
    return agent_contracts.get_agent_contract_max_response_chars()


@pytest.fixture
def reported(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        agent_contracts,
        "report_payload_contract_violation",
        lambda violation, **kwargs: calls.append((violation, kwargs)) or True,
    )
    return calls


def _legacy_detail_chars(registries) -> int:
    """Size of the pack-wide detail the 0.9.21 implementation returned."""

    return sum(
        _chars([policy.identity_details() for policy in registry.field_policies])
        + _chars([option.to_dict() for option in registry.validation_attachment_options()])
        + _chars([binding.identity_details() for binding in registry.bindings])
        for pack_id, registry in registries.items()
        if pack_id != "fixture.unrelated"
    )


def test_recorded_calls_are_field_scoped_and_bounded_on_wide_registry(monkeypatch, reported):
    registries = _wide_registries()
    assert _legacy_detail_chars(registries) > 389_039

    first = _call(**RECORDED_CALL, limit=50, registries=registries)
    second = _call(**RECORDED_CALL, limit=20, registries=registries)

    for response in (first, second):
        assert response["success"] is True
        assert _chars(response) <= _default_budget()
        assert [pack["domain_pack_id"] for pack in response["domain_packs"]] == [
            "fixture.expression",
            "fixture.phenotype",
        ]
        assert {item["kind"] for item in response["items"]} <= {
            "field_policy",
            "validator_binding",
        }
        for item in response["items"]:
            assert item["domain_pack_id"] in {"fixture.expression", "fixture.phenotype"}
            if item["kind"] == "validator_binding":
                assert item["field_paths"] == [STAGE_FIELD]
                assert item["targeted_to_agent"] is True
                assert all(
                    option["field_path"] == STAGE_FIELD
                    for option in item["validation_attachments"]
                )
            else:
                assert item["field_path"] == STAGE_FIELD
        assert response["page"]["truncated"] is True
        assert response["page"]["next_cursor"]

    # With a budget that fits the whole scoped collection, page size decides.
    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "80000")
    roomy_first = _call(**RECORDED_CALL, limit=50, registries=registries)
    roomy_second = _call(**RECORDED_CALL, limit=20, registries=registries)
    assert roomy_first["page"]["total_count"] == 33
    assert roomy_first["page"]["returned_count"] == 33
    assert roomy_first["page"]["stopped_by"] == "end"
    assert roomy_second["page"]["returned_count"] == 20
    assert roomy_second["page"]["stopped_by"] == "limit"
    assert roomy_first["items"][:20] == roomy_second["items"]
    assert reported == []


def test_page_size_changes_default_summary_page():
    fifty = _call(**{**RECORDED_CALL, "detail_level": "summary"}, limit=50)
    twenty = _call(**{**RECORDED_CALL, "detail_level": "summary"}, limit=20)

    assert fifty["page"]["total_count"] == 31
    assert fifty["page"]["returned_count"] == 31
    assert twenty["page"]["returned_count"] == 20
    assert twenty["page"]["truncated"] is True
    assert _chars(fifty) <= _default_budget()
    assert {item["kind"] for item in fifty["items"]} == {"validator_binding"}


def test_following_cursors_returns_complete_stable_sequence(monkeypatch):
    _, paged = _all_pages(**RECORDED_CALL, limit=7)
    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "80000")
    whole = _call(**RECORDED_CALL, limit=50)

    assert [item["ref"] for item in paged] == [item["ref"] for item in whole["items"]]
    assert len({item["ref"] for item in paged}) == len(paged)
    assert _call(**RECORDED_CALL, limit=7)["page"] == _call(**RECORDED_CALL, limit=7)["page"]


@pytest.mark.parametrize("agent_id", [VALIDATOR_AGENT, "fixture_expression_extractor"])
@pytest.mark.parametrize("topic", sorted(AGENT_CONTRACT_TOPICS - {"field"}))
@pytest.mark.parametrize("detail_level", ["summary", "detail"])
def test_every_topic_pages_within_budget(agent_id, topic, detail_level, reported):
    responses, items = _all_pages(agent_id=agent_id, topic=topic, detail_level=detail_level)

    for response in responses:
        assert _chars(response) <= _default_budget()
        assert response["page"]["returned_count"] <= 20
    assert len(items) == responses[0]["page"]["total_count"]
    assert len({item["ref"] for item in items}) == len(items)
    packs = {item.get("domain_pack_id") for item in items} - {None}
    assert "fixture.unrelated" not in packs
    if agent_id == "fixture_expression_extractor":
        assert packs <= {"fixture.expression"}
    assert reported == []


@pytest.mark.parametrize(
    "topic", ["field", "validator_bindings", "ontology_constraints", "domain_envelope"]
)
@pytest.mark.parametrize("detail_level", ["summary", "detail"])
def test_every_pack_topic_honors_field_scope(topic, detail_level):
    agent_id = "fixture_expression_extractor" if topic == "domain_envelope" else VALIDATOR_AGENT
    responses, items = _all_pages(
        agent_id=agent_id,
        topic=topic,
        detail_level=detail_level,
        field_path=STAGE_FIELD,
    )

    assert items
    for item in items:
        assert STAGE_FIELD in json.dumps(item)
        if "field_path" in item and item["kind"] != "validator_binding":
            assert item["field_path"] == STAGE_FIELD
    assert all(_chars(response) <= _default_budget() for response in responses)


@pytest.mark.parametrize(
    "topic", ["field", "validator_bindings", "ontology_constraints", "domain_envelope"]
)
def test_unknown_field_is_actionable_and_never_falls_back(topic, reported):
    agent_id = "fixture_expression_extractor" if topic == "domain_envelope" else VALIDATOR_AGENT
    result = _call(
        agent_id=agent_id,
        topic=topic,
        field_path="when_expressed_stage",
        detail_level="detail",
    )

    assert result["success"] is False
    assert "was not found" in result["error"]
    assert "items" not in result
    assert STAGE_FIELD in result["suggested_field_paths"]
    assert "fixture.unrelated" not in result["searched_domain_pack_ids"]
    assert _chars(result) < 2_000
    assert reported == []


def test_output_schema_unknown_field_is_actionable():
    result = _call(agent_id=VALIDATOR_AGENT, topic="output_schema", field_path="slot_1000")

    assert result["success"] is False
    assert "slot_100" in result["suggested_field_paths"] or result["suggested_field_paths"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"topic": "tools", "field_path": STAGE_FIELD}, "not supported for topic 'tools'"),
        (
            {"topic": "output_schema", "domain_pack_id": "fixture.expression"},
            "not supported for topic 'output_schema'",
        ),
        ({"topic": "tools", "detail_pointer": "/name"}, "detail_pointer requires item_ref"),
        (
            {"topic": "validator_bindings", "domain_pack_id": "fixture.unrelated"},
            "is not in scope",
        ),
        ({"topic": "ontology_constraints", "object_type": "Missing"}, "Object type 'Missing'"),
        ({"topic": "field"}, "field_path is required"),
        (
            {"topic": "domain_envelope", "field_path": STAGE_FIELD},
            "has no domain packs in scope",
        ),
        (
            {"topic": "tools", "item_ref": "tool|not_attached"},
            "item_ref 'tool|not_attached' was not found",
        ),
    ],
)
def test_unsupported_selector_combinations_are_rejected(kwargs, message, reported):
    result = _call(agent_id=VALIDATOR_AGENT, **kwargs)

    assert result["success"] is False
    assert message in result["error"]
    assert reported == []


def test_out_of_scope_pack_lists_available_packs():
    result = _call(
        agent_id=VALIDATOR_AGENT,
        topic="validator_bindings",
        domain_pack_id="fixture.unrelated",
    )

    assert result["available_domain_pack_ids"] == [
        "fixture.disease",
        "fixture.expression",
        "fixture.phenotype",
    ]


def test_duplicate_field_names_across_packs_are_pack_qualified_and_selectable():
    both = _call(agent_id=VALIDATOR_AGENT, topic="field", field_path=STAGE_FIELD)
    one_pack = _call(
        agent_id=VALIDATOR_AGENT,
        topic="field",
        field_path=STAGE_FIELD,
        domain_pack_id="fixture.phenotype",
    )
    qualified = _call(
        agent_id=VALIDATOR_AGENT,
        topic="field",
        field_path=f"PhenotypeAnnotation.{STAGE_FIELD}",
    )
    by_object = _call(
        agent_id=VALIDATOR_AGENT,
        topic="field",
        field_path=STAGE_FIELD,
        object_type="ExpressionAnnotation",
    )

    assert [item["ref"] for item in both["items"]] == [
        f"field|fixture.expression|ExpressionAnnotation|{STAGE_FIELD}",
        f"field|fixture.phenotype|PhenotypeAnnotation|{STAGE_FIELD}",
    ]
    assert [item["domain_pack_id"] for item in one_pack["items"]] == ["fixture.phenotype"]
    assert [item["domain_pack_id"] for item in qualified["items"]] == ["fixture.phenotype"]
    assert [item["domain_pack_id"] for item in by_object["items"]] == ["fixture.expression"]


def test_custom_and_packaged_bindings_keep_origin_and_pack_ownership(monkeypatch):
    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "80000")
    validator = _call(
        **RECORDED_CALL,
        limit=50,
        domain_pack_id="fixture.expression",
    )
    extractor = _call(
        agent_id="fixture_expression_extractor",
        topic="validator_bindings",
        field_path=STAGE_FIELD,
        detail_level="detail",
        limit=50,
    )

    bindings = [item for item in validator["items"] if item["kind"] == "validator_binding"]
    custom = [item for item in bindings if item["origin"] == "custom_agent"]
    assert len(custom) == 1
    assert custom[0]["custom_validator"] == {"agent_id": "ca_stage", "revision_id": "rev-1"}
    assert {item["origin"] for item in bindings} == {"package", "custom_agent"}
    assert len(bindings) == 17
    policies = [item for item in validator["items"] if item["kind"] == "field_policy"]
    assert [policy["field_path"] for policy in policies] == [STAGE_FIELD]
    assert policies[0]["required"] is True
    assert "stage_term_lookup_00" in policies[0]["validator_binding_ids"]
    packaged = next(item for item in bindings if item["validator_binding_id"] == "stage_term_lookup_00")
    assert packaged["validation_attachments"][0]["required"] is True
    assert packaged["validation_attachments"][0]["field_path"] == STAGE_FIELD

    # The extractor owns fixture.expression only; the same field name in
    # fixture.phenotype is not merged in.
    assert {item["domain_pack_id"] for item in extractor["items"]} == {"fixture.expression"}
    assert [pack["relation"] for pack in extractor["domain_packs"]] == ["owned"]


def test_field_detail_includes_dependent_definitions():
    enum_field = _call(
        agent_id=VALIDATOR_AGENT,
        topic="field",
        field_path="stage_kind",
        domain_pack_id="fixture.disease",
        detail_level="detail",
    )
    model_field = _call(
        agent_id="fixture_expression_extractor",
        topic="domain_envelope",
        field_path="stage_term",
        detail_level="detail",
    )
    schema_field = _call(
        agent_id=VALIDATOR_AGENT,
        topic="output_schema",
        field_path="stage",
        detail_level="detail",
    )

    enum_definition = enum_field["items"][0]["dependent_definitions"]["enum_definition"]
    assert [value["value"] for value in enum_definition["values"]] == ["embryonic", "larval"]
    model = model_field["items"][0]["dependent_definitions"]["model_definition"]
    assert model["model_id"] == "ExpressionAnnotationPayload"
    schema_item = schema_field["items"][0]
    assert schema_item["field_path"] == "stage"
    assert set(schema_item["definitions"]) == {"StageTerm"}
    assert schema_item["definitions"]["StageTerm"]["properties"]["curie"]["type"] == "string"


def test_invalid_cursors_are_explicit_and_not_reported(reported):
    first = _call(**RECORDED_CALL, limit=5)
    good = first["page"]["next_cursor"]
    other_topic_cursor = _call(agent_id=VALIDATOR_AGENT, topic="tools", limit=5)["page"][
        "next_cursor"
    ]
    fingerprint = good.split(":")[1]

    cases = {
        "abc": "is not a get_agent_contract cursor",
        "5": "is not a get_agent_contract cursor",
        "-5:" + fingerprint: "is not a get_agent_contract cursor",
        other_topic_cursor: "belongs to a different request",
        f"999:{fingerprint}": "is past the end",
    }
    for cursor, message in cases.items():
        result = _call(**RECORDED_CALL, limit=5, cursor=cursor)
        assert result["success"] is False, cursor
        assert message in result["error"]
        assert "items" not in result
    assert _call(**RECORDED_CALL, limit=5, cursor=good)["page"]["offset"] == 5
    assert reported == []


def test_shared_bounded_list_cursor_semantics_are_unchanged():
    from src.lib.openai_agents.bounded_list import parse_offset_cursor

    assert parse_offset_cursor("abc") == 0
    assert parse_offset_cursor("7") == 7


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(None, 20), (0, 1), (-5, 1), (3, 3), (10**9, 50)],
)
def test_omitted_and_extreme_limits_are_clamped(limit, expected):
    result = _call(agent_id=VALIDATOR_AGENT, topic="output_schema", limit=limit)

    assert result["page"]["limit"] == expected
    assert result["page"]["returned_count"] <= expected
    assert _chars(result) <= _default_budget()


def test_oversized_single_definition_uses_exact_bounded_drilldown(reported):
    registries = _wide_registries(oversized_prefixes=6000)
    base = {**RECORDED_CALL, "registries": registries, "domain_pack_id": "fixture.expression"}

    listing = _call(**base, limit=50)
    outlined = [item for item in listing["items"] if item.get("detail_complete") is False]
    assert [item["ref"] for item in outlined] == [
        "binding|fixture.expression|stage_term_lookup_00"
    ]
    outline = outlined[0]
    assert outline["validator_binding_id"] == "stage_term_lookup_00"
    assert outline["serialized_chars"] > agent_contracts.get_agent_contract_max_item_chars()
    pointers = {value["detail_pointer"] for value in outline["omitted_values"]}
    assert "/input_fields" in pointers
    assert listing["drilldown"]["repeat_arguments"]["field_path"] == STAGE_FIELD

    inputs = _call(**base, item_ref=outline["ref"], detail_pointer="/input_fields")
    by_key = {entry["key"]: entry for entry in inputs["entries"]}
    assert by_key["label"]["value"]["path"] == STAGE_FIELD
    assert by_key["accepted_prefixes"]["detail_complete"] is False

    literal = _call(
        **base,
        item_ref=outline["ref"],
        detail_pointer=by_key["accepted_prefixes"]["detail_pointer"],
    )
    literal_entries = {entry["key"]: entry for entry in literal["entries"]}
    assert literal_entries["source"]["value"] == "literal"
    assert literal_entries["value"]["detail_complete"] is False
    assert literal_entries["value"]["value_type"] == "array"
    assert literal_entries["value"]["detail_pointer"] == "/input_fields/accepted_prefixes/value"

    values: list[str] = []
    cursor = None
    while True:
        page = _call(
            **base,
            item_ref=outline["ref"],
            detail_pointer="/input_fields/accepted_prefixes/value",
            limit=50,
            cursor=cursor,
        )
        assert page["success"] is True
        assert _chars(page) <= _default_budget()
        values.extend(entry["value"] for entry in page["entries"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    assert values == [f"STAGE{number:05d}" for number in range(6000)]
    assert reported == []


def test_single_scalar_over_item_budget_is_reported_once(reported):
    registries = _wide_registries(description_chars=20_000)
    base = {
        "agent_id": VALIDATOR_AGENT,
        "topic": "field",
        "field_path": STAGE_FIELD,
        "domain_pack_id": "fixture.expression",
        "detail_level": "detail",
        "registries": registries,
    }

    listing = _call(**base)
    item = listing["items"][0]
    assert item["detail_complete"] is False
    field_view = _call(**base, item_ref=item["ref"], detail_pointer="/field")
    description = next(entry for entry in field_view["entries"] if entry["key"] == "description")
    assert description["detail_complete"] is False
    assert reported == []

    result = _call(**base, item_ref=item["ref"], detail_pointer=description["detail_pointer"])

    assert result["success"] is False
    assert result["failure"]["category"] == "tool_result_budget_escape"
    assert result["failure"]["setting"] == "AGENT_CONTRACT_MAX_ITEM_CHARS"
    assert len(reported) == 1
    violation, context = reported[0]
    assert violation.category == "tool_result_budget_escape"
    assert context["tool_name"] == "get_agent_contract"
    assert context["agent"] == VALIDATOR_AGENT


def test_unserializable_metadata_reports_contract_serialization_failure(reported):
    def broken_details(_agent_id: str, tool_id: str) -> dict:
        return {"name": tool_id, "required_context": [object()]}

    result = _call(agent_id=VALIDATOR_AGENT, topic="tools", tool_details_resolver=broken_details)

    assert result["success"] is False
    assert result["failure"]["category"] == "contract_serialization_failure"
    assert len(reported) == 1
    assert reported[0][0].category == "contract_serialization_failure"


class ScriptedOntologyValidator(Model):
    """Mocked validator: scoped contract lookup, then a result from that contract."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_outputs: list[str] = []

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.calls += 1
        self.tool_outputs = [
            item["output"]
            for item in input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if self.calls in (1, 2):
            arguments = {**RECORDED_CALL, "limit": 50 if self.calls == 1 else 20}
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        type="function_call",
                        name="get_agent_contract",
                        call_id=f"contract-{self.calls}",
                        arguments=json.dumps(arguments),
                    )
                ],
                usage=Usage(requests=1),
                response_id=f"response-{self.calls}",
            )
        contract = ast.literal_eval(self.tool_outputs[-1])
        binding = next(
            item
            for item in contract["items"]
            if item["kind"] == "validator_binding" and item["origin"] == "package"
        )
        answer = {
            "binding": binding["validator_binding_id"],
            "input": binding["input_fields"]["label"]["path"],
            "expected": binding["expected_result_fields"],
        }
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="final",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            type="output_text", text=json.dumps(answer), annotations=[]
                        )
                    ],
                )
            ],
            usage=Usage(requests=1),
            response_id="response-final",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Non-streaming validator expected")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_mocked_validator_run_uses_scoped_contract_without_registry_payload(
    monkeypatch, reported
):
    from src.lib.openai_agents.tools.agent_contract import get_agent_contract as contract_tool

    registries = _wide_registries()
    monkeypatch.setattr(agent_contracts, "domain_pack_validation_registries", lambda: registries)
    monkeypatch.setattr(agent_contracts, "_default_agent_registry", _agent_registry)
    model = ScriptedOntologyValidator()
    agent = Agent(name="ontology validator", model=model, tools=[contract_tool])

    result = await Runner.run(agent, "validate stage", run_config=RunConfig(tracing_disabled=True))

    assert model.calls == 3
    assert len(model.tool_outputs) == 2
    assert all(len(output) <= _default_budget() for output in model.tool_outputs)
    assert sum(len(output) for output in model.tool_outputs) < 389_039 // 8
    assert "fixture.disease" not in "".join(model.tool_outputs)
    assert "fixture.unrelated" not in "".join(model.tool_outputs)
    answer = json.loads(result.final_output)
    assert answer == {
        "binding": "stage_term_lookup_00",
        "input": STAGE_FIELD,
        "expected": {"curie": STAGE_FIELD},
    }
    assert reported == []


def test_contract_budget_settings_are_env_configurable_and_clamped(monkeypatch):
    from src.lib.openai_agents import config

    monkeypatch.delenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", raising=False)
    monkeypatch.delenv("AGENT_CONTRACT_MAX_ITEM_CHARS", raising=False)
    assert config.get_agent_contract_max_response_chars() == 24000
    assert config.get_agent_contract_max_item_chars() == 8000

    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "10")
    monkeypatch.setenv("AGENT_CONTRACT_MAX_ITEM_CHARS", "999999")
    assert config.get_agent_contract_max_response_chars() == 4000
    assert config.get_agent_contract_max_item_chars() == 2000

    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "6000")
    monkeypatch.setenv("AGENT_CONTRACT_MAX_ITEM_CHARS", "1")
    assert config.get_agent_contract_max_item_chars() == 1000
    responses, _items = _all_pages(**RECORDED_CALL)
    assert all(_chars(response) <= 6000 for response in responses)


@pytest.mark.parametrize("pointer", ["/validator_bindings/²", "/validator_bindings/00", "field"])
def test_malformed_detail_pointers_are_structured_errors(pointer, reported):
    base = {
        "agent_id": VALIDATOR_AGENT,
        "topic": "field",
        "field_path": STAGE_FIELD,
        "domain_pack_id": "fixture.expression",
    }
    ref = _call(**base)["items"][0]["ref"]

    result = _call(**base, item_ref=ref, detail_pointer=pointer)

    assert result["success"] is False
    assert "detail_pointer" in result["error"]
    assert reported == []


def test_known_field_without_covering_entries_explains_empty_page():
    result = _call(
        agent_id=VALIDATOR_AGENT,
        topic="ontology_constraints",
        field_path="stage_kind",
        domain_pack_id="fixture.expression",
    )

    assert result["success"] is True
    assert result["items"] == []
    assert "exists in scope" in result["note"]


def test_declared_validators_keep_their_attachment_options():
    _, items = _all_pages(
        agent_id="fixture_expression_extractor",
        topic="validator_bindings",
        detail_level="detail",
    )

    validators = [item for item in items if item["kind"] == "validator"]
    assert [item["validator_id"] for item in validators] == [
        "fixture.expression.declared_validator"
    ]
    assert validators[0]["validation_attachments"][0]["scope"] == "pack"


def test_cursor_is_invalidated_when_collection_changes_without_version_bump():
    registries = _wide_registries()
    first = _call(**RECORDED_CALL, limit=5, registries=registries)
    cursor = first["page"]["next_cursor"]

    expression = registries["fixture.expression"]
    source = next(b for b in expression.bindings if b.binding_id == "stage_term_lookup_00")
    added = replace(source, binding_id="stage_term_lookup_added")
    changed = {
        **registries,
        "fixture.expression": replace(expression, bindings=(*expression.bindings, added)),
    }
    assert (
        changed["fixture.expression"].domain_pack.metadata.version
        == expression.domain_pack.metadata.version
    )

    stale = _call(**RECORDED_CALL, limit=5, cursor=cursor, registries=changed)
    assert stale["success"] is False
    assert "belongs to a different request" in stale["error"]
    assert _call(**RECORDED_CALL, limit=5, cursor=cursor, registries=registries)["success"] is True


def test_contract_clamp_warnings_are_emitted_once_per_value(monkeypatch, caplog):
    from src.lib.openai_agents import config

    monkeypatch.setattr(config, "_AGENT_CONTRACT_CLAMP_WARNINGS", set())
    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "10")
    monkeypatch.setenv("AGENT_CONTRACT_MAX_ITEM_CHARS", "5")
    with caplog.at_level("WARNING", logger=config.logger.name):
        for _ in range(3):
            config.get_agent_contract_max_item_chars()
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count(
        "AGENT_CONTRACT_MAX_RESPONSE_CHARS=10 is below minimum 4000; using 4000"
    ) == 1
    assert messages.count("AGENT_CONTRACT_MAX_ITEM_CHARS=5 is below minimum 1000; using 1000") == 1

    caplog.clear()
    monkeypatch.setenv("AGENT_CONTRACT_MAX_ITEM_CHARS", "7")
    with caplog.at_level("WARNING", logger=config.logger.name):
        config.get_agent_contract_max_item_chars()
        config.get_agent_contract_max_item_chars()
    assert [record.getMessage() for record in caplog.records] == [
        "AGENT_CONTRACT_MAX_ITEM_CHARS=7 is below minimum 1000; using 1000"
    ]


def test_default_item_budget_caps_silently_but_explicit_value_warns(monkeypatch, caplog):
    from src.lib.openai_agents import config

    monkeypatch.setattr(config, "_AGENT_CONTRACT_CLAMP_WARNINGS", set())
    monkeypatch.setenv("AGENT_CONTRACT_MAX_RESPONSE_CHARS", "10000")
    monkeypatch.delenv("AGENT_CONTRACT_MAX_ITEM_CHARS", raising=False)
    with caplog.at_level("WARNING", logger=config.logger.name):
        assert config.get_agent_contract_max_item_chars() == 5000
    assert caplog.records == []

    monkeypatch.setenv("AGENT_CONTRACT_MAX_ITEM_CHARS", "9000")
    with caplog.at_level("WARNING", logger=config.logger.name):
        assert config.get_agent_contract_max_item_chars() == 5000
        assert config.get_agent_contract_max_item_chars() == 5000
    assert [record.getMessage() for record in caplog.records] == [
        "AGENT_CONTRACT_MAX_ITEM_CHARS=9000 exceeds half of "
        "AGENT_CONTRACT_MAX_RESPONSE_CHARS; using 5000"
    ]
