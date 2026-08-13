"""Upgrade unambiguous legacy flow graphs without stranding other flows.

Revision ID: c5d6e7f8a9b0
Revises: b3c4d5e6f7a8
Create Date: 2026-07-13 19:00:00.000000

The v0.8.11 output-attachment contract made v1.0 formatter control steps
unrunnable. This migration converts only terminal formatters with exactly one
active, owner-visible upstream Extraction agent. Other v1.0 graphs deliberately
remain unchanged and continue through the application's legacy compatibility
path. It also restores the Task Input node required by the current schema on
older otherwise-linear graphs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
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


def _agent_id(node: Mapping[str, Any]) -> str:
    data = node.get("data")
    return str(data.get("agent_id") or "") if isinstance(data, Mapping) else ""


def _linear_path(
    definition: Mapping[str, Any],
    *,
    require_declared_entry: bool = True,
) -> tuple[list[str] | None, str]:
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not nodes:
        return None, "malformed_arrays"
    if not all(isinstance(node, Mapping) for node in nodes):
        return None, "malformed_node"
    if not all(isinstance(edge, Mapping) for edge in edges):
        return None, "malformed_edge"

    node_ids = [str(node.get("id") or "").strip() for node in nodes]
    edge_ids = [str(edge.get("id") or "").strip() for edge in edges]
    if any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
        return None, "invalid_node_ids"
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
    if (
        require_declared_entry
        and str(definition.get("entry_node_id") or "") != entries[0]
    ):
        return None, "entry_mismatch"

    ordered: list[str] = []
    seen: set[str] = set()
    current = entries[0]
    while current not in seen:
        ordered.append(current)
        seen.add(current)
        next_nodes = outgoing[current]
        if not next_nodes:
            break
        current = next_nodes[0]
    if len(ordered) != len(node_ids):
        return None, "cycle_or_disconnected"
    return ordered, "linear"


def _unique_value(base: str, existing: set[str], *, max_length: int = 50) -> str:
    candidate = base[:max_length]
    suffix = 1
    while candidate in existing:
        suffix_text = f"_{suffix}"
        candidate = f"{base[: max_length - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _add_task_input(
    definition: dict[str, Any],
    *,
    flow_name: str,
) -> tuple[bool, str]:
    nodes = definition["nodes"]
    task_nodes = [node for node in nodes if node.get("type") == "task_input"]
    if len(task_nodes) == 1:
        return False, "task_present"
    if task_nodes:
        return False, "multiple_task_inputs"

    ordered, reason = _linear_path(definition, require_declared_entry=False)
    if ordered is None:
        return False, f"task_skipped_{reason}"
    old_entry = ordered[0]
    nodes_by_id = {str(node["id"]): node for node in nodes}
    old_position = nodes_by_id[old_entry].get("position")
    raw_x = old_position.get("x", 0) if isinstance(old_position, Mapping) else 0
    raw_y = old_position.get("y", 0) if isinstance(old_position, Mapping) else 0
    x = raw_x if isinstance(raw_x, (int, float)) else 0
    y = raw_y if isinstance(raw_y, (int, float)) else 0
    node_id = _unique_value("task_input_migrated", set(nodes_by_id))
    output_keys = {
        str((node.get("data") or {}).get("output_key") or "")
        for node in nodes
        if isinstance(node.get("data"), Mapping)
    }
    output_key = _unique_value("task_input", output_keys)
    edge_ids = {str(edge.get("id") or "") for edge in definition["edges"]}
    edge_id = _unique_value("edge_task_input_migrated", edge_ids)
    normalized_name = str(flow_name or "").strip() or "Saved flow"
    instructions = f"Execute the '{normalized_name}' curation workflow."

    nodes.insert(
        0,
        {
            "id": node_id,
            "type": "task_input",
            "position": {"x": x - 250, "y": y},
            "data": {
                "agent_id": "task_input",
                "agent_display_name": "Task Input",
                "task_instructions": instructions,
                "output_key": output_key,
            },
        },
    )
    definition["edges"].insert(
        0,
        {
            "id": edge_id,
            "source": node_id,
            "target": old_entry,
            "role": "control_flow",
        },
    )
    definition["entry_node_id"] = node_id
    definition["task_instructions_default_only"] = True
    return True, "task_added"


def _valid_task_input(definition: Mapping[str, Any]) -> tuple[bool, str]:
    """Require the single schema-valid Task Input needed before any write."""

    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return False, "malformed_nodes"
    task_nodes = [
        node
        for node in nodes
        if isinstance(node, Mapping) and node.get("type") == "task_input"
    ]
    if len(task_nodes) != 1:
        return False, "missing_or_multiple_task_inputs"
    task_node = task_nodes[0]
    data = task_node.get("data")
    if not isinstance(data, Mapping):
        return False, "malformed_task_input_data"
    if str(data.get("agent_id") or "") != "task_input":
        return False, "invalid_task_input_agent"
    instructions = data.get("task_instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        return False, "invalid_task_instructions"
    if str(definition.get("entry_node_id") or "") != str(task_node.get("id") or ""):
        return False, "task_input_not_entry"
    return True, "task_valid"


def _upgrade_terminal_formatter(
    definition: dict[str, Any],
    *,
    extraction_agent_ids: set[str],
) -> tuple[bool, str]:
    if str(definition.get("version") or "1.0") != "1.0":
        return False, "already_current"
    ordered, reason = _linear_path(definition)
    if ordered is None:
        return False, f"formatter_skipped_{reason}"
    nodes_by_id = {str(node["id"]): node for node in definition["nodes"]}
    formatter_ids = [
        node_id
        for node_id in ordered
        if _agent_id(nodes_by_id[node_id]) in _FORMATTER_AGENT_IDS
    ]
    if not formatter_ids:
        return False, "no_supported_formatter"
    if len(formatter_ids) != 1 or formatter_ids[0] != ordered[-1]:
        return False, "formatter_skipped_ambiguous_topology"

    formatter_id = formatter_ids[0]
    other_output_ids = [
        node_id
        for node_id, node in nodes_by_id.items()
        if node_id != formatter_id and str(node.get("type") or "agent") == "output"
    ]
    if other_output_ids:
        return False, "formatter_skipped_preexisting_output_node"
    upstream_extraction_ids = [
        node_id
        for node_id in ordered[:-1]
        if _agent_id(nodes_by_id[node_id]) in extraction_agent_ids
    ]
    if not upstream_extraction_ids:
        return False, "formatter_skipped_no_visible_extraction"
    if len(upstream_extraction_ids) != 1:
        return False, "formatter_skipped_multiple_extractions"

    incoming_edges = [
        edge
        for edge in definition["edges"]
        if str(edge.get("role") or "control_flow") == "control_flow"
        and str(edge.get("target") or "") == formatter_id
    ]
    if len(incoming_edges) != 1:
        return False, "formatter_skipped_ambiguous_incoming"

    nodes_by_id[formatter_id]["type"] = "output"
    incoming_edges[0]["source"] = upstream_extraction_ids[0]
    incoming_edges[0]["role"] = "output_attachment"
    definition["version"] = "1.1"
    return True, "formatter_upgraded"


def _valid_transformed_definition(definition: Mapping[str, Any]) -> tuple[bool, str]:
    """Conservatively validate only graph shapes this migration can emit."""

    task_valid, task_reason = _valid_task_input(definition)
    if not task_valid:
        return False, task_reason
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return False, "malformed_arrays"
    output_keys = []
    for node in nodes:
        if not isinstance(node, Mapping) or not isinstance(node.get("data"), Mapping):
            return False, "malformed_node_data"
        output_key = str(node["data"].get("output_key") or "")
        if not output_key:
            return False, "missing_output_key"
        output_keys.append(output_key)
    if len(output_keys) != len(set(output_keys)):
        return False, "duplicate_output_keys"

    version = str(definition.get("version") or "")
    if version == "1.0":
        ordered, reason = _linear_path(definition)
        return (ordered is not None, reason if ordered is None else "valid_v1_0")
    if version != "1.1":
        return False, "unsupported_version"

    output_nodes = [
        node for node in nodes if str(node.get("type") or "agent") == "output"
    ]
    attachments = [
        edge
        for edge in edges
        if isinstance(edge, Mapping)
        and str(edge.get("role") or "control_flow") == "output_attachment"
    ]
    if len(output_nodes) != 1 or len(attachments) != 1:
        return False, "invalid_output_attachment_count"
    output_id = str(output_nodes[0].get("id") or "")
    attachment = attachments[0]
    if str(attachment.get("target") or "") != output_id:
        return False, "output_attachment_target_mismatch"
    if any(
        str(edge.get("role") or "control_flow") == "control_flow"
        and (str(edge.get("source") or "") == output_id or str(edge.get("target") or "") == output_id)
        for edge in edges
        if isinstance(edge, Mapping)
    ):
        return False, "output_node_in_control_flow"

    ordinary_definition = {
        "nodes": [node for node in nodes if str(node.get("id") or "") != output_id],
        "edges": [
            edge
            for edge in edges
            if str(edge.get("role") or "control_flow") == "control_flow"
        ],
        "entry_node_id": definition.get("entry_node_id"),
    }
    ordered, reason = _linear_path(ordinary_definition)
    if ordered is None:
        return False, f"invalid_control_projection_{reason}"
    if str(attachment.get("source") or "") not in set(ordered):
        return False, "output_attachment_source_not_executable"
    return True, "valid_v1_1"


def _classify_and_upgrade(
    definition: Mapping[str, Any],
    *,
    flow_name: str,
    extraction_agent_ids: set[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    version = str(definition.get("version") or "1.0")
    if version != "1.0":
        return dict(definition), (f"unsupported_or_current_version_{version}",)
    if not isinstance(definition.get("nodes"), list) or not isinstance(
        definition.get("edges"), list
    ):
        return dict(definition), ("task_skipped_malformed_arrays",)
    if not all(isinstance(node, Mapping) for node in definition["nodes"]):
        return dict(definition), ("task_skipped_malformed_node",)
    if not all(isinstance(edge, Mapping) for edge in definition["edges"]):
        return dict(definition), ("task_skipped_malformed_edge",)

    upgraded = deepcopy(dict(definition))
    reasons: list[str] = []
    task_changed, task_reason = _add_task_input(upgraded, flow_name=flow_name)
    reasons.append(task_reason)
    task_valid, task_validation_reason = _valid_task_input(upgraded)
    if not task_valid:
        reasons.append(f"formatter_skipped_{task_validation_reason}")
        return dict(definition), tuple(reasons)
    formatter_changed, formatter_reason = _upgrade_terminal_formatter(
        upgraded,
        extraction_agent_ids=extraction_agent_ids,
    )
    reasons.append(formatter_reason)
    if not task_changed and not formatter_changed:
        return dict(definition), tuple(reasons)
    candidate_valid, candidate_reason = _valid_transformed_definition(upgraded)
    if not candidate_valid:
        reasons.append(f"candidate_rejected_{candidate_reason}")
        return dict(definition), tuple(reasons)
    return upgraded, tuple(reasons)


def _visible_extraction_ids_by_user(bind: sa.engine.Connection) -> dict[int, set[str]]:
    agent_rows = bind.execute(
        sa.text(
            """
            SELECT agent_key, user_id, visibility, project_id
            FROM agents
            WHERE is_active = true
              AND lower(
                    coalesce(category, '') || ' ' ||
                    coalesce(to_jsonb(agents)->>'subcategory', '')
                  ) LIKE '%extract%'
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
        for value in bind.execute(sa.text("SELECT DISTINCT user_id FROM curation_flows")).scalars()
    ]
    visible: dict[int, set[str]] = {}
    for user_id in user_ids:
        keys: set[str] = set()
        for row in agent_rows:
            visibility = str(row["visibility"] or "")
            if visibility == "system":
                keys.add(str(row["agent_key"]))
            elif visibility == "private" and row["user_id"] == user_id:
                keys.add(str(row["agent_key"]))
            elif (
                visibility == "project"
                and row["project_id"] is not None
                and str(row["project_id"]) in projects_by_user[user_id]
            ):
                keys.add(str(row["agent_key"]))
        visible[user_id] = keys
    return visible


def _migrate(bind: sa.engine.Connection) -> Counter[str]:
    bind.execute(
        sa.text(
            "SET LOCAL application_name = "
            "'alembic:c5d6e7f8a9b0:legacy-flow-graphs'"
        )
    )
    audit_trigger_enabled = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgrelid = 'curation_flows'::regclass
                  AND tgname = 'audit_curation_flows_update'
                  AND NOT tgisinternal
                  AND tgenabled IN ('O', 'A')
            )
            """
        )
    ).scalar_one()
    if not audit_trigger_enabled:
        raise RuntimeError(
            "Refusing legacy flow migration because audit_curation_flows_update "
            "is absent or disabled"
        )
    extraction_ids_by_user = _visible_extraction_ids_by_user(bind)
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
    counts: Counter[str] = Counter()
    skipped: list[dict[str, Any]] = []
    update_statement = sa.text(
        """
        UPDATE curation_flows
        SET flow_definition = :new_definition
        WHERE id = :flow_id
          AND flow_definition = :old_definition
        """
    ).bindparams(
        sa.bindparam("new_definition", type_=JSONB),
        sa.bindparam("old_definition", type_=JSONB),
    )

    for row in rows:
        old_definition = row["flow_definition"]
        counts["total"] += 1
        if not isinstance(old_definition, Mapping):
            counts["skipped_non_object"] += 1
            skipped.append(
                {
                    "flow_id": str(row["id"]),
                    "is_active": bool(row["is_active"]),
                    "reasons": ["skipped_non_object"],
                }
            )
            continue
        new_definition, reasons = _classify_and_upgrade(
            old_definition,
            flow_name=str(row["name"] or ""),
            extraction_agent_ids=extraction_ids_by_user.get(int(row["user_id"]), set()),
        )
        for reason in reasons:
            counts[reason] += 1
        if new_definition == old_definition:
            if any(
                "skipped" in reason or "rejected" in reason
                for reason in reasons
            ):
                skipped.append(
                    {
                        "flow_id": str(row["id"]),
                        "is_active": bool(row["is_active"]),
                        "reasons": list(reasons),
                    }
                )
            continue
        result = bind.execute(
            update_statement,
            {
                "flow_id": row["id"],
                "old_definition": dict(old_definition),
                "new_definition": new_definition,
            },
        )
        if result.rowcount != 1:
            counts["concurrent_change"] += 1
            continue
        counts["updated"] += 1
        counts["updated_active" if row["is_active"] else "updated_inactive"] += 1

    print("LEGACY_FLOW_MIGRATION_SUMMARY=" + json.dumps(dict(sorted(counts.items()))))
    if skipped:
        print("LEGACY_FLOW_MANUAL_REVIEW=" + json.dumps(skipped, sort_keys=True))
    return counts


def upgrade() -> None:
    _migrate(op.get_bind())


def downgrade() -> None:
    """Forward-only normalization; audit_log retains exact pre-migration JSON."""
