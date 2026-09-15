"""Read-only, same-envelope validation summaries on declared source objects.

No label matching, cross-result joins, row expansion, or validation/writeback.
Package metadata declares which object types and validator values are exposed.
"""
from copy import deepcopy
from typing import Any, Mapping


def summary_fields(object_type: str, config: dict) -> list[dict]:
    fields = {
        "status": ("Recorded lookup status (not acceptance)", "string"),
        "finding_statuses": ("Recorded finding statuses", "list"),
        "finding_ids": ("Source validation finding IDs", "list"),
        "results": ("Source validation results", "list"),
        "notes": ("Validation and linkage notes", "list"),
        "evidence": ("Source-linked evidence", "list"),
    }
    fields.update({f"resolved.{key}": (label, "string")
                   for key, label in config["resolved_fields"].items()})
    return [{"ref": f"object.summary.{object_type}.{key}", "label": label,
             "value_type": kind, "object_type": object_type,
             "summary_key": key, "summary_config": deepcopy(config),
             "group": f"{object_type} source-linked summary"}
            for key, (label, kind) in fields.items()]


def _matches(ref: Mapping, item: Mapping) -> bool:
    """Typed IDs only, with both identity components enforced when supplied."""
    return bool(ref.get("object_type") == item.get("object_type")
                and (ref.get("object_id") or ref.get("pending_ref_id"))
                and all(not ref.get(key) or ref[key] == item.get(key)
                        for key in ("object_id", "pending_ref_id")))


def _unique(values: list) -> list:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def object_validation_summary(item: Mapping, items: list, findings: list,
                              config: dict, domain_pack_id: str) -> dict[str, Any]:
    """Keep every source row; ambiguous/mixed findings never supply a flat ID."""
    notes, linked = [], []
    own_ref = {key: item.get(key) for key in ("object_type", "object_id", "pending_ref_id")}
    unique_identity = sum(_matches(own_ref, other) for other in items) == 1
    if not unique_identity:
        notes.append("Missing or ambiguous source object identity; validation enrichment withheld.")
    conflict = not unique_identity
    for finding in findings if unique_identity else []:
        field_ref = finding.get("field_ref") or {}
        ref = field_ref.get("object_ref") or finding.get("object_ref") or {}
        result = (finding.get("details") or {}).get("validation_result") or {}
        target = result.get("target") or {}
        # Canonical targets use object_id for the effective durable-or-pending ID.
        target_matches = (target.get("object_type") == item.get("object_type")
                          and target.get("object_id") in
                          [value for value in (item.get("object_id"), item.get("pending_ref_id")) if value]
                          and target.get("domain_pack_id") == domain_pack_id)
        if ref:
            if not _matches(ref, item):
                continue
            if sum(_matches(ref, other) for other in items) != 1:
                conflict = True
                notes.append("Ambiguous finding object reference; enrichment withheld.")
                continue
        elif not target_matches:
            continue
        elif sum(other.get("object_type") == target.get("object_type")
                 and target.get("object_id") in (other.get("object_id"), other.get("pending_ref_id"))
                 for other in items) != 1:
            conflict = True
            notes.append("Ambiguous validation target; enrichment withheld.")
            continue
        if result and (not target_matches or result.get("validator_binding_id") != config["binding_id"]):
            conflict = True
            notes.append("Contradictory validation target or binding; enrichment withheld.")
        linked.append({"finding_id": finding.get("finding_id"), "status": finding.get("status"),
                       "object_ref": deepcopy(ref), "field_path": field_ref.get("field_path"),
                       "result": {key: deepcopy(result[key]) for key in (
                           "status", "request_id", "validator_binding_id", "target",
                           "resolved_values", "explanation", "curator_message",
                       ) if key in result}})
        if finding.get("message"):
            notes.append(finding["message"])
    statuses = _unique([entry["result"].get("status") or entry["status"] for entry in linked])
    status = statuses[0] if len(statuses) == 1 else "mixed" if statuses else "not_recorded"
    if not linked:
        notes.append("No uniquely linked validation finding recorded for this source object.")
    resolved = {}
    for key in config["resolved_fields"]:
        values = _unique([(entry["result"].get("resolved_values") or {}).get(key) for entry in linked])
        resolved[key] = (values[0] if status == "resolved" and len(values) == 1
                         and isinstance(values[0], str) and not conflict else None)
        if len(values) > 1 and status == "resolved":
            conflict = True
    if conflict:
        resolved = dict.fromkeys(resolved)
        status = "ambiguous"
        notes.append("Validation values withheld; inspect the source results for conflicting provenance or values.")

    evidence = []
    evidence_config = config["evidence"]
    for evidence_id in _unique(list(item.get("evidence_record_ids") or [])):
        matches = _unique([deepcopy(other.get("payload") or {}) for other in items
                           if other.get("object_type") == evidence_config["object_type"]
                           and (other.get("payload") or {}).get(evidence_config["id_field"]) == evidence_id])
        if len(matches) == 1:
            evidence.append(matches[0])
        else:
            notes.append(f"Evidence {evidence_id}: {'missing' if not matches else 'ambiguous'} saved record; content withheld.")
    return {"status": status, "finding_statuses": _unique([entry["status"] for entry in linked]),
            "finding_ids": _unique([entry["finding_id"] for entry in linked]),
            "results": linked, "notes": _unique(notes), "evidence": evidence,
            **{f"resolved.{key}": value for key, value in resolved.items()}}


def populate_summary_fields(rows: list, items: list, findings: list,
                            fields: list, domain_pack_id: str) -> None:
    summaries = [field for field in fields if "summary_key" in field]
    for row, item in zip(rows, items):
        cache = {}
        for field in summaries:
            object_type = field["object_type"]
            if item.get("object_type") != object_type:
                row[field["ref"]] = None
                continue
            if object_type not in cache:
                cache[object_type] = object_validation_summary(
                    item, items, findings, field["summary_config"], domain_pack_id)
            row[field["ref"]] = cache[object_type][field["summary_key"]]
