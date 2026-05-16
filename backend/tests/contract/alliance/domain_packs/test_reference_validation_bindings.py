"""Contract tests for under-development reference validation bindings."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry  # noqa: E402
from src.lib.domain_packs.validation_registry import (  # noqa: E402
    DomainPackValidationRegistry,
    ValidationBindingState,
)


REFERENCE_BINDING_CASES = {
    "agr.alliance.allele": {
        "binding_id": "source_reference_validation",
        "object_types": ("Reference",),
        "field_paths": ("reference_id", "curie", "title"),
        "expected_result_fields": {
            "reference_id": "Reference.reference_id",
            "curie": "Reference.curie",
            "title": "Reference.title",
        },
    },
    "agr.alliance.chemical_condition": {
        "binding_id": "source_reference_validation",
        "object_types": ("Reference",),
        "field_paths": ("reference_id", "curie", "title"),
        "expected_result_fields": {
            "reference_id": "Reference.reference_id",
            "curie": "Reference.curie",
            "title": "Reference.title",
        },
    },
    "agr.alliance.disease": {
        "binding_id": "disease_reference_materialization",
        "object_types": ("DiseaseAnnotation",),
        "field_paths": (
            "single_reference.reference_id",
            "single_reference.curie",
            "single_reference.title",
        ),
        "expected_result_fields": {
            "reference_id": "single_reference.reference_id",
            "curie": "single_reference.curie",
            "title": "single_reference.title",
        },
    },
    "agr.alliance.phenotype": {
        "binding_id": "phenotype_reference_validator",
        "object_types": ("Reference",),
        "field_paths": ("reference_id", "curie", "title"),
        "expected_result_fields": {
            "reference_id": "Reference.reference_id",
            "curie": "Reference.curie",
            "title": "Reference.title",
        },
    },
    "agr.alliance.gene_expression": {
        "binding_id": "source_reference_validation",
        "object_types": ("GeneExpressionAnnotation",),
        "field_paths": (
            "single_reference.reference_id",
            "single_reference.curie",
            "single_reference.title",
        ),
        "expected_result_fields": {
            "reference_id": "single_reference.reference_id",
            "curie": "single_reference.curie",
            "title": "single_reference.title",
        },
    },
}


def test_reference_validation_bindings_remain_under_development_metadata_only():
    alliance_registry = load_alliance_domain_pack_registry()

    for pack_id, expected in REFERENCE_BINDING_CASES.items():
        pack = alliance_registry.get_pack(pack_id)
        registry = DomainPackValidationRegistry.from_domain_pack(pack)
        bindings = {binding.binding_id: binding for binding in registry.bindings}
        binding = bindings[expected["binding_id"]]

        assert binding.state is ValidationBindingState.UNDER_DEVELOPMENT
        assert binding.validator_agent is not None
        assert binding.validator_agent.package_id == "agr.alliance"
        assert binding.validator_agent.agent_id == "reference_validation"
        assert binding.required is False
        assert binding.blocking is False
        assert binding.allow_opt_out is False
        assert binding.object_types == expected["object_types"]
        assert binding.field_paths == expected["field_paths"]
        assert binding.expected_result_fields == expected["expected_result_fields"]
        assert binding.reason

        active_reference_bindings = [
            item
            for item in registry.bindings
            if item.state is ValidationBindingState.ACTIVE
            and item.validator_agent is not None
            and item.validator_agent.agent_id == "reference_validation"
        ]
        assert active_reference_bindings == []


def test_reference_validation_bindings_select_optional_lookup_context():
    alliance_registry = load_alliance_domain_pack_registry()

    for pack_id, expected in REFERENCE_BINDING_CASES.items():
        pack = alliance_registry.get_pack(pack_id)
        registry = DomainPackValidationRegistry.from_domain_pack(pack)
        binding = {
            item.binding_id: item
            for item in registry.bindings
        }[expected["binding_id"]]

        assert set(binding.input_fields) == {
            "reference_id",
            "curie",
            "pmid",
            "doi",
            "title",
            "source_document_id",
        }
        assert all(not selector.required for selector in binding.input_fields.values())
        assert binding.input_fields["source_document_id"].source == "envelope_metadata"
        assert binding.input_fields["source_document_id"].path == "document_id"
