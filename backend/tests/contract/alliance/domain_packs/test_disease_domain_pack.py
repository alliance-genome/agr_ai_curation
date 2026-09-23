"""Contract tests for the Alliance disease domain pack."""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
    project_validation_summary_projections,
)
from src.lib.domain_packs.resolvable_values import (
    OUTCOME_MATCHED,
    RESOLVED,
    unresolved_value,
)
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    field_path_exists,
)
from src.schemas.domain_pack_metadata import DomainPackFieldType
from src.schemas.domain_validator import DomainValidatorResultBase, ValidatorFieldResolution

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    OBJECT_ROLE_METADATA_KEY,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_DOMAIN_PACK_VERSION,
    DISEASE_FIXTURE_PACK_ID,
    DISEASE_LINKML_SCHEMA_ID,
    DISEASE_MODEL_ID,
    DISEASE_OBJECT_TYPE,
    DISEASE_VALIDATOR_STATES,
    get_disease_domain_pack_metadata_path,
    tool_verified_disease_output_to_pending_envelope,
    validate_pending_disease_envelope,
)

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)

DISEASE_PACK_DIR = REPO_ROOT / "packages" / "alliance" / "domain_packs" / "disease"
DISEASE_RAW_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "disease"
    / "tool_verified_disease_output.yaml"
)
FORBIDDEN_LEGACY_COLLECTIONS = {
    "items",
    "annotations",
    "genes",
    "alleles",
    "diseases",
    "chemicals",
    "phenotypes",
    "CurationPrepCandidate",
    "NormalizedCandidate",
    "normalized_payload",
    "annotation_drafts",
}


def _disease_pack():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(DISEASE_DOMAIN_PACK_ID)
    assert pack is not None
    return pack


def _disease_object_definition():
    metadata = _disease_pack().metadata
    return next(
        item
        for item in metadata.object_definitions
        if item.object_type == DISEASE_OBJECT_TYPE
    )


def _staged_list(mentions: list[str], identity_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """An extractor-proposed list as the builder stages it: one unresolved value per entry."""

    return [unresolved_value(mention, identity_keys=identity_keys) for mention in mentions]


def _load_raw_disease_fixture() -> dict[str, Any]:
    return yaml.safe_load(DISEASE_RAW_FIXTURE_PATH.read_text(encoding="utf-8"))


def _iter_mapping_keys(value: Any):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def test_disease_domain_pack_loads_from_alliance_registry():
    registry = load_alliance_domain_pack_registry()
    loaded_pack = registry.get_pack(DISEASE_DOMAIN_PACK_ID)

    assert registry.failed_packs == ()
    assert loaded_pack is not None
    assert loaded_pack.metadata_path == get_disease_domain_pack_metadata_path()
    assert loaded_pack.metadata.version == DISEASE_DOMAIN_PACK_VERSION

    fixture_ref = registry.get_fixture_pack_ref(
        DISEASE_DOMAIN_PACK_ID,
        DISEASE_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/tool_verified.yaml"


def test_disease_pack_declares_pending_assertion_metadata_and_validator_states():
    metadata = _disease_pack().metadata
    disease_object = _disease_object_definition()
    object_metadata = disease_object.metadata

    assert metadata.pack_id == DISEASE_DOMAIN_PACK_ID
    # FULL LinkML alignment (D1): the pack declares the concrete Gene/Allele/AGM subtypes
    # the builder materializes by subject kind. Abstract DiseaseAnnotation remains only as the
    # conceptual parent and unknown-subject fallback.
    object_types = [item.object_type for item in metadata.object_definitions]
    assert DISEASE_OBJECT_TYPE in object_types
    assert {
        "GeneDiseaseAnnotation",
        "AlleleDiseaseAnnotation",
        "AGMDiseaseAnnotation",
    } <= set(object_types)
    assert disease_object.model_ref == DISEASE_MODEL_ID
    assert metadata.status.value == "active"
    assert disease_object.definition_state.value == "stable"
    assert object_metadata[OBJECT_ROLE_METADATA_KEY] == "curatable_unit"
    assert object_metadata["assertion_kind"] == "pending_disease_assertion"
    assert "write_behavior" not in object_metadata
    assert "export_behavior" not in object_metadata
    assert "curatable_unit_object_type" not in metadata.metadata

    declared_curatable_types = {
        item.metadata.get("curatable_unit_object_type")
        for item in metadata.object_definitions
        if item.metadata.get("curatable_unit_object_type") is not None
    }
    assert declared_curatable_types == {
        "GeneDiseaseAnnotation",
        "AlleleDiseaseAnnotation",
        "AGMDiseaseAnnotation",
    }

    definition_state_summary = object_metadata["definition_state_summary"]
    assert {
        "disease_annotation_object",
        "disease_annotation_subject",
        "disease_relation",
        "evidence_code_curies",
        "condition_relations",
        "data_provider",
    } <= set(definition_state_summary["complete"])
    assert "under_development" not in definition_state_summary
    assert definition_state_summary["blocked"] == ["single_reference"]

    validators = metadata.metadata["validators"]
    assert tuple(validators) == DISEASE_VALIDATOR_STATES
    assert all(validators[state] for state in DISEASE_VALIDATOR_STATES)
    assert [
        validator["validator_id"] for validator in validators["under_development"]
    ] == ["disease_reference_materialization"]

    validator_bindings = metadata.metadata["validator_bindings"]
    binding_items = [
        *validator_bindings["active"],
        *validator_bindings["under_development"],
    ]
    binding_ids = {binding["binding_id"] for binding in binding_items}
    assert len(binding_items) == len(binding_ids)
    assert {
        "disease_ontology_term_lookup",
        "disease_relation_cv_lookup",
        "experimental_condition_validation",
        "disease_condition_relation_lookup",
        "disease_subject_materialization",
        "disease_reference_materialization",
        "disease_evidence_code_lookup",
        "disease_data_provider_lookup",
        # R4 optional-slot bindings.
        "disease_annotation_type_cv_lookup",
        "disease_genetic_sex_cv_lookup",
        "disease_qualifier_cv_lookup",
        "disease_with_gene_validation",
    } == binding_ids

    active_binding_ids = {
        binding["binding_id"] for binding in validator_bindings["active"]
    }
    assert {
        "disease_ontology_term_lookup",
        "disease_relation_cv_lookup",
        "disease_condition_relation_lookup",
        "disease_data_provider_lookup",
        # Composite experimental-condition validation is now ACTIVE (condition grain).
        "experimental_condition_validation",
    }.issubset(active_binding_ids)
    # D2 + D3 full LinkML alignment: subject + ECO evidence-code bindings moved to active.
    # Experimental conditions activated (composite validation at the condition grain).
    # D4 (reference) stays under_development (blocked: no durable reference identity at extraction).
    assert {
        "disease_reference_materialization",
    } == {binding["binding_id"] for binding in validator_bindings["under_development"]}
    assert {
        "disease_subject_materialization",
        "disease_evidence_code_lookup",
    }.issubset(active_binding_ids)
    disease_term_binding = next(
        binding
        for binding in validator_bindings["active"]
        if binding["binding_id"] == "disease_ontology_term_lookup"
    )
    assert disease_term_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "ontology_term_validation",
    }
    # The paper wording and the extractor's proposals are the inputs (ALL-1283); only the
    # validator result writes curie/name.
    assert disease_term_binding["input_fields"]["curie"] == {
        "source": "payload",
        "path": "disease_annotation_object.proposed_curie",
        "required": False,
    }
    assert disease_term_binding["input_fields"]["label"] == {
        "source": "payload",
        "path": "disease_annotation_object.mention",
        "required": True,
    }
    assert disease_term_binding["input_fields"]["name"] == {
        "source": "payload",
        "path": "disease_annotation_object.proposed_name",
        "required": False,
    }
    assert disease_term_binding["input_fields"]["source_mentions"] == {
        "source": "payload",
        "path": "source_mentions",
        "required": False,
        "allow_multiple": True,
        "context_only": True,
    }
    assert disease_term_binding["input_fields"]["ontology_term_type"]["source"] == (
        "literal"
    )
    assert disease_term_binding["input_fields"]["ontology_term_type"]["value"] == (
        "DOTerm"
    )
    assert disease_term_binding["input_fields"]["accepted_prefixes"]["source"] == (
        "literal"
    )
    assert disease_term_binding["input_fields"]["accepted_prefixes"]["value"] == [
        "DOID"
    ]
    assert disease_term_binding["expected_result_fields"] == {
        "curie": "disease_annotation_object.curie",
        "label": "disease_annotation_object.name",
    }
    # D3 full LinkML alignment: ECO evidence-code lookup is now ACTIVE.
    evidence_code_binding = next(
        binding
        for binding in validator_bindings["active"]
        if binding["binding_id"] == "disease_evidence_code_lookup"
    )
    assert evidence_code_binding["input_fields"]["curie"] == {
        "source": "payload",
        "path": "evidence_code_curies.mention",
        "required": False,
    }
    assert evidence_code_binding["input_fields"]["ontology_term_type"]["source"] == (
        "literal"
    )
    assert evidence_code_binding["input_fields"]["ontology_term_type"]["value"] == (
        "ECOTerm"
    )
    assert evidence_code_binding["input_fields"]["accepted_prefixes"]["source"] == (
        "literal"
    )
    assert evidence_code_binding["input_fields"]["accepted_prefixes"]["value"] == [
        "ECO"
    ]
    assert evidence_code_binding["expected_result_fields"] == {
        "curie": "evidence_code_curies.curie",
    }


def test_disease_pack_records_db_projection_and_representative_rows():
    object_metadata = _disease_object_definition().metadata
    db_projection = object_metadata["db_projection"]

    assert db_projection["base_table"] == "public.diseaseannotation"
    assert db_projection["concrete_tables"] == [
        "public.genediseaseannotation",
        "public.allelediseaseannotation",
        "public.agmdiseaseannotation",
    ]
    assert "public.experimentalcondition" in db_projection["condition_tables"]

    representative_rows = db_projection["representative_rows"]
    assert representative_rows == [
        {
            "row_id": 209127250,
            "subtype": "GeneDiseaseAnnotation",
            "subject_primary_external_id": "SGD:S000004578",
            "disease_curie": "DOID:0050730",
            "relation_name": "is_implicated_in",
            "data_provider": "SGD",
        },
        {
            "row_id": 209127267,
            "subtype": "AlleleDiseaseAnnotation",
            "subject_primary_external_id": "MGI:3689328",
            "disease_curie": "DOID:0111789",
            "relation_name": "is_implicated_in",
            "data_provider": "MGI",
        },
        {
            "row_id": 209127402,
            "subtype": "AGMDiseaseAnnotation",
            "subject_primary_external_id": "MGI:8308849",
            "disease_curie": "DOID:0111441",
            "relation_name": "is_model_of",
            "data_provider": "MGI",
        },
    ]
    assert db_projection["representative_condition_row"] == {
        "diseaseannotation_id": 209127194,
        "condition_relation_type": "has_condition",
        "condition_class_curie": "ZECO:0000111",
        "condition_id_curie": "ZECO:0000238",
        "condition_chemical_curie": "CHEBI:6909",
    }

    curation_db_ref = object_metadata["provider_refs"]["alliance_curation_db"]
    inspected_table_names = {
        table_name.removeprefix("public.")
        for table_name in curation_db_ref["inspected_tables"]
    }
    assert set(curation_db_ref["row_counts"]) == inspected_table_names
    assert (
        curation_db_ref["row_counts"]["conditionrelation_experimentalcondition"]
        == 23532
    )


def test_disease_pack_declares_validatable_disease_and_condition_fields(monkeypatch):
    monkeypatch.delenv(
        "EXPERIMENTAL_CONDITION_VALIDATOR_BATCH_MAX_SIZE",
        raising=False,
    )
    disease_object = _disease_object_definition()
    fields_by_path = {field.field_path: field for field in disease_object.fields}
    raw_validator_bindings = _disease_pack().metadata.metadata["validator_bindings"]
    validator_bindings = {
        binding["binding_id"]: binding
        for binding in [
            *raw_validator_bindings["active"],
            *raw_validator_bindings["under_development"],
        ]
    }
    referenced_validator_binding_ids = {
        field.metadata["validator_binding_id"]
        for field in disease_object.fields
        if field.metadata.get("validatable") is True
    }
    assert referenced_validator_binding_ids <= set(validator_bindings)

    required_fields = {
        field.field_path for field in disease_object.fields if field.required
    }
    assert required_fields == {
        "mention",
        "disease_annotation_object",
        # The paper wording is required; the validated name is empty until a validator runs.
        "disease_annotation_object.mention",
        "role",
        "confidence",
        "evidence_record_ids",
        "evidence_records",
    }
    assert fields_by_path["role"].field_type is DomainPackFieldType.ENUM
    assert fields_by_path["role"].enum_ref == "DiseaseAssertionRole"
    assert fields_by_path["confidence"].enum_ref == "DiseaseAssertionConfidence"

    disease_curie = fields_by_path["disease_annotation_object.curie"]
    assert disease_curie.metadata["validatable"] is True
    assert (
        disease_curie.metadata["validator_binding_id"] == "disease_ontology_term_lookup"
    )
    assert disease_curie.metadata["validator_state"] == "active"

    # Experimental conditions are declared with BARE nested paths now (the [0] literals are
    # retired); the nested fan-out engine indexes condition_relations[i].conditions[j].
    condition_fields = {
        "condition_relations",
        "condition_relations.conditions",
        "condition_relations.conditions.condition_class.curie",
        "condition_relations.conditions.condition_chemical.curie",
        "condition_relations.conditions.condition_taxon.curie",
    }
    assert condition_fields.issubset(fields_by_path)
    # No legacy [0] condition field_paths remain.
    assert not any(
        "condition_relations[0]" in path for path in fields_by_path
    )
    # Both list levels are declared multivalued so the engine fans the cartesian product.
    for multivalued_path in ("condition_relations", "condition_relations.conditions"):
        field = fields_by_path[multivalued_path]
        assert field.metadata["multivalued"] is True
        assert field.multivalued is True
        # Conditions are now wired (no longer under_development gated metadata).
        assert field.metadata["definition_state_category"] == "complete"
        assert "protected" not in field.metadata
    # The ExperimentalCondition object (condition_relations.conditions) is the ONE composite
    # fan-out target and is ACTIVE.
    conditions_field = fields_by_path["condition_relations.conditions"]
    assert conditions_field.metadata["validatable"] is True
    assert conditions_field.metadata["validator_state"] == "active"
    assert (
        conditions_field.metadata["validator_binding_id"]
        == "experimental_condition_validation"
    )
    # The relation type fans per relation through the (already active) CV lookup.
    condition_relation_type = fields_by_path[
        "condition_relations.condition_relation_type.name"
    ]
    assert condition_relation_type.metadata["validator_state"] == "active"
    assert (
        condition_relation_type.metadata["validator_binding_id"]
        == "disease_condition_relation_lookup"
    )
    composite_binding = validator_bindings["experimental_condition_validation"]
    assert composite_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "experimental_condition_validation",
    }
    # The ONE fan-out target is the ExperimentalCondition object.
    assert composite_binding["applies_to"]["field_paths"] == [
        "condition_relations.conditions"
    ]
    # Per-condition component input_fields use BARE nested paths (the engine substitutes (i, j)).
    # The extractor's CURIE is a proposal and the component's paper wording rides alongside.
    assert composite_binding["input_fields"]["condition_class_curie"]["path"] == (
        "condition_relations.conditions.condition_class.proposed_curie"
    )
    assert composite_binding["input_fields"]["condition_class_name"]["path"] == (
        "condition_relations.conditions.condition_class.mention"
    )
    # Every component is its own write-back target; an absent one is never required.
    assert composite_binding["expected_result_fields"] == {
        "condition_class_curie": "condition_relations.conditions.condition_class.curie",
        "condition_class_name": "condition_relations.conditions.condition_class.name",
        "condition_id_curie": "condition_relations.conditions.condition_id.curie",
        "condition_id_name": "condition_relations.conditions.condition_id.name",
        "condition_chemical_curie": "condition_relations.conditions.condition_chemical.curie",
        "condition_chemical_name": "condition_relations.conditions.condition_chemical.name",
        "condition_taxon_curie": "condition_relations.conditions.condition_taxon.curie",
        "condition_taxon_name": "condition_relations.conditions.condition_taxon.name",
    }
    # The relation context is a sibling under the OUTER multivalued list.
    assert composite_binding["input_fields"]["condition_relation_type"]["path"] == (
        "condition_relations.condition_relation_type.mention"
    )
    assert composite_binding["input_fields"]["evidence_quotes"] == {
        "source": "evidence_record",
        "output": "quote_bundle",
        "allow_multiple": True,
        "required": False,
        "context_only": True,
    }
    assert composite_binding["input_fields"]["source_mentions"] == {
        "source": "payload",
        "path": "source_mentions",
        "required": False,
        "allow_multiple": True,
        "context_only": True,
    }
    # Composite binding is batchable.
    assert composite_binding["batch"]["enabled"] is True
    assert composite_binding["batch"]["family"] == "experimental_condition_validation"
    assert composite_binding["batch"]["max_size"] == 4

    # D4 stays blocked: no durable reference identity is available at chat-extraction time.
    blocked_fields = {
        "single_reference",
    }
    for field_path in blocked_fields:
        field = fields_by_path[field_path]
        assert field.definition_state.value == "in_development"
        assert field.metadata["validator_state"] == "under_development"
        assert field.metadata["definition_state_category"] == "blocked"
        binding = validator_bindings[field.metadata["validator_binding_id"]]
        assert binding["state_explanation"]

    # D2 + D3 full LinkML alignment: subject and ECO evidence-code fields are now ACTIVE (unblocked).
    activated_fields = {
        "disease_annotation_subject.subject_identifier": "disease_subject_materialization",
        "evidence_code_curies": "disease_evidence_code_lookup",
    }
    for field_path, binding_id in activated_fields.items():
        field = fields_by_path[field_path]
        assert field.metadata["validatable"] is True
        assert field.metadata["validator_state"] == "active"
        assert field.metadata["validator_binding_id"] == binding_id
        assert binding_id in validator_bindings

    # Per-element validation: ECO evidence codes, disease qualifier names, and
    # with/from gene identifiers are declared multivalued (bare field_path +
    # multivalued: true), so every staged element is validated, not just [0].
    for multivalued_path in (
        "evidence_code_curies",
        "disease_qualifier_names",
        "with_gene_identifiers",
    ):
        multivalued_field = fields_by_path[multivalued_path]
        assert multivalued_field.metadata["multivalued"] is True
        assert multivalued_field.multivalued is True

    for field_path in ("data_provider", "data_provider.abbreviation"):
        field = fields_by_path[field_path]
        assert field.definition_state.value == "stable"
        assert field.metadata["validator_state"] == "active"
        assert field.metadata["definition_state_category"] == "complete"
        binding = validator_bindings[field.metadata["validator_binding_id"]]
        assert binding["validator_agent"]["agent_id"] == "data_provider_validation"


def test_disease_pack_linkml_class_slot_attribute_and_range_refs_exist(tmp_path: Path):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = _disease_pack().metadata

    provider_refs = tuple(_iter_linkml_provider_refs(metadata))
    assert provider_refs

    for provider_ref in provider_refs:
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert provider_ref["schema_ref"] == "alliance.linkml"

        class_name = provider_ref.get("class")
        if class_name is not None:
            assert (
                class_name in index["classes"]
            ), f"LinkML class {class_name} is missing from pinned schema"
            actual_file, class_definition = index["classes"][class_name]
            if "slot" not in provider_ref:
                _assert_source_file_matches(
                    provider_ref=provider_ref,
                    actual_file=actual_file,
                    ref_kind="class",
                    ref_name=class_name,
                )

            attribute_name = provider_ref.get("attribute")
            if attribute_name is not None:
                attributes = class_definition.get("attributes") or {}
                assert attribute_name in attributes
                if "range" in provider_ref:
                    assert attributes[attribute_name]["range"] == provider_ref["range"]

        slot_name = provider_ref.get("slot")
        if slot_name is not None:
            assert (
                slot_name in index["slots"]
            ), f"LinkML slot {slot_name} is missing from pinned schema"
            actual_file, _definition = index["slots"][slot_name]
            _assert_source_file_matches(
                provider_ref=provider_ref,
                actual_file=actual_file,
                ref_kind="slot",
                ref_name=slot_name,
            )

        _assert_range_exists(index, provider_ref)


def test_tool_verified_disease_fixture_converts_to_pending_envelope():
    raw_fixture = _load_raw_disease_fixture()
    converted_envelope = tool_verified_disease_output_to_pending_envelope(raw_fixture)

    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        DISEASE_DOMAIN_PACK_ID,
        DISEASE_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(DISEASE_PACK_DIR / fixture_ref.path)
    expected_envelope = fixture_pack.fixtures[0].envelope

    assert converted_envelope.model_dump(mode="json", exclude_none=True) == (
        expected_envelope.model_dump(mode="json", exclude_none=True)
    )
    assert validate_pending_disease_envelope(converted_envelope) == ()
    assert converted_envelope.domain_pack_id == DISEASE_DOMAIN_PACK_ID
    assert converted_envelope.schema_ref.schema_id == DISEASE_LINKML_SCHEMA_ID
    assert converted_envelope.extracted_objects[0].pending_ref_id == "disease-assertion-1"
    assert converted_envelope.extracted_objects[0].status is CuratableObjectStatus.PENDING
    assert converted_envelope.extracted_objects[0].metadata[OBJECT_ROLE_METADATA_KEY] == (
        "curatable_unit"
    )

    payload = converted_envelope.extracted_objects[0].payload
    # The tool-verified DOID/name are proposals: the term stays unresolved until a validator
    # resolves it (ALL-1283).
    assert payload["disease_annotation_object"] == unresolved_value(
        "Andersen-Tawil syndrome",
        identity_keys=("curie", "name"),
        proposed_curie="DOID:0050434",
        proposed_name="Andersen-Tawil syndrome",
    )
    assert payload["data_provider"] == unresolved_value("ZFIN", identity_keys=("abbreviation",))
    assert field_path_exists(payload, "evidence_records[0].verified_quote")
    assert payload["disease_relation"] == unresolved_value("is_model_of", identity_keys=("name",))
    assert payload["disease_annotation_subject"] == unresolved_value(
        "kcnj2",
        identity_keys=("subject_identifier", "subject_label"),
        subject_type="gene",
    )


def test_converted_disease_envelope_omits_legacy_semantic_stores():
    raw_fixture = _load_raw_disease_fixture()
    converted_envelope = tool_verified_disease_output_to_pending_envelope(raw_fixture)

    observed_keys = set(
        _iter_mapping_keys(converted_envelope.model_dump(mode="python"))
    )
    assert FORBIDDEN_LEGACY_COLLECTIONS.isdisjoint(observed_keys)


def test_pending_disease_validator_rejects_legacy_semantic_store():
    raw_fixture = _load_raw_disease_fixture()
    converted_envelope = tool_verified_disease_output_to_pending_envelope(raw_fixture)
    legacy_envelope = converted_envelope.model_copy(
        update={"metadata": {**converted_envelope.metadata, "diseases": []}},
    )

    findings = validate_pending_disease_envelope(legacy_envelope)
    assert [finding.code for finding in findings] == [
        "alliance.disease.legacy_semantic_store_present"
    ]


def test_tool_verified_disease_fixture_rejects_malformed_required_data():
    raw_fixture = _load_raw_disease_fixture()

    missing_assertions = copy.deepcopy(raw_fixture)
    missing_assertions.pop("disease_assertions")
    with pytest.raises(ValidationError, match="disease_assertions"):
        tool_verified_disease_output_to_pending_envelope(missing_assertions)

    blank_note = copy.deepcopy(raw_fixture)
    blank_note["normalization_notes"].append("  ")
    with pytest.raises(ValidationError, match="normalization_notes"):
        tool_verified_disease_output_to_pending_envelope(blank_note)

    unknown_evidence = copy.deepcopy(raw_fixture)
    unknown_evidence["disease_assertions"][0]["evidence_record_ids"].append("missing")
    with pytest.raises(ValidationError, match="unknown evidence_record_ids"):
        tool_verified_disease_output_to_pending_envelope(unknown_evidence)

    missing_subject_type = copy.deepcopy(raw_fixture)
    missing_subject_type["disease_assertions"][0]["subject"].pop("subject_type")
    with pytest.raises(ValidationError, match="subject_type"):
        tool_verified_disease_output_to_pending_envelope(missing_subject_type)

    # A subject is named by its paper wording; an identifier alone is not a subject.
    missing_subject_wording = copy.deepcopy(raw_fixture)
    missing_subject_wording["disease_assertions"][0]["subject"] = {
        "subject_type": "gene",
        "subject_identifier": "ZFIN:ZDB-GENE-000000-1",
    }
    with pytest.raises(ValidationError, match="subject_label"):
        tool_verified_disease_output_to_pending_envelope(missing_subject_wording)


def test_disease_evidence_code_lookup_validates_every_staged_element():
    """A 2+-element evidence_code_curies payload fans out to one validator target
    per element — every ECO code is validated, not just ``[0]``."""

    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    evidence_code_curies = ["ECO:0000315", "ECO:0000316", "ECO:0000501"]
    envelope = DomainEnvelope(
        envelope_id="disease-multivalued-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={"evidence_code_curies": _staged_list(evidence_code_curies, ("curie",))},
            )
        ],
    )

    matches = registry.match_bindings(
        envelope, states=[ValidationBindingState.ACTIVE]
    )
    evidence_matches = [
        match
        for match in matches
        if match.binding.binding_id == "disease_evidence_code_lookup"
        and match.field_definition is not None
    ]

    # One match per staged element, each indexed.
    assert [match.element_index for match in evidence_matches] == [0, 1, 2]
    assert [match.field_path for match in evidence_matches] == [
        "evidence_code_curies[0]",
        "evidence_code_curies[1]",
        "evidence_code_curies[2]",
    ]

    # Each element resolves its own curie into the validator request and write-back.
    requests = [
        build_domain_validation_request(match).request for match in evidence_matches
    ]
    assert [request.selected_inputs["curie"] for request in requests] == evidence_code_curies
    assert [
        request.expected_result_fields["curie"] for request in requests
    ] == [
        "evidence_code_curies[0].curie",
        "evidence_code_curies[1].curie",
        "evidence_code_curies[2].curie",
    ]


def test_disease_qualifier_cv_lookup_validates_every_staged_element():
    """A 2+-element disease_qualifier_names payload fans out to one validator target
    per element — every qualifier name is validated, not just ``[0]``."""

    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    qualifier_names = ["severe", "early-onset", "progressive"]
    envelope = DomainEnvelope(
        envelope_id="disease-qualifier-multivalued-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={"disease_qualifier_names": _staged_list(qualifier_names, ("name",))},
            )
        ],
    )

    matches = registry.match_bindings(
        envelope, states=[ValidationBindingState.ACTIVE]
    )
    qualifier_matches = [
        match
        for match in matches
        if match.binding.binding_id == "disease_qualifier_cv_lookup"
        and match.field_definition is not None
    ]

    # One match per staged element, each indexed.
    assert [match.element_index for match in qualifier_matches] == [0, 1, 2]
    assert [match.field_path for match in qualifier_matches] == [
        "disease_qualifier_names[0]",
        "disease_qualifier_names[1]",
        "disease_qualifier_names[2]",
    ]

    # Each element resolves its own term_name into the validator request and write-back.
    requests = [
        build_domain_validation_request(match).request for match in qualifier_matches
    ]
    assert [
        request.selected_inputs["term_name"] for request in requests
    ] == qualifier_names
    assert [
        request.expected_result_fields["term_name"] for request in requests
    ] == [
        "disease_qualifier_names[0].name",
        "disease_qualifier_names[1].name",
        "disease_qualifier_names[2].name",
    ]


def test_disease_relation_lookup_projects_sibling_expected_result_fields():
    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    envelope = DomainEnvelope(
        envelope_id="disease-relation-projection-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={
                    "disease_relation": unresolved_value(
                        "implicated in", identity_keys=("name",)
                    ),
                    "disease_annotation_subject": unresolved_value(
                        "Appl",
                        identity_keys=("subject_identifier", "subject_label"),
                        subject_type="gene",
                    ),
                },
            )
        ],
    )
    matches = [
        match
        for match in registry.match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
        if match.binding.binding_id == "disease_relation_cv_lookup"
    ]
    assert len(matches) == 1
    request = build_domain_validation_request(matches[0]).request
    assert request is not None

    result = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [
            ValidatorResultMaterializationInput(
                match=matches[0],
                request=request,
                result=DomainValidatorResultBase(
                    status="resolved",
                    request_id=request.request_id,
                    validator_binding_id=request.validator_binding_id,
                    validator_agent=request.validator_agent,
                    target=request.target,
                    resolved_values={
                        "term_name": "is_implicated_in",
                        "vocabulary": "Gene Disease Relation",
                        "internal_id": "4011",
                    },
                    resolved_objects=[],
                    missing_expected_fields=[],
                    candidates=[],
                    lookup_attempts=[
                        {
                            "provider": "agr_curation_query",
                            "method": "search_controlled_vocabulary",
                            "query": {
                                "vocabulary": "Gene Disease Relation",
                                "term_name": "is_implicated_in",
                            },
                            "result_count": 1,
                            "outcome": "success",
                        }
                    ],
                    curator_message="Resolved disease relation.",
                    explanation="Resolved relation vocabulary from fixture.",
                ),
            )
        ],
    )

    payload = result.envelope.extracted_objects[0].payload
    # The validator's term, vocabulary and id land on the relation value; the extractor's
    # chosen text stays as the value's mention.
    assert payload["disease_relation"] == {
        "name": "is_implicated_in",
        "vocabulary": "Gene Disease Relation",
        "id": "4011",
        "mention": "implicated in",
        "resolution_state": RESOLVED,
        "lookup_outcome": OUTCOME_MATCHED,
        "validator_explanation": "Resolved relation vocabulary from fixture.",
        "validator_curator_message": "Resolved disease relation.",
    }
    summaries = project_validation_summary_projections(
        result.envelope,
        envelope_revision=1,
        object_id="gene-disease-1",
    )
    assert {
        summary.field_path: summary.status.value
        for summary in summaries
        if summary.field_path is not None
    } == {
        "disease_relation.name": "resolved",
        "disease_relation.vocabulary": "resolved",
        "disease_relation.id": "resolved",
    }


def test_disease_condition_relation_lookup_projects_indexed_sibling_fields():
    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    envelope = DomainEnvelope(
        envelope_id="disease-condition-relation-projection-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={
                    "condition_relations": [
                        {
                            "condition_relation_type": unresolved_value(
                                "has_condition", identity_keys=("name",)
                            ),
                            "conditions": [
                                {
                                    "condition_summary": "heat shock",
                                }
                            ],
                        }
                    ],
                },
            )
        ],
    )
    matches = [
        match
        for match in registry.match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
        if match.binding.binding_id == "disease_condition_relation_lookup"
    ]
    assert len(matches) == 1
    assert matches[0].field_path == "condition_relations[0].condition_relation_type.name"
    request = build_domain_validation_request(matches[0]).request
    assert request is not None
    assert request.expected_result_fields == {
        "term_name": "condition_relations[0].condition_relation_type.name",
        "vocabulary": "condition_relations[0].condition_relation_type.vocabulary",
        "internal_id": "condition_relations[0].condition_relation_type.id",
    }

    result = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [
            ValidatorResultMaterializationInput(
                match=matches[0],
                request=request,
                result=DomainValidatorResultBase(
                    status="resolved",
                    request_id=request.request_id,
                    validator_binding_id=request.validator_binding_id,
                    validator_agent=request.validator_agent,
                    target=request.target,
                    resolved_values={
                        "term_name": "has_condition",
                        "vocabulary": "Condition Relation Type",
                        "internal_id": "200000001",
                    },
                    resolved_objects=[],
                    missing_expected_fields=[],
                    candidates=[],
                    lookup_attempts=[
                        {
                            "provider": "agr_curation_query",
                            "method": "search_controlled_vocabulary",
                            "query": {
                                "vocabulary": "Condition Relation Type",
                                "term_name": "has_condition",
                            },
                            "result_count": 1,
                            "outcome": "success",
                        }
                    ],
                    curator_message="Resolved condition relation.",
                    explanation="Resolved condition relation from fixture.",
                ),
            )
        ],
    )

    relation_type = result.envelope.extracted_objects[0].payload["condition_relations"][0][
        "condition_relation_type"
    ]
    assert relation_type == {
        "name": "has_condition",
        "vocabulary": "Condition Relation Type",
        "id": "200000001",
        "mention": "has_condition",
        "resolution_state": RESOLVED,
        "lookup_outcome": OUTCOME_MATCHED,
        "validator_explanation": "Resolved condition relation from fixture.",
        "validator_curator_message": "Resolved condition relation.",
    }
    summaries = project_validation_summary_projections(
        result.envelope,
        envelope_revision=1,
        object_id="gene-disease-1",
    )
    assert {
        summary.field_path: summary.status.value
        for summary in summaries
        if summary.field_path is not None
    } == {
        "condition_relations[0].condition_relation_type.name": "resolved",
        "condition_relations[0].condition_relation_type.vocabulary": "resolved",
        "condition_relations[0].condition_relation_type.id": "resolved",
    }


def test_disease_with_gene_validation_validates_every_staged_element():
    """A 2+-element with_gene_identifiers payload fans out to one validator target
    per element — every with/from gene is validated, not just ``[0]``."""

    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    gene_identifiers = ["FB:FBgn0000001", "FB:FBgn0000002", "FB:FBgn0000003"]
    envelope = DomainEnvelope(
        envelope_id="disease-with-gene-multivalued-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={"with_gene_identifiers": _staged_list(gene_identifiers, ("primary_external_id",))},
            )
        ],
    )

    matches = registry.match_bindings(
        envelope, states=[ValidationBindingState.ACTIVE]
    )
    with_gene_matches = [
        match
        for match in matches
        if match.binding.binding_id == "disease_with_gene_validation"
        and match.field_definition is not None
    ]

    # One match per staged element, each indexed.
    assert [match.element_index for match in with_gene_matches] == [0, 1, 2]
    assert [match.field_path for match in with_gene_matches] == [
        "with_gene_identifiers[0]",
        "with_gene_identifiers[1]",
        "with_gene_identifiers[2]",
    ]

    # Each element resolves its own gene_id into the validator request and write-back.
    requests = [
        build_domain_validation_request(match).request for match in with_gene_matches
    ]
    assert [
        request.selected_inputs["gene_id"] for request in requests
    ] == gene_identifiers
    assert [
        request.expected_result_fields["primary_external_id"] for request in requests
    ] == [
        "with_gene_identifiers[0].primary_external_id",
        "with_gene_identifiers[1].primary_external_id",
        "with_gene_identifiers[2].primary_external_id",
    ]


# ---------------------------------------------------------------------------
# Per-element validator BATCHING for the three multivalued disease fields.
#
# These guard the batch optimization: a multivalued field fans out to one
# validation request per element, and the three opted-in bindings must group
# those per-element requests into a SINGLE batch run (not N individual runs)
# while still mapping each result back to its own request_id (resolved stays
# resolved — batching must not regress the working N-individual-call path).
# ---------------------------------------------------------------------------

_BATCH_ENABLED_DISEASE_BINDINGS = {
    "disease_evidence_code_lookup",
    "disease_qualifier_cv_lookup",
    "disease_with_gene_validation",
}


def _disease_result_payload(request, *, resolved_values: dict[str, Any]):
    """A minimal resolved DomainValidatorResultBase-compatible payload."""

    return {
        "status": "resolved",
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": resolved_values,
        "resolved_objects": [
            {
                "object_type": "DiseaseAnnotationInput",
                "canonical_id": next(iter(resolved_values.values()), None),
                "payload": dict(resolved_values),
            }
        ],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "deterministic_contract_lookup",
                "method": "exact_match",
                "query": dict(resolved_values),
                "result_count": 1,
                "outcome": "success",
            }
        ],
        "curator_message": None,
        "explanation": "Deterministic batch contract result.",
    }


def test_three_multivalued_disease_bindings_are_batch_enabled():
    """The three multivalued disease bindings must opt into batch execution with
    a unique family so per-element requests for one field group into one batch."""

    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    by_id = {
        binding.binding_id: binding
        for binding in registry.bindings
        if binding.binding_id in _BATCH_ENABLED_DISEASE_BINDINGS
    }

    assert set(by_id) == _BATCH_ENABLED_DISEASE_BINDINGS

    families = set()
    for binding_id, binding in by_id.items():
        assert binding.batch_enabled is True, binding_id
        assert binding.batch_family, binding_id
        assert binding.batch_max_size and binding.batch_max_size >= 2, binding_id
        families.add(binding.batch_family)

    # Distinct families keep the three bindings in separate batch groups.
    assert len(families) == len(by_id)


def test_evidence_code_multivalued_field_groups_into_one_batch():
    """A 2-element evidence_code_curies field drives EXACTLY ONE batch run that
    receives both jobs and maps each result back to its own request_id resolved
    — proving the batched-agent path resolves the same elements the individual
    path would, with no regression to unresolved."""

    pack = _disease_pack()
    evidence_code_curies = ["ECO:0000315", "ECO:0000316"]
    envelope = DomainEnvelope(
        envelope_id="disease-evidence-batch-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-batch-1",
                payload={"evidence_code_curies": _staged_list(evidence_code_curies, ("curie",))},
            )
        ],
    )

    batch_calls: list[list[str]] = []

    def _single_runner(request, *, binding):
        # The evidence-code binding is batch-enabled; it must NOT fall back to
        # the single runner. Other always-on bindings (e.g. the constant
        # annotation_type CV lookup) legitimately resolve via the single runner.
        if binding.binding_id == "disease_evidence_code_lookup":
            raise AssertionError(
                "batch-enabled evidence_code binding must use the batch runner, "
                "not the single runner"
            )
        return _disease_result_payload(
            request,
            resolved_values=dict(request.selected_inputs),
        )

    def _batch_runner(jobs, *, binding):
        batch_calls.append(
            [job.request.selected_inputs.get("curie") for job in jobs]
        )
        return [
            _disease_result_payload(
                job.request,
                resolved_values={"curie": job.request.selected_inputs["curie"]},
            )
            for job in jobs
        ]

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=_single_runner,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    evidence_results = [
        item
        for item in result.validator_results
        if item.validator_binding_id == "disease_evidence_code_lookup"
    ]

    # Exactly one batch run handled both elements.
    assert batch_calls == [evidence_code_curies]
    assert result.batch_validator_run_count >= 1

    # Both elements resolved, mapped back to their own request_id + curie.
    assert len(evidence_results) == 2
    assert {item.status for item in evidence_results} == {"resolved"}
    resolved_curies = sorted(
        item.resolved_values.get("curie") for item in evidence_results
    )
    assert resolved_curies == sorted(evidence_code_curies)
    assert len({item.request_id for item in evidence_results}) == 2


def _condition_relation_type(mention: str) -> dict[str, Any]:
    return unresolved_value(mention, identity_keys=("name",))


def _condition_term(mention: str, proposed_curie: str) -> dict[str, Any]:
    return unresolved_value(mention, identity_keys=("curie",), proposed_curie=proposed_curie)


def _two_condition_payload() -> dict[str, Any]:
    """A disease annotation carrying one relation with TWO experimental conditions."""

    return {
        "mention": "rapamycin-modulated disease model",
        "disease_annotation_object": unresolved_value(
            "rapamycin-modulated disease model",
            identity_keys=("curie", "name"),
            proposed_curie="DOID:0050730",
        ),
        "role": "primary",
        "confidence": "high",
        "data_provider": unresolved_value("MGI", identity_keys=("abbreviation",)),
        "source_mentions": ["rapamycin-modulated disease model"],
        "evidence_record_ids": ["evidence-1"],
        "evidence_records": [
            {
                "evidence_record_id": "evidence-1",
                "verified_quote": "treated with 3 pM rapamycin at 37C",
                "page": 2,
                "section": "Results",
                "chunk_id": "chunk-9",
            }
        ],
        "condition_relations": [
            {
                "condition_relation_type": _condition_relation_type("has_condition"),
                "conditions": [
                    {
                        "condition_class": _condition_term("chemical treatment", "ZECO:0000111"),
                        "condition_chemical": _condition_term("rapamycin", "CHEBI:9168"),
                        "condition_summary": "treated with 3 pM rapamycin",
                    },
                    {
                        "condition_class": _condition_term("temperature exposure", "ZECO:0000160"),
                        "condition_summary": "incubated at 37C",
                    },
                ],
            }
        ],
    }


def test_experimental_condition_binding_fans_out_one_composite_per_condition():
    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    envelope = DomainEnvelope(
        envelope_id="disease-conditions-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="DiseaseAnnotation",
                pending_ref_id="disease-conditions-1",
                payload=_two_condition_payload(),
            )
        ],
    )

    matches = registry.match_bindings(
        envelope, states=[ValidationBindingState.ACTIVE]
    )

    # The composite binding fans out ONE match per ExperimentalCondition (2 conditions -> 2).
    composite_matches = [
        match
        for match in matches
        if match.binding.binding_id == "experimental_condition_validation"
    ]
    assert len(composite_matches) == 2
    # Each composite target is a distinct nested ExperimentalCondition object.
    assert [match.field_path for match in composite_matches] == [
        "condition_relations[0].conditions[0]",
        "condition_relations[0].conditions[1]",
    ]
    assert [match.element_index_path for match in composite_matches] == [(0, 0), (0, 1)]

    # The relation-type CV lookup fans out PER RELATION (1 relation -> 1 match).
    relation_matches = [
        match
        for match in matches
        if match.binding.binding_id == "disease_condition_relation_lookup"
    ]
    assert len(relation_matches) == 1
    assert relation_matches[0].field_path == (
        "condition_relations[0].condition_relation_type.name"
    )

    # Each per-condition composite request resolves THIS condition's own components AND the
    # shared relation context (the sibling under the outer multivalued list).
    requests = [build_domain_validation_request(match) for match in composite_matches]
    first = requests[0].request
    second = requests[1].request
    assert first is not None
    assert second is not None

    # Each component's proposed CURIE and paper wording reach the composite validator.
    assert first.selected_inputs["condition_class_curie"] == "ZECO:0000111"
    assert first.selected_inputs["condition_class_name"] == "chemical treatment"
    assert first.selected_inputs["condition_chemical_curie"] == "CHEBI:9168"
    assert first.selected_inputs["condition_chemical_name"] == "rapamycin"
    assert first.selected_inputs["condition_statement"] == "treated with 3 pM rapamycin"
    # Relation context substituted from the OUTER multivalued index (relation 0).
    assert first.selected_inputs["condition_relation_type"] == "has_condition"
    assert first.selected_inputs["source_mentions"] == [
        "rapamycin-modulated disease model"
    ]

    assert second.selected_inputs["condition_class_curie"] == "ZECO:0000160"
    # The second condition stated no chemical -> that optional component is absent.
    assert "condition_chemical_curie" not in second.selected_inputs
    assert second.selected_inputs["condition_statement"] == "incubated at 37C"
    assert second.selected_inputs["condition_relation_type"] == "has_condition"
    assert second.selected_inputs["source_mentions"] == [
        "rapamycin-modulated disease model"
    ]

    # The two composite requests are distinct and target their own indexed condition.
    assert first.target.field_path == "condition_relations[0].conditions[0]"
    assert second.target.field_path == "condition_relations[0].conditions[1]"
    assert first.request_id != second.request_id
    # Both carry the annotation's backend-resolved evidence (evidence contract: no LLM quote).
    assert first.evidence and first.evidence[0]["verified_quote"]


def test_two_relations_fan_out_per_relation_and_per_condition():
    """Two condition_relations, each with conditions, exercise BOTH fan-out levels."""

    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    payload = _two_condition_payload()
    # Add a SECOND relation holding one condition.
    payload["condition_relations"].append(
        {
            "condition_relation_type": _condition_relation_type("induced_by"),
            "conditions": [
                {
                    "condition_class": _condition_term("radiation exposure", "ZECO:0000240"),
                    "condition_summary": "exposed to ionizing radiation",
                }
            ],
        }
    )
    envelope = DomainEnvelope(
        envelope_id="disease-conditions-2rel-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="DiseaseAnnotation",
                pending_ref_id="disease-conditions-2rel-1",
                payload=payload,
            )
        ],
    )

    matches = registry.match_bindings(
        envelope, states=[ValidationBindingState.ACTIVE]
    )
    composite_matches = [
        match
        for match in matches
        if match.binding.binding_id == "experimental_condition_validation"
    ]
    # 2 conditions in relation 0 + 1 condition in relation 1 = 3 composite validations.
    assert [match.field_path for match in composite_matches] == [
        "condition_relations[0].conditions[0]",
        "condition_relations[0].conditions[1]",
        "condition_relations[1].conditions[0]",
    ]
    # The relation-type CV lookup fans out per relation (2 relations -> 2 matches).
    relation_matches = [
        match
        for match in matches
        if match.binding.binding_id == "disease_condition_relation_lookup"
    ]
    assert [match.field_path for match in relation_matches] == [
        "condition_relations[0].condition_relation_type.name",
        "condition_relations[1].condition_relation_type.name",
    ]

    # The condition in relation 1 resolves relation 1's relation type (outer index substituted).
    requests = {
        match.field_path: build_domain_validation_request(match).request
        for match in composite_matches
    }
    assert (
        requests["condition_relations[1].conditions[0]"].selected_inputs[
            "condition_relation_type"
        ]
        == "induced_by"
    )
    assert (
        requests["condition_relations[0].conditions[0]"].selected_inputs[
            "condition_relation_type"
        ]
        == "has_condition"
    )


# --- ALL-1283: validator write-back onto resolvable values --------------------------------------


def _validator_result(request, *, status: str, resolved_values: dict[str, Any], outcome: str):
    return DomainValidatorResultBase(
        status=status,
        request_id=request.request_id,
        validator_binding_id=request.validator_binding_id,
        validator_agent=request.validator_agent,
        target=request.target,
        resolved_values=resolved_values,
        resolved_objects=[],
        missing_expected_fields=[],
        candidates=[],
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_ontology_terms",
                "query": {"term": "fixture"},
                "result_count": 1 if outcome == "success" else 0,
                "outcome": outcome,
            }
        ],
        curator_message="Fixture decision.",
        explanation="Fixture explanation.",
    )


def _materialize(envelope: DomainEnvelope, binding_id: str, decide) -> dict[str, Any]:
    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    matches = [
        match
        for match in registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
        if match.binding.binding_id == binding_id
    ]
    inputs = []
    seen: set[str] = set()
    for match in matches:
        request = build_domain_validation_request(match).request
        assert request is not None
        if request.request_id in seen:
            continue
        seen.add(request.request_id)
        inputs.append(
            ValidatorResultMaterializationInput(match=match, request=request, result=decide(request))
        )
    result = materialize_validator_results_into_envelope(envelope, pack.metadata, inputs)
    return result.envelope.extracted_objects[0].payload


def _term_envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="disease-writeback-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={
                    "mention": "Andersen syndrome",
                    "disease_annotation_object": unresolved_value(
                        "Andersen syndrome",
                        identity_keys=("curie", "name"),
                        proposed_curie="DOID:0050434",
                    ),
                    "source_mentions": ["Andersen syndrome"],
                },
            )
        ],
    )


def test_disease_term_resolves_in_place_and_keeps_the_paper_wording():
    def decide(request):
        assert request.selected_inputs["label"] == "Andersen syndrome"
        assert request.selected_inputs["curie"] == "DOID:0050434"
        return _validator_result(
            request,
            status="resolved",
            resolved_values={"curie": "DOID:0050434", "label": "Andersen-Tawil syndrome"},
            outcome="success",
        )

    payload = _materialize(_term_envelope(), "disease_ontology_term_lookup", decide)

    assert payload["disease_annotation_object"] == {
        "proposed_curie": "DOID:0050434",
        "curie": "DOID:0050434",
        "name": "Andersen-Tawil syndrome",
        "mention": "Andersen syndrome",
        "resolution_state": RESOLVED,
        "lookup_outcome": OUTCOME_MATCHED,
        "validator_explanation": "Fixture explanation.",
        "validator_curator_message": "Fixture decision.",
    }


def test_unresolved_disease_term_records_why_and_never_gains_an_identity():
    def decide(request):
        return _validator_result(request, status="unresolved", resolved_values={}, outcome="not_found")

    payload = _materialize(_term_envelope(), "disease_ontology_term_lookup", decide)

    term = payload["disease_annotation_object"]
    assert term["resolution_state"] == "unresolved"
    assert term["lookup_outcome"] == "not_found"
    assert term["curie"] is None
    assert term["name"] is None
    assert term["mention"] == "Andersen syndrome"
    assert term["validator_explanation"] == "Fixture explanation."


def test_each_evidence_code_records_its_own_validator_result():
    envelope = DomainEnvelope(
        envelope_id="disease-eco-writeback-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload={"evidence_code_curies": _staged_list(["ECO:0000315", "IMP"], ("curie",))},
            )
        ],
    )

    def decide(request):
        if request.selected_inputs["curie"] == "ECO:0000315":
            return _validator_result(
                request, status="resolved", resolved_values={"curie": "ECO:0000315"}, outcome="success"
            )
        return _validator_result(request, status="unresolved", resolved_values={}, outcome="not_found")

    payload = _materialize(envelope, "disease_evidence_code_lookup", decide)

    resolved, unresolved = payload["evidence_code_curies"]
    assert (resolved["curie"], resolved["resolution_state"], resolved["mention"]) == (
        "ECO:0000315",
        RESOLVED,
        "ECO:0000315",
    )
    assert (unresolved["curie"], unresolved["resolution_state"], unresolved["lookup_outcome"]) == (
        None,
        "unresolved",
        "not_found",
    )
    assert unresolved["mention"] == "IMP"


def test_each_condition_component_records_its_own_composite_decision():
    """ALL-1283 Q3: the composite condition validator's per-component decisions land on each
    component; an absent component is untouched and the relation type keeps its own state."""

    envelope = DomainEnvelope(
        envelope_id="disease-condition-writeback-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-1",
                payload=_two_condition_payload(),
            )
        ],
    )

    def decide(request):
        assert "condition_id_curie" in request.expected_result_fields
        result = _validator_result(request, status="unresolved", resolved_values={}, outcome="not_found")
        if request.target.field_path.endswith("conditions[0]"):
            resolutions = {
                "condition_class_curie": {
                    "status": "resolved", "lookup_outcome": "matched",
                    "resolved_values": {
                        "condition_class_curie": "ZECO:0000111",
                        "condition_class_name": "chemical treatment",
                    },
                    "explanation": "Class matches.",
                },
                "condition_chemical_curie": {
                    "status": "unresolved", "lookup_outcome": "not_found",
                    "explanation": "No ChEBI term for this CURIE.",
                },
            }
        else:
            resolutions = {
                "condition_class_curie": {
                    "status": "unresolved", "lookup_outcome": "not_validated",
                    "explanation": "Not checked.",
                },
            }
        return result.model_copy(update={"field_resolutions": {
            key: ValidatorFieldResolution.model_validate(value) for key, value in resolutions.items()
        }})

    payload = _materialize(envelope, "experimental_condition_validation", decide)

    first, second = payload["condition_relations"][0]["conditions"]
    assert first["condition_class"]["resolution_state"] == RESOLVED
    assert (first["condition_class"]["curie"], first["condition_class"]["name"]) == (
        "ZECO:0000111",
        "chemical treatment",
    )
    assert first["condition_class"]["mention"] == "chemical treatment"
    assert first["condition_chemical"]["resolution_state"] == "unresolved"
    assert first["condition_chemical"]["lookup_outcome"] == "not_found"
    assert first["condition_chemical"]["curie"] is None
    assert first["condition_chemical"]["validator_explanation"] == "No ChEBI term for this CURIE."
    assert "condition_id" not in first
    assert second["condition_class"]["lookup_outcome"] == "not_validated"
    assert payload["condition_relations"][0]["condition_relation_type"]["lookup_outcome"] == "not_validated"


# --- ALL-1283: disease records stored in the previous format ------------------------------------


def _previous_format_payload() -> dict[str, Any]:
    """A GeneDiseaseAnnotation payload exactly as the builder stored it before ALL-1283."""

    return {
        "annotation_kind": "disease_assertion",
        "annotation_type_name": "manually_curated",
        "mention": "Alzheimer's disease",
        "disease_annotation_object": {"curie": "DOID:10652", "name": "Alzheimer's disease"},
        "disease_annotation_subject": {
            "resolution_state": "pending_entity_resolution",
            "subject_identifier": "FB:FBgn0000108",
            "subject_label": "Appl",
            "subject_type": "gene",
        },
        "role": "model_context",
        "confidence": "high",
        "data_provider": {"abbreviation": "FB"},
        "disease_relation_name": "is_implicated_in",
        "disease_relation_vocabulary": "Disease Relation",
        "disease_relation_id": "4011",
        "evidence_code_curies": ["ECO:0000315"],
        "genetic_sex_name": "male",
        "disease_qualifier_names": ["severity_of"],
        "with_gene_identifiers": ["FB:FBgn0003089"],
        "source_mentions": ["a transgenic Drosophila model of Alzheimer's disease"],
        "rationale": "Stored before the extracted-vs-validated contract.",
        "negated": False,
    }


def test_previous_format_disease_values_display_as_legacy_paper_wording_only():
    from agr_ai_curation_alliance.domain_packs.disease.legacy import (
        is_previous_format,
        previous_format_display_payload,
    )

    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields, effective_payload

    stored = _previous_format_payload()
    reshaped = previous_format_display_payload(stored)
    # The mapper only reshapes (no state); the shared legacy rule reads the values once.
    assert reshaped["disease_relation"] == {
        "name": "is_implicated_in", "vocabulary": "Disease Relation", "id": "4011",
    }
    # List elements stay as stored; the shared legacy rule reads plain strings at declared paths.
    assert reshaped["evidence_code_curies"] == ["ECO:0000315"]
    display = effective_payload(
        reshaped,
        declared_resolvable_fields(_disease_pack().metadata, "GeneDiseaseAnnotation"),
        object_metadata={},
    )

    assert is_previous_format(stored)
    assert stored == _previous_format_payload()  # The stored record is never rewritten.
    assert display["disease_relation"]["mention"] == "is_implicated_in (legacy, unverified)"
    assert display["disease_relation"]["name"] is None
    assert display["disease_relation"]["lookup_outcome"] == "legacy_unverified"
    assert display["annotation_type"]["mention"] == "manually_curated (legacy, unverified)"
    assert display["genetic_sex"]["mention"] == "male (legacy, unverified)"
    assert display["evidence_code_curies"][0]["mention"] == "ECO:0000315 (legacy, unverified)"
    assert display["evidence_code_curies"][0]["curie"] is None
    assert display["disease_qualifier_names"][0]["mention"] == "severity_of (legacy, unverified)"
    assert display["with_gene_identifiers"][0]["mention"] == "FB:FBgn0003089 (legacy, unverified)"
    for previous_key in ("disease_relation_name", "disease_relation_vocabulary", "disease_relation_id",
                         "genetic_sex_name", "annotation_type_name"):
        assert previous_key not in display
    assert not is_previous_format(
        _annotation_payload_from_builder()
    ), "current builder output is never read as the previous format"


def _annotation_payload_from_builder() -> dict[str, Any]:
    return {
        "mention": "Alzheimer's disease",
        "disease_annotation_object": unresolved_value("Alzheimer's disease", identity_keys=("curie", "name")),
        "data_provider": unresolved_value("FB", identity_keys=("abbreviation",)),
        "evidence_code_curies": _staged_list(["ECO:0000315"], ("curie",)),
    }


def test_previous_format_disease_record_gets_one_revalidation_finding():
    from agr_ai_curation_alliance.domain_packs.disease.legacy import (
        PREVIOUS_FORMAT_MESSAGE,
        validate_disease_envelope,
    )

    envelope = DomainEnvelope(
        envelope_id="disease-previous-format-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-old",
                payload=_previous_format_payload(),
            ),
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-new",
                payload=_annotation_payload_from_builder(),
            ),
        ],
    )

    finding, = validate_disease_envelope(envelope)

    assert finding.message == PREVIOUS_FORMAT_MESSAGE
    assert finding.message == "Recorded in the previous disease format; re-run extraction to validate."
    assert finding.object_ref.pending_ref_id == "gene-disease-old"


def test_previous_format_disease_record_reads_as_legacy_in_review_rows():
    from agr_ai_curation_alliance.domain_packs.disease.legacy import DiseaseReviewRowMaterializer

    stored = _previous_format_payload()
    envelope = DomainEnvelope(
        envelope_id="disease-previous-format-review-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-old",
                payload=stored,
                evidence_record_ids=[],
            )
        ],
    )

    [row] = DiseaseReviewRowMaterializer(metadata=_disease_pack().metadata).materialize(
        envelope, envelope_revision=1
    )

    fields = {field["field_path"]: field["value"] for field in row.metadata["workspace_fields"]}
    assert fields["disease_relation.mention"] == "is_implicated_in (legacy, unverified)"
    assert fields["disease_relation.name"] is None
    assert envelope.extracted_objects[0].payload == _previous_format_payload()


def test_old_disease_record_gets_exactly_the_one_previous_format_finding():
    """The previous-format finding marks the object not validatable: structural checks and
    validator dispatch skip it, so the curator sees only that one finding."""

    from agr_ai_curation_alliance.domain_packs.disease.legacy import (
        PREVIOUS_FORMAT_FINDING_CODE,
        validate_disease_envelope,
    )
    from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks

    envelope = DomainEnvelope(
        envelope_id="disease-previous-format-pipeline-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-old",
                payload=_previous_format_payload(),
            )
        ],
    )
    finding, = validate_disease_envelope(envelope)
    assert finding.details["not_validatable"] is True
    flagged = envelope.model_copy(update={"validation_findings": [finding]})

    structural = run_domain_envelope_structural_checks(flagged, _disease_pack())

    def runner(request, *, binding):
        raise AssertionError(f"no validator runs on a previous-format record: {binding.binding_id}")

    dispatched = dispatch_active_validator_bindings(flagged, _disease_pack(), runner=runner)

    assert structural.appended_findings == ()
    assert dispatched.appended_findings == ()
    assert [item.code for item in flagged.validation_findings] == [PREVIOUS_FORMAT_FINDING_CODE]


def test_extractor_proposals_are_declared_but_never_export_columns():
    from src.lib.flows.export_fields import _pack_export_fields

    pack = _disease_pack()
    proposal_fields = [
        field.field_path
        for obj in pack.metadata.object_definitions
        for field in obj.fields
        if ".proposed_" in field.field_path
    ]
    catalog_paths = {entry["payload_path"] for entry in _pack_export_fields(pack)}

    assert proposal_fields
    assert catalog_paths.isdisjoint(proposal_fields)
    assert "disease_annotation_object.mention" in catalog_paths


def test_a_curator_edit_never_makes_a_current_record_the_previous_format():
    """Review #7: only a record with no contract state at all is the previous format."""

    from agr_ai_curation_alliance.domain_packs.disease.legacy import (
        is_previous_format,
        validate_disease_envelope,
    )

    edited = {
        **_annotation_payload_from_builder(),
        "annotation_type": unresolved_value("manually_curated", identity_keys=("name",)),
        # A curator replaced the term with a bare edit and typed a with/from gene as text.
        "disease_annotation_object": {"curie": "DOID:10652", "name": "Alzheimer's disease"},
        "with_gene_identifiers": ["FB:FBgn0003089"],
    }
    envelope = DomainEnvelope(
        envelope_id="disease-edited-env",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneDiseaseAnnotation",
                pending_ref_id="gene-disease-edited",
                payload=edited,
            )
        ],
    )

    assert not is_previous_format(edited)
    assert validate_disease_envelope(envelope) == ()
    assert is_previous_format(_previous_format_payload())


def test_a_later_unresolved_result_overrules_a_resolved_disease_term():
    """The validator is the authority: re-validating a resolved term as unresolved keeps the
    overruled identity only under overruled_* (informational), leaves the extractor's
    proposal untouched, and the export then blocks on it."""

    def resolve(request):
        return _validator_result(
            request, status="resolved",
            resolved_values={"curie": "DOID:0050434", "label": "Andersen-Tawil syndrome"},
            outcome="success",
        )

    resolved_payload = _materialize(_term_envelope(), "disease_ontology_term_lookup", resolve)
    resolved_envelope = _term_envelope()
    resolved_envelope.extracted_objects[0].payload.update(resolved_payload)

    def overrule(request):
        return _validator_result(request, status="unresolved", resolved_values={}, outcome="not_found")

    payload = _materialize(resolved_envelope, "disease_ontology_term_lookup", overrule)

    term = payload["disease_annotation_object"]
    assert (term["resolution_state"], term["lookup_outcome"]) == ("unresolved", "not_found")
    assert (term["curie"], term["name"]) == (None, None)
    assert (term["overruled_curie"], term["overruled_name"]) == ("DOID:0050434", "Andersen-Tawil syndrome")
    assert term["proposed_curie"] == "DOID:0050434"
    assert "proposed_name" not in term
    assert term["mention"] == "Andersen syndrome"

    from agr_ai_curation_alliance.domain_packs._resolvable_payloads import export_identity

    identity, blocker = export_identity(
        candidate={"object": {"metadata": {}}}, payload=payload, field_path="disease_annotation_object",
        identity_keys=("curie", "name"), code="fixture.unresolved", label="Disease term",
    )
    assert identity is None
    assert blocker["details"]["lookup_outcome"] == "not_found"
