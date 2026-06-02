"""Tips are part of the agent documentation contract and survive conversion."""
from src.lib.agent_studio.models import AgentDocumentation
from src.lib.agent_studio.catalog_service import _convert_documentation


def test_agent_documentation_has_tips_field_defaulting_empty():
    doc = AgentDocumentation(summary="s")
    assert doc.tips == []


def test_convert_documentation_maps_tips():
    doc_dict = {
        "summary": "Validates genes.",
        "capabilities": [{"name": "Gene lookup", "description": "Find genes"}],
        "tips": ["Include the species when possible"],
    }
    converted = _convert_documentation(doc_dict)
    assert converted is not None
    assert converted.tips == ["Include the species when possible"]


def test_convert_documentation_tips_defaults_empty_when_absent():
    converted = _convert_documentation({"summary": "Validates genes."})
    assert converted is not None
    assert converted.tips == []
