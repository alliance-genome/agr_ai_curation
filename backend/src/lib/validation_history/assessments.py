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


def _assessable_paths(obj, specs, profile_context):
    """Every plain field the binding check below accepts, written as it must be sent."""
    from src.lib.domain_packs.resolvable_values import _format_path, declared_spec_for, has_resolution_state

    paths = []

    def walk(value, tokens):
        if declared_spec_for(specs, tokens) is not None or has_resolution_state(value):
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, (*tokens, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*tokens, index))
        elif tokens:
            path = _format_path(tokens)
            try:
                addressable = parse_field_path(path) == tokens
            except ValueError:
                addressable = False  # A key no field path can name.
            if addressable and (profile_context is None
                                or profile_context.profile.resolvable_container(path) is None):
                paths.append(path)

    walk(obj.payload, ())
    return paths


def _bind_one(obj, path, specs, profile_context):
    """Why an assessed path cannot be used, and whether that is a source conflict; None if usable."""
    from src.lib.domain_packs.resolvable_values import declared_spec_for, has_resolution_state

    try:
        parts = parse_field_path(path)
    except ValueError as error:
        return f"is not a valid field path: {error}", False
    value = obj.payload
    for length, part in enumerate(parts):
        if declared_spec_for(specs, parts[:length]) is not None or has_resolution_state(value):
            return "is a structured identity that keeps its validator resolution contract", False
        if isinstance(part, int):
            if not isinstance(value, list):
                return "uses an index on a value that is not a collection", True
            if part >= len(value):
                return "does not exist in the source record", True
        elif not isinstance(value, dict):
            return "uses a key on a value that is not an object", True
        elif part not in value:
            return "does not exist in the source record", True
        value = value[part]
    if declared_spec_for(specs, parts) is not None or (
            profile_context is not None and profile_context.profile.resolvable_container(path) is not None):
        return "is a structured identity that keeps its validator resolution contract", False
    if isinstance(value, (dict, list)):
        return "is not an individual plain field", False
    return None


def refusal(problems, usable, *, names=None):
    """One refusal naming every unusable entry, and each affected record's usable fields once.

    Entries are named by position unless the caller supplies its own names, such
    as review packet handles. Field lists are cut, and the cut stated, to keep the
    message within the configured review response size. A problem that once
    meant the source changed keeps its conflict type; the rest stay invalid input.
    """
    from src.lib.openai_agents.config import get_validation_review_limits

    def label(index):
        return (names or {}).get(index) or f"entry {index + 1}"

    message = "Nothing in this call was saved. " + "; ".join(
        f"{label(index)}: {text}" for index, text, _ in problems) + "."
    limit = get_validation_review_limits()["packet_bytes"]
    def cut(count):
        return f" ({count} more not listed; this message was cut to fit the review response size limit)"

    for indexes, paths in usable.values():
        prefix = " Fields that can be assessed for " + " and ".join(label(index) for index in indexes) + ": "
        listed = []
        for path in paths:
            if len(message) + len(prefix) + len(", ".join([*listed, path])) + len(cut(len(paths))) + 1 > limit:
                break
            listed.append(path)
        message += prefix + (", ".join(listed) if listed else "none listed")
        if len(listed) < len(paths):
            message += cut(len(paths) - len(listed))
        message += "."
    error = (store.HistoryConflict if any(conflict for *_, conflict in problems) else ValueError)(message)
    error.assessment_refusal = (problems, usable)
    return error


def bind_assessments(db, scope, artifact_id, envelope, entries, *, domain_pack, profile_context):
    """Bind model-provided paths to server-owned source identities before commit.

    Every entry is checked before anything is refused, so one refusal names each
    unusable entry and path, with the plain fields of each affected record that
    can be assessed. Which field was meant stays the model's choice.
    """
    from .projection import source_envelope_in_transaction
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    if not any(entry.get("unresolved_fields") is not None for entry in entries):
        return entries
    source = source_envelope_in_transaction(db, scope, artifact_id, envelope)
    objects = {obj.metadata["validation_history"]["target_id"]: obj for obj in source.extracted_objects}
    bound = deepcopy(entries)
    problems, usable = [], {}
    for position, entry in enumerate(bound):
        assessments = entry.get("unresolved_fields")
        if assessments is None:
            continue
        if entry["action"] != "accept" or entry.get("subject") != "baseline":
            problems.append((position, "field uncertainty requires extraction-only acceptance", False))
            continue
        if not assessments:
            problems.append((position, "unresolved fields must be nonempty", False))
            continue
        obj = objects.get(entry["target_id"])
        if obj is None:
            problems.append((position, "field assessment must name its extraction record", True))
            continue
        refs = obj.metadata["validation_history"]
        specs = declared_resolvable_fields(domain_pack.metadata, obj.object_type)
        failed, paths = False, set()
        for index, raw in enumerate(assessments):
            assessment = UnresolvedFieldAssessment.model_validate(raw)
            path = assessment.field_path
            quoted = '"' + path + '"'
            if path in paths:
                problems.append((position, f"unresolved field {quoted} is listed more than once", False))
                failed = True
                continue
            paths.add(path)
            problem = _bind_one(obj, path, specs, profile_context)
            if problem is not None:
                problems.append((position, f"unresolved field {quoted} {problem[0]}", problem[1]))
                failed = True
                continue
            assessments[index] = {
                **assessment.model_dump(mode="json"),
                "object_target_id": refs["target_id"],
                "anchor": anchor_for_path(refs, path),
            }
        if failed:
            if entry["target_id"] not in usable:
                usable[entry["target_id"]] = ([], _assessable_paths(obj, specs, profile_context))
            usable[entry["target_id"]][0].append(position)
    if problems:
        raise refusal(problems, usable)
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
