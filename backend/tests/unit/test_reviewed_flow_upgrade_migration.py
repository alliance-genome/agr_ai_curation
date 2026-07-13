"""Regression tests for the semantically reviewed saved-flow upgrades."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d6e7f8a9b0c1_upgrade_semantically_reviewed_flows.py"
)
_SPEC = importlib.util.spec_from_file_location("reviewed_flow_upgrade", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _node(node_id: str, agent_id: str, *, node_type: str = "agent") -> dict:
    data = {
        "agent_id": agent_id,
        "agent_display_name": agent_id,
        "output_key": f"{node_id}_output",
    }
    if node_type == "task_input":
        data["task_instructions"] = "Run the saved workflow"
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": data,
    }


def _flow(*agent_ids: str) -> dict:
    nodes = [_node("task", "task_input", node_type="task_input")]
    nodes.extend(_node(f"node_{index}", agent_id) for index, agent_id in enumerate(agent_ids))
    return {
        "version": "1.0",
        "entry_node_id": "task",
        "nodes": nodes,
        "edges": [
            {
                "id": f"edge_{index}",
                "source": nodes[index]["id"],
                "target": nodes[index + 1]["id"],
            }
            for index in range(len(nodes) - 1)
        ],
    }


def test_upgrades_reviewed_allele_flow_and_removes_redundant_validator():
    original = _flow("pdf_extraction", "allele_extractor", "allele", "tsv_formatter")

    upgraded, reason = migration._upgrade_reviewed_definition(
        "161e8637-6fec-4bcb-82f1-a34e8a17962b",
        original,
        artifact_source_agent_ids={"pdf_extraction", "allele_extractor"},
    )

    assert reason == "reviewed_formatter_upgraded"
    assert upgraded["version"] == "1.1"
    assert [node["data"]["agent_id"] for node in upgraded["nodes"]] == [
        "task_input",
        "pdf_extraction",
        "allele_extractor",
        "tsv_formatter",
    ]
    assert upgraded["nodes"][-1]["type"] == "output"
    assert [(edge["source"], edge["target"], edge.get("role")) for edge in upgraded["edges"]] == [
        ("task", "node_0", None),
        ("node_0", "node_1", None),
        ("node_1", "node_3", "output_attachment"),
    ]
    assert original["version"] == "1.0"


def test_upgrades_reviewed_aggregate_flow_with_ordered_sources():
    original = _flow("gene_expression", "disease_extractor", "chat_output")

    upgraded, reason = migration._upgrade_reviewed_definition(
        "38b682c7-6c7f-4624-a21b-b8314416cf9f",
        original,
        artifact_source_agent_ids={"gene_expression", "disease_extractor"},
    )

    assert reason == "reviewed_formatter_upgraded"
    attachments = [
        edge for edge in upgraded["edges"] if edge.get("role") == "output_attachment"
    ]
    assert [(edge["source"], edge["target"]) for edge in attachments] == [
        ("node_0", "node_2"),
        ("node_1", "node_2"),
    ]
    assert len({edge["id"] for edge in upgraded["edges"]}) == len(upgraded["edges"])


def test_promotes_valid_formatter_free_flow_to_current_schema():
    original = _flow("gene_extractor", "curation_prep")

    upgraded, reason = migration._upgrade_reviewed_definition(
        "not-production-specific",
        original,
        artifact_source_agent_ids={"gene_extractor"},
    )

    assert reason == "schema_promoted_no_formatter"
    assert upgraded["version"] == "1.1"
    assert upgraded["nodes"] == original["nodes"]
    assert upgraded["edges"] == original["edges"]


def test_non_allowlisted_formatter_flow_remains_for_manual_review():
    original = _flow("gene_extractor", "chat_output")

    upgraded, reason = migration._upgrade_reviewed_definition(
        "00000000-0000-0000-0000-000000000000",
        original,
        artifact_source_agent_ids={"gene_extractor"},
    )

    assert reason == "manual_review_required"
    assert upgraded == original


def test_allowlisted_flow_fails_closed_when_reviewed_source_is_unavailable():
    original = _flow("gene_expression", "disease_extractor", "chat_output")

    upgraded, reason = migration._upgrade_reviewed_definition(
        "38b682c7-6c7f-4624-a21b-b8314416cf9f",
        original,
        artifact_source_agent_ids={"gene_expression"},
    )

    assert reason == "reviewed_source_unavailable"
    assert upgraded == original


def test_current_definition_is_idempotent():
    original = _flow("gene_extractor")
    original["version"] = "1.1"

    upgraded, reason = migration._upgrade_reviewed_definition(
        "anything",
        original,
        artifact_source_agent_ids={"gene_extractor"},
    )

    assert reason == "already_current"
    assert upgraded == original


def test_plain_custom_flow_removes_redundant_chat_formatter():
    original = _flow(
        "ca_c2bca435-1396-47c8-9b3f-b774998c6c74",
        "chat_output",
    )

    upgraded, reason = migration._upgrade_reviewed_definition(
        "558b5fb2-5703-47bb-886e-044d8594e30d",
        original,
        artifact_source_agent_ids=set(),
    )

    assert reason == "plain_final_promoted"
    assert upgraded["version"] == "1.1"
    assert [node["data"]["agent_id"] for node in upgraded["nodes"]] == [
        "task_input",
        "ca_c2bca435-1396-47c8-9b3f-b774998c6c74",
    ]
    assert len(upgraded["edges"]) == 1
    assert upgraded["migration_note"] == "removed_redundant_plain_text_chat_formatter"


def test_validation_result_can_be_reviewed_formatter_source():
    original = _flow("allele", "csv_formatter")

    upgraded, reason = migration._upgrade_reviewed_definition(
        "976491a0-54d9-4c20-8dcb-e0f9f4a010f5",
        original,
        artifact_source_agent_ids={"allele"},
    )

    assert reason == "reviewed_formatter_upgraded"
    assert upgraded["edges"][-1]["role"] == "output_attachment"
    assert upgraded["edges"][-1]["source"] == "node_0"
    assert upgraded["nodes"][-1]["type"] == "output"


def test_go_formatter_suffix_becomes_two_outputs_from_same_typed_sources():
    original = _flow(
        "pdf_extraction",
        "gene_ontology",
        "chat_output",
        "tsv_formatter",
    )

    upgraded, reason = migration._upgrade_reviewed_definition(
        "303e38a2-3fab-4410-bca4-5384cb699d21",
        original,
        artifact_source_agent_ids={"pdf_extraction", "gene_ontology"},
    )

    assert reason == "go_formatter_suffix_upgraded"
    assert upgraded["version"] == "1.1"
    assert [node["type"] for node in upgraded["nodes"][-2:]] == ["output", "output"]
    control_edges = [edge for edge in upgraded["edges"] if edge.get("role") is None]
    assert [(edge["source"], edge["target"]) for edge in control_edges] == [
        ("task", "node_0"),
        ("node_0", "node_1"),
    ]
    attachments = [
        (edge["source"], edge["target"])
        for edge in upgraded["edges"]
        if edge.get("role") == "output_attachment"
    ]
    assert attachments == [
        ("node_0", "node_2"),
        ("node_0", "node_3"),
        ("node_1", "node_2"),
        ("node_1", "node_3"),
    ]


def test_archive_preserves_task_and_records_reason_in_current_schema():
    original = _flow("chemical_extractor", "chemical", "csv_formatter")

    archived = migration._archived_definition(
        original,
        reason="chemical_extractor_removed",
    )

    assert archived is not None
    assert archived["version"] == "1.1"
    assert [node["data"]["agent_id"] for node in archived["nodes"]] == ["task_input"]
    assert archived["edges"] == []
    assert archived["archived_reason"] == "chemical_extractor_removed"
    assert archived["archived_by_migration"] == "d6e7f8a9b0c1"
