"""Tests for custom tool label extraction in streaming runner."""

from types import SimpleNamespace

from src.lib.openai_agents.runner import _build_custom_tool_display_names


def test_build_custom_tool_display_names_maps_custom_tool_from_description():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="ask_ca_7fffddac_c7ad_4ee3_b641_97b6c652fc5b_specialist",
                description="Ask the Gene Validation Agent (Custom)",
            ),
        ]
    )

    labels = _build_custom_tool_display_names(agent)
    assert labels == {
        "ask_ca_7fffddac_c7ad_4ee3_b641_97b6c652fc5b_specialist": "Gene Validation Agent (Custom)"
    }


def test_build_custom_tool_display_names_ignores_non_custom_tools():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(name="ask_pdf_specialist", description="Ask the PDF Specialist"),
            SimpleNamespace(name="search_document", description="Search document"),
        ]
    )

    labels = _build_custom_tool_display_names(agent)
    assert labels == {}

