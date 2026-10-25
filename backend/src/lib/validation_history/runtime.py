"""Capture boundaries shared by extractor, packaged and attached validators."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from . import store


@dataclass(frozen=True)
class CapturedArtifact:
    scope: store.HistoryScope
    artifact_id: str


def capture_extraction(
    *,
    scope: store.HistoryScope,
    producer_id: str,
    typed: dict | None,
    envelope,
    provenance: dict,
    subject_kind: str = "extracted",
):
    """Capture before inline dispatch; metadata carries references, not full history."""
    if "validation_history" in envelope.metadata or any(
        "validation_history" in obj.metadata for obj in envelope.extracted_objects
    ):
        raise store.HistoryConflict("New extraction already carries a history identity")
    artifact_id = store.capture_baseline(
        scope,
        producer_id=producer_id,
        subject_kind=subject_kind,
        typed=typed,
        normalized=envelope.model_dump(mode="json"),
        provenance=provenance,
    )
    updated = envelope.model_copy(deep=True)
    root_target = store.register_target(
        scope,
        artifact_id,
        identity="envelope",
        original={"envelope_id": envelope.envelope_id, "baseline_target": True},
    )
    updated.metadata["validation_history"] = {
        "artifact_id": artifact_id,
        "session_id": scope.session_id,
        "target_id": root_target,
    }
    return _attach_object_targets(
        updated,
        scope,
        artifact_id,
        root_target,
        origin={"kind": subject_kind, "baseline_target": True},
    )


def _attach_object_targets(updated, scope, artifact_id, root_target, *, origin):
    # One transaction for all objects/fields/elements; no per-element auth round trips.
    with store.SessionLocal.begin() as db:
        store._artifact(db, artifact_id, scope)
        from sqlalchemy import select
        from src.models.sql.validation_history import ValidationArtifact

        db.scalar(
            select(ValidationArtifact.id)
            .where(ValidationArtifact.id == artifact_id)
            .with_for_update()
        )
        store._require_live_artifact(db, artifact_id)

        return attach_object_targets_in_transaction(
            db, updated, artifact_id, root_target, origin=origin,
        )


def attach_object_targets_in_transaction(db, updated, artifact_id, root_target, *, origin):
    """Attach immutable targets within the caller's locked publication transaction."""
    def register(identity, original, parent):
        return store.register_target_in_transaction(
            db,
            artifact_id,
            identity=identity,
            original=original,
            parent_target_id=parent,
        )

    for obj in updated.extracted_objects:
        if "validation_history" in obj.metadata:
            continue
        object_key = obj.object_id or obj.pending_ref_id
        if object_key is None:
            raise store.HistoryConflict(
                "Extraction object requires a stable source identity"
            )
        object_target = register(
            "object:" + object_key + (":" + origin["completion_id"] if "completion_id" in origin else ""),
            {
                "object_ref": object_key,
                "object_type": obj.object_type,
                "payload": obj.payload,
                "object_refs": [ref.model_dump(mode="json") for ref in obj.object_refs],
                "origin": origin,
            },
            root_target,
        )
        fields, elements = {}, {}

        def walk(value, path, parent):
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else key
                    tid = register(
                        f"field:{parent}:{key}",
                        {"initial_path": child_path, "value": child},
                        parent,
                    )
                    fields[child_path] = tid
                    walk(child, child_path, tid)
            elif isinstance(value, list):
                ids = []
                for index, child in enumerate(value):
                    child_path = f"{path}[{index}]"
                    tid = register(
                        f"element:{parent}:{index}",
                        {
                            "object_target": object_target,
                            "initial_path": child_path,
                            "value": child,
                        },
                        parent,
                    )
                    ids.append(tid)
                    walk(child, child_path, tid)
                elements[path] = ids

        walk(obj.payload, "", object_target)
        obj.metadata["validation_history"] = {
            "target_id": object_target,
            "fields": fields,
            "elements": elements,
        }
    return updated


def bind_artifact(envelope, runtime_context) -> CapturedArtifact:
    if runtime_context is None:
        raise store.HistoryAccessError(
            "Validator capture requires authenticated runtime context"
        )
    ref = envelope.metadata.get("validation_history")
    if not isinstance(ref, dict) or not all(
        ref.get(key) for key in ("session_id", "artifact_id")
    ):
        raise store.HistoryAccessError(
            "Validation requires a captured extraction history"
        )
    scope = store.HistoryScope(
        runtime_context.user_id, runtime_context.document_id, ref["session_id"]
    )
    baseline = store.read_baseline(scope, ref["artifact_id"])
    if baseline["normalized"]["envelope_id"] != envelope.envelope_id:
        raise store.HistoryConflict(
            "Envelope history identity does not match its baseline"
        )
    return CapturedArtifact(scope, ref["artifact_id"])


def request_target(binding: CapturedArtifact, job) -> str:
    return binding_target(binding, job.match)


def binding_location(obj, field):
    """Resolve exact source ancestry plus an optional absent string-key suffix.

    Used by dispatch and selected-recipe replay. Missing list elements and
    present values without captured identities remain structural conflicts.
    """
    from src.schemas.domain_envelope import parse_field_path

    refs = obj.metadata["validation_history"]
    parts = parse_field_path(field) if field else ()
    stable = []
    prefix = ""
    current = obj.payload
    parent_target = refs["target_id"]
    for position, part in enumerate(parts):
        if isinstance(part, int):
            if not isinstance(current, list) or len(current) != len(
                refs["elements"].get(prefix, [])
            ):
                raise store.HistoryConflict(
                    "Collection mutation requires an explicit stable-identity update"
                )
            if part >= len(current) or part < 0:
                raise store.HistoryConflict("Collection target is no longer present")
            parent_target = refs["elements"][prefix][part]
            stable.append(parent_target)
            prefix += f"[{part}]"
        else:
            prefix = f"{prefix}.{part}" if prefix else part
            if prefix not in refs["fields"]:
                suffix = list(parts[position:])
                if ((current is None or isinstance(current, dict) and part not in current)
                        and all(isinstance(key, str) for key in suffix)):
                    # A configured scalar location can be absent in the source.
                    # It is a binding location, never a fabricated source value.
                    return stable, parent_target, {
                        "target_id": parent_target, "suffix": suffix,
                    }
                raise store.HistoryConflict(
                    "Field requires an explicit stable-identity update"
                )
            parent_target = refs["fields"][prefix]
            stable.append(parent_target)
        current = (
            current.get(part)
            if isinstance(current, dict)
            else current[part]
            if isinstance(current, list) and isinstance(part, int)
            else None
        )
    if isinstance(current, list) and len(current) != len(
        refs["elements"].get(prefix, [])
    ):
        raise store.HistoryConflict(
            "Collection mutation requires an explicit stable-identity update"
        )
    return stable, parent_target, None


def reconcile_absent_binding_targets(db, scope, artifact_id, objects, *, operation_id, actor, reason):
    """Retire obsolete absence locations inside an authorized source mutation.

    This changes structural coverage only: it never approves a missing value or
    changes the immutable extraction. Unrelated edits retain the same location.
    """
    from sqlalchemy import select
    from src.models.sql.validation_history import ValidationFact
    from .curator_authority import anchored_path

    store._artifact(db, artifact_id, scope)
    by_root = {obj.metadata["validation_history"]["target_id"]: obj for obj in objects}
    entries = []
    for fact in db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target",
            ValidationFact.operation_id.startswith("target:binding:"))
            .order_by(ValidationFact.sequence)):
        body = store._content(db, fact)
        anchor = body.get("absent_anchor")
        obj = by_root.get(body.get("object_target"))
        if anchor is None or obj is None:
            continue
        state = store._current(db, artifact_id, fact.id)
        if not state["active"]:
            continue
        refs = obj.metadata["validation_history"]
        current_ids = {refs["target_id"], *refs["fields"].values(),
                       *(tid for ids in refs["elements"].values() for tid in ids)}
        same = False
        if anchor["target_id"] in current_ids:
            stable, _, current_anchor = binding_location(obj, anchored_path(refs, anchor))
            same = stable == body["stable_path"] and current_anchor == anchor
        if not same:
            entries.append({"target_id": fact.id, "expected_version": state["version"],
                            "action": "retire", "reason": reason})
    if entries:
        store.apply_decisions_in_transaction(db, scope, artifact_id,
            operation_id="absence-reconciliation:" + operation_id, actor=actor, entries=entries)


def binding_target(binding: CapturedArtifact, match, *, db=None) -> str:
    """Binding target identity pins stable element, never a mutable array position."""
    if db is None:
        with store.SessionLocal.begin() as session:
            return binding_target(binding, match, db=session)
    store._artifact(db, binding.artifact_id, binding.scope)

    obj = match.object_envelope
    if obj is None:
        root_target = match.envelope.metadata["validation_history"]["target_id"]
        return store.register_target_in_transaction(
            db,
            binding.artifact_id,
            identity="envelope-binding:" + match.binding.binding_id,
            original={"binding_id": match.binding.binding_id},
            parent_target_id=root_target,
        )
    refs = obj.metadata["validation_history"]
    original = store._fact(db, binding.artifact_id, refs["target_id"], kind="target")
    if store._content(db, original)["object_ref"] not in {
        obj.object_id,
        obj.pending_ref_id,
    }:
        raise store.HistoryConflict("Object identity does not match its history target")
    stable, parent_target, absent_anchor = binding_location(obj, match.field_path)
    previous = refs["target_id"]
    store._require_active_target(db, binding.artifact_id, previous)
    for target_id in stable:
        target = store._fact(db, binding.artifact_id, target_id, kind="target")
        if target.parent_id != previous:
            raise store.HistoryConflict("Binding source belongs to another parent")
        store._require_active_target(db, binding.artifact_id, target_id)
        previous = target_id
    location = {"absent_anchor": absent_anchor} if absent_anchor is not None else {}
    identity = store.canonical(
        {
            "object": refs["target_id"],
            "binding": match.binding.binding_id,
            "path": stable,
            **location,
        }
    )[0]
    predecessor = {}
    if absent_anchor is not None:
        # A location can disappear and recur. A retired incarnation must never
        # regain an earlier decision simply because the same suffix reappears.
        from sqlalchemy import select
        from src.models.sql.validation_history import ValidationFact
        while True:
            existing = db.scalar(select(ValidationFact).where(
                ValidationFact.artifact_id == binding.artifact_id,
                ValidationFact.operation_id == "target:binding:" + identity))
            if existing is None or store._current(db, binding.artifact_id, existing.id)["active"]:
                break
            predecessor = {"retired_predecessor": existing.id}
            identity = store.canonical({"location": identity, **predecessor})[0]
    return store.register_target_in_transaction(
        db,
        binding.artifact_id,
        identity="binding:" + identity,
        original={
            "object_target": refs["target_id"],
            "binding_id": match.binding.binding_id,
            "stable_path": stable,
            **location,
            **predecessor,
        },
        parent_target_id=parent_target,
    )


def frozen_job_requests(binding, jobs, *, db=None):
    """Shared immutable request shape for historical and admitted executions."""
    from .curator_authority import anchor_for_path
    if db is None:
        with store.SessionLocal.begin() as session:
            return frozen_job_requests(binding, jobs, db=session)
    from src.lib.validation_review.source_inputs import latest, reference
    return [
        {
            "target_id": target,
            "source_input_ref": reference(latest(db, binding.artifact_id, target)),
            "binding_id": j.request.validator_binding_id,
            "request": j.request.model_dump(mode="json"),
            "result_fields": [
                *j.request.target.expected_fields,
                *(j.request.target.optional_fields or []),
            ],
            "result_anchors": {slot: anchor_for_path(j.match.object_envelope.metadata["validation_history"], path)
                for slot, path in {**j.request.expected_result_fields, **(j.request.optional_result_fields or {})}.items()
                if j.match.object_envelope is not None and isinstance(path, str)},
            "decision_units": list(j.match.binding.decision_units),
            "dispatch_wave": j.history_wave,
            "review_episode_id": j.history_episode_id or j.match.envelope.metadata.get("review_episode_id"),
            "available_prior_completions": list(j.history_prior_completions),
            "prerequisites": list(j.history_prerequisites),
        }
        for j in jobs
        for target in [binding_target(binding, j.match, db=db)]
    ]


def begin_jobs(
    binding: CapturedArtifact, jobs, *, operation_id: str, receipt: dict
) -> dict:
    return store.begin_execution(
        binding.scope,
        binding.artifact_id,
        operation_id=operation_id,
        receipt=receipt,
        requests=frozen_job_requests(binding, jobs),
    )


def complete_jobs(binding: CapturedArtifact, invocation: dict, results) -> list[str]:
    refs = []
    for attempt, result in zip(invocation["attempt_ids"], results, strict=True):
        if result._execution_failure is not None:
            state = "failed"
            body = None
            diagnostics = {"failure_kind": result._execution_failure}
        else:
            state = "incomplete" if result.output_issues else "completed"
            body = (
                result._raw_output
                if result._raw_output is not None
                else result.model_dump(mode="json")
            )
            diagnostics = {}
        completion_id = store.complete_attempt(
            binding.scope,
            binding.artifact_id,
            attempt,
            state=state,
            result=body,
            diagnostics=diagnostics,
        )
        completion = store.read_fact(binding.scope, binding.artifact_id, completion_id)
        result._history_ref = {
            "artifact_id": binding.artifact_id,
            "session_id": binding.scope.session_id,
            "attempt_id": attempt,
            "fact_id": completion_id,
            "body_sha256": completion["sha256"],
            "execution_state": state,
            "sequence": completion["sequence"],
            "created_at": completion["created_at"],
        }
        refs.append(completion_id)
    return refs


class _HistoryCaptureError(RuntimeError):
    """Fixed-message audit failure safe for Sentry."""


def _sanitized_capture_error(error_type: str) -> _HistoryCaptureError:
    try:
        raise _HistoryCaptureError(
            f"Validation history write failed ({error_type})"
        ) from None
    except _HistoryCaptureError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def fail_jobs(
    binding: CapturedArtifact, invocation: dict, error: BaseException
) -> None:
    """Preserve an execution failure even if recording its terminal fact fails.

    A failed audit write leaves the committed attempt visibly started/unknown.
    Report that operational failure through Sentry without masking the original.
    """
    import asyncio
    from src.lib.observability.runtime import report_runtime_exception

    state = (
        "unknown"
        if isinstance(error, store.HistoryRecordingError)
        else "interrupted"
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
        else "failed"
    )
    for attempt in invocation["attempt_ids"]:
        try:
            store.complete_attempt(
                binding.scope,
                binding.artifact_id,
                attempt,
                state=state,
                result=None,
                diagnostics={"failure_type": type(error).__name__},
            )
        except Exception as capture_error:
            report_runtime_exception(
                _sanitized_capture_error(type(capture_error).__name__),
                component="validation_history",
                operation="record_execution_failure",
            )


def retire_element_projection(
    metadata: dict, field_path: str, *, payload: dict
) -> tuple[dict, str]:
    """Remove only the active index mapping; immutable target/content stay stored.

    Nested list paths are presentation addresses. Rebase them after removal while
    retaining the permanent identity of every surviving element.
    """
    from copy import deepcopy
    from src.schemas.domain_envelope import parse_field_path

    tokens = parse_field_path(field_path)
    if not tokens or not isinstance(tokens[-1], int):
        raise ValueError("Retirement must identify one collection element")

    def format_path(parts):
        path = ""
        for token in parts:
            path += (
                f"[{token}]"
                if isinstance(token, int)
                else (("." if path else "") + token)
            )
        return path

    parent = tuple(tokens[:-1])
    index = tokens[-1]
    updated = deepcopy(metadata)
    refs = updated["validation_history"]
    elements = refs["elements"]
    values = payload
    for part in parent:
        values = values[part]
    ids = elements.get(format_path(parent), [])
    if not isinstance(values, list) or len(ids) != len(values) or index >= len(ids):
        raise store.HistoryConflict(
            "Collection no longer matches its stable identity mapping"
        )
    retired = ids[index]
    new_elements = {}
    for path, ids in elements.items():
        path_tokens = list(parse_field_path(path))
        if tuple(path_tokens[: len(parent)]) == parent and len(path_tokens) > len(
            parent
        ):
            position = path_tokens[len(parent)]
            if isinstance(position, int):
                if position == index:
                    continue
                if position > index:
                    path_tokens[len(parent)] -= 1
        new_elements[format_path(path_tokens)] = ids
    new_elements[format_path(parent)].pop(index)
    refs["elements"] = new_elements
    fields = {}
    for path, tid in refs["fields"].items():
        parts = list(parse_field_path(path))
        if tuple(parts[: len(parent)]) == parent and len(parts) > len(parent):
            position = parts[len(parent)]
            if isinstance(position, int):
                if position == index:
                    continue
                if position > index:
                    parts[len(parent)] -= 1
        fields[format_path(parts)] = tid
    refs["fields"] = fields
    return updated, retired


def replace_projection_targets(before, after, *, replacement_path=None):
    """Plan source edits without IO; checkpoint commits new IDs and links atomically.

    Explicit identity replacements replace the entire declared value. Other
    curator source edits replace changed fields; equality only detects edits,
    never matches one list element to another.
    """
    from copy import deepcopy
    from uuid import uuid4
    from src.schemas.domain_envelope import parse_field_path

    refs = deepcopy(before.metadata["validation_history"])
    changes, additions = [], []

    def descendant(path, root):
        return (
            not root
            or path == root
            or path.startswith(root + ".")
            or path.startswith(root + "[")
        )

    def target_at(path):
        if not path:
            return refs["target_id"]
        parts = parse_field_path(path)
        if isinstance(parts[-1], int):
            prefix = path[: path.rfind("[")]
            ids = refs["elements"].get(prefix, [])
            if parts[-1] >= len(ids):
                raise store.HistoryConflict("Element identity is unavailable")
            return ids[parts[-1]]
        return refs["fields"].get(path)

    def set_target(path, tid):
        if not path:
            refs["target_id"] = tid
            return
        parts = parse_field_path(path)
        if isinstance(parts[-1], int):
            refs["elements"][path[: path.rfind("[")]][parts[-1]] = tid
        else:
            refs["fields"][path] = tid

    def walk_new(value, path, parent):
        if isinstance(value, dict):
            children = [
                (f"{path}.{key}" if path else key, child)
                for key, child in value.items()
            ]
        elif isinstance(value, list):
            children = [(f"{path}[{i}]", child) for i, child in enumerate(value)]
            refs["elements"][path] = []
        else:
            return
        for child_path, child in children:
            tid = str(uuid4())
            additions.append(
                {
                    "id": tid,
                    "parent_id": parent,
                    "original": {
                        "initial_path": child_path,
                        "value": deepcopy(child),
                        "origin": {"kind": "curator_authored"},
                    },
                }
            )
            if isinstance(value, list):
                refs["elements"][path].append(tid)
            else:
                refs["fields"][child_path] = tid
            walk_new(child, child_path, tid)

    def replace(path, value):
        old = target_at(path)
        if old is None:
            # New source field: no fictitious predecessor.
            parts = parse_field_path(path)
            parent_path = path.rsplit(".", 1)[0] if len(parts) > 1 else ""
            parent = target_at(parent_path) if parent_path else refs["target_id"]
            tid = str(uuid4())
            additions.append(
                {
                    "id": tid,
                    "parent_id": parent,
                    "original": {
                        "initial_path": path,
                        "value": deepcopy(value),
                        "origin": {"kind": "curator_authored"},
                    },
                }
            )
        else:
            tid = str(uuid4())
            original = {
                "initial_path": path,
                "value": deepcopy(value),
                "origin": {"kind": "curator_authored"},
            }
            if not path:
                original.update(
                    object_ref=after.object_id or after.pending_ref_id,
                    object_type=after.object_type,
                    payload=deepcopy(value),
                )
            changes.append(
                {
                    "target_id": old,
                    "replacement_target_id": tid,
                    "replacement_original": original,
                }
            )
        refs["fields"] = {
            p: t for p, t in refs["fields"].items() if not descendant(p, path)
        }
        refs["elements"] = {
            p: ids for p, ids in refs["elements"].items() if not descendant(p, path)
        }
        set_target(path, tid)
        walk_new(value, path, tid)

    def diff(old, new, path=""):
        if path == replacement_path:
            replace(path, new)
        elif old == new:
            return
        elif (
            isinstance(old, list)
            and isinstance(new, list)
            and len(old) == len(new)
            and replacement_path
            and replacement_path.startswith(path + "[")
        ):
            for index, (a, b) in enumerate(zip(old, new, strict=True)):
                diff(a, b, f"{path}[{index}]")
        elif isinstance(old, dict) and isinstance(new, dict):
            for key in old.keys() | new.keys():
                child_path = f"{path}.{key}" if path else key
                if key not in new:
                    tid = target_at(child_path)
                    if tid:
                        changes.append({"target_id": tid, "action": "retire"})
                    refs["fields"] = {
                        p: t
                        for p, t in refs["fields"].items()
                        if not descendant(p, child_path)
                    }
                    refs["elements"] = {
                        p: ids
                        for p, ids in refs["elements"].items()
                        if not descendant(p, child_path)
                    }
                elif key not in old:
                    replace(child_path, new[key])
                else:
                    diff(old[key], new[key], child_path)
        else:
            # Whole list assignment is an explicit replacement, never a guessed reorder.
            replace(path, new)

    diff(before.payload, after.payload)
    result = after.model_copy(deep=True)
    result.metadata["validation_history"] = refs
    return result, {"changes": changes, "additions": additions}


def record_match_scheduling(binding, match, outcome, *, details=None):
    from uuid import uuid4

    obj = match.object_envelope
    ref = (obj.metadata if obj is not None else match.envelope.metadata)[
        "validation_history"
    ]
    return store.record_scheduling(
        binding.scope,
        binding.artifact_id,
        operation_id="scheduling:" + str(uuid4()),
        outcome=outcome,
        details={
            "target_id": ref["target_id"],
            "binding_id": match.binding.binding_id,
            "target": match.target_details(),
            **(details or {}),
        },
    )


def record_preflight(
    binding: CapturedArtifact, job, result, *, operation_id: str
) -> None:
    target = request_target(binding, job)
    fact_id = store.record_scheduling(
        binding.scope,
        binding.artifact_id,
        operation_id=operation_id,
        outcome="unsupported_preflight",
        details={
            "target_id": target,
            "request": job.request.model_dump(mode="json"),
            "reason": result.explanation,
        },
    )
    fact = store.read_fact(binding.scope, binding.artifact_id, fact_id)
    result._history_ref = {
        "artifact_id": binding.artifact_id,
        "session_id": binding.scope.session_id,
        "fact_id": fact_id,
        "body_sha256": fact["sha256"],
        "execution_state": "not_executed",
        "sequence": fact["sequence"],
        "created_at": fact["created_at"],
    }


def attach_failed_completion(
    binding: CapturedArtifact, attempt_id: str, result
) -> None:
    fact = store.completion_for_attempt(binding.scope, binding.artifact_id, attempt_id)
    if fact["state"] not in {"failed", "interrupted", "unknown"}:
        raise store.HistoryConflict(
            "Failure projection cannot reference a scientific answer"
        )
    result._history_ref = {
        "artifact_id": binding.artifact_id,
        "session_id": binding.scope.session_id,
        "attempt_id": attempt_id,
        "fact_id": fact["id"],
        "body_sha256": fact["sha256"],
        "execution_state": fact["state"],
        "sequence": fact["sequence"],
        "created_at": fact["created_at"],
    }


def reorder_element_projection(
    domain_object, field_path: str, ordered_target_ids: list[str]
):
    """Reorder by permanent identity, never by equality of scientific payloads."""
    from copy import deepcopy
    from src.schemas.domain_envelope import parse_field_path

    def format_path(parts):
        path = ""
        for token in parts:
            path += (
                f"[{token}]"
                if isinstance(token, int)
                else (("." if path else "") + token)
            )
        return path

    updated = domain_object.model_copy(deep=True)
    refs = updated.metadata["validation_history"]
    current_ids = refs["elements"][field_path]
    if len(ordered_target_ids) != len(current_ids) or set(ordered_target_ids) != set(
        current_ids
    ):
        raise ValueError("Reordering must name each current element exactly once")
    tokens = tuple(parse_field_path(field_path))
    values = updated.payload
    for token in tokens:
        values = values[token]
    if len(values) != len(current_ids):
        raise store.HistoryConflict(
            "Current list no longer matches its stable identity mapping"
        )
    old_positions = {target: index for index, target in enumerate(current_ids)}
    new_positions = {
        old_positions[target]: index for index, target in enumerate(ordered_target_ids)
    }
    reordered = [
        deepcopy(values[old_positions[target]]) for target in ordered_target_ids
    ]
    values[:] = reordered
    remapped = {}
    for path, ids in refs["elements"].items():
        nested = list(parse_field_path(path))
        if (
            tuple(nested[: len(tokens)]) == tokens
            and len(nested) > len(tokens)
            and isinstance(nested[len(tokens)], int)
        ):
            nested[len(tokens)] = new_positions[nested[len(tokens)]]
        remapped[format_path(nested)] = ids
    remapped[field_path] = list(ordered_target_ids)
    refs["elements"] = remapped
    fields = {}
    for path, tid in refs["fields"].items():
        parts = list(parse_field_path(path))
        if (
            tuple(parts[: len(tokens)]) == tokens
            and len(parts) > len(tokens)
            and isinstance(parts[len(tokens)], int)
        ):
            parts[len(tokens)] = new_positions[parts[len(tokens)]]
        fields[format_path(parts)] = tid
    refs["fields"] = fields
    return updated


def require_captured_envelope(payload: dict, *, user_id: str, document_id: str) -> None:
    reference = (payload.get("metadata") or {}).get("validation_history")
    if not isinstance(reference, dict) or not all(
        reference.get(key) for key in ("session_id", "artifact_id")
    ):
        raise store.HistoryAccessError(
            "Persistence requires a captured extraction history"
        )
    baseline = store.read_baseline(
        store.HistoryScope(user_id, document_id, reference["session_id"]),
        reference["artifact_id"],
    )
    if baseline["normalized"]["envelope_id"] != payload["envelope_id"]:
        raise store.HistoryConflict(
            "Extraction projection does not match its baseline identity"
        )


def _check_collection_materialization(before, after):
    """Permit declared descendant writes, never treat whole-list assignment as identity."""
    from src.schemas.domain_envelope import parse_field_path

    allowed = []
    for key, path_key in (
        ("validator_resolved_value_materialization", "materialized_field_paths"),
        ("profile_validator_materialization", "field_paths"),
    ):
        prior_count = len(before.metadata.get(key, []))
        for event in after.metadata.get(key, [])[prior_count:]:
            allowed.extend(parse_field_path(path) for path in event.get(path_key, []))

    def walk(old, new, parts=()):
        if isinstance(old, list) or isinstance(new, list):
            if old == new:
                return
            if (
                not isinstance(old, list)
                or not isinstance(new, list)
                or len(old) != len(new)
            ):
                raise store.HistoryConflict(
                    "Collection materialization requires explicit stable identities"
                )
            for index, (a, b) in enumerate(zip(old, new, strict=True)):
                child = (*parts, index)
                if a != b and not any(
                    tuple(path[: len(child)]) == child
                    and (
                        len(path) > len(child)
                        or (
                            len(path) == len(child)
                            and not isinstance(a, (dict, list))
                            and not isinstance(b, (dict, list))
                        )
                    )
                    for path in allowed
                ):
                    raise store.HistoryConflict(
                        "Whole-element materialization requires explicit stable identities"
                    )
                walk(a, b, child)
        elif isinstance(old, dict) and isinstance(new, dict):
            for key in old.keys() | new.keys():
                walk(old.get(key), new.get(key), (*parts, key))

    walk(before.payload, after.payload)


def capture_curator_objects(binding: CapturedArtifact, envelope, *, actor_id: str):
    """Register new curator-authored objects without inventing extractor provenance."""
    if actor_id != binding.scope.owner_id:
        raise store.HistoryAccessError(
            "Curator contribution must use the authenticated owner"
        )
    return _attach_object_targets(
        envelope.model_copy(deep=True),
        binding.scope,
        binding.artifact_id,
        envelope.metadata["validation_history"]["target_id"],
        origin={"kind": "curator_authored", "actor_id": actor_id},
    )


# Context follows the actual logical execution through existing runner adapters;
# unrelated standalone lookups do not acquire fictional extraction obligations.
ACTIVE_VALIDATION_EXECUTION: ContextVar[tuple[CapturedArtifact, dict] | None] = ContextVar("active_validation_execution", default=None)


def record_runner_receipt(agent, *, model: str, input_payload: dict) -> None:
    active = ACTIVE_VALIDATION_EXECUTION.get()
    if active is None:
        return
    from dataclasses import asdict
    from src.lib.openai_agents.model_settings_contract import model_settings_contract
    from pydantic_core import to_jsonable_python
    from src.lib.context import get_current_trace_id
    from src.lib.openai_agents.run_history import ACTIVE_HISTORY

    binding, invocation = active
    instructions = getattr(agent, "instructions", None)
    settings = getattr(agent, "model_settings", None)
    run_history = ACTIVE_HISTORY.get()
    receipt = {
        "granularity": "agent_execution",
        "execution_receipt": getattr(agent, "execution_receipt", None),
        "model": model,
        "input": input_payload,
        "instructions": instructions if isinstance(instructions, str) else None,
        "instructions_available": isinstance(instructions, str),
        "model_settings": model_settings_contract(settings),
        "tools": [
            {
                "name": tool.name,
                "description": getattr(tool, "description", None),
                "parameters": getattr(tool, "params_json_schema", None),
            }
            for tool in getattr(agent, "tools", [])
        ],
        "trace_id": get_current_trace_id(),
        "parent_runtime_history": asdict(run_history.scope)
        if run_history is not None
        else None,
    }
    receipt = to_jsonable_python(receipt)
    try:
        with store.SessionLocal.begin() as db:
            store._artifact(db, binding.artifact_id, binding.scope)
            store._fact(
                db, binding.artifact_id, invocation["execution_id"], kind="execution"
            )
            store._append(
                db,
                binding.artifact_id,
                operation_id="runner-receipt:" + invocation["execution_id"] + ":" + store.canonical(receipt)[0],
                kind="runner_receipt",
                parent_id=invocation["execution_id"],
                body=receipt,
            )
    except Exception:
        try:
            raise store.HistoryRecordingError(
                "Validation execution receipt could not be recorded"
            ) from None
        except store.HistoryRecordingError as sanitized:
            sanitized.__context__ = None
            sanitized.__cause__ = None
            raise
