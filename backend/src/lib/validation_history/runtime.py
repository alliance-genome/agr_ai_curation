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
    for obj in updated.extracted_objects:
        if "validation_history" in obj.metadata:
            continue
        object_key = obj.object_id or obj.pending_ref_id
        if object_key is None:
            raise store.HistoryConflict(
                "Extraction object requires a stable source identity"
            )
        object_target = store.register_target(
            scope,
            artifact_id,
            identity="object:" + object_key,
            original={
                "object_ref": object_key,
                "object_type": obj.object_type,
                "payload": obj.payload,
                "origin": origin,
            },
            parent_target_id=root_target,
        )
        elements = {}

        def walk(value, path, parent_target):
            if isinstance(value, dict):
                for key, child in value.items():
                    walk(child, f"{path}.{key}" if path else key, parent_target)
            elif isinstance(value, list):
                ids = []
                for index, child in enumerate(value):
                    # Ordinal is only the initial producer idempotency key; identity
                    # thereafter is the persisted target ID, independent of content.
                    key = f"element:{object_target}:{path}:{index}"
                    tid = store.register_target(
                        scope,
                        artifact_id,
                        identity=key,
                        original={
                            "object_target": object_target,
                            "initial_path": f"{path}[{index}]",
                            "value": child,
                        },
                        parent_target_id=parent_target,
                    )
                    ids.append(tid)
                    walk(child, f"{path}[{index}]", tid)
                elements[path] = ids

        walk(obj.payload, "", object_target)
        obj.metadata["validation_history"] = {
            "target_id": object_target,
            "elements": elements,
        }
    return updated


def bind_artifact(envelope, runtime_context) -> CapturedArtifact:
    if runtime_context is None:
        raise store.HistoryAccessError(
            "Validator capture requires authenticated runtime context"
        )
    ref = envelope.metadata["validation_history"]
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
    """Binding target identity pins stable element, never a mutable array position."""
    from src.schemas.domain_envelope import parse_field_path

    obj = job.match.object_envelope
    if obj is None:
        root_target = job.match.envelope.metadata["validation_history"]["target_id"]
        return store.register_target(
            binding.scope,
            binding.artifact_id,
            identity="envelope-binding:" + job.request.validator_binding_id,
            original={"binding_id": job.request.validator_binding_id},
            parent_target_id=root_target,
        )
    refs = obj.metadata["validation_history"]
    original = store.read_fact(binding.scope, binding.artifact_id, refs["target_id"])
    if original["kind"] != "target" or original["content"]["object_ref"] not in {
        obj.object_id,
        obj.pending_ref_id,
    }:
        raise store.HistoryConflict("Object identity does not match its history target")
    field = job.request.target.field_path
    parts = parse_field_path(field) if field else ()
    stable = []
    prefix = ""
    current = obj.payload
    for part in parts:
        if isinstance(part, int):
            if not isinstance(current, list) or len(current) != len(
                refs["elements"][prefix]
            ):
                raise store.HistoryConflict(
                    "Collection mutation requires an explicit stable-identity update"
                )
            stable.append(refs["elements"][prefix][part])
            prefix += f"[{part}]"
        else:
            stable.append(part)
            prefix = f"{prefix}.{part}" if prefix else part
        current = (
            current.get(part)
            if isinstance(current, dict)
            else current[part]
            if isinstance(current, list)
            else None
        )
    identity = store.canonical(
        {
            "object": refs["target_id"],
            "binding": job.request.validator_binding_id,
            "path": stable,
        }
    )[0]
    return store.register_target(
        binding.scope,
        binding.artifact_id,
        identity="binding:" + identity,
        original={
            "object_target": refs["target_id"],
            "binding_id": job.request.validator_binding_id,
            "stable_path": stable,
        },
        parent_target_id=next(
            (
                part
                for part in reversed(stable)
                if part in {tid for ids in refs["elements"].values() for tid in ids}
            ),
            refs["target_id"],
        ),
    )


def begin_jobs(
    binding: CapturedArtifact, jobs, *, operation_id: str, receipt: dict
) -> dict:
    requests = [
        {
            "target_id": request_target(binding, j),
            "binding_id": j.request.validator_binding_id,
            "request": j.request.model_dump(mode="json"),
        }
        for j in jobs
    ]
    return store.begin_execution(
        binding.scope,
        binding.artifact_id,
        operation_id=operation_id,
        receipt=receipt,
        requests=requests,
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
            body = result.model_dump(mode="json")
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
        "interrupted"
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
                capture_error,
                component="validation_history",
                operation="record_execution_failure",
            )


def retire_element_projection(metadata: dict, field_path: str) -> tuple[dict, str]:
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
    retired = elements[format_path(parent)][index]
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
    return updated, retired


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
            "reason": result._execution_failure,
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
    return updated


def require_captured_envelope(payload: dict, *, user_id: str, document_id: str) -> None:
    reference = payload["metadata"]["validation_history"]
    baseline = store.read_baseline(
        store.HistoryScope(user_id, document_id, reference["session_id"]),
        reference["artifact_id"],
    )
    if baseline["normalized"]["envelope_id"] != payload["envelope_id"]:
        raise store.HistoryConflict(
            "Extraction projection does not match its baseline identity"
        )


def capture_derived_objects(
    binding: CapturedArtifact, envelope, results, *, previous_envelope
):
    """Give newly materialized objects identities as validator contributions.

    This never labels the contribution an extraction or an accepted selection.
    Existing collection edits must use the explicit stable-identity APIs.
    """
    from copy import deepcopy

    envelope = envelope.model_copy(deep=True)
    previous_targets = {
        obj.object_id or obj.pending_ref_id: obj.metadata.get("validation_history")
        for obj in previous_envelope.extracted_objects
    }
    for obj in envelope.extracted_objects:
        prior = previous_targets.get(obj.object_id or obj.pending_ref_id)
        if "validation_history" not in obj.metadata and prior is not None:
            obj.metadata["validation_history"] = deepcopy(prior)
    if all("validation_history" in obj.metadata for obj in envelope.extracted_objects):
        return envelope
    contributions = [
        result._history_ref["fact_id"] for result in results if result._history_ref
    ]
    for fact_id in contributions:
        store.read_fact(binding.scope, binding.artifact_id, fact_id)
    if not contributions:
        raise store.HistoryConflict(
            "Derived objects require recorded source contributions"
        )
    root = envelope.metadata["validation_history"]["target_id"]
    return _attach_object_targets(
        envelope.model_copy(deep=True),
        binding.scope,
        binding.artifact_id,
        root,
        origin={"kind": "validator_contribution", "fact_ids": contributions},
    )


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
ACTIVE_VALIDATION_EXECUTION = ContextVar("active_validation_execution", default=None)


def record_runner_receipt(agent, *, model: str, input_payload: dict) -> None:
    active = ACTIVE_VALIDATION_EXECUTION.get()
    if active is None:
        return
    from dataclasses import asdict, is_dataclass
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
        "model_settings": asdict(settings) if is_dataclass(settings) else None,
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
    with store.SessionLocal.begin() as db:
        store._artifact(db, binding.artifact_id, binding.scope)
        store._fact(
            db, binding.artifact_id, invocation["execution_id"], kind="execution"
        )
        store._append(
            db,
            binding.artifact_id,
            operation_id="runner-receipt:" + invocation["execution_id"],
            kind="runner_receipt",
            parent_id=invocation["execution_id"],
            body=receipt,
        )
