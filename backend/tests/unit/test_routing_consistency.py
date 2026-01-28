"""
Test routing configuration consistency across the system.

This test validates that routing destinations and handlers are properly
configured and synchronized:
- Python Destination enum
- Generated JSON schemas (from Pydantic models)
- RoutingPlan execution_order descriptions
- Response envelope schemas

Based on AGENT_DEVELOPMENT_GUIDE.md requirements.
"""
import sys
from pathlib import Path
import pytest

# Add backend directory to path so we can import from src.schemas.models
_backend_path = Path(__file__).parent.parent.parent
if str(_backend_path) not in sys.path:
    sys.path.insert(0, str(_backend_path))

from src.schemas.models import Destination, RoutingPlan, SCHEMA_REGISTRY


def get_generated_schema(schema_name: str) -> dict:
    """Generate JSON schema from Pydantic model."""
    model_class = SCHEMA_REGISTRY.get(schema_name)
    if not model_class:
        raise ValueError(f"Schema '{schema_name}' not found in SCHEMA_REGISTRY")
    return model_class.model_json_schema()


class TestRoutingConsistency:
    """Test that all routing configuration is consistent."""

    def test_destination_enum_matches_generated_schemas(self):
        """Verify Destination enum matches generated JSON schema enums."""
        # Get Python enum values
        python_destinations = {d.value for d in Destination}

        # Get generated JSON schema enums
        supervisor_schema = get_generated_schema("supervisor")

        # Extract Destination enum from $defs
        schema_destinations = set(supervisor_schema["$defs"]["Destination"]["enum"])

        # All should match
        assert python_destinations == schema_destinations, (
            f"Python Destination enum doesn't match generated supervisor schema!\n"
            f"Only in Python: {python_destinations - schema_destinations}\n"
            f"Only in schema: {schema_destinations - python_destinations}\n"
            f"\nNote: Schemas are now generated from Python models, so this test verifies\n"
            f"that the Pydantic model's JSON schema generation is working correctly."
        )

    def test_execution_order_descriptions_match(self):
        """Verify execution_order field description is included in generated schema."""
        # Get description from Python model
        python_desc = RoutingPlan.model_fields['execution_order'].description

        # Get generated schema
        supervisor_schema = get_generated_schema("supervisor")

        # Extract description from generated schema
        schema_desc = supervisor_schema["$defs"]["RoutingPlan"]["properties"]["execution_order"]["description"]

        # They should match exactly (schemas generated from Python models)
        assert python_desc == schema_desc, (
            f"Python RoutingPlan execution_order description doesn't match generated schema!\n"
            f"Python: {python_desc}\n"
            f"Schema: {schema_desc}\n"
            f"\nNote: Since schemas are generated from Python models, these should always match.\n"
            f"This test verifies Pydantic's field description propagation."
        )

    def test_response_envelope_schemas_exist(self):
        """Verify each destination has a corresponding envelope schema in SCHEMA_REGISTRY."""
        # Get all registered envelope schemas from SCHEMA_REGISTRY
        envelope_schemas = {}
        for schema_name, model_class in SCHEMA_REGISTRY.items():
            class_name = model_class.__name__
            if class_name.endswith("Envelope") and class_name != "StructuredMessageEnvelope":
                # schema_name is already in snake_case (e.g., 'disease_ontology')
                envelope_schemas[schema_name] = class_name

        # Get destinations that need envelopes (skip special ones)
        skip_destinations = {
            "direct_response",  # Has DirectResponseEnvelope
            "immediate_response",  # Handled inline by supervisor, no envelope needed
            "no_document_response",  # Has NoDocumentEnvelope (name differs)
            "synthesize",  # Has SynthesisEnvelope
            "pdf_and_disease",  # Combined handler
        }

        # Map special naming cases
        name_mappings = {
            "no_document_response": "no_document",  # NoDocumentEnvelope
        }

        destinations_needing_envelopes = {
            d.value for d in Destination
            if d.value not in skip_destinations
        }

        # Check each destination has an envelope
        missing_envelopes = []
        for dest in destinations_needing_envelopes:
            # Check if destination or its mapped name exists
            mapped_name = name_mappings.get(dest, dest)
            if mapped_name not in envelope_schemas:
                missing_envelopes.append(dest)

        assert not missing_envelopes, (
            f"These destinations don't have envelope schemas in SCHEMA_REGISTRY:\n"
            f"{missing_envelopes}\n"
            f"Available envelopes: {sorted(envelope_schemas.keys())}\n"
            f"Create missing envelope schema files in backend/src/schemas/models/ and register in SCHEMA_REGISTRY"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
