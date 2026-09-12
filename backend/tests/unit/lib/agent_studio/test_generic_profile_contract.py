"""Closed profile definitions are normalized once, without creating source data."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.lib.agent_studio.profile_compatibility import profile_compatibility
from src.schemas.generic_extraction_profile import normalize_profile_contract


def test_revision_history_rejects_corrupt_fingerprint(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from src.lib.agent_studio import generic_profile_service as service

    monkeypatch.setattr(service, "get_profile", lambda *_args, **_kwargs: None)
    db = MagicMock()
    row = SimpleNamespace(contract=contract(field()), fingerprint="sha256:" + "0" * 64)
    db.execute.return_value.scalars.return_value = [row]
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        service.list_profile_revisions(db, uuid4(), 1)
    row.fingerprint = normalize_profile_contract(row.contract).fingerprint()
    assert service.list_profile_revisions(db, uuid4(), 1) == ([row], None)


def field(key="paper_name", kind="string", **schema):
    return {
        "key": key,
        "required": True,
        "nullable": False,
        "value_schema": {"kind": kind, **schema},
    }


def contract(*fields):
    return {
        "name": "Example record",
        "semantic_class": "example_record",
        "fields": list(fields),
    }


@pytest.mark.parametrize("kind", ["string", "integer", "number", "boolean"])
def test_scalar_kinds_round_trip_without_value_defaults(kind):
    result = normalize_profile_contract(contract(field(kind=kind)))
    assert result.fields[0].value_schema.kind == kind
    assert result.fields[0].required is True
    assert result.fields[0].nullable is False
    assert "default" not in result.fields[0].value_schema.model_dump()
    assert (
        normalize_profile_contract(result.model_dump()).fingerprint()
        == result.fingerprint()
    )


def test_nested_pairs_and_nullable_required_are_distinct():
    raw = contract(
        field(
            "sources",
            "array",
            items={
                "kind": "object",
                "fields": [
                    field("source_name"),
                    field("identifier"),
                ],
            },
        )
    )
    raw["fields"][0]["nullable"] = True
    result = normalize_profile_contract(raw)
    assert result.fields[0].required and result.fields[0].nullable
    assert result.fields[0].value_schema.items.fields[1].key == "identifier"


def test_normalization_and_fingerprint_preserve_order_and_labels():
    raw = contract(
        field("Paper Name"), field("status", "enum", values=["new", "known"])
    )
    raw["fields"][0]["source_labels"] = ["  Alternative   heading "]
    original = deepcopy(raw)
    parsed = normalize_profile_contract(raw)
    assert raw == original
    assert parsed.fields[0].key == "paper_name"
    assert parsed.fields[0].source_labels == ["Alternative heading"]
    canonical = parsed.model_dump(mode="json")
    assert normalize_profile_contract(canonical).fingerprint() == parsed.fingerprint()
    canonical["fields"].reverse()
    assert normalize_profile_contract(canonical).fingerprint() != parsed.fingerprint()
    canonical["fields"][1]["source_labels"] = ["Another heading"]
    assert normalize_profile_contract(canonical).fingerprint() != parsed.fingerprint()


@pytest.mark.parametrize(
    "bad",
    [
        contract(field("label")),
        contract(field("sources", "object", fields=[field("object_id")])),
        contract(field("a.b")),
        contract(field("a"), field(" A ")),
        contract(field(kind="enum", values=[])),
        contract(field(kind="enum", values=["a", " a "])),
        contract(field(kind="enum", values=[""])),
        contract(field(kind="enum", values=[1])),
        contract(field(kind="string", default="invented")),
        contract(field(kind="object", fields=[], additional_fields=True)),
        contract(field(kind="array")),
        contract(field(kind="oneOf")),
        {**contract(), "validator_mappings": [{"validator": "arbitrary"}]},
        {**contract(), "additional_fields": True},
    ],
)
def test_invalid_contracts_fail_closed(bad):
    with pytest.raises(ValidationError):
        normalize_profile_contract(bad)


@pytest.mark.parametrize(
    "labels", [[""], ["a", " A "], ["label"], ["other_field"], ["Other Field"]]
)
def test_source_label_collisions(labels):
    raw = contract(field(), field("other_field"))
    raw["fields"][0]["source_labels"] = labels
    with pytest.raises(ValidationError):
        normalize_profile_contract(raw)


def test_source_label_cannot_identify_two_sibling_fields():
    raw = contract(field("first"), field("second"))
    for item in raw["fields"]:
        item["source_labels"] = ["Paper heading"]
    with pytest.raises(ValidationError, match="source_labels"):
        normalize_profile_contract(raw)


def test_separate_nested_objects_have_separate_label_namespaces():
    child = field("identifier")
    child["source_labels"] = ["ID"]
    parsed = normalize_profile_contract(
        contract(
            field("first", "object", fields=[child]),
            field("second", "object", fields=[deepcopy(child)]),
        )
    )
    assert len(parsed.fields) == 2


@pytest.mark.parametrize(
    "setting,value,bad",
    [
        ("GENERIC_PROFILE_MAX_FIELDS", "1", contract(field("a"), field("b"))),
        (
            "GENERIC_PROFILE_MAX_DEPTH",
            "1",
            contract(field("a", "array", items={"kind": "string"})),
        ),
        ("GENERIC_PROFILE_MAX_CONTRACT_BYTES", "20", contract(field())),
    ],
)
def test_environment_limits_are_enforced(setting, value, bad, monkeypatch):
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValidationError, match=setting):
        normalize_profile_contract(bad)


def test_already_parsed_mutated_model_is_revalidated():
    parsed = normalize_profile_contract(contract(field()))
    parsed.fields[0].key = "label"
    with pytest.raises(ValidationError):
        normalize_profile_contract(parsed)


def test_compatibility_reports_nested_changes_and_separate_metadata_changes():
    old = contract(
        field(
            "sources",
            "array",
            items={"kind": "object", "fields": [field("identifier")]},
        )
    )
    new = deepcopy(old)
    new["fields"][0]["value_schema"]["items"]["fields"][0]["value_schema"]["kind"] = (
        "integer"
    )
    new["name"] = "Renamed display"
    findings = profile_compatibility(old, new)
    assert [(item["path"], item["breaking"]) for item in findings] == [
        ("attributes.sources[].identifier", True),
        ("name", False),
    ]


def test_compatibility_add_remove_required_nullability_and_enum():
    old = contract(field("status", "enum", values=["a", "b"]), field("removed"))
    new = contract(
        field("status", "enum", values=["a"]), field("optional"), field("required")
    )
    new["fields"][1]["required"] = False
    findings = {item["path"]: item for item in profile_compatibility(old, new)}
    assert findings["attributes.optional"]["breaking"] is False
    assert findings["attributes.required"]["breaking"] is True
    assert findings["attributes.removed"]["breaking"] is True
    assert findings["attributes.status"]["breaking"] is True
    changed = deepcopy(old)
    changed["fields"][0]["nullable"] = True
    changed["fields"][0]["required"] = False
    assert all(not item["breaking"] for item in profile_compatibility(old, changed))
    assert all(item["breaking"] for item in profile_compatibility(changed, old))


def test_contract_openapi_describes_discriminated_value_types():
    parsed = normalize_profile_contract(contract())
    schema = parsed.model_json_schema()
    assert schema["additionalProperties"] is False
    assert (
        schema["$defs"]["ProfileField"]["properties"]["value_schema"]["discriminator"][
            "propertyName"
        ]
        == "kind"
    )
