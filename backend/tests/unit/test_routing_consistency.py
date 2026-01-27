"""
Test routing configuration consistency across the system.

This test validates that all routing destinations and handlers are properly
configured and synchronized across multiple files:
- Python Destination enum
- Generated JSON schemas (from Pydantic models)
- RoutingPlan execution_order descriptions
- Domain orchestrator RESOURCE_MAPs
- DESTINATION_TO_DOMAIN mappings
- Agent/Task YAML files
- Response envelope schemas

Based on AGENT_DEVELOPMENT_GUIDE.md requirements.
"""
import sys
from pathlib import Path
import pytest
from typing import Dict, Set
import yaml

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


def load_yaml_configs() -> Dict[str, Dict]:
    """Load all agent and task YAML configs."""
    configs = {"agents": {}, "tasks": {}}

    agents_dir = Path(__file__).parent.parent.parent / "src" / "crew_config" / "agents"
    tasks_dir = Path(__file__).parent.parent.parent / "src" / "crew_config" / "tasks"

    # Load all agent configs
    for yaml_file in agents_dir.glob("*.yaml"):
        with open(yaml_file) as f:
            content = yaml.safe_load(f)
            if content:
                configs["agents"].update(content)

    # Load all task configs
    for yaml_file in tasks_dir.glob("*.yaml"):
        with open(yaml_file) as f:
            content = yaml.safe_load(f)
            if content:
                configs["tasks"].update(content)

    return configs


def get_resource_map_entries() -> Dict[str, Set[str]]:
    """Extract RESOURCE_MAP entries from domain orchestrators."""
    maps = {}

    # Internal DB orchestrator
    internal_db_file = Path(__file__).parent.parent.parent / "src" / "lib" / "chat" / "flows" / "domain_orchestrators" / "internal_db_orchestrator.py"
    if internal_db_file.exists():
        with open(internal_db_file) as f:
            content = f.read()
            # Extract RESOURCE_MAP keys - match until outer closing brace at 4-space indentation
            import re
            matches = re.findall(r'RESOURCE_MAP.*?=\s*{(.*?)\n    }', content, re.DOTALL)
            if matches:
                keys = re.findall(r'"([^"]+)":\s*{', matches[0])
                maps["internal_db"] = set(keys)

    # External API orchestrator
    external_api_file = Path(__file__).parent.parent.parent / "src" / "lib" / "chat" / "flows" / "domain_orchestrators" / "external_api_orchestrator.py"
    if external_api_file.exists():
        with open(external_api_file) as f:
            content = f.read()
            import re
            matches = re.findall(r'RESOURCE_MAP.*?=\s*{(.*?)\n    }', content, re.DOTALL)
            if matches:
                keys = re.findall(r'"([^"]+)":\s*{', matches[0])
                maps["external_api"] = set(keys)

    return maps


def get_destination_to_domain_mapping():
    """Extract DESTINATION_TO_DOMAIN mapping from supervisor_flow.py."""
    from pathlib import Path
    supervisor_file = Path(__file__).parent.parent.parent / "src" / "lib" / "chat" / "flows" / "supervisor_flow.py"
    mapping = {}

    if supervisor_file.exists():
        with open(supervisor_file) as f:
            content = f.read()
            import re

            # Extract the DESTINATION_TO_DOMAIN dictionary
            matches = re.findall(r'DESTINATION_TO_DOMAIN\s*=\s*\{(.*?)\n    \}', content, re.DOTALL)
            if matches:
                # Extract each line with a mapping
                lines = matches[0].strip().split('\n')
                for line in lines:
                    # Match Destination.ENUM_NAME.value: "domain"
                    match = re.search(r'Destination\.([A-Z_]+)\.value:\s*"([^"]+)"', line)
                    if match:
                        enum_name = match.group(1).lower()
                        domain = match.group(2)
                        mapping[enum_name] = domain
                    else:
                        # Try plain string pattern: "key": "domain"
                        match = re.search(r'"([^"]+)":\s*"([^"]+)"', line)
                        if match:
                            key = match.group(1)
                            domain = match.group(2)
                            mapping[key] = domain

    return mapping




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

    def test_resource_maps_have_matching_yaml_files(self):
        """Verify each RESOURCE_MAP entry has corresponding agent and task YAML files."""
        resource_maps = get_resource_map_entries()
        configs = load_yaml_configs()

        for orchestrator, destinations in resource_maps.items():
            for dest in destinations:
                agent_key = f"{dest}_agent"
                task_key = f"{dest}_task"

                assert agent_key in configs["agents"], (
                    f"RESOURCE_MAP entry '{dest}' in {orchestrator} orchestrator "
                    f"requires {agent_key}.yaml in crew_config/agents/"
                )

                assert task_key in configs["tasks"], (
                    f"RESOURCE_MAP entry '{dest}' in {orchestrator} orchestrator "
                    f"requires {task_key}.yaml in crew_config/tasks/"
                )

    def test_destinations_have_domain_mappings(self):
        """Verify each Destination has a mapping in DESTINATION_TO_DOMAIN."""
        # Skip special destinations that don't need domain mappings
        skip_destinations = {
            "direct_response",
            "immediate_response",
            "no_document_response",
        }

        python_destinations = {d.value for d in Destination if d.value not in skip_destinations}
        domain_mappings = get_destination_to_domain_mapping()

        mapped_destinations = set(domain_mappings.keys())

        # Every non-special destination should have a domain mapping
        unmapped = python_destinations - mapped_destinations

        assert not unmapped, (
            f"These destinations are missing from DESTINATION_TO_DOMAIN in supervisor_flow.py:\n"
            f"{unmapped}\n"
            f"Add them to the DESTINATION_TO_DOMAIN dictionary."
        )

    def test_domain_mapped_destinations_exist_in_resource_maps(self):
        """Verify destinations mapped to domains actually exist in those domain's RESOURCE_MAPs."""
        domain_mappings = get_destination_to_domain_mapping()
        resource_maps = get_resource_map_entries()

        # Map domain names to resource map keys
        domain_to_map = {
            "internal_db_domain": "internal_db",
            "external_api_domain": "external_api",
        }

        for destination, domain in domain_mappings.items():
            # Skip PDF domain (handled differently)
            if domain == "pdf_domain":
                continue

            map_key = domain_to_map.get(domain)
            if map_key:
                assert map_key in resource_maps, (
                    f"Domain '{domain}' referenced in DESTINATION_TO_DOMAIN "
                    f"but no RESOURCE_MAP found for it"
                )

                assert destination in resource_maps[map_key], (
                    f"Destination '{destination}' is mapped to '{domain}' "
                    f"but doesn't exist in {map_key} RESOURCE_MAP.\n"
                    f"Add it to the RESOURCE_MAP in the {domain} orchestrator."
                )

    def test_yaml_agent_task_pairs_match(self):
        """Verify every agent has a matching task and vice versa."""
        configs = load_yaml_configs()

        agents = set(configs["agents"].keys())
        tasks = set(configs["tasks"].keys())

        # Remove "_agent" and "_task" suffixes for comparison
        agent_bases = {a.replace("_agent", "") for a in agents if a.endswith("_agent")}
        task_bases = {t.replace("_task", "") for t in tasks if t.endswith("_task")}

        # Skip special cases (tasks that reuse existing agents)
        agent_bases.discard("supervisor")
        task_bases.discard("supervisor")
        task_bases.discard("supervisor_routing")
        task_bases.discard("supervisor_synthesis")
        task_bases.discard("final_coordination")  # Uses supervisor_agent
        task_bases.discard("pdf_processing")      # Uses pdf_specialist_agent

        agents_without_tasks = agent_bases - task_bases
        tasks_without_agents = task_bases - agent_bases

        assert not agents_without_tasks, (
            f"These agents don't have matching tasks:\n"
            f"{agents_without_tasks}\n"
            f"Create matching task YAML files in crew_config/tasks/"
        )

        assert not tasks_without_agents, (
            f"These tasks don't have matching agents:\n"
            f"{tasks_without_agents}\n"
            f"Create matching agent YAML files in crew_config/agents/"
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
