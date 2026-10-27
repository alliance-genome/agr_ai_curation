"""Explicit repair of execution groups generated from descriptive attachments.

This is migration tooling, never an execution-time fallback. Unknown unbound
groups remain visible for operator investigation and strict runtime rejection.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any


def definition_digest(definition: Any) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class MetadataGroupRepair:
    definition: Any
    removed: tuple[dict, ...]
    unmatched: tuple[dict, ...]


def repair_metadata_groups(definition: Any) -> MetadataGroupRepair:
    """Remove only exact old-producer groups with same-node attachment proof."""
    updated = deepcopy(definition)
    removed, unmatched = [], []
    if not isinstance(updated, dict) or not isinstance(updated.get("nodes"), list):
        return MetadataGroupRepair(updated, (), ())
    for index, node in enumerate(updated["nodes"]):
        if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
            continue
        data = node["data"]
        groups, attachments = data.get("validation_groups"), data.get("validation_attachments")
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if not isinstance(group, dict) or group.get("binding_id") or group.get("validator_binding_id"):
                kept.append(group)
                continue
            receipt = {"node_index": index, "node_id": node.get("id"), "group": deepcopy(group)}
            matches = [a for a in attachments if isinstance(a, dict)
                       and a.get("attachment_id") == group.get("attachment_id")] if isinstance(attachments, list) else []
            expected = None
            if len(matches) == 1:
                attachment = matches[0]
                identity = attachment.get("attachment_id")
                if (isinstance(identity, str) and identity
                        and attachment.get("state") == "active"
                        and attachment.get("validator_binding_id") is None
                        and all(type(attachment.get(k)) is bool for k in
                                ("enabled", "required", "blocking", "allow_opt_out"))):
                    expected = {
                        "group_id": identity, "attachment_id": identity,
                        "state": "automatic" if attachment["enabled"] else "skipped",
                        "binding_id": None, "edge_id": None, "validator_node_id": None,
                        "replaces_attachment_id": None, "label": attachment.get("label") or None,
                        **{k: attachment[k] for k in ("required", "blocking", "allow_opt_out")},
                    }
            if expected is not None and definition_digest(group) == definition_digest(expected):
                removed.append(receipt)
            else:
                kept.append(group)
                unmatched.append(receipt)
        if len(kept) != len(groups):
            data["validation_groups"] = kept
    return MetadataGroupRepair(updated, tuple(removed), tuple(unmatched))


def migrate_metadata_groups(bind, *, apply: bool = False) -> dict:
    """Inventory all saved flows; caller owns transaction and commit/rollback."""
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import JSONB

    if apply:
        bind.execute(sa.text("LOCK TABLE curation_flows IN SHARE ROW EXCLUSIVE MODE"))
        enabled = bind.execute(sa.text("""
            SELECT EXISTS (SELECT 1 FROM pg_trigger
              WHERE tgrelid='curation_flows'::regclass
                AND tgname='audit_curation_flows_update'
                AND NOT tgisinternal AND tgenabled IN ('O','A'))
        """)).scalar_one()
        if not enabled:
            raise RuntimeError("Flow audit trigger must be enabled before metadata-group repair")
        bind.execute(sa.text("SET LOCAL application_name = 'migration:unbound-metadata-groups'"))
    rows = bind.execute(sa.text(
        "SELECT id, name, description, is_active, flow_definition FROM curation_flows ORDER BY id"
        + (" FOR UPDATE" if apply else "")
    )).mappings()
    statement = sa.text("""
        UPDATE curation_flows SET flow_definition=:updated, updated_at=now()
        WHERE id=:id AND flow_definition=:expected
    """).bindparams(sa.bindparam("updated", type_=JSONB), sa.bindparam("expected", type_=JSONB))
    reports = []
    total = 0
    for row in rows:
        total += 1
        before = row["flow_definition"]
        repair = repair_metadata_groups(before)
        if not repair.removed and not repair.unmatched:
            continue
        if repair.removed and apply:
            result = bind.execute(statement, {"id": row["id"], "expected": before, "updated": repair.definition})
            if result.rowcount != 1:
                raise RuntimeError("Saved flow changed during metadata-group repair")
            actual = bind.execute(sa.text("SELECT flow_definition FROM curation_flows WHERE id=:id"),
                                  {"id": row["id"]}).scalar_one()
            if actual != repair.definition or repair_metadata_groups(actual).removed:
                raise RuntimeError("Saved flow metadata-group repair verification failed")
        # Match saved_flows.flow_summary without loading agent/provider services
        # in migration tooling. Persistence tests compare against that API.
        identity = {"flow_id": str(row["id"]), "name": row["name"], "description": row["description"]}
        reports.append({"before_revision": "sha256:" + definition_digest({**identity, "definition": before}),
                        "after_revision": "sha256:" + definition_digest({**identity, "definition": repair.definition}),
                        "flow_id": str(row["id"]), "is_active": row["is_active"],
                        "before_sha256": definition_digest(before),
                        "after_sha256": definition_digest(repair.definition),
                        "removed": list(repair.removed), "unmatched": list(repair.unmatched),
                        "applied": bool(apply and repair.removed)})
    if apply:
        definitions = bind.execute(sa.text("SELECT flow_definition FROM curation_flows")).scalars()
        if any(repair_metadata_groups(definition).removed for definition in definitions):
            raise RuntimeError("Matched metadata groups remain after repair")
    return {"flow_count": total, "apply": apply, "flows": reports}
