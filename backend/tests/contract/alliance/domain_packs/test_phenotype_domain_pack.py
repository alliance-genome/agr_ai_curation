"""Contract tests for the Alliance phenotype domain pack."""

from __future__ import annotations

import copy
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
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
from src.schemas.domain_validator import DomainValidatorResultBase

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.phenotype import (  # noqa: E402
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_FIXTURE_PACK_ID,
    PHENOTYPE_OBJECT_TYPE,
    PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
    PHENOTYPE_SUBJECT_OBJECT_TYPE,
    PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
    PHENOTYPE_TERM_OBJECT_TYPE,
    PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
    build_pending_phenotype_envelope_from_tool_verified_fixture,
    get_phenotype_domain_pack_metadata_path,
    validate_pending_phenotype_envelope,
)
from tests.fixtures.evidence.harness import load_evidence_fixture  # noqa: E402

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)

PHENOTYPE_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "phenotype"
    / "tool_verified_pending_envelope.yaml"
)
LEGACY_SEMANTIC_KEYS = {
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


def _phenotype_pack():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None
    return pack


def _phenotype_object_definition():
    metadata = load_domain_pack_metadata(get_phenotype_domain_pack_metadata_path())
    return next(
        item
        for item in metadata.object_definitions
        if item.object_type == PHENOTYPE_OBJECT_TYPE
    )


def _phenotype_subject_object_definition():
    metadata = load_domain_pack_metadata(get_phenotype_domain_pack_metadata_path())
    return next(
        item
        for item in metadata.object_definitions
        if item.object_type == PHENOTYPE_SUBJECT_OBJECT_TYPE
    )


def _iter_mapping_keys(value: Any):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def test_phenotype_domain_pack_loads_from_alliance_registry():
    registry = load_alliance_domain_pack_registry()
    loaded_pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)

    assert registry.failed_packs == ()
    assert loaded_pack is not None
    assert loaded_pack.metadata_path == get_phenotype_domain_pack_metadata_path()
    assert loaded_pack.metadata.version == "0.1.0"


def test_phenotype_pack_declares_roles_and_validator_bindings():
    metadata = _phenotype_pack().metadata

    roles_by_object_type = {
        object_definition.object_type: object_definition.metadata[
            OBJECT_ROLE_METADATA_KEY
        ]
        for object_definition in metadata.object_definitions
    }
    assert roles_by_object_type == {
        PHENOTYPE_OBJECT_TYPE: "curatable_unit",
        PHENOTYPE_SUBJECT_OBJECT_TYPE: "validated_reference",
        PHENOTYPE_TERM_OBJECT_TYPE: "validated_reference",
        "Reference": "validated_reference",
        "EvidenceQuote": "metadata_only",
    }

    annotation = _phenotype_object_definition()
    object_ref_fields = {
        field.field_path: field.object_type_ref
        for field in annotation.fields
        if field.field_type is DomainPackFieldType.OBJECT_REF
    }
    assert object_ref_fields == {
        "phenotype_annotation_subject": PHENOTYPE_SUBJECT_OBJECT_TYPE,
        "phenotype_terms[0]": PHENOTYPE_TERM_OBJECT_TYPE,
        "single_reference": "Reference",
        "evidence_quote": "EvidenceQuote",
    }

    validator_bindings = metadata.metadata["validator_bindings"]
    active_bindings = validator_bindings["active"]
    assert [binding["binding_id"] for binding in active_bindings] == [
        PHENOTYPE_TERM_VALIDATOR_BINDING_ID
    ]
    term_binding = active_bindings[0]
    assert term_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "ontology_term_validation",
    }
    assert term_binding["input_fields"] == {
        "curie": {
            "source": "payload",
            "path": "curie",
            "required": False,
        },
        "label": {
            "source": "payload",
            "path": "label",
            "required": False,
        },
        "data_provider": {
            "source": "payload",
            "path": "ontology_lookup_hint.data_provider",
            "required": False,
        },
        "taxon_id": {
            "source": "payload",
            "path": "ontology_lookup_hint.taxon_id",
            "required": False,
        },
        "evidence_record_id": {
            "source": "payload",
            "path": "ontology_lookup_hint.evidence_record_id",
            "required": False,
        },
        "evidence_quote": {
            "source": "evidence_record",
            "path": "verified_quote",
            "required": False,
        },
        "source_chunk_id": {
            "source": "evidence_record",
            "path": "chunk_id",
            "required": False,
        },
        "source_section": {
            "source": "evidence_record",
            "path": "section",
            "required": False,
        },
        "ontology_family": {
            "source": "literal",
            "value": "phenotype",
            "required": True,
        },
        "accepted_prefixes": {
            "source": "literal",
            "value": ["MP", "WBPhenotype"],
            "required": True,
        },
        "provider_taxon_ontology_mappings": {
            "source": "literal",
            "value": [
                {
                    "data_provider": "WB",
                    "taxon_id": "NCBITaxon:6239",
                    "ontology_term_type": "WBPhenotypeTerm",
                    "accepted_prefixes": ["WBPhenotype"],
                    "grounding": {
                        "package_tool": "agr_curation_query.search_ontology_terms",
                        "live_db_term_type_verified": True,
                        "representative_terms": [
                            "WBPhenotype:0000180",
                            "WBPhenotype:0000886",
                        ],
                    },
                },
                {
                    "data_provider": "MGI",
                    "taxon_id": "NCBITaxon:10090",
                    "ontology_term_type": "MPTerm",
                    "accepted_prefixes": ["MP"],
                    "grounding": {
                        "package_tool": "agr_curation_query.search_ontology_terms",
                        "live_db_term_type_verified": True,
                        "representative_terms": ["MP:0001569", "MP:0003733"],
                    },
                },
            ],
            "required": True,
        },
    }
    assert term_binding["expected_result_fields"] == {
        "curie": "curie",
        "label": "label",
    }
    assert term_binding["required"] is True
    assert term_binding["blocking"] is False
    assert term_binding["allow_opt_out"] is True
    assert term_binding["curator_override"] == {"allowed": False}

    validators = metadata.metadata["validators"]
    active_validator_ids = {
        validator["validator_id"] for validator in validators["active"]
    }
    under_development_validator_ids = {
        validator["validator_id"] for validator in validators["under_development"]
    }
    assert PHENOTYPE_TERM_VALIDATOR_BINDING_ID in active_validator_ids
    assert "phenotype.ontology_term_resolution" not in under_development_validator_ids
    assert (
        "phenotype.additional_provider_ontology_mappings"
        in under_development_validator_ids
    )

    under_development_bindings = validator_bindings["under_development"]
    binding_ids = [binding["binding_id"] for binding in under_development_bindings]
    assert binding_ids == [
        PHENOTYPE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
        PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
        "phenotype_reference_validator",
    ]

    pending_binding = under_development_bindings[0]
    assert pending_binding["state_explanation"]

    subject_binding = under_development_bindings[1]
    assert subject_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "subject_entity_validation",
    }
    assert subject_binding["input_fields"] == {
        "subject_type": {
            "source": "payload",
            "path": "subject_type",
            "required": True,
        },
        "subject_identifier": {
            "source": "payload",
            "path": "subject_identifier",
            "required": True,
        },
        "subject_label": {
            "source": "payload",
            "path": "subject_label",
            "required": False,
        },
        "taxon": {
            "source": "payload",
            "path": "taxon",
            "required": False,
        },
    }
    assert subject_binding["expected_result_fields"] == {
        "subject_identifier": "subject_identifier",
        "subject_type": "subject_type",
        "subject_label": "subject_label",
        "taxon": "taxon",
    }

    reference_binding = under_development_bindings[2]
    assert reference_binding["binding_id"] == "phenotype_reference_validator"


def test_phenotype_pack_records_grounding_and_export_blocker_policy():
    metadata = _phenotype_pack().metadata
    assert metadata.metadata["semantic_source"] == "domain_envelope.objects"
    assert metadata.metadata["legacy_semantic_lists"] == []

    curation_db_grounding = metadata.metadata["curation_db_grounding"]
    assert curation_db_grounding["verified_with"] == "read_only_curation_db"
    tables = {table["table"]: table for table in curation_db_grounding["tables"]}
    assert set(tables) >= {
        "public.phenotypeannotation",
        "public.genephenotypeannotation",
        "public.allelephenotypeannotation",
        "public.agmphenotypeannotation",
        "public.phenotypeannotation_ontologyterm",
    }
    assert (
        "phenotypeannotation_ontologyterm_phenotypeterms_id_fk references "
        "public.ontologyterm(id)"
    ) in tables["public.phenotypeannotation_ontologyterm"]["verified_constraints"]
    assert curation_db_grounding["verified_fixture_terms"] == [
        {"curie": "WBPhenotype:0000886"},
        {"curie": "WBPhenotype:0001174"},
        {"curie": "MP:0001569"},
        {"curie": "MP:0003733"},
    ]
    mapping_policy = curation_db_grounding["phenotype_provider_taxon_mappings"]
    assert {
        (mapping["data_provider"], mapping["taxon_id"], mapping["ontology_term_type"])
        for mapping in mapping_policy["active"]
    } == {
        ("WB", "NCBITaxon:6239", "WBPhenotypeTerm"),
        ("MGI", "NCBITaxon:10090", "MPTerm"),
    }
    assert {mapping["data_provider"] for mapping in mapping_policy["under_development"]} == {
        "RGD",
        "HGNC",
        "ZFIN",
        "FB",
        "SGD",
    }

    blocker_policy = metadata.metadata["export_blocker_policy"]
    assert blocker_policy["status"] == "blocked"
    assert "insert public.phenotypeannotation" in blocker_policy["blocked_operations"]
    assert (
        "Resolve phenotype_annotation_subject to exactly one Gene, Allele, or AGM DB row."
        in blocker_policy["required_before_export"]
    )


def test_phenotype_annotation_declares_required_linkml_grounded_fields():
    annotation = _phenotype_object_definition()
    fields_by_path = {field.field_path: field for field in annotation.fields}

    assert {
        "phenotype_annotation_object",
        "phenotype_annotation_subject",
        "phenotype_terms[0]",
        "phenotype_terms[0].curie",
        "single_reference",
        "evidence_quote",
        "evidence_record_ids[0]",
    }.issubset(fields_by_path)

    statement_ref = fields_by_path["phenotype_annotation_object"].metadata[
        PROVIDER_REFS_METADATA_KEY
    ][ALLIANCE_LINKML_PROVIDER_KEY]
    assert statement_ref["class"] == "PhenotypeAnnotation"
    assert statement_ref["attribute"] == "phenotype_annotation_object"
    assert statement_ref["range"] == "string"

    subject_ref = fields_by_path["phenotype_annotation_subject"].metadata[
        PROVIDER_REFS_METADATA_KEY
    ][ALLIANCE_LINKML_PROVIDER_KEY]
    assert subject_ref["slot"] == "phenotype_annotation_subject"
    assert subject_ref["range"] == "BiologicalEntity"
    assert (
        fields_by_path["phenotype_annotation_subject"].metadata["validator_binding_id"]
        == PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID
    )

    term_ref = fields_by_path["phenotype_terms[0].curie"].metadata[
        PROVIDER_REFS_METADATA_KEY
    ][ALLIANCE_LINKML_PROVIDER_KEY]
    assert term_ref["class"] == "PhenotypeTerm"
    assert term_ref["slot"] == "curie"
    assert term_ref["range"] == "uriorcurie"
    assert (
        fields_by_path["phenotype_terms[0].curie"].metadata["validator_binding_id"]
        == PHENOTYPE_TERM_VALIDATOR_BINDING_ID
    )


def test_phenotype_subject_declares_linkml_grounded_taxon_context():
    subject = _phenotype_subject_object_definition()
    fields_by_path = {field.field_path: field for field in subject.fields}

    assert {
        "resolution_state",
        "subject_identifier",
        "subject_label",
        "subject_type",
        "taxon",
    }.issubset(fields_by_path)

    taxon_field = fields_by_path["taxon"]
    assert taxon_field.field_type is DomainPackFieldType.STRING
    assert taxon_field.required is True
    assert taxon_field.metadata["validatable"] is True
    assert (
        taxon_field.metadata["validator_binding_id"]
        == PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID
    )
    taxon_ref = taxon_field.metadata[PROVIDER_REFS_METADATA_KEY][
        ALLIANCE_LINKML_PROVIDER_KEY
    ]
    assert taxon_ref["commit"] == ALLIANCE_LINKML_COMMIT
    assert taxon_ref["source_file"] == "model/schema/core.yaml"
    assert taxon_ref["class"] == "BiologicalEntity"
    assert taxon_ref["slot"] == "taxon"
    assert taxon_ref["range"] == "NCBITaxonTerm"


def test_tool_verified_phenotype_fixture_converts_to_pending_envelope():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(
        fixture,
        envelope_id="phenotype-tool-verified-envelope",
        created_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
    )

    assert validate_pending_phenotype_envelope(envelope) == ()
    assert envelope.domain_pack_id == PHENOTYPE_DOMAIN_PACK_ID
    assert {obj.status for obj in envelope.objects} == {CuratableObjectStatus.PENDING}

    counts = Counter(obj.object_type for obj in envelope.objects)
    assert counts == {
        "Reference": 1,
        PHENOTYPE_SUBJECT_OBJECT_TYPE: 1,
        PHENOTYPE_TERM_OBJECT_TYPE: 1,
        "EvidenceQuote": 2,
        PHENOTYPE_OBJECT_TYPE: 1,
    }

    annotation = next(
        obj for obj in envelope.objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    assert annotation.payload["phenotype_annotation_object"] == "reduced brood size"
    assert annotation.payload["phenotype_terms"] == [
        {
            "resolution_state": "pending_ontology_resolution",
            "curie": "WBPhenotype:0000886",
            "label": "reduced brood size",
            "source_mentions": ["reduced brood size"],
            "ontology_lookup_hint": {"evidence_record_id": "verified_exact"},
            "export_state": "blocked_pending_ontology_resolution",
            "write_blocked_reason": "phenotype term CURIE unresolved",
        }
    ]
    assert annotation.metadata["export_behavior"]["status"] == "blocked"
    assert annotation.metadata["write_behavior"]["status"] == "blocked"

    missing_required_fields = [
        field.field_path
        for field in _phenotype_object_definition().fields
        if field.required
        and not field_path_exists(annotation.payload, field.field_path)
    ]
    assert missing_required_fields == []

    expected = yaml.safe_load(PHENOTYPE_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert (
        envelope.model_dump(
            mode="json",
            exclude_defaults=True,
            exclude_none=True,
        )
        == expected["envelope"]
    )


def test_tool_verified_phenotype_fixture_preserves_subject_taxon_context():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    fixture["extraction"]["items"][0].update(
        {
            "subject_identifier": "WB:WBGene00000912",
            "subject_label": "daf-2(e1370)",
            "subject_type": "gene",
            "taxon": "NCBITaxon:6239",
        }
    )
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(fixture)

    assert validate_pending_phenotype_envelope(envelope) == ()
    subject = next(
        obj
        for obj in envelope.objects
        if obj.object_type == PHENOTYPE_SUBJECT_OBJECT_TYPE
    )
    annotation = next(
        obj for obj in envelope.objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    assert subject.payload["taxon"] == "NCBITaxon:6239"
    assert (
        annotation.payload["phenotype_annotation_subject"]["taxon"] == "NCBITaxon:6239"
    )


def test_pending_phenotype_term_without_curie_dispatches_with_context():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    item = fixture["extraction"]["items"][0]
    item.pop("normalized_id")
    item.update(
        {
            "data_provider": "MGI",
            "taxon": "NCBITaxon:10090",
            "subject_identifier": "MGI:109583",
            "subject_type": "gene",
        }
    )
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(fixture)

    assert validate_pending_phenotype_envelope(envelope) == ()
    phenotype_term = next(
        obj for obj in envelope.objects if obj.object_type == PHENOTYPE_TERM_OBJECT_TYPE
    )
    assert phenotype_term.payload["curie"] is None
    assert phenotype_term.payload["resolution_state"] == "pending_ontology_resolution"
    assert phenotype_term.payload["ontology_lookup_hint"] == {
        "data_provider": "MGI",
        "taxon_id": "NCBITaxon:10090",
        "evidence_record_id": "verified_exact",
    }
    assert phenotype_term.payload["export_state"] == (
        "blocked_pending_ontology_resolution"
    )
    assert phenotype_term.payload["write_blocked_reason"] == (
        "phenotype term CURIE unresolved"
    )

    registry = DomainPackValidationRegistry.from_domain_pack(_phenotype_pack())
    matches = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )
    assert len(matches) == 1

    selector_result = build_domain_validation_request(matches[0])

    assert selector_result.findings == ()
    assert selector_result.request is not None
    assert selector_result.selected_inputs["label"] == "reduced brood size"
    assert "curie" not in selector_result.selected_inputs
    assert selector_result.selected_inputs["data_provider"] == "MGI"
    assert selector_result.selected_inputs["taxon_id"] == "NCBITaxon:10090"
    assert selector_result.selected_inputs["evidence_record_id"] == "verified_exact"
    assert selector_result.selected_inputs["evidence_quote"] == (
        "daf-2(e1370) adults produced 40% fewer progeny than wild type."
    )
    assert selector_result.selected_inputs["source_chunk_id"] == (
        "chunk-phenotype-count"
    )
    assert selector_result.evidence[0]["evidence_record_id"] == "verified_exact"
    assert selector_result.selected_inputs["provider_taxon_ontology_mappings"][1] == {
        "data_provider": "MGI",
        "taxon_id": "NCBITaxon:10090",
        "ontology_term_type": "MPTerm",
        "accepted_prefixes": ["MP"],
        "grounding": {
            "package_tool": "agr_curation_query.search_ontology_terms",
            "live_db_term_type_verified": True,
            "representative_terms": ["MP:0001569", "MP:0003733"],
        },
    }


def test_unsupported_phenotype_provider_taxon_label_lookup_is_blocked_preflight():
    envelope = DomainEnvelope(
        envelope_id="phenotype-zfin-env",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        objects=[
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id="phenotype-term-zfin",
                payload={
                    "resolution_state": "pending_ontology_resolution",
                    "curie": None,
                    "label": "boundary disruptions",
                    "source_mentions": ["boundary disruptions"],
                    "ontology_lookup_hint": {
                        "taxon_id": "NCBITaxon:7955",
                    },
                    "export_state": "blocked_pending_ontology_resolution",
                    "write_blocked_reason": "phenotype term CURIE unresolved",
                },
            )
        ],
    )

    def _runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("unsupported phenotype mapping should preflight-block")

    result = dispatch_active_validator_bindings(
        envelope,
        _phenotype_pack(),
        runner=_runner,
    )

    assert len(result.validator_results) == 1
    validator_result = result.validator_results[0]
    assert validator_result.status == "unresolved"
    assert validator_result.missing_expected_fields == []
    assert validator_result.lookup_attempts[0].outcome == "blocked"
    assert (
        validator_result.lookup_attempts[0].method
        == "unsupported_provider_taxon_mapping"
    )
    assert validator_result.lookup_attempts[0].query["taxon_id"] == (
        "NCBITaxon:7955"
    )
    assert validator_result.lookup_attempts[0].query[
        "active_provider_taxon_ontology_mappings"
    ] == [
        {
            "data_provider": "WB",
            "taxon_id": "NCBITaxon:6239",
            "ontology_term_type": "WBPhenotypeTerm",
            "accepted_prefixes": ["WBPhenotype"],
        },
        {
            "data_provider": "MGI",
            "taxon_id": "NCBITaxon:10090",
            "ontology_term_type": "MPTerm",
            "accepted_prefixes": ["MP"],
        },
    ]
    assert "no active provider/taxon ontology mapping matched" in (
        validator_result.explanation
    )

    finding = result.envelope.validation_findings[0]
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.details["failure_classification"] == "blocked"
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "blocked"


def test_phenotype_term_curie_remains_optional_fast_path_for_dispatch():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    fixture["extraction"]["items"][0].update(
        {
            "data_provider": "WB",
            "taxon": "NCBITaxon:6239",
        }
    )
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(fixture)
    registry = DomainPackValidationRegistry.from_domain_pack(_phenotype_pack())
    match = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )[0]

    selector_result = build_domain_validation_request(match)

    assert selector_result.request is not None
    assert selector_result.selected_inputs["curie"] == "WBPhenotype:0000886"
    assert selector_result.selected_inputs["label"] == "reduced brood size"
    assert selector_result.selected_inputs["data_provider"] == "WB"
    assert selector_result.selected_inputs["taxon_id"] == "NCBITaxon:6239"


def test_phenotype_term_materializes_only_after_validator_resolution():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    fixture["extraction"]["items"][0].pop("normalized_id")
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(fixture)
    pack = _phenotype_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    match = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )[0]
    selector_result = build_domain_validation_request(match)
    assert selector_result.request is not None

    unresolved = DomainValidatorResultBase(
        status="unresolved",
        request_id=selector_result.request.request_id,
        validator_binding_id=selector_result.request.validator_binding_id,
        validator_agent=selector_result.request.validator_agent,
        target=selector_result.request.target,
        resolved_values={},
        resolved_objects=[],
        missing_expected_fields=["curie", "label"],
        candidates=[],
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_ontology_terms",
                "query": {"term": "reduced brood size"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        curator_message="Phenotype term remains unresolved.",
        explanation="No ontology term resolved from tool evidence.",
    )
    unresolved_result = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [
            ValidatorResultMaterializationInput(
                match=match,
                request=selector_result.request,
                result=unresolved,
            )
        ],
    )

    assert unresolved_result.materialized_objects == ()
    assert all(
        obj.status is CuratableObjectStatus.PENDING
        for obj in unresolved_result.envelope.objects
    )
    assert unresolved_result.appended_findings[0].code == (
        "domain_pack.validator_unresolved"
    )

    resolved = DomainValidatorResultBase(
        status="resolved",
        request_id=selector_result.request.request_id,
        validator_binding_id=selector_result.request.validator_binding_id,
        validator_agent=selector_result.request.validator_agent,
        target=selector_result.request.target,
        resolved_values={
            "curie": "WBPhenotype:0000886",
            "label": "reduced brood size",
        },
        resolved_objects=[
            {
                "object_type": PHENOTYPE_TERM_OBJECT_TYPE,
                "canonical_id": "WBPhenotype:0000886",
                "payload": {
                    "resolution_state": "resolved",
                    "curie": "WBPhenotype:0000886",
                    "label": "reduced brood size",
                },
            }
        ],
        missing_expected_fields=[],
        candidates=[],
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_ontology_terms",
                "query": {
                    "term": "reduced brood size",
                    "ontology_term_type": "WBPhenotypeTerm",
                },
                "result_count": 1,
                "outcome": "success",
            }
        ],
        curator_message="Resolved phenotype term.",
        explanation="Resolved from ontology lookup evidence.",
    )
    resolved_result = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [
            ValidatorResultMaterializationInput(
                match=match,
                request=selector_result.request,
                result=resolved,
            )
        ],
    )

    assert len(resolved_result.materialized_objects) == 1
    materialized_term = resolved_result.materialized_objects[0]
    assert materialized_term.object_type == PHENOTYPE_TERM_OBJECT_TYPE
    assert materialized_term.status is CuratableObjectStatus.VALIDATED
    assert materialized_term.payload == {
        "resolution_state": "resolved",
        "curie": "WBPhenotype:0000886",
        "label": "reduced brood size",
    }
    assert resolved_result.appended_findings[0].code == "domain_pack.validator_resolved"


def test_tool_verified_phenotype_envelope_omits_legacy_semantic_stores():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(fixture)

    observed_keys = set(_iter_mapping_keys(envelope.model_dump(mode="python")))
    assert LEGACY_SEMANTIC_KEYS.isdisjoint(observed_keys)


def test_pending_phenotype_validator_requires_explicit_blockers():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")
    envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(fixture)

    without_export_behavior = copy.deepcopy(envelope)
    annotation = next(
        obj
        for obj in without_export_behavior.objects
        if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    annotation.metadata["export_behavior"] = {"status": "ready"}
    findings = validate_pending_phenotype_envelope(without_export_behavior)
    assert [finding.code for finding in findings] == [
        "alliance.phenotype.export_behavior_not_blocked"
    ]

    without_subject_finding = copy.deepcopy(envelope)
    without_subject_finding.validation_findings = [
        finding
        for finding in without_subject_finding.validation_findings
        if finding.code != "alliance.phenotype.subject_resolution_required"
    ]
    findings = validate_pending_phenotype_envelope(without_subject_finding)
    assert [finding.code for finding in findings] == [
        "alliance.phenotype.subject_resolution_blocker_missing"
    ]


def test_phenotype_pack_linkml_class_slot_attribute_and_range_refs_exist(
    tmp_path: Path,
):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = _phenotype_pack().metadata

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


def test_tool_verified_phenotype_fixture_rejects_malformed_required_data():
    fixture = load_evidence_fixture("tool_verified_phenotype_paper")

    missing_extraction = copy.deepcopy(fixture)
    missing_extraction.pop("extraction")
    with pytest.raises(ValueError, match="extraction must be an object"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(missing_extraction)

    legacy_items_missing = copy.deepcopy(fixture)
    legacy_items_missing["extraction"].pop("items")
    with pytest.raises(ValueError, match="extraction.items must be a list"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            legacy_items_missing
        )

    missing_term_identity = copy.deepcopy(fixture)
    missing_term_identity["extraction"]["items"][0].pop("normalized_id")
    missing_term_identity["extraction"]["items"][0].pop("label")
    with pytest.raises(ValueError, match="extraction.items\\[\\].label"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            missing_term_identity
        )

    missing_label = copy.deepcopy(fixture)
    missing_label["extraction"]["items"][0].pop("label")
    with pytest.raises(ValueError, match="extraction.items\\[\\].label"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(missing_label)

    missing_source_mentions = copy.deepcopy(fixture)
    missing_source_mentions["extraction"]["items"][0].pop("source_mentions")
    with pytest.raises(ValueError, match="extraction.items\\[\\].source_mentions"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            missing_source_mentions
        )

    empty_source_mentions = copy.deepcopy(fixture)
    empty_source_mentions["extraction"]["items"][0]["source_mentions"] = []
    with pytest.raises(ValueError, match="source_mentions must include at least one"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            empty_source_mentions
        )

    missing_tool_evidence_id = copy.deepcopy(fixture)
    missing_tool_evidence_id["tool_cases"][0]["expected_tool_result"].pop(
        "evidence_record_id"
    )
    with pytest.raises(
        ValueError,
        match="tool_cases\\[\\].expected_tool_result.evidence_record_id",
    ):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            missing_tool_evidence_id
        )

    missing_embedded_evidence_id = copy.deepcopy(fixture)
    first_item = missing_embedded_evidence_id["extraction"]["items"][0]
    first_item.pop("evidence_case_ids")
    first_item["evidence_records"] = [
        {
            "verified_quote": (
                "daf-2(e1370) adults produced 40% fewer progeny than wild type."
            ),
            "page": 5,
            "section": "Results",
            "chunk_id": "chunk-phenotype-count",
        }
    ]
    first_item["evidence_record_ids"] = ["missing-id"]
    with pytest.raises(
        ValueError,
        match="extraction.items\\[\\].evidence_records\\[\\].evidence_record_id",
    ):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            missing_embedded_evidence_id
        )

    unknown_evidence_case = copy.deepcopy(fixture)
    unknown_evidence_case["extraction"]["items"][0]["evidence_case_ids"] = [
        "missing-case"
    ]
    with pytest.raises(ValueError, match="unknown tool case"):
        build_pending_phenotype_envelope_from_tool_verified_fixture(
            unknown_evidence_case
        )


def test_phenotype_constants_include_fixture_id_for_contract_callers():
    assert PHENOTYPE_FIXTURE_PACK_ID == "tool_verified_pending"
