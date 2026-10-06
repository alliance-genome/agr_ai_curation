"""Moving a flow's selected-fields layouts when the steps they read get new pins.

Used by the retired-model conversion and by flow import. A layout follows its source
only if it was current under the source's old receipt; otherwise its owner must
choose the output fields again.
"""

from typing import Any

from src.lib.flows.export_fields import catalog_fingerprint
from src.schemas.flows import FlowDefinition


def move_layouts(definition: FlowDefinition, catalogs: dict[str, dict],
                 old_receipts: dict[str, dict]) -> list[dict[str, Any]]:
    """Selected-fields layouts follow a re-pinned source only if they were current before."""
    moved = []
    for node in definition.nodes:
        plan = node.data.projection_plan
        if not isinstance(plan, dict) or plan.get("selection_mode") != "selected_fields":
            continue
        for source in plan.get("selected_sources") or []:
            source_id = source.get("node_id")
            if source_id not in old_receipts:
                continue
            catalog = catalogs.get(source_id) or {}
            # A copy declares exactly its source's output, so under the old receipt
            # the same fields give the fingerprint a current layout recorded.
            before = catalog_fingerprint(catalog.get("fields") or [], old_receipts[source_id])
            if not catalog.get("fields") or source.get("schema_fingerprint") != before:
                raise ValueError(f"file output step {node.id} chose its fields from an earlier version of "
                                 f"step {source_id}; its owner must choose the output fields again")
            moved.append({"output_node_id": node.id, "source_node_id": source_id,
                          "from": source["schema_fingerprint"], "to": catalog["schema_fingerprint"]})
            source["schema_fingerprint"] = catalog["schema_fingerprint"]
    return moved
