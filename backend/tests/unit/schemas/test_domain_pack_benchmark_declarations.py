"""Benchmark declarations in domain packs: record-kind families and free-text fields."""

import pytest
from pydantic import ValidationError

from src.lib.flows.validation_attachments import domain_pack_validation_registries
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


def _pack(families, *, roles=None):
    roles = roles or {"A": "curatable_unit", "B": "curatable_unit", "C": "validated_reference"}
    return {
        "pack_id": "fixture.pack", "display_name": "Fixture", "version": "0.1.0",
        "metadata_api_version": "1.0.0",
        "object_definitions": [
            {"object_type": name, "display_name": name, "metadata": {"object_role": role}}
            for name, role in roles.items()
        ],
        "metadata": {"object_role_key": "object_role", "record_kind_families": families},
    }


def _family(**overrides):
    return {"id": "things", "label": "Things", "fallback_object_type": "A",
            "object_types": ["A", "B"], **overrides}


def test_family_of_curatable_units_loads():
    metadata = DomainPackMetadata.model_validate(_pack([_family()]))
    assert metadata.metadata["record_kind_families"][0]["object_types"] == ["A", "B"]


@pytest.mark.parametrize("family,message", [
    (_family(fallback_object_type="C", object_types=["A", "C"]), "not a curatable unit"),
    (_family(fallback_object_type="Z"), "fallback_object_type must be one of its object_types"),
    (_family(object_types=["A", "Missing"]), "unknown object_type 'Missing'"),
])
def test_invalid_family_is_rejected_at_load(family, message):
    with pytest.raises(ValidationError, match=message):
        DomainPackMetadata.model_validate(_pack([family]))


def test_object_type_belongs_to_one_family_only():
    second = _family(id="others", fallback_object_type="B", object_types=["B", "A"])
    with pytest.raises(ValidationError, match="belongs to both"):
        DomainPackMetadata.model_validate(_pack([_family(), second]))


def test_family_ids_are_unique():
    roles = {"A": "curatable_unit", "B": "curatable_unit", "D": "curatable_unit",
             "E": "curatable_unit"}
    twin = _family(fallback_object_type="D", object_types=["D", "E"])
    with pytest.raises(ValidationError, match="declared twice"):
        DomainPackMetadata.model_validate(_pack([_family(), twin], roles=roles))


def test_free_text_must_be_a_boolean_on_a_string_field():
    DomainPackFieldDefinition(field_path="rationale", field_type="string",
                              metadata={"free_text": True})
    with pytest.raises(ValidationError, match="'free_text' must be a boolean"):
        DomainPackFieldDefinition(field_path="rationale", field_type="string",
                                  metadata={"free_text": "yes"})
    with pytest.raises(ValidationError, match="only valid on string fields"):
        DomainPackFieldDefinition(field_path="count", field_type="integer",
                                  metadata={"free_text": True})


def test_free_text_list_holds_plain_text_items_only():
    DomainPackFieldDefinition(field_path="notes", field_type="array",
                              metadata={"free_text": True})
    with pytest.raises(ValidationError, match="only valid on string fields or lists of text"):
        DomainPackFieldDefinition(field_path="records", field_type="array",
                                  model_ref="RecordPayload", metadata={"free_text": True})
    with pytest.raises(ValidationError, match="declares parts, so it is not free text"):
        DomainPackObjectDefinition(object_type="Thing", display_name="Thing", fields=[
            {"field_path": "notes", "field_type": "array", "metadata": {"free_text": True}},
            {"field_path": "notes.text", "field_type": "string"},
        ])


def _fields(pack_id, object_type):
    pack = domain_pack_validation_registries()[pack_id].domain_pack
    obj = next(o for o in pack.metadata.object_definitions if o.object_type == object_type)
    return {field.field_path: field for field in obj.fields}, pack.metadata


def test_disease_pack_declares_its_family_and_free_text():
    fields, metadata = _fields("agr.alliance.disease", "AGMDiseaseAnnotation")
    assert metadata.metadata["record_kind_families"] == [{
        "id": "disease_annotations", "label": "Disease annotations",
        "fallback_object_type": "DiseaseAnnotation",
        "object_types": ["DiseaseAnnotation", "GeneDiseaseAnnotation",
                         "AlleleDiseaseAnnotation", "AGMDiseaseAnnotation"],
    }]
    assert fields["rationale"].metadata["free_text"] is True
    assert fields["condition_relations.conditions.condition_summary"].metadata["free_text"] is True
    assert "free_text" not in fields["mention"].metadata


@pytest.mark.parametrize("pack_id,object_type,paths", [
    ("agr.alliance.phenotype", "PhenotypeAnnotation",
     ["rationale", "condition_relations.conditions.condition_summary"]),
    ("agr.alliance.gene_expression", "GeneExpressionAnnotation",
     ["rationale", "where_expressed_statement",
      "condition_relations.conditions.condition_summary"]),
    ("agr.alliance.allele", "AllelePaperEvidenceAssociation", ["rationale"]),
    ("agr.alliance.go", "GOCuratableObject", ["rationale", "blocking_reasons"]),
])
def test_prose_fields_are_marked_free_text(pack_id, object_type, paths):
    fields, _ = _fields(pack_id, object_type)
    for path in paths:
        assert fields[path].metadata["free_text"] is True, path
