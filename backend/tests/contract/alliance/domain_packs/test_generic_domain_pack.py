"""Contract tests for generic PDF extraction domain-pack generation."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBinding,
)
from src.lib.flows.output_projection import (
    apply_projection_plan,
    build_extraction_result_artifact_bundle,
    default_projection_plan,
)
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackObjectDefinition,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.generic import (  # noqa: E402
    GENERIC_DOMAIN_PACK_ID,
    GENERIC_MATERIALIZER_ID,
    GENERIC_OBJECT_TYPE,
    GenericBuilderExtractionOutput,
    GenericClassCatalog,
    get_generated_generic_domain_pack,
    load_generic_class_catalog,
    materialize_generic_builder_state,
    proxy_object_type,
)
from agr_ai_curation_alliance.domain_packs.generic.catalog import (  # noqa: E402
    _binding_applies_to_object,
    _proxy_field_definition,
)
from agr_ai_curation_alliance.tools.builder_finalization import (  # noqa: E402
    finalize_builder_extraction,
)

BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"
PDF_AGENT_PATH = REPO_ROOT / "packages" / "alliance" / "agents" / "pdf" / "agent.yaml"


def test_catalog_exposes_independent_reagent_capabilities():
    catalog = load_generic_class_catalog()
    entry = catalog.entries_by_class_key["generic:generic_reagent_candidate"]
    payload = entry.compact_tool_dict()
    assert payload["stageable"] is True
    assert payload["definition_state"] == "stable"
    assert payload["capabilities"]["pack_state"] == "in_development"
    assert payload["capabilities"]["schema_ref"] is None
    assert payload["capabilities"]["validate"]["state"] == "none"


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-generic-1",
            "entity": "TRiP.HMS00001",
            "verified_quote": "The screen used the TRiP.HMS00001 RNAi reagent.",
            "page": 6,
            "section": "Methods",
            "subsection": "RNAi screen",
            "chunk_id": "chunk-generic-1",
        }
    ]


def _generic_workspace(staged_fields: Mapping[str, Any]) -> ExtractionBuilderWorkspace:
    workspace = ExtractionBuilderWorkspace(
        run_id="generic-builder-test-run",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )
    workspace.upsert_candidate(
        candidate_id="generic-candidate-1",
        staged_fields=dict(staged_fields),
        pending_ref_ids=["generic-object-1"],
        evidence_record_ids=["evidence-generic-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return workspace


def test_generic_catalog_derives_stageable_classes_from_domain_pack_metadata():
    catalog = load_generic_class_catalog()
    class_keys = {entry.class_key for entry in catalog.entries if entry.stageable}
    assert "generic:generic_object" in class_keys
    assert "generic:generic_reagent_candidate" in class_keys
    assert "gene:gene_mention_evidence" in class_keys

    gene_entry = catalog.entries_by_class_key["gene:gene_mention_evidence"]
    assert gene_entry.generic_object_type == proxy_object_type(
        "gene", "gene_mention_evidence"
    )
    assert gene_entry.source_is_generic_native is False
    assert gene_entry.validator_state == "active"
    assert [
        binding.binding_id for binding in gene_entry.active_validator_bindings
    ] == ["alliance_gene_reference_lookup"]
    assert "mention" in gene_entry.payload_fields
    assert "identity_resolution_notes" in gene_entry.required_payload_fields


def test_generated_generic_domain_pack_reuses_existing_validator_bindings():
    pack = get_generated_generic_domain_pack()
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    object_definitions = {
        object_definition.object_type: object_definition
        for object_definition in pack.metadata.object_definitions
    }
    assert GENERIC_OBJECT_TYPE in object_definitions
    assert proxy_type in object_definitions

    proxy_definition = object_definitions[proxy_type]
    assert proxy_definition.model_ref is None
    confidence_field = next(
        field for field in proxy_definition.fields if field.field_path == "confidence"
    )
    assert confidence_field.enum_ref is None
    assert (
        confidence_field.metadata["generic_extraction_proxy_source_refs"]["enum_ref"]
        == "GeneMentionConfidence"
    )

    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    active_bindings = [
        binding
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
    ]
    assert [binding.binding_id for binding in active_bindings] == [
        "proxy__gene__gene_mention_evidence__alliance_gene_reference_lookup"
    ]
    assert active_bindings[0].applies_to_domain_pack_id == GENERIC_DOMAIN_PACK_ID
    assert active_bindings[0].object_types == (proxy_type,)


def test_generated_generic_binding_applicability_reuses_role_and_field_targets():
    object_definition = DomainPackObjectDefinition(
        object_type="fixture_stageable",
        display_name="Fixture stageable",
        metadata={"object_role": "fixture_role"},
        fields=[
            DomainPackFieldDefinition(
                field_path="symbol",
                field_type=DomainPackFieldType.STRING,
                metadata={
                    "validator_bindings": {
                        "active": [
                            {
                                "binding_id": "source_field_binding",
                                "validator_agent": {
                                    "package_id": "fixture",
                                    "agent_id": "fixture_validator",
                                },
                                "applies_to": {
                                    "domain_pack_id": "fixture",
                                    "field_paths": ["symbol"],
                                },
                            }
                        ]
                    }
                },
            ),
            DomainPackFieldDefinition(
                field_path="confidence",
                field_type=DomainPackFieldType.STRING,
            ),
        ],
    )
    role_binding = ValidatorBinding(
        binding_id="role_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="object",
        applies_to_domain_pack_id="fixture",
        object_roles=("fixture_role",),
    )
    field_path_binding = ValidatorBinding(
        binding_id="field_path_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="field",
        applies_to_domain_pack_id="fixture",
        field_paths=("symbol",),
    )
    field_type_binding = ValidatorBinding(
        binding_id="field_type_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="field",
        applies_to_domain_pack_id="fixture",
        field_types=(DomainPackFieldType.STRING,),
    )
    wrong_role_binding = ValidatorBinding(
        binding_id="wrong_role_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="object",
        applies_to_domain_pack_id="fixture",
        object_roles=("other_role",),
    )

    assert _binding_applies_to_object(
        role_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )
    assert _binding_applies_to_object(
        field_path_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )
    assert _binding_applies_to_object(
        field_type_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )
    assert not _binding_applies_to_object(
        wrong_role_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )


def test_proxy_field_definition_strips_unproxied_field_validator_metadata():
    field_definition = DomainPackFieldDefinition(
        field_path="symbol",
        field_type=DomainPackFieldType.STRING,
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "source_field_binding",
                        "validator_agent": {
                            "package_id": "fixture",
                            "agent_id": "fixture_validator",
                        },
                        "applies_to": {
                            "domain_pack_id": "fixture",
                            "field_paths": ["symbol"],
                        },
                    }
                ]
            },
            "display": {"label": "symbol"},
        },
    )

    proxy_field = _proxy_field_definition(
        field_definition, source_pack=_source_pack(), proxied_enums={}, proxied_models={},
    )

    assert "validator_bindings" not in proxy_field.metadata
    assert proxy_field.metadata["display"] == {"label": "symbol"}


def _source_pack(enum_definitions=(), model_definitions=()):
    from src.lib.domain_packs.registry import LoadedDomainPack
    from src.schemas.domain_pack_metadata import DomainPackMetadata

    metadata = DomainPackMetadata(
        pack_id="fixture.source", display_name="Source", version="0.1.0", metadata_api_version="1.0.0",
        enum_definitions=list(enum_definitions), model_definitions=list(model_definitions),
    )
    return LoadedDomainPack(
        pack_id="fixture.source", display_name="Source", version="0.1.0",
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    )


def test_proxy_keeps_resolvable_vocabulary_enums_and_flattens_other_enums():
    """ALL-1283: resolution_state / lookup_outcome stay closed vocabularies in the generic view."""

    from src.lib.domain_packs.resolvable_values import LOOKUP_OUTCOMES
    from src.schemas.domain_pack_metadata import DomainPackEnumDefinition

    outcome_enum = DomainPackEnumDefinition(
        enum_id="LookupOutcome", display_name="Lookup outcome",
        values=[{"value": value} for value in LOOKUP_OUTCOMES],
    )
    other_enum = DomainPackEnumDefinition(enum_id="Color", display_name="Color", values=[{"value": "red"}])
    source_pack = _source_pack([outcome_enum, other_enum])
    proxied: dict = {}

    outcome = _proxy_field_definition(
        DomainPackFieldDefinition(field_path="term.lookup_outcome", field_type=DomainPackFieldType.ENUM,
                                  enum_ref="LookupOutcome"),
        source_pack=source_pack, proxied_enums=proxied, proxied_models={},
    )
    color = _proxy_field_definition(
        DomainPackFieldDefinition(field_path="color", field_type=DomainPackFieldType.ENUM, enum_ref="Color"),
        source_pack=source_pack, proxied_enums=proxied, proxied_models={},
    )

    assert outcome.field_type is DomainPackFieldType.ENUM
    assert outcome.enum_ref in proxied
    assert [value.value for value in proxied[outcome.enum_ref].values] == list(LOOKUP_OUTCOMES)
    assert (color.field_type, color.enum_ref) == (DomainPackFieldType.STRING, None)
    assert list(proxied) == [outcome.enum_ref]


def test_generated_generic_pack_carries_proxied_vocabulary_enums():
    pack = get_generated_generic_domain_pack()
    enum_ids = {enum.enum_id for enum in pack.metadata.enum_definitions}
    for obj in pack.metadata.object_definitions:
        for field in obj.fields:
            if field.enum_ref is not None:
                assert field.enum_ref in enum_ids


def test_generated_generic_validator_dispatch_builds_source_validator_request():
    pack = get_generated_generic_domain_pack()
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    envelope = DomainEnvelope(
        envelope_id="generic-gene-validator-fixture",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=proxy_type,
                pending_ref_id="generic-gene-1",
                object_role="generic_proxy_object",
                payload={
                    "mention": "daf-16",
                    "identity_resolution_notes": [
                        "The paper reports daf-16 in C. elegans."
                    ],
                    "species": "Caenorhabditis elegans",
                    "taxon_hint": "NCBITaxon:6239",
                    "data_provider_hint": "WB",
                },
            )
        ],
    )
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    matches = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )
    assert len(matches) == 1

    request = build_domain_validation_request(matches[0]).request
    assert request is not None
    assert request.target.domain_pack_id == GENERIC_DOMAIN_PACK_ID
    assert request.validator_binding_id == (
        "proxy__gene__gene_mention_evidence__alliance_gene_reference_lookup"
    )
    assert request.selected_inputs["mention"] == "daf-16"
    assert request.selected_inputs["species"] == "Caenorhabditis elegans"
    assert request.selected_inputs["identity_resolution_notes"] == [
        "The paper reports daf-16 in C. elegans."
    ]


def test_generic_builder_materializer_requires_explicit_class_key_and_label():
    missing_class_workspace = _generic_workspace(
        {
            "label": "TRiP.HMS00001",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
        }
    )
    missing_class_result = materialize_generic_builder_state(
        workspace=missing_class_workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not missing_class_result.ok
    assert any(
        issue["reason"] == "missing_class_key"
        for issue in missing_class_result.issues
    )

    missing_label_workspace = _generic_workspace(
        {
            "class_key": "generic:generic_reagent_candidate",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
        }
    )
    missing_label_result = materialize_generic_builder_state(
        workspace=missing_label_workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not missing_label_result.ok
    assert any(issue["reason"] == "missing_label" for issue in missing_label_result.issues)


def test_explicit_generic_object_class_materializes_without_fallback():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_object",
            "label": "TRiP.HMS00001",
            "source_label": "TRiP.HMS00001",
            "description": "RNAi reagent mentioned in the paper.",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": [
                "The paper calls this a reagent but no more specific class is needed."
            ],
            "attributes": {"source_identifier": "TRiP.HMS00001"},
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    assert result.payload is not None

    output = GenericBuilderExtractionOutput.model_validate(result.payload)
    obj = output.curatable_objects[0]
    assert obj.object_type == GENERIC_OBJECT_TYPE
    assert obj.payload["class_key"] == "generic:generic_object"
    assert obj.payload["label"] == "TRiP.HMS00001"
    assert obj.payload["attributes"]["source_identifier"] == "TRiP.HMS00001"
    assert obj.payload["rationale"] == "The paper names this item in its Results."
    assert obj.evidence_record_ids == ["evidence-generic-1"]
    assert obj.metadata["generic_extraction"]["class_key"] == "generic:generic_object"
    assert result.payload["metadata"]["provenance"]["source"] == GENERIC_MATERIALIZER_ID
    assert "items" not in result.payload
    assert "raw_mentions" not in result.payload


@pytest.mark.parametrize("rationale", [None, "", "   "])
def test_generic_materializer_rejects_new_candidate_without_rationale(rationale):
    staged_fields = {
        "class_key": "generic:generic_claim",
        "label": "RNAi screen result",
        "classification_notes": ["This is a paper-level result claim."],
        "payload": {"claim_text": "The screen identified TRiP.HMS00001."},
    }
    if rationale is not None:
        staged_fields["rationale"] = rationale
    result = materialize_generic_builder_state(
        workspace=_generic_workspace(staged_fields),
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )

    assert not result.ok
    assert [(issue["field_path"], issue["reason"]) for issue in result.issues] == [
        ("rationale", "missing_rationale")
    ]
    assert result.issues[0]["message"].endswith("patch the candidate with a rationale saying why you selected it.")


_GENERIC_DETAILS_FIELDS = {
    "generic_object": ["source_label", "description", "confidence"],
    "generic_claim": ["claim_text", "confidence"],
    "generic_reagent_candidate": ["source", "source_identifier", "count", "reagent_type"],
}


@pytest.mark.parametrize("object_type", sorted(_GENERIC_DETAILS_FIELDS))
def test_generic_classes_declare_protected_rationale_in_its_own_group(object_type):
    pack = get_generated_generic_domain_pack()
    definition = next(
        obj for obj in pack.metadata.object_definitions if obj.object_type == object_type
    )
    fields = {field.field_path: field for field in definition.fields}
    rationale = fields["rationale"]
    assert rationale.required is False
    assert rationale.display_name == "Rationale"
    assert rationale.metadata["protected"] is True
    assert rationale.metadata["curator_action_note"] == "Written by the extraction agent; not editable."
    assert "hide_when_empty" not in rationale.metadata
    workspace_display = definition.metadata["workspace_display"]
    assert workspace_display["groups"] == [
        {"id": "details", "label": "Details", "fields": _GENERIC_DETAILS_FIELDS[object_type]},
        {"id": "rationale", "label": "Rationale", "fields": ["rationale"]},
    ]
    # summary_fields (secondary-label fallback, candidate summaries) stay as before.
    assert workspace_display["summary_fields"] == _GENERIC_DETAILS_FIELDS[object_type]
    for path in _GENERIC_DETAILS_FIELDS[object_type]:
        assert fields[path].metadata["hide_when_empty"] is True


@pytest.mark.parametrize("object_type", sorted(_GENERIC_DETAILS_FIELDS))
def test_generic_rationale_stays_out_of_supervisor_manifest_summaries(object_type):
    from src.lib.domain_packs.supervisor_manifest import supervisor_manifest_policy_for_object

    # Each generic class declares its own supervisor_manifest, which wins over
    # workspace_display, so the review-only rationale never reaches supervisor
    # result summaries or inspect_results.
    policy = supervisor_manifest_policy_for_object(
        get_generated_generic_domain_pack().metadata, object_type
    )
    assert "rationale" not in policy.field_paths


def _generic_pack_metadata() -> Any:
    from src.lib.domain_packs.loader import load_domain_pack_metadata

    return load_domain_pack_metadata(
        REPO_ROOT / "packages" / "alliance" / "domain_packs" / "generic" / "domain_pack.yaml"
    )


def _generic_review_row(metadata: Any, object_type: str, payload: Mapping[str, Any]) -> Any:
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
    from src.schemas.domain_envelope import CuratableObjectStatus, DomainEnvelopeStatus

    envelope = DomainEnvelope(
        envelope_id="generic-review",
        domain_pack_id=metadata.pack_id,
        domain_pack_version=metadata.version,
        status=DomainEnvelopeStatus.EXTRACTED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=object_type,
                object_id="generic-1",
                status=CuratableObjectStatus.PENDING,
                payload=dict(payload),
            )
        ],
    )
    row, = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope, envelope_revision=1
    )
    return row


def _draft_fields(row: Any) -> list[dict[str, Any]]:
    from src.lib.curation_workspace.pipeline import _draft_fields_from_review_row

    return [
        {
            "field_key": field.field_key,
            "value": field.value,
            "group_key": field.group_key,
            "read_only": field.read_only,
        }
        for field in _draft_fields_from_review_row(row)
    ]


def _metadata_without_rationale(metadata: Any) -> Any:
    """The generic pack as it was before rationale and its groups were declared."""

    object_definitions = []
    for definition in metadata.object_definitions:
        display = {
            key: value
            for key, value in definition.metadata["workspace_display"].items()
            if key != "groups"
        }
        object_definitions.append(
            definition.model_copy(
                update={
                    "fields": [
                        field.model_copy(
                            update={
                                "metadata": {
                                    key: value
                                    for key, value in field.metadata.items()
                                    if key != "hide_when_empty"
                                }
                            }
                        )
                        for field in definition.fields
                        if field.field_path != "rationale"
                    ],
                    "metadata": {**definition.metadata, "workspace_display": display},
                },
                deep=True,
            )
        )
    return metadata.model_copy(update={"object_definitions": object_definitions}, deep=True)


_REAGENT_WITHOUT_COUNT_OR_IDENTIFIER = {
    "label": "TRiP.HMS00001",
    "class_key": "generic:generic_reagent_candidate",
    "source": "BDSC",
    "reagent_type": "RNAi",
    "classification_notes": ["The Methods list this RNAi line."],
}


def test_generic_reagent_review_row_adds_only_the_rationale_field():
    metadata = _generic_pack_metadata()
    before = _draft_fields(
        _generic_review_row(
            _metadata_without_rationale(metadata),
            "generic_reagent_candidate",
            _REAGENT_WITHOUT_COUNT_OR_IDENTIFIER,
        )
    )
    old_item = _draft_fields(
        _generic_review_row(metadata, "generic_reagent_candidate", _REAGENT_WITHOUT_COUNT_OR_IDENTIFIER)
    )
    new_item = _draft_fields(
        _generic_review_row(
            metadata,
            "generic_reagent_candidate",
            {**_REAGENT_WITHOUT_COUNT_OR_IDENTIFIER, "rationale": "The knockdown line used for the screen."},
        )
    )

    field_value = lambda fields: [(field["field_key"], field["value"]) for field in fields]  # noqa: E731
    # Absent count/source_identifier stay absent: no always-empty decision columns.
    assert field_value(before) == [("source", "BDSC"), ("reagent_type", "RNAi")]
    # A record stored before rationale existed shows the same fields plus a
    # rationale field with no value, which review renders as "Not recorded".
    assert field_value(old_item) == [*field_value(before), ("rationale", None)]
    assert field_value(new_item) == [
        *field_value(before),
        ("rationale", "The knockdown line used for the screen."),
    ]
    # Only the grouping header changes for existing fields.
    assert [field["group_key"] for field in new_item] == ["details", "details", "rationale"]
    # Curators read the rationale; only the extraction agent writes it.
    assert new_item[-1]["read_only"] is True
    assert old_item[-1]["read_only"] is True


@pytest.mark.parametrize(
    ("object_type", "payload"),
    [
        (
            "generic_claim",
            {"label": "Claim", "class_key": "generic:generic_claim", "claim_text": "X increases Y."},
        ),
        (
            "generic_reagent_candidate",
            {"label": "Reagent", "class_key": "generic:generic_reagent_candidate", "source": "BDSC"},
        ),
    ],
)
def test_generic_rationale_never_becomes_the_secondary_label(object_type, payload):
    metadata = _generic_pack_metadata()
    with_rationale = {**payload, "rationale": "Why this item was selected."}

    assert _generic_review_row(
        _metadata_without_rationale(metadata), object_type, payload
    ).secondary_label is None
    assert _generic_review_row(metadata, object_type, payload).secondary_label is None
    row = _generic_review_row(metadata, object_type, with_rationale)
    assert row.secondary_label is None
    assert "rationale" not in [field.field_path for field in row.summary_fields]


def test_generic_materializer_rejects_invalid_semantic_attributes():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_object",
            "label": "B cell lymphoma",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["The paper reports this tumor classification."],
            "attributes": {
                "Cell Type": "B cell",
                "cell-type": "duplicate",
            },
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )

    assert not result.ok
    assert any(
        issue["reason"] == "duplicate_normalized_attribute_key"
        for issue in result.issues
    )


def test_generic_materializer_enforces_required_class_payload_fields():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_claim",
            "label": "principal finding",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["This is a paper-level result claim."],
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )

    assert not result.ok
    assert any(
        issue["reason"] == "missing_required_payload_field"
        and issue["field_path"] == "payload.claim_text"
        for issue in result.issues
    )


def test_generic_materializer_rejects_payload_keys_outside_selected_class():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_reagent_candidate",
            "label": "TRiP.HMS00001",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
            "payload": {"source_identifer": "typo"},
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )

    assert not result.ok
    assert any(
        issue["reason"] == "unknown_payload_field"
        and issue["field_path"] == "payload.source_identifer"
        for issue in result.issues
    )


def test_generic_materializer_allows_empty_no_result_extraction():
    workspace = ExtractionBuilderWorkspace(
        run_id="generic-empty-builder-test-run",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )

    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=[],
        evidence_records=[],
        resolver_entry_lookup=None,
    )

    assert result.ok, result.summary()
    assert result.payload is not None
    output = GenericBuilderExtractionOutput.model_validate(result.payload)
    assert output.curatable_objects == []
    assert output.run_summary.candidate_count == 0
    assert output.run_summary.kept_count == 0


def test_generic_builder_finalization_projects_to_object_tsv_rows():
    workspace = ExtractionBuilderWorkspace(
        run_id="generic-builder-tsv-regression",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )
    evidence_records = [
        {
            "evidence_record_id": "evidence-generic-1",
            "verified_quote": "Ck:GFP was used as a genetic reagent.",
        },
        {
            "evidence_record_id": "evidence-generic-2",
            "verified_quote": "Actn RNAi was used as a genetic reagent.",
        },
    ]
    for index, (candidate_id, label, source, count, evidence_id) in enumerate(
        [
            ("generic-candidate-1", "Ck:GFP", "This study", 4, "evidence-generic-1"),
            (
                "generic-candidate-2",
                "Actn RNAi",
                "Source not found",
                2,
                "evidence-generic-2",
            ),
        ],
        start=1,
    ):
        workspace.upsert_candidate(
            candidate_id=candidate_id,
            staged_fields={
                "class_key": "generic:generic_reagent_candidate",
                "label": label,
                "rationale": "The paper names this item in its Results.",
                "classification_notes": ["The prompt asked for a reagent inventory."],
                "payload": {
                    "source": source,
                    "source_identifier": "New in paper" if index == 1 else "Not found",
                    "count": count,
                },
            },
            pending_ref_ids=[f"generic-object-{index}"],
            evidence_record_ids=[evidence_id],
            resolver_selection_refs=[],
            status=CANDIDATE_STATUS_VALID,
        )

    outcome = finalize_builder_extraction(
        workspace=workspace,
        candidate_ids=["generic-candidate-1", "generic-candidate-2"],
        materialize=materialize_generic_builder_state,
        evidence_records=evidence_records,
        resolver_entry_lookup=None,
        materialized_candidate_prefix="generic-envelope",
        require_evidence_record_ids=True,
        require_resolver_selections=False,
    )

    assert outcome.ok, outcome.issues
    assert outcome.finalization is not None
    extraction_record = CurationExtractionResultRecord(
        extraction_result_id="generic-reagent-result-1",
        document_id="generic-reagent-document",
        adapter_key="generic",
        agent_key="pdf_extraction",
        source_kind=CurationExtractionSourceKind.FLOW,
        flow_run_id="flow-gillian-regression",
        candidate_count=2,
        conversation_summary="Extracted two generic reagents.",
        payload_json=outcome.finalization.payload,
        created_at=datetime.now(timezone.utc),
        metadata={
            "flow_id": "flow-gillian-regression",
            "step": 1,
            "tool_name": "ask_pdf_specialist",
        },
    )
    bundle = build_extraction_result_artifact_bundle(
        extraction_results=[extraction_record],
        bundle_name="Gillian Regression Flow",
        output_format="tsv",
    )
    result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="tsv"),
    )

    assert result.row_source == "object"
    assert result.total_count == 2
    assert "artifact_preview" not in [column.key for column in result.columns]
    assert [row["object_payload_label"] for row in result.rows] == ["Ck:GFP", "Actn RNAi"]


def test_generic_proxy_materializer_hydrates_required_evidence_fields_and_schema_ref():
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    workspace = _generic_workspace(
        {
            "class_key": "gene:gene_mention_evidence",
            "label": "daf-16",
            "confidence": "high",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["The paper-backed mention is a gene symbol."],
            "payload": {
                "identity_resolution_notes": [
                    "The paper reports this symbol in C. elegans."
                ],
                "species": "Caenorhabditis elegans",
                "taxon_hint": "NCBITaxon:6239",
                "data_provider_hint": "WB",
            },
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=[
            {
                "evidence_record_id": "evidence-generic-1",
                "entity": "daf-16",
                "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
                "page": 4,
                "section": "Results",
                "chunk_id": "chunk-daf16-1",
            }
        ],
        resolver_entry_lookup=None,
    )

    assert result.ok, result.summary()
    assert result.payload is not None
    obj = result.payload["curatable_objects"][0]
    assert obj["object_type"] == proxy_type
    assert obj["schema_ref"]["schema_id"] == "alliance.linkml.Gene"
    assert obj["payload"]["mention"] == "daf-16"
    assert obj["payload"]["evidence_record_id"] == "evidence-generic-1"
    assert obj["payload"]["verified_quote"] == (
        "DAF-16 translocated to nuclei after heat shock."
    )
    assert obj["payload"]["page"] == 4
    assert obj["payload"]["section"] == "Results"
    assert obj["payload"]["chunk_id"] == "chunk-daf16-1"
    payload_fields = {
        field_definition.field_path
        for object_definition in get_generated_generic_domain_pack().metadata.object_definitions
        if object_definition.object_type == proxy_type
        for field_definition in object_definition.fields
    }
    assert set(obj["payload"]).issubset(payload_fields)
    assert "label" not in obj["payload"]
    assert "class_key" not in obj["payload"]
    assert "semantic_class" not in obj["payload"]
    assert "classification_notes" not in obj["payload"]
    assert obj["metadata"]["generic_extraction"]["label"] == "daf-16"


def test_unknown_generic_class_key_is_rejected_not_silently_fallbacked():
    workspace = _generic_workspace(
        {
            "class_key": "unknown:thing",
            "label": "TRiP.HMS00001",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["This deliberately uses an unknown class key."],
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(issue["reason"] == "invalid_class_key" for issue in result.issues)


def test_generic_adapter_resolves_generated_domain_pack_view():
    pack = resolve_curation_domain_pack_by_id(GENERIC_DOMAIN_PACK_ID)
    assert pack is not None
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    assert any(
        object_definition.object_type == proxy_type
        for object_definition in pack.metadata.object_definitions
    )


def test_finalize_generic_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_generic_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.generic_builder_tools:"
        "finalize_generic_extraction"
    )
    assert "list_generic_object_classes" in by_id
    for tool_id in (
        "stage_generic_object",
        "patch_generic_object",
        "discard_generic_object",
        "list_staged_generic_objects",
        "find_staged_generic_objects",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_pdf_extraction_agent_is_converted_to_generic_builder_contract():
    agent = yaml.safe_load(PDF_AGENT_PATH.read_text(encoding="utf-8"))
    assert agent["agent_id"] == "pdf_extraction"
    assert agent["output_schema"] is None
    assert "structured_finalization" not in agent
    assert agent["curation"] == {
        "adapter_key": "generic",
        "domain_pack_id": "generic",
        "launchable": True,
    }
    tools = set(agent["tools"])
    assert "list_generic_object_classes" in tools
    assert "stage_generic_object" in tools
    assert "finalize_generic_extraction" in tools
    assert "finalize_pdf_extraction" not in tools
    assert "PdfExtractionResultEnvelope" not in str(agent)


def test_generic_materializer_rejects_unknown_evidence_record_id():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_reagent_candidate",
            "label": "TRiP.HMS00001",
            "rationale": "The paper names this item in its Results.",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=[],
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_non_stageable_catalog_entry_cannot_be_required():
    catalog = load_generic_class_catalog()
    entry = catalog.entries_by_class_key["generic:generic_object"]
    non_stageable_catalog = GenericClassCatalog(
        entries=(
            replace(
                entry,
                class_key="generic:temporarily_non_stageable",
                stageable=False,
            ),
        ),
        generated_domain_pack=get_generated_generic_domain_pack(),
    )
    with pytest.raises(ValueError):
        non_stageable_catalog.require_stageable("generic:temporarily_non_stageable")



def test_proxy_keeps_a_resolvable_object_root_and_nested_resolvable_models():
    """ALL-1283: the generic view reads the same resolvable values as the source pack."""

    from types import SimpleNamespace

    from agr_ai_curation_alliance.domain_packs.generic.catalog import _proxy_object_definition
    from src.lib.domain_packs.resolvable_values import (
        LEGACY_UNVERIFIED_SUFFIX,
        LOOKUP_OUTCOMES,
        RESOLUTION_STATES,
        declared_resolvable_fields,
        unresolved_header_text,
    )
    from src.schemas.domain_pack_metadata import (
        DomainPackEnumDefinition,
        DomainPackMetadata,
        DomainPackModelDefinition,
    )

    resolvable_display = {"label": "symbol", "id": "curie", "mention": "mention"}
    source_pack = _source_pack(
        enum_definitions=[
            DomainPackEnumDefinition(enum_id="ResolutionState", display_name="State",
                                     values=[{"value": value} for value in RESOLUTION_STATES]),
            DomainPackEnumDefinition(enum_id="LookupOutcome", display_name="Outcome",
                                     values=[{"value": value} for value in LOOKUP_OUTCOMES]),
        ],
        model_definitions=[
            DomainPackModelDefinition(model_id="MentionPayload", display_name="Mention",
                                      metadata={"display": resolvable_display}),
            DomainPackModelDefinition(model_id="PlainPayload", display_name="Plain",
                                      metadata={"display": {"label": "symbol"}}),
        ],
    )
    source_object = DomainPackObjectDefinition(
        object_type="Mention", display_name="Mention", model_ref="MentionPayload",
        fields=[
            DomainPackFieldDefinition(field_path="symbol", field_type=DomainPackFieldType.STRING),
            DomainPackFieldDefinition(field_path="resolution_state", field_type=DomainPackFieldType.ENUM,
                                      enum_ref="ResolutionState"),
            DomainPackFieldDefinition(field_path="lookup_outcome", field_type=DomainPackFieldType.ENUM,
                                      enum_ref="LookupOutcome"),
            DomainPackFieldDefinition(field_path="partner", field_type=DomainPackFieldType.OBJECT,
                                      model_ref="MentionPayload"),
            DomainPackFieldDefinition(field_path="plain", field_type=DomainPackFieldType.OBJECT,
                                      model_ref="PlainPayload"),
        ],
    )
    entry = SimpleNamespace(class_key="fixture:mention", source_domain_pack_id="fixture.source",
                            source_object_type="Mention", display_name="Mention",
                            generic_object_type="generic_proxy__fixture_source__Mention")
    enums: dict = {}
    models: dict = {}
    proxy = _proxy_object_definition(source_object, entry=entry, source_pack=source_pack,
                                     proxied_enums=enums, proxied_models=models)

    assert proxy.model_ref in models
    fields = {field.field_path: field for field in proxy.fields}
    assert fields["partner"].model_ref == proxy.model_ref
    assert fields["plain"].model_ref is None
    generic = DomainPackMetadata(
        pack_id="generic", display_name="Generic", version="0.1.0", metadata_api_version="1.0.0",
        enum_definitions=list(enums.values()), model_definitions=list(models.values()),
        object_definitions=[proxy],
    )
    assert set(declared_resolvable_fields(generic, proxy.object_type)) == {"", "partner"}
    assert unresolved_header_text(
        {"symbol": "unc-54"}, "symbol",
        resolvable_fields=declared_resolvable_fields(generic, proxy.object_type),
    ) == f"unc-54 {LEGACY_UNVERIFIED_SUFFIX}"
