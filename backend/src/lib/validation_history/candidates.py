"""Retain normalized validator proposals without publishing their values."""
from copy import deepcopy

from src.lib.domain_packs.materialization import ValidatorResultMaterializationResult
from src.lib.domain_packs.validator_result_records import retain_validator_original
from . import store


def _record_recipe(scope, artifact_id, item, source_envelope_revision):
    with store.SessionLocal.begin() as db:
        store._artifact(db, artifact_id, scope)
        reference = item.result._history_ref
        if reference is None or reference["artifact_id"] != artifact_id:
            raise store.HistoryConflict("Candidate requires its own immutable completion")
        completion = store._fact(db, artifact_id, reference["fact_id"])
        if completion.kind == "scheduling":
            return
        if completion.kind != "completion":
            raise store.HistoryConflict("Candidate is not a completion")
        if completion.body_sha != reference["body_sha256"]:
            raise store.HistoryConflict("Candidate completion hash mismatch")
        attempt = store._fact(db, artifact_id, completion.parent_id, kind="attempt")
        frozen = store._content(db, attempt)["input"]["request"]
        actual = item.request.model_dump(mode="json")
        allowed = {**frozen["expected_result_fields"], **(frozen.get("optional_result_fields") or {})}
        if (any(actual[key] != value for key, value in frozen.items()
                if key != "expected_result_fields")
                or any(allowed.get(key) != value
                       for key, value in actual["expected_result_fields"].items())
                or any(actual["expected_result_fields"].get(key) != value
                       for key, value in frozen["expected_result_fields"].items())):
            raise store.HistoryConflict("Candidate request differs from frozen input")
        store._append(
            db, artifact_id, operation_id="candidate:" + completion.id,
            kind="candidate", parent_id=completion.id,
            body={
                "binding_fingerprint": store.canonical({
                    "binding": item.match.binding.raw,
                    "object_definition": item.match.object_definition.model_dump(mode="json")
                    if item.match.object_definition is not None else None,
                })[0],
                "request": actual,
                "result": item.result.model_dump(mode="json"),
                "history_ref": deepcopy(reference),
                "source_envelope_revision": source_envelope_revision,
            },
        )


def retain_candidates(envelope, items, *, scope, source_envelope_revision=None):
    """Retain proposals; source and previously selected values stay unchanged."""
    from src.lib.domain_packs.materialization import _finding_for_validator_result
    from src.lib.domain_packs.validation_findings import append_validation_findings_to_envelope
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    findings = []
    for item in items:
        _record_recipe(scope, artifact_id, item, source_envelope_revision)
        envelope, reference = retain_validator_original(
            envelope, result=item.result, request=item.request,
            source_envelope_revision=source_envelope_revision,
        )
        finding = _finding_for_validator_result(item, source_envelope_revision=source_envelope_revision)
        findings.append(finding.model_copy(update={"details": {
            **finding.details, "validation_result_ref": reference,
            "machine_review": "pending", "selected_value": False,
        }}))
    envelope, appended = append_validation_findings_to_envelope(
        envelope, findings, actor_id="validation_review_pending",
    )
    return ValidatorResultMaterializationResult(envelope, appended, ())
