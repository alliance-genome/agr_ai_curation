"""Upgrade semantically reviewed legacy saved flows.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-13 23:00:00.000000

The broad v0.8.12 migration deliberately left graphs unchanged whenever more
than one Extraction agent was visible.  A read-only review of the production
definitions and their audit history identified a smaller set whose intended
formatter sources are explicit.  This migration applies only those reviewed
bindings and promotes valid formatter-free legacy graphs to the current schema.

Every remaining production v1.0 graph is accounted for: viable flows receive
reviewed bindings or topology repairs, formatter-free graphs are promoted,
retired/inactive dependencies are archived as Task Input-only v1.1 records,
and two exact disposable smoke rows are deleted. The transaction aborts if any
non-v1.1 definition remains. Destructive actions are pinned to immutable flow
UUID plus canonical preimage hash so a curator edit is never overwritten.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
from typing import Any

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy.dialects.postgresql import JSONB
from src.lib.config.schema_discovery import resolve_output_schema


revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORMATTER_AGENT_IDS = frozenset(
    {
        "chat_output",
        "chat_output_formatter",
        "csv_formatter",
        "tsv_formatter",
        "json_formatter",
    }
)

# Each tuple is ordered by the original control path.  The first thirteen
# entries are allele-family flows whose formatter intent was verified against
# their saved instructions, June normalization audit, and current domain-pack
# validation contract.  The final two are explicitly aggregate chat summaries.
_REVIEWED_SOURCE_AGENT_IDS_BY_FLOW: dict[str, tuple[str, ...]] = {
    "161e8637-6fec-4bcb-82f1-a34e8a17962b": ("allele_extractor",),
    "38b682c7-6c7f-4624-a21b-b8314416cf9f": (
        "gene_expression",
        "disease_extractor",
    ),
    "42f42763-104b-4b13-9b7e-c6dd4d30fa34": ("allele_extractor",),
    "55e3d67e-0492-4644-8a78-31179f4a3fa5": ("allele_extractor",),
    "6a43c3b8-143e-4746-869e-39a7c60a80ba": ("allele_extractor",),
    "71f6caeb-61ac-44b4-926e-af282e782855": ("allele_extractor",),
    "80ffdf0e-75ff-441c-b17a-bba23d2c8120": ("allele_extractor",),
    "8207552b-c41a-4086-9b6c-8cae0e6be04b": ("allele_extractor",),
    "98ccaaf4-7830-4ee0-a3db-7eb31391f9c3": ("allele_extractor",),
    "bc9fb1b3-9df6-4c80-9351-ff488c7e58eb": ("allele_extractor",),
    "ce968487-475f-4c81-bc1c-363707fd33ae": ("allele_extractor",),
    "eae42c19-6dbf-49dc-854d-040dddfd199a": ("allele_extractor",),
    "f5b5a118-92a8-469a-a90c-6f8bb094caba": ("allele_extractor",),
    "f977c37b-9088-420b-8625-b60cdaec0e96": ("allele_extractor",),
    # Validation-only strain-symbol lookup. The active ``allele`` agent has a
    # declared AlleleResultEnvelope and is a typed artifact source in v0.8.13.
    "976491a0-54d9-4c20-8dcb-e0f9f4a010f5": ("allele",),
}

_PLAIN_FINAL_OUTPUT_FLOW_IDS = frozenset(
    {
        # These custom agents intentionally return curator-ready plain text.
        # Removing the redundant chat formatter makes the final ordinary step
        # the explicit chat result without pretending it is a DomainEnvelope.
        "558b5fb2-5703-47bb-886e-044d8594e30d",
        "611f46d5-b701-4f7b-8da7-78a0d20a607b",
    }
)

_GO_FORMATTER_SOURCE_AGENT_IDS_BY_FLOW: dict[str, tuple[str, ...]] = {
    "303e38a2-3fab-4410-bca4-5384cb699d21": (
        "pdf_extraction",
        "gene_ontology",
    ),
    "b3c17f31-accb-4a3f-bd29-2589c64c7dd8": (
        "pdf_extraction",
        "gene",
        "gene_ontology",
        "go_annotations",
    ),
    "cc4f402c-6a62-49f2-9b99-ba32522c48e6": (
        "pdf_extraction",
        "gene",
        "gene_ontology",
        "go_annotations",
    ),
}

_AGENT_ID_REWRITES_BY_FLOW: dict[str, dict[str, str]] = {
    "cc4f402c-6a62-49f2-9b99-ba32522c48e6": {
        "gene_validation": "gene",
        "gene_ontology_lookup": "gene_ontology",
        "go_annotations_lookup": "go_annotations",
    },
    "d3598120-fc98-4d04-ade0-8eddc13341c4": {
        "gene_validation": "gene",
    },
}

_ARCHIVE_FLOW_REASONS: dict[str, str] = {
    "5750a96f-f2f9-42f2-8b8a-60b86b37e946": "chemical_extractor_removed",
    "9eeed059-f265-4180-b934-5da5f13eee16": "chemical_extractor_removed",
    "dfe883b3-6d79-4840-84a0-e12f3bc1259d": "chemical_extractor_removed",
    "e12c5a24-34f4-4202-8638-bf7e7da5a36b": "chemical_extractor_removed",
    "8846169a-a73a-449d-a025-84b8565d4480": "private_source_agent_inactive",
    "f7b44e55-85b1-407e-a5a2-81f3ba45d405": "private_source_agent_inactive",
    "b3dc0c4c-910d-4114-9bb0-e11c8ed0498a": "private_source_agent_inactive",
    "53492ce3-8e95-4da9-b8b0-195685a39016": "release_smoke_agent_inactive",
    "ae0c4b9b-0051-40cf-ae96-fccff485582a": "release_smoke_agent_inactive",
    "28d9abca-ca82-4917-964f-4f891cdbc53f": "ontology_mapping_agent_inactive",
    "bc2c4630-4471-4555-b78f-82436786b090": "ontology_mapping_agent_inactive",
    "1dff4943-0094-4cda-aefb-c4a253ebd9c0": "ontology_mapping_agent_inactive",
    "269a86a1-5ef3-4a62-9aa5-5c1416b0af4a": "ontology_mapping_agent_inactive",
    "d9b35a69-12e2-42d4-9d29-9a5527a5c4ad": "ontology_mapping_agent_inactive",
    "742b9ef6-284d-4f25-9df0-d7b7bdae2fac": "chemical_extractor_removed",
}

_DELETE_SMOKE_FLOW_IDS = frozenset(
    {
        "5f3fb920-5f19-4d96-8b61-1c3130ecc4dd",
        "74b67fdb-7983-4c60-bf66-3497eae17709",
    }
)

_DESTRUCTIVE_PREIMAGE_SHA256_BY_FLOW: dict[str, str] = {
    "5750a96f-f2f9-42f2-8b8a-60b86b37e946": "2b7d3e129ed0d9436cb4deb6eea1928493072b1f68dca384486ec3c54c262960",
    "9eeed059-f265-4180-b934-5da5f13eee16": "e8688f9f95fd3a8b5d68da1ca277075a0c4713cbee19c85d956f521a63f2cdc3",
    "dfe883b3-6d79-4840-84a0-e12f3bc1259d": "baf4a62028e6586a26460819b4a4a9a81b1075855589677f20d1e69d3d9d5897",
    "e12c5a24-34f4-4202-8638-bf7e7da5a36b": "ed2a55a1bc982c870af4d2ae39b5175fb67285a6d336ada47df7ad04c64997ca",
    "8846169a-a73a-449d-a025-84b8565d4480": "fe16085c25104f47be94864dc3d869165d6be6d6385ff886b598996f6bc57554",
    "f7b44e55-85b1-407e-a5a2-81f3ba45d405": "5e8f8c69d17b9eb4c5f25313fa52a7008bf33bedff879f581eef964a5340457c",
    "5f3fb920-5f19-4d96-8b61-1c3130ecc4dd": "3fa2010d8755296985c9d3d07b018c0b728b7a878b87e40e89d0ec6de430bf99",
    "74b67fdb-7983-4c60-bf66-3497eae17709": "3fa2010d8755296985c9d3d07b018c0b728b7a878b87e40e89d0ec6de430bf99",
    "28d9abca-ca82-4917-964f-4f891cdbc53f": "9532e0cbfa8985aab145b7ec3c8207f1e856f8727887d99433ba6fc7edfbe2a7",
    "bc2c4630-4471-4555-b78f-82436786b090": "badb2166763c34b83075766f5986dd5c0e20b8ae1d16b646c956ca9cd6fc892e",
    "1dff4943-0094-4cda-aefb-c4a253ebd9c0": "12873535a81752fddadb7d9162d2288918f60343ea97c8511fea72af337aa1cd",
    "269a86a1-5ef3-4a62-9aa5-5c1416b0af4a": "17d3660d47960f42be60cf7d1ccd2be79ada70daeaa30f4bafa905c8bb512934",
    "b3dc0c4c-910d-4114-9bb0-e11c8ed0498a": "2a669ac29325db3aaadae38b25a70f21689fcb21851dec35938b4a9079f4500a",
    "53492ce3-8e95-4da9-b8b0-195685a39016": "e1a3daee5e25f6370babdf6404420ad073c028bdabf1f4cce21b76200ee6dd39",
    "ae0c4b9b-0051-40cf-ae96-fccff485582a": "5373b261e88c90abbaf5ed226bd6832b37233be0e0f472dc8d36d6a02ac7026d",
    "742b9ef6-284d-4f25-9df0-d7b7bdae2fac": "91aa6becec5155537c8a8d35fba43291e32b3a7cbf53cc48aaf6ef728202da40",
    "d9b35a69-12e2-42d4-9d29-9a5527a5c4ad": "c430c078ecda1043b438e177a1acd750971a576ae77bc43cf6eac224954c3d34",
}


def _agent_id(node: Mapping[str, Any]) -> str:
    data = node.get("data")
    return str(data.get("agent_id") or "") if isinstance(data, Mapping) else ""


def _definition_sha256(definition: Mapping[str, Any]) -> str:
    payload = json.dumps(
        definition,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _linear_path(definition: Mapping[str, Any]) -> tuple[list[str] | None, str]:
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not nodes:
        return None, "malformed_arrays"
    if not all(isinstance(node, Mapping) for node in nodes):
        return None, "malformed_node"
    if not all(isinstance(edge, Mapping) for edge in edges):
        return None, "malformed_edge"

    node_ids = [str(node.get("id") or "").strip() for node in nodes]
    if any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
        return None, "invalid_node_ids"
    edge_ids = [str(edge.get("id") or "").strip() for edge in edges]
    if any(not edge_id for edge_id in edge_ids) or len(edge_ids) != len(set(edge_ids)):
        return None, "invalid_edge_ids"
    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if str(edge.get("role") or "control_flow") != "control_flow":
            return None, "non_control_legacy_edge"
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in outgoing or target not in incoming:
            return None, "missing_edge_node"
        outgoing[source].append(target)
        incoming[target].append(source)
    if any(len(values) > 1 for values in incoming.values()):
        return None, "join"
    if any(len(values) > 1 for values in outgoing.values()):
        return None, "branch"
    entries = [node_id for node_id in node_ids if not incoming[node_id]]
    terminals = [node_id for node_id in node_ids if not outgoing[node_id]]
    if len(entries) != 1 or len(terminals) != 1:
        return None, "ambiguous_entry_or_terminal"
    if str(definition.get("entry_node_id") or "") != entries[0]:
        return None, "entry_mismatch"

    ordered: list[str] = []
    current = entries[0]
    while current not in ordered:
        ordered.append(current)
        next_nodes = outgoing[current]
        if not next_nodes:
            break
        current = next_nodes[0]
    if len(ordered) != len(node_ids):
        return None, "cycle_or_disconnected"
    return ordered, "linear"


def _has_valid_task_input(definition: Mapping[str, Any]) -> bool:
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return False
    tasks = [
        node
        for node in nodes
        if isinstance(node, Mapping)
        and node.get("type") == "task_input"
        and _agent_id(node) == "task_input"
    ]
    if len(tasks) != 1:
        return False
    data = tasks[0].get("data")
    return bool(
        isinstance(data, Mapping)
        and str(data.get("task_instructions") or "").strip()
        and str(definition.get("entry_node_id") or "")
        == str(tasks[0].get("id") or "")
    )


def _unique_edge_id(base: str, existing: set[str]) -> str:
    candidate = base[:50]
    suffix = 1
    while candidate in existing:
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 50 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    existing.add(candidate)
    return candidate


def _valid_typed_candidate(definition: Mapping[str, Any]) -> bool:
    if str(definition.get("version") or "") != "1.1" or not _has_valid_task_input(
        definition
    ):
        return False
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return False
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, Mapping) and node.get("id")
    }
    output_keys = [
        str((node.get("data") or {}).get("output_key") or "").strip()
        for node in nodes
        if isinstance(node, Mapping) and isinstance(node.get("data"), Mapping)
    ]
    if (
        len(output_keys) != len(nodes)
        or any(not output_key for output_key in output_keys)
        or len(output_keys) != len(set(output_keys))
    ):
        return False
    control_definition = {
        "nodes": [
            node
            for node in nodes
            if isinstance(node, Mapping) and node.get("type") != "output"
        ],
        "edges": [
            edge
            for edge in edges
            if isinstance(edge, Mapping)
            and str(edge.get("role") or "control_flow") == "control_flow"
        ],
        "entry_node_id": definition.get("entry_node_id"),
    }
    ordered, _ = _linear_path(control_definition)
    if ordered is None:
        return False
    control_ids = set(ordered)
    output_nodes = {
        node_id for node_id, node in node_by_id.items() if node.get("type") == "output"
    }
    attachments = [
        edge
        for edge in edges
        if isinstance(edge, Mapping)
        and str(edge.get("role") or "control_flow") == "output_attachment"
    ]
    targets = {str(edge.get("target") or "") for edge in attachments}
    if targets != output_nodes:
        return False
    source_target_pairs: set[tuple[str, str]] = set()
    for edge in attachments:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        pair = (source, target)
        if source not in control_ids or target not in output_nodes or pair in source_target_pairs:
            return False
        source_target_pairs.add(pair)
    return True


def _source_node_ids(
    *,
    ordered: list[str],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    source_agent_ids: tuple[str, ...],
    before_index: int,
    artifact_source_agent_ids: set[str],
) -> list[str] | None:
    source_node_ids: list[str] = []
    for source_agent_id in source_agent_ids:
        matches = [
            node_id
            for node_id in ordered[:before_index]
            if _agent_id(nodes_by_id[node_id]) == source_agent_id
        ]
        if len(matches) != 1 or source_agent_id not in artifact_source_agent_ids:
            return None
        source_node_ids.append(matches[0])
    if len(source_node_ids) != len(set(source_node_ids)):
        return None
    return source_node_ids


def _remove_plain_chat_formatter(
    candidate: dict[str, Any],
    *,
    ordered: list[str],
) -> tuple[dict[str, Any] | None, str]:
    nodes_by_id = {str(node["id"]): node for node in candidate["nodes"]}
    formatter_ids = [
        node_id
        for node_id in ordered
        if _agent_id(nodes_by_id[node_id]) in _FORMATTER_AGENT_IDS
    ]
    if len(formatter_ids) != 1 or formatter_ids[0] != ordered[-1]:
        return None, "plain_final_shape_changed"
    formatter_id = formatter_ids[0]
    if _agent_id(nodes_by_id[formatter_id]) not in {"chat_output", "chat_output_formatter"}:
        return None, "plain_final_not_chat"
    incoming = [
        edge
        for edge in candidate["edges"]
        if str(edge.get("role") or "control_flow") == "control_flow"
        and str(edge.get("target") or "") == formatter_id
    ]
    if len(incoming) != 1:
        return None, "plain_final_incoming_changed"
    candidate["edges"].remove(incoming[0])
    candidate["nodes"].remove(nodes_by_id[formatter_id])
    candidate["version"] = "1.1"
    candidate["migration_note"] = "removed_redundant_plain_text_chat_formatter"
    if not _valid_typed_candidate(candidate):
        return None, "candidate_rejected"
    return candidate, "plain_final_promoted"


def _upgrade_go_formatter_suffix(
    candidate: dict[str, Any],
    *,
    ordered: list[str],
    source_agent_ids: tuple[str, ...],
    artifact_source_agent_ids: set[str],
) -> tuple[dict[str, Any] | None, str]:
    nodes_by_id = {str(node["id"]): node for node in candidate["nodes"]}
    formatter_ids = [
        node_id
        for node_id in ordered
        if _agent_id(nodes_by_id[node_id]) in _FORMATTER_AGENT_IDS
    ]
    if len(formatter_ids) != 2 or formatter_ids != ordered[-2:]:
        return None, "go_formatter_suffix_shape_changed"
    if _agent_id(nodes_by_id[formatter_ids[0]]) not in {
        "chat_output",
        "chat_output_formatter",
    } or _agent_id(nodes_by_id[formatter_ids[1]]) != "tsv_formatter":
        return None, "go_formatter_suffix_agents_changed"
    first_formatter_index = len(ordered) - 2
    source_node_ids = _source_node_ids(
        ordered=ordered,
        nodes_by_id=nodes_by_id,
        source_agent_ids=source_agent_ids,
        before_index=first_formatter_index,
        artifact_source_agent_ids=artifact_source_agent_ids,
    )
    if source_node_ids is None:
        return None, "reviewed_source_unavailable"

    incoming_by_formatter: dict[str, dict[str, Any]] = {}
    for formatter_id in formatter_ids:
        incoming = [
            edge
            for edge in candidate["edges"]
            if str(edge.get("role") or "control_flow") == "control_flow"
            and str(edge.get("target") or "") == formatter_id
        ]
        if len(incoming) != 1:
            return None, "go_formatter_incoming_changed"
        incoming_by_formatter[formatter_id] = incoming[0]

    existing_edge_ids = {str(edge.get("id") or "") for edge in candidate["edges"]}
    for formatter_id in formatter_ids:
        attachment = incoming_by_formatter[formatter_id]
        attachment["source"] = source_node_ids[0]
        attachment["role"] = "output_attachment"
        nodes_by_id[formatter_id]["type"] = "output"
        for source_node_id in source_node_ids[1:]:
            candidate["edges"].append(
                {
                    "id": _unique_edge_id(
                        f"edge_output_{source_node_id}_{formatter_id}",
                        existing_edge_ids,
                    ),
                    "source": source_node_id,
                    "target": formatter_id,
                    "role": "output_attachment",
                }
            )
    candidate["version"] = "1.1"
    candidate["migration_note"] = (
        "converted_sequential_chat_tsv_to_parallel_draft_outputs"
    )
    if not _valid_typed_candidate(candidate):
        return None, "candidate_rejected"
    return candidate, "go_formatter_suffix_upgraded"


def _archived_definition(
    definition: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any] | None:
    candidate = deepcopy(dict(definition))
    nodes = candidate.get("nodes")
    if not isinstance(nodes, list):
        return None
    task_nodes = [
        node
        for node in nodes
        if isinstance(node, Mapping)
        and node.get("type") == "task_input"
        and _agent_id(node) == "task_input"
    ]
    if len(task_nodes) != 1:
        return None
    task = deepcopy(dict(task_nodes[0]))
    candidate["nodes"] = [task]
    candidate["edges"] = []
    candidate["entry_node_id"] = str(task.get("id") or "")
    candidate["version"] = "1.1"
    candidate["archived_reason"] = reason
    candidate["archived_by_migration"] = revision
    return candidate if _valid_typed_candidate(candidate) else None


def _upgrade_reviewed_definition(
    flow_id: str,
    definition: Mapping[str, Any],
    *,
    artifact_source_agent_ids: set[str],
    active_agent_ids: set[str] | None = None,
) -> tuple[dict[str, Any], str]:
    candidate = deepcopy(dict(definition))
    rewrites = _AGENT_ID_REWRITES_BY_FLOW.get(flow_id, {})
    rewritten = False
    for node in candidate.get("nodes", []):
        if not isinstance(node, Mapping):
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        replacement = rewrites.get(str(data.get("agent_id") or ""))
        if replacement is not None:
            data["agent_id"] = replacement
            rewritten = True

    if str(candidate.get("version") or "1.0") != "1.0":
        if rewritten and _valid_typed_candidate(candidate):
            candidate["migration_note"] = "normalized_active_agent_routing_keys"
            return candidate, "agent_ids_normalized"
        return dict(definition), "already_current"
    ordered, reason = _linear_path(candidate)
    if ordered is None or not _has_valid_task_input(candidate):
        return dict(definition), f"skipped_{reason}"

    if flow_id in _PLAIN_FINAL_OUTPUT_FLOW_IDS:
        remaining_agent_ids = {
            _agent_id(node)
            for node in candidate["nodes"]
            if _agent_id(node)
            not in {"task_input", "chat_output", "chat_output_formatter"}
        }
        if active_agent_ids is not None and not remaining_agent_ids.issubset(
            active_agent_ids
        ):
            return dict(definition), "plain_final_source_unavailable"
        upgraded, upgraded_reason = _remove_plain_chat_formatter(
            candidate,
            ordered=ordered,
        )
        return (
            (upgraded, upgraded_reason)
            if upgraded is not None
            else (dict(definition), upgraded_reason)
        )
    go_source_agent_ids = _GO_FORMATTER_SOURCE_AGENT_IDS_BY_FLOW.get(flow_id)
    if go_source_agent_ids is not None:
        upgraded, upgraded_reason = _upgrade_go_formatter_suffix(
            candidate,
            ordered=ordered,
            source_agent_ids=go_source_agent_ids,
            artifact_source_agent_ids=artifact_source_agent_ids,
        )
        return (
            (upgraded, upgraded_reason)
            if upgraded is not None
            else (dict(definition), upgraded_reason)
        )
    nodes_by_id = {str(node["id"]): node for node in candidate["nodes"]}
    formatter_ids = [
        node_id
        for node_id in ordered
        if _agent_id(nodes_by_id[node_id]) in _FORMATTER_AGENT_IDS
    ]
    if not formatter_ids:
        candidate["version"] = "1.1"
        return (
            (candidate, "schema_promoted_no_formatter")
            if _valid_typed_candidate(candidate)
            else (dict(definition), "candidate_rejected")
        )

    reviewed_agent_ids = _REVIEWED_SOURCE_AGENT_IDS_BY_FLOW.get(str(flow_id))
    if reviewed_agent_ids is None:
        return dict(definition), "manual_review_required"
    if len(formatter_ids) != 1 or formatter_ids[0] != ordered[-1]:
        return dict(definition), "reviewed_shape_changed"
    formatter_id = formatter_ids[0]
    formatter_position = ordered.index(formatter_id)
    source_node_ids = _source_node_ids(
        ordered=ordered,
        nodes_by_id=nodes_by_id,
        source_agent_ids=reviewed_agent_ids,
        before_index=formatter_position,
        artifact_source_agent_ids=artifact_source_agent_ids,
    )
    if source_node_ids is None:
        return dict(definition), "reviewed_source_unavailable"

    incoming = [
        edge
        for edge in candidate["edges"]
        if str(edge.get("role") or "control_flow") == "control_flow"
        and str(edge.get("target") or "") == formatter_id
    ]
    if len(incoming) != 1:
        return dict(definition), "reviewed_incoming_changed"
    original_edge = incoming[0]

    # Three older allele flows carried the retired standalone ``allele``
    # validator as the last control step.  The current allele domain pack runs
    # the same required, blocking validation on the extractor envelope.  The
    # reviewed candidate therefore removes only that redundant terminal node;
    # leaving it in place would run validation twice while the formatter was
    # correctly scoped to the extractor artifact.
    immediate_predecessor_id = str(original_edge.get("source") or "")
    immediate_predecessor = nodes_by_id.get(immediate_predecessor_id)
    if (
        immediate_predecessor is not None
        and _agent_id(immediate_predecessor) == "allele"
        and reviewed_agent_ids == ("allele_extractor",)
        and _agent_id(nodes_by_id[source_node_ids[0]]) == "allele_extractor"
    ):
        validator_incoming = [
            edge
            for edge in candidate["edges"]
            if str(edge.get("role") or "control_flow") == "control_flow"
            and str(edge.get("target") or "") == immediate_predecessor_id
        ]
        if (
            len(validator_incoming) != 1
            or str(validator_incoming[0].get("source") or "") != source_node_ids[0]
        ):
            return dict(definition), "reviewed_validator_shape_changed"
        candidate["edges"].remove(validator_incoming[0])
        candidate["nodes"].remove(immediate_predecessor)
        nodes_by_id.pop(immediate_predecessor_id, None)

    original_edge["source"] = source_node_ids[0]
    original_edge["role"] = "output_attachment"
    existing_edge_ids = {str(edge.get("id") or "") for edge in candidate["edges"]}
    for source_node_id in source_node_ids[1:]:
        candidate["edges"].append(
            {
                "id": _unique_edge_id(
                    f"edge_output_{source_node_id}_{formatter_id}",
                    existing_edge_ids,
                ),
                "source": source_node_id,
                "target": formatter_id,
                "role": "output_attachment",
            }
        )
    nodes_by_id[formatter_id]["type"] = "output"
    candidate["version"] = "1.1"
    if not _valid_typed_candidate(candidate):
        return dict(definition), "candidate_rejected"
    return candidate, "reviewed_formatter_upgraded"


def _visible_artifact_source_ids_by_user(
    bind: sa.engine.Connection,
) -> dict[int, set[str]]:
    agent_rows = bind.execute(
        sa.text(
            """
            SELECT agent_key, user_id, visibility, project_id, category,
                   to_jsonb(agents)->>'subcategory' AS subcategory,
                   output_schema_key
            FROM agents
            WHERE is_active = true
              AND (
                    lower(
                      coalesce(category, '') || ' ' ||
                      coalesce(to_jsonb(agents)->>'subcategory', '')
                    ) LIKE '%extract%'
                    OR (
                      lower(coalesce(category, '')) LIKE '%valid%'
                      AND nullif(trim(coalesce(output_schema_key, '')), '') IS NOT NULL
                    )
                  )
            """
        )
    ).mappings().all()
    membership_rows = bind.execute(
        sa.text("SELECT user_id, project_id FROM project_members")
    ).mappings().all()
    projects_by_user: dict[int, set[str]] = defaultdict(set)
    for row in membership_rows:
        projects_by_user[int(row["user_id"])].add(str(row["project_id"]))
    user_ids = [
        int(value)
        for value in bind.execute(
            sa.text("SELECT DISTINCT user_id FROM curation_flows")
        ).scalars()
    ]
    visible: dict[int, set[str]] = {}
    for user_id in user_ids:
        keys: set[str] = set()
        for row in agent_rows:
            category = str(row["category"] or "").strip().lower()
            subcategory = str(row["subcategory"] or "").strip().lower()
            output_schema_key = str(row["output_schema_key"] or "").strip()
            if (
                "extract" not in category
                and "extract" not in subcategory
                and resolve_output_schema(output_schema_key) is None
            ):
                continue
            visibility = str(row["visibility"] or "")
            if visibility == "system":
                visible_to_user = True
            elif visibility == "private" and row["user_id"] == user_id:
                visible_to_user = True
            elif (
                visibility == "project"
                and row["project_id"] is not None
                and str(row["project_id"]) in projects_by_user[user_id]
            ):
                visible_to_user = True
            else:
                visible_to_user = False
            if not visible_to_user:
                continue
            agent_key = str(row["agent_key"])
            keys.add(agent_key)
        visible[user_id] = keys
    return visible


def _visible_active_agent_ids_by_user(
    bind: sa.engine.Connection,
) -> dict[int, set[str]]:
    agent_rows = bind.execute(
        sa.text(
            """
            SELECT agent_key, user_id, visibility, project_id
            FROM agents
            WHERE is_active = true
            """
        )
    ).mappings().all()
    membership_rows = bind.execute(
        sa.text("SELECT user_id, project_id FROM project_members")
    ).mappings().all()
    projects_by_user: dict[int, set[str]] = defaultdict(set)
    for row in membership_rows:
        projects_by_user[int(row["user_id"])].add(str(row["project_id"]))
    user_ids = [
        int(value)
        for value in bind.execute(
            sa.text("SELECT DISTINCT user_id FROM curation_flows")
        ).scalars()
    ]
    visible: dict[int, set[str]] = {}
    for user_id in user_ids:
        keys: set[str] = set()
        for row in agent_rows:
            visibility = str(row["visibility"] or "")
            if visibility == "system":
                visible_to_user = True
            elif visibility == "private" and row["user_id"] == user_id:
                visible_to_user = True
            elif (
                visibility == "project"
                and row["project_id"] is not None
                and str(row["project_id"]) in projects_by_user[user_id]
            ):
                visible_to_user = True
            else:
                visible_to_user = False
            if visible_to_user:
                keys.add(str(row["agent_key"]))
        visible[user_id] = keys
    return visible


def _migrate(bind: sa.engine.Connection) -> Counter[str]:
    bind.execute(
        sa.text(
            "SET LOCAL application_name = "
            "'alembic:d6e7f8a9b0c1:reviewed-flow-upgrades'"
        )
    )
    trigger_rows = bind.execute(
        sa.text(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE tgrelid = 'curation_flows'::regclass
              AND tgname IN (
                    'audit_curation_flows_update',
                    'audit_curation_flows_delete'
                  )
              AND NOT tgisinternal
              AND tgenabled IN ('O', 'A')
            """
        )
    ).scalars().all()
    enabled_triggers = {str(value) for value in trigger_rows}
    required_triggers = {
        "audit_curation_flows_update",
        "audit_curation_flows_delete",
    }
    if enabled_triggers != required_triggers:
        raise RuntimeError(
            "Refusing reviewed flow migration because required flow audit triggers "
            f"are absent or disabled: {sorted(required_triggers - enabled_triggers)}"
        )

    # The zero-v1.0 postcondition covers the whole table, so prevent a new flow
    # insert or concurrent edit from racing between the inventory and final
    # assertion. Production runs this short lock while public maintenance is on.
    bind.execute(sa.text("LOCK TABLE curation_flows IN SHARE ROW EXCLUSIVE MODE"))

    artifact_source_ids_by_user = _visible_artifact_source_ids_by_user(bind)
    active_agent_ids_by_user = _visible_active_agent_ids_by_user(bind)
    rows = bind.execute(
        sa.text(
            """
            SELECT id, user_id, name, is_active, flow_definition
            FROM curation_flows
            ORDER BY id
            FOR UPDATE
            """
        )
    ).mappings().all()
    update_statement = sa.text(
        """
        UPDATE curation_flows
        SET flow_definition = :new_definition,
            is_active = :new_is_active
        WHERE id = :flow_id
          AND is_active = :old_is_active
          AND flow_definition = :old_definition
        """
    ).bindparams(
        sa.bindparam("new_definition", type_=JSONB),
        sa.bindparam("old_definition", type_=JSONB),
    )
    delete_statement = sa.text(
        """
        DELETE FROM curation_flows
        WHERE id = :flow_id
          AND is_active = false
          AND flow_definition = :old_definition
        """
    ).bindparams(sa.bindparam("old_definition", type_=JSONB))
    counts: Counter[str] = Counter()
    for row in rows:
        counts["total"] += 1
        flow_id = str(row["id"])
        old_definition = row["flow_definition"]
        if not isinstance(old_definition, Mapping):
            raise RuntimeError(f"Flow {flow_id} has a non-object definition")

        if flow_id in _DELETE_SMOKE_FLOW_IDS:
            expected_hash = _DESTRUCTIVE_PREIMAGE_SHA256_BY_FLOW[flow_id]
            actual_hash = _definition_sha256(old_definition)
            if actual_hash != expected_hash:
                raise RuntimeError(
                    f"Disposable smoke flow {flow_id} preimage drifted: "
                    f"expected {expected_hash}, found {actual_hash}"
                )
            agent_ids = [
                _agent_id(node)
                for node in old_definition.get("nodes", [])
                if isinstance(node, Mapping)
            ]
            if (
                bool(row["is_active"])
                or not str(row["name"] or "").startswith(
                    "codex-flow-save-public-smoke-"
                )
                or agent_ids != ["task_input", "chat_output"]
            ):
                raise RuntimeError(
                    f"Disposable smoke flow {flow_id} no longer matches its reviewed shape"
                )
            result = bind.execute(
                delete_statement,
                {"flow_id": row["id"], "old_definition": dict(old_definition)},
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Failed to delete reviewed smoke flow {flow_id}")
            counts["deleted_smoke"] += 1
            continue

        archive_reason = _ARCHIVE_FLOW_REASONS.get(flow_id)
        if archive_reason is not None:
            if (
                str(old_definition.get("version") or "") == "1.1"
                and old_definition.get("archived_by_migration") == revision
                and old_definition.get("archived_reason") == archive_reason
                and not bool(row["is_active"])
            ):
                counts["already_archived"] += 1
                continue
            expected_hash = _DESTRUCTIVE_PREIMAGE_SHA256_BY_FLOW[flow_id]
            actual_hash = _definition_sha256(old_definition)
            if actual_hash != expected_hash:
                raise RuntimeError(
                    f"Archived flow {flow_id} preimage drifted: "
                    f"expected {expected_hash}, found {actual_hash}"
                )
            agent_ids = {
                _agent_id(node)
                for node in old_definition.get("nodes", [])
                if isinstance(node, Mapping)
            }
            expected_dependency = (
                "chemical_extractor"
                if archive_reason == "chemical_extractor_removed"
                else None
            )
            if expected_dependency is not None and expected_dependency not in agent_ids:
                raise RuntimeError(
                    f"Archived flow {flow_id} no longer contains {expected_dependency}"
                )
            new_definition = _archived_definition(
                old_definition,
                reason=archive_reason,
            )
            if new_definition is None:
                raise RuntimeError(
                    f"Archived flow {flow_id} could not preserve a valid Task Input"
                )
            reason = "archived_unavailable_dependency"
            new_is_active = False
        else:
            new_definition, reason = _upgrade_reviewed_definition(
                flow_id,
                old_definition,
                artifact_source_agent_ids=artifact_source_ids_by_user.get(
                    int(row["user_id"]), set()
                ),
                active_agent_ids=active_agent_ids_by_user.get(
                    int(row["user_id"]), set()
                ),
            )
            new_is_active = bool(row["is_active"])
        candidate_agent_ids = {
            _agent_id(node)
            for node in new_definition.get("nodes", [])
            if isinstance(node, Mapping) and _agent_id(node) != "task_input"
        }
        unavailable_agent_ids = candidate_agent_ids - active_agent_ids_by_user.get(
            int(row["user_id"]), set()
        )
        if unavailable_agent_ids:
            raise RuntimeError(
                f"Flow {flow_id} retains unavailable agent IDs after migration: "
                + ", ".join(sorted(unavailable_agent_ids))
            )
        nodes_by_id = {
            str(node.get("id") or ""): node
            for node in new_definition.get("nodes", [])
            if isinstance(node, Mapping)
        }
        artifact_source_agent_ids = artifact_source_ids_by_user.get(
            int(row["user_id"]), set()
        )
        for edge in new_definition.get("edges", []):
            if not isinstance(edge, Mapping) or edge.get("role") != "output_attachment":
                continue
            source_node = nodes_by_id.get(str(edge.get("source") or ""))
            source_agent_id = _agent_id(source_node or {})
            if source_agent_id not in artifact_source_agent_ids:
                raise RuntimeError(
                    f"Flow {flow_id} output attachment {edge.get('id')} retains "
                    f"non-artifact source agent '{source_agent_id}'"
                )
        counts[reason] += 1
        if new_definition == old_definition:
            if str(old_definition.get("version") or "1.0") != "1.1":
                raise RuntimeError(
                    f"Flow {flow_id} remains legacy after reviewed migration: {reason}"
                )
            continue
        result = bind.execute(
            update_statement,
            {
                "flow_id": row["id"],
                "old_is_active": bool(row["is_active"]),
                "new_is_active": new_is_active,
                "old_definition": dict(old_definition),
                "new_definition": new_definition,
            },
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Concurrent change detected for flow {flow_id}")
        counts["updated"] += 1
        counts["updated_active" if new_is_active else "updated_inactive"] += 1
        if bool(row["is_active"]) and not new_is_active:
            counts["newly_archived"] += 1

    remaining_legacy = bind.execute(
        sa.text(
            """
            SELECT id::text
            FROM curation_flows
            WHERE coalesce(flow_definition->>'version', '1.0') <> '1.1'
            ORDER BY id
            """
        )
    ).scalars().all()
    if remaining_legacy:
        raise RuntimeError(
            "Reviewed flow migration left non-v1.1 definitions: "
            + ", ".join(str(value) for value in remaining_legacy)
        )

    print("REVIEWED_FLOW_MIGRATION_SUMMARY=" + json.dumps(dict(sorted(counts.items()))))
    return counts


def upgrade() -> None:
    _migrate(op.get_bind())


def downgrade() -> None:
    """Forward-only normalization; the audit log retains every old definition."""
