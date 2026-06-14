"""Guardrails for agent output-schema versus builder-finalize-tool configuration."""

import pytest

from src.lib.agent_studio.agent_finalize_invariant import (
    validate_agent_finalize_tool_invariant,
)
from src.lib.config.agent_loader import (
    canonical_system_agent_key,
    load_agent_definitions,
    reset_cache,
)


@pytest.fixture(autouse=True)
def reset_agent_loader_cache():
    reset_cache()
    yield
    reset_cache()


def _violation_messages(**kwargs) -> list[str]:
    return [
        violation.message
        for violation in validate_agent_finalize_tool_invariant(**kwargs)
    ]


def test_extractor_with_output_schema_is_rejected():
    messages = _violation_messages(
        agent_key="stale_gene_expression",
        category="Extraction",
        output_schema_key="GeneExpressionEnvelope",
        tool_ids=["stage_gene_expression_observation", "finalize_gene_expression_extraction"],
    )

    assert any(
        "stale_gene_expression: builder/extractor agent declares output_schema "
        "'GeneExpressionEnvelope'" in message
        for message in messages
    )


def test_agent_with_output_schema_and_finalize_tool_is_rejected():
    messages = _violation_messages(
        agent_key="hybrid_validator",
        category="Validation",
        output_schema_key="GeneResultEnvelope",
        tool_ids=["search_genes", "finalize_gene_extraction"],
    )

    assert any(
        "hybrid_validator: declares both output_schema 'GeneResultEnvelope' "
        "and builder finalize tool(s): finalize_gene_extraction" in message
        for message in messages
    )


def test_extractor_missing_finalize_tool_is_rejected():
    messages = _violation_messages(
        agent_key="doug_shape",
        category="Extraction",
        output_schema_key=None,
        tool_ids=["search_document", "read_section", "stage_gene_expression_observation"],
    )

    assert any(
        "doug_shape: extractor agent is missing a builder finalize tool" in message
        for message in messages
    )
    assert any("registry-derived builder finalization tools" in message for message in messages)


def test_validation_agent_with_output_schema_and_no_finalize_tool_is_allowed():
    assert _violation_messages(
        agent_key="gene",
        category="Validation",
        output_schema_key="GeneResultEnvelope",
        tool_ids=["search_genes"],
    ) == []


def test_output_agent_without_schema_or_finalize_tool_is_allowed():
    assert _violation_messages(
        agent_key="chat_output",
        category="Output",
        output_schema_key=None,
        tool_ids=[],
    ) == []


def test_shipped_active_agent_fleet_satisfies_output_finalize_invariant():
    agent_definitions = load_agent_definitions(force_reload=True)
    violations = []

    for agent in sorted(agent_definitions.values(), key=canonical_system_agent_key):
        violations.extend(
            validate_agent_finalize_tool_invariant(
                agent_key=canonical_system_agent_key(agent),
                category=agent.category,
                output_schema_key=agent.output_schema,
                tool_ids=agent.tools,
            )
        )

    assert violations == [], "\n".join(violation.message for violation in violations)
