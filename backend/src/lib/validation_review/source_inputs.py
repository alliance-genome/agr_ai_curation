"""Exact structural input versions for affected validator bindings."""
from copy import deepcopy

from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationFact
from .audit import append_fact


def latest(db, artifact_id, target_id):
    return db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "validation_source_input",
        ValidationFact.target_id == target_id).order_by(ValidationFact.sequence.desc()).limit(1))


def reference(fact):
    return {"fact_id": fact.id, "sha256": fact.body_sha} if fact is not None else None


def current(db, artifact_id, frozen):
    return frozen.get("source_input_ref") == reference(latest(db, artifact_id, frozen["target_id"]))


def absent_input(match, result, target_id, *, requires_prerequisites=None):
    """Freeze verified selector absence without fabricating a validator request."""
    if result.request is not None or result.findings or not result.absent_inputs:
        raise ValueError("An absence fact requires verified optional-input absence")
    if match.binding.validator_agent is None or match.binding.unrouted:
        raise ValueError("An absence fact requires a configured validator")
    content = {
        "binding_id": match.binding.binding_id,
        "runs_after": list(match.binding.runs_after),
        "requires_prerequisites": (bool(match.binding.runs_after) if requires_prerequisites is None else requires_prerequisites),
        "absent_inputs": list(result.absent_inputs),
        "validator_agent": match.binding.validator_agent.to_dict(),
        "selected_inputs": deepcopy(result.selected_inputs),
        "input_selectors": deepcopy(result.input_selectors),
        "evidence": deepcopy(result.evidence),
        "validation_guidance": (match.object_envelope.validation_guidance
                                if match.object_envelope is not None else None),
        "source_provenance": [],
    }
    if match.binding.route_value is not None:
        content["route_value"] = match.binding.route_value
    return {"target_id": target_id, "binding_id": match.binding.binding_id,
            "input": content, "request": None}


def unsupported_input(match, request, target_id, *, requires_prerequisites=None):
    """Freeze an actual configured preflight refusal, never a validator answer."""
    from src.lib.domain_packs.validator_dispatch import preflight_unresolved_validator_result

    result = preflight_unresolved_validator_result(request, policy=match.binding.preflight_policy)
    if result is None:
        return None
    content = {
        "binding_id": match.binding.binding_id,
        "runs_after": list(match.binding.runs_after),
        "requires_prerequisites": (bool(match.binding.runs_after) if requires_prerequisites is None else requires_prerequisites),
        "unsupported_preflight": {"policy": match.binding.preflight_policy, "explanation": result.explanation},
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "selected_inputs": deepcopy(request.selected_inputs),
        "input_selectors": deepcopy(request.input_selectors),
        "evidence": deepcopy(request.evidence),
        "validation_guidance": request.validation_guidance,
        "source_provenance": [],
    }
    if match.binding.route_value is not None:
        content["route_value"] = match.binding.route_value
    return {"target_id": target_id, "binding_id": match.binding.binding_id,
            "input": content, "request": None}


def record_initial(binding, frozen):
    """Initialize applicability without replacing a later selected-source fact."""
    from uuid import uuid4
    from src.models.sql.validation_history import ValidationArtifact

    with store.SessionLocal.begin() as db:
        store._artifact(db, binding.artifact_id, binding.scope)
        db.scalar(select(ValidationArtifact.id).where(
            ValidationArtifact.id == binding.artifact_id).with_for_update())
        target = frozen["target_id"]
        previous = latest(db, binding.artifact_id, target)
        if previous is not None and previous.parent_id is not None:
            # Source selection owns subsequent inputs; initial-plan replay cannot
            # replace them with the original request, even when both are unsupported.
            return reference(previous)
        update(db, binding.artifact_id, operation_id="applicability:" + str(uuid4()),
               before={}, after={target: frozen})
        return reference(latest(db, binding.artifact_id, target))


def is_applicability(body):
    """Only explicit absence or unsupported-preflight facts are reviewable."""
    content = body.get("input")
    return (body.get("request") is None and isinstance(content, dict)
            and bool(content.get("absent_inputs") or content.get("unsupported_preflight")))


def applicability_scientific_content(body):
    """Progress identity excludes prerequisite bookkeeping; stored history stays exact."""
    return {"input": {key: value for key, value in body["input"].items() if key != "prerequisites"},
            "lineage_generations": body["lineage_generations"]}


def require_applicability(db, artifact_id, unit_id, fact_id, *, scope, sha256=None):
    """Verify exact latest source identity and binding coverage, not its meaning."""
    unit = store._fact(db, artifact_id, unit_id, kind="target")
    store._require_active_target(db, artifact_id, unit_id)
    fact = store._fact(db, artifact_id, fact_id, kind="validation_source_input")
    body = store._content(db, fact)
    if not is_applicability(body) or (sha256 is not None and fact.body_sha != sha256):
        raise store.HistoryConflict("Review requires an exact applicability source fact")
    unit_body = store._content(db, unit)
    parent_id = unit.parent_id if unit_body.get("decision_unit") is not None else unit.id
    if (fact.target_id != parent_id or unit_body.get("binding_id") != body["input"]["binding_id"]
            or reference(latest(db, artifact_id, parent_id)) != reference(fact)):
        raise store.HistoryConflict("Applicability source is stale or outside this binding unit")
    if body["lineage_generations"] != store._target_lineage(db, artifact_id, parent_id):
        raise store.HistoryConflict("Applicability source lineage has changed")
    pins = body["input"].get("prerequisites", [])
    if body["input"]["requires_prerequisites"] and not pins:
        raise store.HistoryConflict("Applicability source is awaiting prerequisite review")
    store._prerequisites_current(db, scope, pins)
    return fact


def scientific_input(frozen):
    """Ignore mutable output addresses; include supplied evidence and provenance.

    Selectors are trusted configured code. This comparison never interprets the
    scientific content or decides whether a change is important.
    """
    request = frozen["request"]
    if request is None:
        if not is_applicability(frozen):
            raise ValueError("No-request inputs require a verified applicability marker")
        content = deepcopy(frozen["input"])
        if frozen.get("prerequisites"):
            content["prerequisites"] = deepcopy(frozen["prerequisites"])
        return content
    return {"binding_id": frozen["binding_id"],
            **({"requires_prerequisites": True, "prerequisite_targets": frozen.get("prerequisite_targets", [])}
               if frozen.get("requires_prerequisites") else {}),
            "validator_agent": request.get("validator_agent"),
            "selected_inputs": request.get("selected_inputs", {}),
            "input_selectors": request.get("input_selectors", {}),
            "evidence": request.get("evidence", []),
            "validation_guidance": request.get("validation_guidance"),
            "source_provenance": frozen.get("source_provenance", [])}


def update(db, artifact_id, *, operation_id, before, after, selection_id=None):
    """Both maps come from the same trusted binding resolver, never the model."""
    changed = []
    for target in before.keys() - after.keys():
        try:
            store._require_active_target(db, artifact_id, target)
        except store.HistoryConflict:
            continue  # Retirement already invalidates every descendant attempt.
        fact = append_fact(db, artifact_id, operation_id="source-input:" + operation_id + ":" + target,
            kind="validation_source_input", target_id=target, parent_id=selection_id,
            body={"input": None, "request": None})
        changed.append({"target_id": target, "reference": reference(fact), "runnable": False})
    for target, frozen in after.items():
        if target != frozen["target_id"]:
            raise store.HistoryConflict("Binding input map has a mismatched target")
        content = scientific_input(frozen)
        absence = is_applicability(frozen)
        lineage = store._target_lineage(db, artifact_id, target) if absence else None
        previous = latest(db, artifact_id, target)
        if previous is not None:
            previous_body = store._content(db, previous)
            if (previous_body["input"] == content
                    and (not absence or previous_body.get("lineage_generations") == lineage)):
                continue
        if (previous is None and not is_applicability(frozen) and target in before
                and scientific_input(before[target]) == content):
            continue
        fact = append_fact(db, artifact_id,
            operation_id="source-input:" + operation_id + ":" + target,
            kind="validation_source_input", target_id=target, parent_id=selection_id,
            body={"input": content, "request": None if absence else frozen,
                  **({"lineage_generations": lineage} if absence else {})})
        changed.append({"target_id": target, "reference": reference(fact)})
    return changed
