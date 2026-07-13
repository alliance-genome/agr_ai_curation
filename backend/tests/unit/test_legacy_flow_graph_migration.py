"""Regression tests for the v0.8.12 legacy-flow data migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c5d6e7f8a9b0_upgrade_legacy_flow_graphs.py"
)
_SPEC = importlib.util.spec_from_file_location("legacy_flow_graph_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _node(node_id: str, agent_id: str, *, node_type: str = "agent") -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 100, "y": 100},
        "data": {
            "agent_id": agent_id,
            "agent_display_name": agent_id,
            "output_key": f"{node_id}_output",
            "custom_instructions": "preserve me",
        },
    }


def _edge(edge_id: str, source: str, target: str) -> dict:
    return {"id": edge_id, "source": source, "target": target}


def _legacy_flow(*agent_ids: str, include_task: bool = True) -> dict:
    nodes = []
    if include_task:
        task = _node("task", "task_input", node_type="task_input")
        task["data"]["task_instructions"] = "Do the work"
        nodes.append(task)
    nodes.extend(_node(f"node_{index}", agent_id) for index, agent_id in enumerate(agent_ids))
    edges = [
        _edge(f"edge_{index}", nodes[index]["id"], nodes[index + 1]["id"])
        for index in range(len(nodes) - 1)
    ]
    return {
        "version": "1.0",
        "nodes": nodes,
        "edges": edges,
        "entry_node_id": nodes[0]["id"],
        "unknown_root_field": {"preserved": True},
    }


def test_upgrades_terminal_formatter_with_one_visible_extractor():
    original = _legacy_flow("pdf_extraction", "gene", "chat_output")

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Gene flow",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert reasons == ("task_present", "formatter_upgraded")
    assert upgraded["version"] == "1.1"
    assert upgraded["nodes"][-1]["type"] == "output"
    assert upgraded["nodes"][-1]["data"]["custom_instructions"] == "preserve me"
    assert upgraded["edges"][-1] == {
        "id": "edge_2",
        "source": "node_0",
        "target": "node_2",
        "role": "output_attachment",
    }
    assert upgraded["unknown_root_field"] == {"preserved": True}
    assert original["version"] == "1.0"


def test_multiple_or_missing_extractors_remain_legacy_compatible():
    multiple = _legacy_flow("pdf_extraction", "allele_extractor", "chat_output")
    missing = _legacy_flow("gene", "chat_output")

    multiple_result, multiple_reasons = migration._classify_and_upgrade(
        multiple,
        flow_name="Multiple",
        extraction_agent_ids={"pdf_extraction", "allele_extractor"},
    )
    missing_result, missing_reasons = migration._classify_and_upgrade(
        missing,
        flow_name="Missing",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert multiple_result == multiple
    assert multiple_reasons[-1] == "formatter_skipped_multiple_extractions"
    assert missing_result == missing
    assert missing_reasons[-1] == "formatter_skipped_no_visible_extraction"


def test_adds_task_input_then_upgrades_formatter_without_losing_fields():
    original = _legacy_flow(
        "pdf_extraction",
        "allele",
        "csv_formatter",
        include_task=False,
    )

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Allele ID Extraction (CSV)",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert reasons == ("task_added", "formatter_upgraded")
    task = upgraded["nodes"][0]
    assert task["type"] == "task_input"
    assert task["data"]["task_instructions"] == (
        "Execute the 'Allele ID Extraction (CSV)' curation workflow."
    )
    assert upgraded["task_instructions_default_only"] is True
    assert upgraded["entry_node_id"] == task["id"]
    assert upgraded["edges"][0]["source"] == task["id"]
    assert upgraded["edges"][0]["target"] == "node_0"
    assert upgraded["edges"][-1]["source"] == "node_0"
    assert upgraded["version"] == "1.1"


def test_task_repair_replaces_stale_declared_entry_with_unique_graph_entry():
    original = _legacy_flow("pdf_extraction", include_task=False)
    original["entry_node_id"] = "stale-entry"

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Archived test",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert reasons == ("task_added", "no_supported_formatter")
    assert upgraded["nodes"][0]["type"] == "task_input"
    assert upgraded["entry_node_id"] == upgraded["nodes"][0]["id"]
    assert upgraded["edges"][0]["target"] == "node_0"


def test_ambiguous_formatter_chain_is_not_typed():
    original = _legacy_flow("pdf_extraction", "chat_output", "tsv_formatter")

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Two outputs",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert upgraded == original
    assert reasons[-1] == "formatter_skipped_ambiguous_topology"


def test_existing_v1_1_definition_is_idempotent():
    original = _legacy_flow("pdf_extraction", "chat_output")
    original["version"] = "1.1"
    original["nodes"][-1]["type"] = "output"
    original["edges"][-1]["role"] = "output_attachment"

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Current",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert upgraded == original
    assert reasons == ("unsupported_or_current_version_1.1",)


def test_future_version_is_never_modified():
    original = _legacy_flow("pdf_extraction", "chat_output", include_task=False)
    original["version"] = "2.0"

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Future",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert upgraded == original
    assert reasons == ("unsupported_or_current_version_2.0",)


def test_invalid_existing_task_input_blocks_formatter_upgrade():
    original = _legacy_flow("pdf_extraction", "chat_output")
    original["nodes"][0]["data"]["task_instructions"] = ""

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Invalid task",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert upgraded == original
    assert reasons == ("task_present", "formatter_skipped_invalid_task_instructions")


def test_preexisting_unrelated_output_node_blocks_version_promotion():
    original = _legacy_flow("pdf_extraction", "gene", "chat_output")
    original["nodes"][2]["type"] = "output"

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Unsafe output",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert upgraded == original
    assert reasons[-1] == "formatter_skipped_preexisting_output_node"


def test_malformed_node_is_skipped_without_aborting_migration():
    original = _legacy_flow("pdf_extraction", include_task=False)
    original["nodes"].append("not-a-node")

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Malformed",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert upgraded == original
    assert reasons == ("task_skipped_malformed_node",)


def test_task_repair_uses_safe_position_fallbacks():
    original = _legacy_flow("pdf_extraction", include_task=False)
    original["nodes"][0]["position"] = {"x": "left", "y": None}

    upgraded, reasons = migration._classify_and_upgrade(
        original,
        flow_name="Odd position",
        extraction_agent_ids={"pdf_extraction"},
    )

    assert reasons == ("task_added", "no_supported_formatter")
    assert upgraded["nodes"][0]["position"] == {"x": -250, "y": 0}
