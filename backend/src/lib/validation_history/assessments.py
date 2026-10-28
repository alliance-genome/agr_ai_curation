"""Explicit extraction-only uncertainty projected from immutable review decisions.

The model chooses the field and scientific explanation. These checks establish
ownership, stable location, and the plain-value contract; they do not judge it.
"""
from copy import deepcopy

from src.schemas.domain_envelope import FieldRef, ValidationFinding, ValidationFindingSeverity, parse_field_path
from src.schemas.validation_history import UnresolvedFieldAssessment
from . import store
from .curator_authority import anchor_for_path, anchored_path

FINDING_CODE = "validation_review.assessment_unresolved"


def bind_assessments(db, scope, artifact_id, envelope, entries, *, domain_pack, profile_context):
    """Bind model-provided paths to server-owned source identities before commit."""
    from .projection import source_envelope_in_transaction
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields, declared_spec_for, has_resolution_state

    if not any(entry.get("unresolved_fields") is not None for entry in entries):
        return entries
    source = source_envelope_in_transaction(db, scope, artifact_id, envelope)
    objects = {obj.metadata["validation_history"]["target_id"]: obj for obj in source.extracted_objects}
    bound = deepcopy(entries)
    for entry in bound:
        assessments = entry.get("unresolved_fields")
        if assessments is None:
            continue
        if entry["action"] != "accept" or entry.get("subject") != "baseline":
            raise ValueError("Field uncertainty requires extraction-only acceptance")
        if not assessments:
            raise ValueError("Unresolved fields must be nonempty")
        obj = objects.get(entry["target_id"])
        if obj is None:
            raise store.HistoryConflict("Field assessment must name its extraction record")
        refs = obj.metadata["validation_history"]
        paths = set()
        for index, raw in enumerate(assessments):
            assessment = UnresolvedFieldAssessment.model_validate(raw)
            path = assessment.field_path
            if path in paths:
                raise ValueError("Unresolved fields must be distinct")
            paths.add(path)
            parts = parse_field_path(path)
            value = obj.payload
            specs = declared_resolvable_fields(domain_pack.metadata, obj.object_type)
            try:
                for length, part in enumerate(parts):
                    if declared_spec_for(specs, parts[:length]) is not None or has_resolution_state(value):
                        raise ValueError("Structured identities retain their validator resolution contract")
                    if isinstance(part, int):
                        if not isinstance(value, list):
                            raise store.HistoryConflict("Assessment index must address a collection")
                        value = value[part]
                    else:
                        if not isinstance(value, dict):
                            raise store.HistoryConflict("Assessment key must address an object")
                        value = value[part]
            except (KeyError, IndexError, TypeError) as exc:
                raise store.HistoryConflict("Assessment field does not exist in the source record") from exc
            if declared_spec_for(specs, parts) is not None:
                raise ValueError("Structured identities retain their validator resolution contract")
            if profile_context is not None and profile_context.profile.resolvable_container(path) is not None:
                raise ValueError("Structured identities retain their validator resolution contract")
            if isinstance(value, (dict, list)):
                raise ValueError("An assessment must identify an individual plain field")
            assessments[index] = {
                **assessment.model_dump(mode="json"),
                "object_target_id": refs["target_id"],
                "anchor": anchor_for_path(refs, path),
            }
    return bound


def project_assessments(db, artifact_id, envelope, manifest):
    """Derive only current, eligible selections; old decisions remain in history."""
    from src.lib.domain_packs.validation_findings import append_validation_findings_to_envelope

    working = envelope.model_copy(update={"validation_findings": [
        finding for finding in envelope.validation_findings if finding.code != FINDING_CODE
    ]})
    objects = {obj.metadata["validation_history"]["target_id"]: obj for obj in working.extracted_objects}
    findings = []
    decisions = {}
    for unit in manifest["units"]:
        if (not unit["eligible"] or unit["selection_id"] is None
                or (unit["selected"] != "baseline" and unit.get("applicability_ref") is None)):
            continue
        selection = store._fact(db, artifact_id, unit["selection_id"], kind="selection")
        if selection.parent_id not in decisions:
            decision = store._fact(db, artifact_id, selection.parent_id, kind="decision")
            decisions[decision.id] = store._content(db, decision)
        body = decisions[selection.parent_id]
        entry = next(item for item in body["entries"] if item["target_id"] == unit["target_id"])
        if unit.get("applicability_ref") is not None:
            if entry.get("absence_assessment") != "unresolved":
                continue
            source = store._fact(db, artifact_id, entry["subject"], kind="validation_source_input")
            binding_fact = store._fact(db, artifact_id, source.target_id, kind="target")
            binding = store._content(db, binding_fact)
            obj = objects.get(binding.get("object_target"))
            anchor = binding.get("absent_anchor")
            if obj is not None and anchor is None:
                stable_path = binding.get("stable_path", [])
                anchor = {"target_id": stable_path[-1] if stable_path else binding["object_target"], "suffix": []}
            path = anchored_path(obj.metadata["validation_history"], anchor) if obj is not None else None
            findings.append(ValidationFinding(
                finding_id=f"review-absence:{selection.parent_id}:{unit['target_id']}",
                severity=ValidationFindingSeverity.WARNING, code=FINDING_CODE, message=entry["reason"],
                object_ref=obj.to_object_ref() if obj is not None else None,
                field_ref=FieldRef(object_ref=obj.to_object_ref(), field_path=path) if obj is not None and path else None,
                details={"review_assessment": {
                    "artifact_id": artifact_id, "decision_id": selection.parent_id,
                    "selection_id": selection.id, "actor": body["actor"],
                    "source": entry["subject"], "applicability_ref": unit["applicability_ref"],
                    "absence_assessment": "unresolved", "anchor": anchor,
                    "object_target_id": binding.get("object_target"),
                }},
            ))
            continue
        for assessment in entry.get("unresolved_fields") or []:
            obj = objects[assessment["object_target_id"]]
            path = anchored_path(obj.metadata["validation_history"], assessment["anchor"])
            findings.append(ValidationFinding(
                finding_id=f"review-assessment:{selection.parent_id}:{store.canonical(assessment['anchor'])[0]}",
                severity=ValidationFindingSeverity.WARNING, code=FINDING_CODE, message=assessment["explanation"],
                field_ref=FieldRef(object_ref=obj.to_object_ref(), field_path=path),
                details={
                    "review_assessment": {
                        "artifact_id": artifact_id, "decision_id": selection.parent_id,
                        "selection_id": selection.id, "actor": body["actor"],
                        "source": entry["subject"], "anchor": assessment["anchor"],
                        "source_selection": obj.metadata.get("selected_extractor_contribution"),
                        "object_target_id": assessment["object_target_id"],
                    },
                },
            ))
    # Ledger decisions are the audit facts; replay must not append duplicate events.
    result, _ = append_validation_findings_to_envelope(working, findings)
    return result.model_copy(update={"history": envelope.history})


def open_assessments(envelope, obj, path, *, descendants=False, include_settled=False):
    """Match exact fields, or explicit editor scope; never neighboring fields."""
    parts = parse_field_path(path)
    return [finding for finding in envelope.validation_findings
            if finding.code == FINDING_CODE and (include_settled or finding.status.value == "open")
            and finding.field_ref is not None
            and finding.field_ref.object_ref.ref_key() in obj.ref_keys()
            and (finding.field_ref.field_path == path or (descendants
                 and parse_field_path(finding.field_ref.field_path)[:len(parts)] == parts))]


def settle_assessments(envelope, finding_ids):
    from src.schemas.domain_envelope import ValidationFindingStatus
    return envelope.model_copy(update={"validation_findings": [
        finding.model_copy(update={"status": ValidationFindingStatus.RESOLVED})
        if finding.finding_id in finding_ids else finding
        for finding in envelope.validation_findings
    ]})
