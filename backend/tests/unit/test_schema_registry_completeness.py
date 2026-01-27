"""
Test to ensure SCHEMA_REGISTRY is complete and properly maintained.

This test prevents the production bug that occurred when a schema file existed
but wasn't registered in the schema loader, causing runtime errors.
"""

import sys
from pathlib import Path
import pytest

# Add backend directory to path so we can import from src.schemas.models
# Note: We need the parent of 'src' in sys.path to import 'src.schemas.models'
_backend_path = Path(__file__).parent.parent.parent
if str(_backend_path) not in sys.path:
    sys.path.insert(0, str(_backend_path))


def test_all_schema_files_have_registry_entries():
    """Ensure every schema file in src/schemas/models/ is registered in SCHEMA_REGISTRY.

    This test will fail if:
    1. A new schema file is created but not registered in SCHEMA_REGISTRY
    2. A registry entry exists but the schema file was deleted

    This prevents the silent failure that caused the production bug where
    pdf_extraction_plan.json was missing and the fallback loaded the wrong schema.
    """
    from src.schemas.models import SCHEMA_REGISTRY

    # Navigate from backend/tests/unit/ to backend/src/schemas/models/
    models_dir = Path(__file__).resolve().parents[2] / "src" / "schemas" / "models"

    # Get all Python files in models directory
    all_files = [f.stem for f in models_dir.glob("*.py")]

    # Filter out special files (base, __init__) and helper classes
    # Helper classes are components of schemas, not schemas themselves
    excluded_stems = {
        "__init__",  # Registry definition file
        "base",  # Contains shared base types (Destination, RoutingPlan, etc.)
        "reagent",  # Helper class used within gene_curation
        "expression_pattern",  # Helper class used within gene_expression
        "expression_evidence",  # Helper class used within gene_expression
        "ontology_mapping_item",  # Helper class used within ontology_mapping
    }

    # Schema files are all remaining files
    schema_file_stems = set(all_files) - excluded_stems

    # Registry keys should match schema file stems exactly
    registry_keys = set(SCHEMA_REGISTRY.keys())

    print(f"\nSchema files found: {len(schema_file_stems)}")
    print(f"Registry entries: {len(registry_keys)}")
    print(f"\nSchema files: {sorted(schema_file_stems)}")
    print(f"\nRegistry keys: {sorted(registry_keys)}")

    # Check for schema files without registry entries
    missing_from_registry = schema_file_stems - registry_keys
    if missing_from_registry:
        pytest.fail(
            f"Schema files exist but are NOT registered in SCHEMA_REGISTRY: {sorted(missing_from_registry)}\n"
            f"Add these to SCHEMA_REGISTRY in backend/src/schemas/models/__init__.py"
        )

    # Check for registry entries without schema files
    missing_schema_files = registry_keys - schema_file_stems
    if missing_schema_files:
        pytest.fail(
            f"Registry entries exist but schema files are MISSING: {sorted(missing_schema_files)}\n"
            f"Either create the missing files or remove from SCHEMA_REGISTRY"
        )

    # Both sets should be identical
    assert schema_file_stems == registry_keys, (
        f"Mismatch between schema files and registry entries.\n"
        f"Files only: {sorted(schema_file_stems - registry_keys)}\n"
        f"Registry only: {sorted(registry_keys - schema_file_stems)}"
    )

    print(f"\n✓ All {len(registry_keys)} schema files properly registered")


def test_registry_models_are_valid():
    """Ensure all registered models can generate valid JSON schemas."""
    from src.schemas.models import SCHEMA_REGISTRY

    for schema_name, model_class in SCHEMA_REGISTRY.items():
        # Try to generate schema - this will fail if model is invalid
        try:
            schema = model_class.model_json_schema()
            assert isinstance(schema, dict), f"Schema for {schema_name} is not a dict"
            assert 'properties' in schema or 'type' in schema, (
                f"Schema for {schema_name} has no 'properties' or 'type' field"
            )
        except Exception as e:
            pytest.fail(f"Failed to generate schema for {schema_name}: {e}")

    print(f"\n✓ All {len(SCHEMA_REGISTRY)} schemas generate valid JSON")


def test_no_helper_classes_in_registry():
    """Ensure helper classes are NOT registered in SCHEMA_REGISTRY.

    Helper classes (Reagent, ExpressionPattern, etc.) should not be in the registry
    because they are components of schemas, not schemas themselves.
    """
    from src.schemas.models import SCHEMA_REGISTRY

    # These should NOT be in the registry
    forbidden_names = [
        'reagent',
        'expression_pattern',
        'expression_evidence',
        'ontology_mapping_item',
        'structured_message_envelope',  # base class
        'destination',  # enum
        'routing_plan',  # helper for supervisor
    ]

    for name in forbidden_names:
        assert name not in SCHEMA_REGISTRY, (
            f"Helper class '{name}' should NOT be in SCHEMA_REGISTRY"
        )

    print(f"\n✓ No helper classes in registry")
