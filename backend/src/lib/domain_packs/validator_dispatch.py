"""Active domain-pack validator binding dispatch.

This service owns execution for package-scoped active validator bindings.  It
keeps biological validation on the validator-dispatch path while reusing the
shared selector and envelope finding contracts.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, create_model

from src.schemas.domain_envelope import (
    DomainEnvelope,
    ValidationFinding,
)
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
    ValidatorLookupAttempt,
    is_domain_validator_result_schema,
)

from .input_selectors import build_domain_validation_request
from .materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from .registry import LoadedDomainPack
from .validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBinding,
    ValidatorBindingMatch,
)
from .validator_result_classification import (
    lookup_status_for_validator_outcome,
    validator_failure_classification,
)
from .validator_result_policies import allowed_term_policy_violations
from .value_presence import missing_resolved_value
from .validation_findings import append_validation_findings_to_envelope


LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_PARALLEL_VALIDATORS = 4
_VALIDATOR_DEDUPE_CONTEXT_INPUT_FIELDS = frozenset(
    {
        "evidence_quote",
        "verified_quote",
        "evidence_record_id",
    }
)


class DomainValidatorAgentRunner(Protocol):
    """Callable that executes one package-owned validator request."""

    def __call__(
        self,
        request: DomainValidationRequest,
        *,
        binding: ValidatorBinding,
    ) -> Any:
        """Return a structured validator result payload or SDK run result."""


class DomainValidatorBatchAgentRunner(Protocol):
    """Callable that executes one compatible batch of validator requests."""

    def __call__(
        self,
        jobs: list["_DispatchJob"],
        *,
        binding: ValidatorBinding,
    ) -> Any:
        """Return a structured batch payload with one result per job/request."""


class ValidatorDispatchEventEmitter(Protocol):
    """Callable used by chat streaming to surface validator dispatch events."""

    def __call__(self, event: dict[str, Any]) -> None:
        """Emit one validator dispatch event."""


@dataclass(frozen=True)
class ActiveValidatorDispatchResult:
    """Result of dispatching active validator bindings for one envelope."""

    envelope: DomainEnvelope
    registry: DomainPackValidationRegistry
    matched_bindings: tuple[ValidatorBindingMatch, ...]
    appended_findings: tuple[ValidationFinding, ...]
    validator_results: tuple[DomainValidatorResultBase, ...]
    validator_agent_run_count: int
    batch_validator_run_count: int
    validator_batch_groups: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _DispatchJob:
    match: ValidatorBindingMatch
    request: DomainValidationRequest


@dataclass(frozen=True)
class _ValidatorRunGroup:
    dedupe_group_indexes: tuple[int, ...]
    batch_key: str | None = None


@dataclass(frozen=True)
class _ValidatorRunGroupResult:
    dedupe_group_results: dict[int, list[DomainValidatorResultBase]]
    validator_agent_run_count: int
    batch_validator_run_count: int
    batch_summaries: tuple[dict[str, Any], ...] = ()


@dataclass
class _ValidatorFinalizationState:
    accepted_result: DomainValidatorResultBase | None = None
    accepted_results: tuple[DomainValidatorResultBase, ...] = ()


@dataclass(frozen=True)
class _ValidatorFinalizationFeedback:
    accepted_result: DomainValidatorResultBase | None
    message: str
    accepted_results: tuple[DomainValidatorResultBase, ...] = ()
    repair_instructions: tuple[str, ...] = ()
    result_errors: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _ValidatorAgentRunOutput:
    raw_output: Any
    accepted_result: DomainValidatorResultBase


@dataclass(frozen=True)
class _ValidatorBatchAgentRunOutput:
    raw_output: Any
    accepted_results: tuple[DomainValidatorResultBase, ...]


def dispatch_active_validator_bindings(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    *,
    actor_id: str = "domain_validator_dispatch",
    registry: DomainPackValidationRegistry | None = None,
    runner: DomainValidatorAgentRunner | None = None,
    batch_runner: DomainValidatorBatchAgentRunner | None = None,
    event_emitter: ValidatorDispatchEventEmitter | None = None,
    source_envelope_revision: int | None = None,
    max_parallel_validators: int = DEFAULT_MAX_PARALLEL_VALIDATORS,
) -> ActiveValidatorDispatchResult:
    """Dispatch active validator bindings and append result findings."""

    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    matches = validation_registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )
    agent_runner = runner or _default_package_scoped_validator_runner()
    agent_batch_runner = batch_runner or _default_package_scoped_validator_batch_runner()

    selector_findings: list[ValidationFinding] = []
    jobs: list[_DispatchJob] = []
    ordered_dispatch_units: list[
        _DispatchJob | ValidatorResultMaterializationInput
    ] = []
    for match in _ordered_matches(matches):
        if not _binding_has_dispatch_contract(match.binding):
            LOGGER.info(
                "Skipping active validator binding %s because it declares no "
                "input_fields or expected_result_fields",
                match.binding.binding_id,
            )
            continue
        selector_result = build_domain_validation_request(match)
        if selector_result.findings:
            selector_findings.extend(selector_result.findings)
            continue
        if selector_result.request is None:
            continue

        request = selector_result.request
        validator_result = preflight_unresolved_validator_result(request)
        if validator_result is not None:
            validator_result = _finalize_validator_result(
                validator_result,
                request=request,
            )
            ordered_dispatch_units.append(
                ValidatorResultMaterializationInput(
                    match=match,
                    request=request,
                    result=validator_result,
                )
            )
            continue

        job = _DispatchJob(match=match, request=request)
        jobs.append(job)
        ordered_dispatch_units.append(job)

    execution_started_at = time.monotonic()
    _, executed_items, run_metadata = _run_validator_jobs(
        jobs,
        agent_runner=agent_runner,
        batch_runner=agent_batch_runner,
        event_emitter=event_emitter,
        max_parallel_validators=max_parallel_validators,
    )
    LOGGER.info(
        "Executed %s active validator dispatch job(s) in %.3fs",
        len(jobs),
        time.monotonic() - execution_started_at,
    )
    executed_items_by_request_id = {
        item.request.request_id: item for item in executed_items
    }

    materialization_items: list[ValidatorResultMaterializationInput] = []
    validator_results: list[DomainValidatorResultBase] = []
    for unit in ordered_dispatch_units:
        if isinstance(unit, _DispatchJob):
            materialization_item = executed_items_by_request_id[unit.request.request_id]
        else:
            materialization_item = unit
        materialization_items.append(materialization_item)
        validator_results.append(materialization_item.result)

    updated_envelope = envelope
    appended_findings: list[ValidationFinding] = []
    if selector_findings:
        updated_envelope, selector_appended_findings = (
            append_validation_findings_to_envelope(
                updated_envelope,
                selector_findings,
                actor_id=actor_id,
            )
        )
        appended_findings.extend(selector_appended_findings)
    if materialization_items:
        materialization_started_at = time.monotonic()
        materialization_result = materialize_validator_results_into_envelope(
            updated_envelope,
            domain_pack.metadata,
            materialization_items,
            actor_id=actor_id,
            source_envelope_revision=source_envelope_revision,
        )
        LOGGER.info(
            "Materialized %s active validator result(s) in %.3fs",
            len(materialization_items),
            time.monotonic() - materialization_started_at,
        )
        updated_envelope = materialization_result.envelope
        appended_findings.extend(materialization_result.appended_findings)

    return ActiveValidatorDispatchResult(
        envelope=updated_envelope,
        registry=validation_registry,
        matched_bindings=matches,
        appended_findings=tuple(appended_findings),
        validator_results=tuple(validator_results),
        validator_agent_run_count=int(run_metadata["validator_agent_run_count"]),
        batch_validator_run_count=int(run_metadata["batch_validator_run_count"]),
        validator_batch_groups=tuple(run_metadata["validator_batch_groups"]),
    )


def _run_validator_jobs(
    jobs: list[_DispatchJob],
    *,
    agent_runner: DomainValidatorAgentRunner,
    batch_runner: DomainValidatorBatchAgentRunner,
    event_emitter: ValidatorDispatchEventEmitter | None,
    max_parallel_validators: int,
) -> tuple[
    list[DomainValidatorResultBase],
    list[ValidatorResultMaterializationInput],
    dict[str, Any],
]:
    if not jobs:
        return [], [], {
            "validator_agent_run_count": 0,
            "batch_validator_run_count": 0,
            "validator_batch_groups": (),
        }

    grouped_jobs = _dedupe_validator_jobs(jobs)
    if len(grouped_jobs) < len(jobs):
        LOGGER.info(
            "Deduplicated %s validator dispatch job(s) into %s unique request(s)",
            len(jobs),
            len(grouped_jobs),
        )

    planning_started_at = time.monotonic()
    run_groups = _plan_validator_run_groups(grouped_jobs)
    LOGGER.info(
        "Planned %s active validator run group(s) from %s unique request group(s) in %.3fs",
        len(run_groups),
        len(grouped_jobs),
        time.monotonic() - planning_started_at,
    )
    batch_run_count = sum(1 for group in run_groups if group.batch_key is not None)
    if batch_run_count:
        LOGGER.info(
            "Planned %s batch validator dispatch run(s) across %s unique request group(s)",
            batch_run_count,
            len(grouped_jobs),
        )

    group_results: dict[int, list[DomainValidatorResultBase]] = {}
    validator_agent_run_count = 0
    batch_validator_run_count = 0
    validator_batch_groups: list[dict[str, Any]] = []
    worker_count = max(1, min(max_parallel_validators, len(run_groups)))
    if worker_count == 1:
        for run_group in run_groups:
            result = _execute_validator_run_group(
                run_group,
                grouped_jobs=grouped_jobs,
                agent_runner=agent_runner,
                batch_runner=batch_runner,
                event_emitter=event_emitter,
            )
            group_results.update(result.dedupe_group_results)
            validator_agent_run_count += result.validator_agent_run_count
            batch_validator_run_count += result.batch_validator_run_count
            validator_batch_groups.extend(result.batch_summaries)
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="domain-validator-dispatch",
        ) as executor:
            future_by_run_group = {
                executor.submit(
                    _execute_validator_run_group,
                    run_group,
                    grouped_jobs=grouped_jobs,
                    agent_runner=agent_runner,
                    batch_runner=batch_runner,
                    event_emitter=event_emitter,
                ): run_group
                for run_group in run_groups
            }
            for future in concurrent.futures.as_completed(future_by_run_group):
                result = future.result()
                group_results.update(result.dedupe_group_results)
                validator_agent_run_count += result.validator_agent_run_count
                batch_validator_run_count += result.batch_validator_run_count
                validator_batch_groups.extend(result.batch_summaries)

    result_by_request_id: dict[str, DomainValidatorResultBase] = {}
    for group_index, group in enumerate(grouped_jobs):
        for job, result in zip(group, group_results[group_index], strict=True):
            result_by_request_id[job.request.request_id] = result

    validator_results: list[DomainValidatorResultBase] = []
    materialization_items: list[ValidatorResultMaterializationInput] = []
    for job in jobs:
        validator_result = result_by_request_id[job.request.request_id]
        validator_results.append(validator_result)
        materialization_items.append(
            ValidatorResultMaterializationInput(
                match=job.match,
                request=job.request,
                result=validator_result,
            )
        )
    return validator_results, materialization_items, {
        "validator_agent_run_count": validator_agent_run_count,
        "batch_validator_run_count": batch_validator_run_count,
        "validator_batch_groups": tuple(
            sorted(
                validator_batch_groups,
                key=lambda summary: (
                    str(summary.get("validator_binding_id") or ""),
                    str(summary.get("batch_family") or ""),
                    str(summary.get("first_request_id") or ""),
                ),
            )
        ),
    }


def _plan_validator_run_groups(
    grouped_jobs: list[list[_DispatchJob]],
) -> list[_ValidatorRunGroup]:
    batch_groups_by_key: dict[str, list[int]] = {}
    ordered_run_groups: list[_ValidatorRunGroup] = []
    batch_group_positions: dict[str, int] = {}

    for group_index, group in enumerate(grouped_jobs):
        key = _batch_group_key_for_deduped_job_group(group)
        if key is None:
            ordered_run_groups.append(
                _ValidatorRunGroup(dedupe_group_indexes=(group_index,))
            )
            continue

        indexes = batch_groups_by_key.get(key)
        if indexes is None:
            indexes = []
            batch_groups_by_key[key] = indexes
            batch_group_positions[key] = len(ordered_run_groups)
            ordered_run_groups.append(
                _ValidatorRunGroup(dedupe_group_indexes=(), batch_key=key)
            )
        indexes.append(group_index)

    for key, indexes in batch_groups_by_key.items():
        position = batch_group_positions[key]
        if len(indexes) == 1:
            ordered_run_groups[position] = _ValidatorRunGroup(
                dedupe_group_indexes=(indexes[0],)
            )
        else:
            ordered_run_groups[position] = _ValidatorRunGroup(
                dedupe_group_indexes=tuple(indexes),
                batch_key=key,
            )
    return ordered_run_groups


def _batch_group_key_for_deduped_job_group(group: list[_DispatchJob]) -> str | None:
    representative = group[0]
    binding = representative.match.binding
    if not binding.batch_enabled or binding.validator_agent is None:
        return None
    family = binding.batch_family or binding.binding_id
    return json.dumps(
        {
            "validator_agent": binding.validator_agent.to_dict(),
            "batch_family": family,
        },
        sort_keys=True,
    )


def _execute_validator_run_group(
    run_group: _ValidatorRunGroup,
    *,
    grouped_jobs: list[list[_DispatchJob]],
    agent_runner: DomainValidatorAgentRunner,
    batch_runner: DomainValidatorBatchAgentRunner,
    event_emitter: ValidatorDispatchEventEmitter | None,
) -> _ValidatorRunGroupResult:
    if run_group.batch_key is None:
        group_index = run_group.dedupe_group_indexes[0]
        return _ValidatorRunGroupResult(
            dedupe_group_results={
                group_index: _run_validator_job_group(
                    grouped_jobs[group_index],
                    agent_runner=agent_runner,
                )
            },
            validator_agent_run_count=1,
            batch_validator_run_count=0,
        )

    representative_jobs = [
        grouped_jobs[group_index][0] for group_index in run_group.dedupe_group_indexes
    ]
    batch_results, batch_summary = _run_validator_job_batch(
        representative_jobs,
        batch_runner=batch_runner,
        event_emitter=event_emitter,
    )
    dedupe_group_results: dict[int, list[DomainValidatorResultBase]] = {}
    for group_index, representative_result in zip(
        run_group.dedupe_group_indexes,
        batch_results,
        strict=True,
    ):
        group = grouped_jobs[group_index]
        representative = group[0]
        dedupe_group_results[group_index] = [
            representative_result
            if job is representative
            else _remap_validator_result_for_request(representative_result, job.request)
            for job in group
        ]

    return _ValidatorRunGroupResult(
        dedupe_group_results=dedupe_group_results,
        validator_agent_run_count=1,
        batch_validator_run_count=1,
        batch_summaries=(batch_summary,),
    )


def _dedupe_validator_jobs(jobs: list[_DispatchJob]) -> list[list[_DispatchJob]]:
    groups_by_key: dict[str, list[_DispatchJob]] = {}
    ordered_groups: list[list[_DispatchJob]] = []
    for job in jobs:
        key = _validator_request_dedupe_key(job.request)
        group = groups_by_key.get(key)
        if group is None:
            group = []
            groups_by_key[key] = group
            ordered_groups.append(group)
        group.append(job)
    return ordered_groups


def _validator_request_dedupe_key(request: DomainValidationRequest) -> str:
    selected_identity_inputs = {
        key: value
        for key, value in request.selected_inputs.items()
        if key not in _VALIDATOR_DEDUPE_CONTEXT_INPUT_FIELDS
    }
    if not selected_identity_inputs:
        selected_identity_inputs = dict(request.selected_inputs)

    return json.dumps(
        {
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": {
                "domain_pack_id": request.target.domain_pack_id,
                "object_type": request.target.object_type,
                "object_role": request.target.object_role,
                "expected_fields": list(request.target.expected_fields),
            },
            "selected_inputs": selected_identity_inputs,
            "expected_result_fields": request.expected_result_fields,
        },
        sort_keys=True,
        default=str,
    )


def _run_validator_job_group(
    jobs: list[_DispatchJob],
    *,
    agent_runner: DomainValidatorAgentRunner,
) -> list[DomainValidatorResultBase]:
    representative = jobs[0]
    validator_result = _run_single_validator_job(
        representative,
        agent_runner=agent_runner,
    )
    return [
        validator_result
        if job is representative
        else _remap_validator_result_for_request(validator_result, job.request)
        for job in jobs
    ]


def _run_validator_job_batch(
    jobs: list[_DispatchJob],
    *,
    batch_runner: DomainValidatorBatchAgentRunner,
    event_emitter: ValidatorDispatchEventEmitter | None,
) -> tuple[list[DomainValidatorResultBase], dict[str, Any]]:
    representative = jobs[0]
    binding = representative.match.binding
    summary = _validator_batch_summary(jobs)
    started_at = time.monotonic()
    _emit_validator_batch_event(
        event_emitter,
        phase="start",
        summary=summary,
    )
    runner_duration_seconds = 0.0
    output_validation_duration_seconds = 0.0
    runner_started_at = time.monotonic()
    try:
        raw_output = batch_runner(jobs, binding=binding)
        runner_duration_seconds = time.monotonic() - runner_started_at
        validation_started_at = time.monotonic()
        validator_results = _validated_results_from_agent_batch_output(
            raw_output,
            jobs=jobs,
        )
        output_validation_duration_seconds = time.monotonic() - validation_started_at
        summary = {
            **summary,
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "runner_duration_seconds": round(runner_duration_seconds, 3),
            "output_validation_duration_seconds": round(
                output_validation_duration_seconds,
                3,
            ),
            "status": "completed",
            "resolved_count": sum(
                1 for result in validator_results if result.status == "resolved"
            ),
            "unresolved_count": sum(
                1 for result in validator_results if result.status == "unresolved"
            ),
        }
        _emit_validator_batch_event(
            event_emitter,
            phase="complete",
            summary=summary,
        )
        return validator_results, summary
    except Exception as exc:
        if runner_duration_seconds == 0.0:
            runner_duration_seconds = time.monotonic() - runner_started_at
        LOGGER.warning(
            "Package-scoped validator batch failed for binding %s request(s) %s",
            binding.binding_id,
            [job.request.request_id for job in jobs],
            exc_info=exc,
        )
        validator_results = [
            _finalize_validator_result(
                _unresolved_result_for_dispatch_problem(
                    job.request,
                    reason="validator_agent_error",
                    explanation=f"Validator batch execution failed: {exc}",
                ),
                request=job.request,
            )
            for job in jobs
        ]
        summary = {
            **summary,
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "runner_duration_seconds": round(runner_duration_seconds, 3),
            "output_validation_duration_seconds": round(
                output_validation_duration_seconds,
                3,
            ),
            "status": "error",
            "error": str(exc),
            "resolved_count": 0,
            "unresolved_count": len(validator_results),
        }
        _emit_validator_batch_event(
            event_emitter,
            phase="complete",
            summary=summary,
        )
        return validator_results, summary


def _run_single_validator_job(
    job: _DispatchJob,
    *,
    agent_runner: DomainValidatorAgentRunner,
) -> DomainValidatorResultBase:
    request = job.request
    try:
        runner_started_at = time.monotonic()
        raw_output = agent_runner(request, binding=job.match.binding)
        runner_duration_seconds = time.monotonic() - runner_started_at
        validation_started_at = time.monotonic()
        if isinstance(raw_output, _ValidatorAgentRunOutput):
            validator_result = raw_output.accepted_result
        else:
            validator_result = _validated_result_from_agent_output(
                raw_output,
                request=request,
            )
        output_validation_duration_seconds = time.monotonic() - validation_started_at
        LOGGER.info(
            "Package-scoped validator agent completed for binding %s request %s "
            "in %.3fs (output validation %.3fs)",
            request.validator_binding_id,
            request.request_id,
            runner_duration_seconds,
            output_validation_duration_seconds,
        )
    except Exception as exc:
        LOGGER.warning(
            "Package-scoped validator agent failed for binding %s request %s",
            request.validator_binding_id,
            request.request_id,
            exc_info=exc,
        )
        validator_result = _unresolved_result_for_dispatch_problem(
            request,
            reason="validator_agent_error",
            explanation=f"Validator agent execution failed: {exc}",
        )
    return _finalize_validator_result(validator_result, request=request)


def _finalize_validator_result(
    validator_result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    validator_result = _enforce_expected_result_fields(
        validator_result,
        request=request,
    )
    return _ensure_classifiable_validator_result(
        validator_result,
        request=request,
    )


def _remap_validator_result_for_request(
    validator_result: DomainValidatorResultBase,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    remapped = validator_result.model_copy(
        update={
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
        }
    )
    return _finalize_validator_result(remapped, request=request)


def validator_result_from_agent_output(
    raw_output: Any,
    *,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    """Validate and normalize one validator agent output for materialization."""

    validator_result = _validated_result_from_agent_output(
        raw_output,
        request=request,
    )
    validator_result = _enforce_expected_result_fields(
        validator_result,
        request=request,
    )
    return _ensure_classifiable_validator_result(
        validator_result,
        request=request,
    )


def unresolved_validator_result_for_dispatch_problem(
    request: DomainValidationRequest,
    *,
    reason: str,
    explanation: str,
) -> DomainValidatorResultBase:
    """Build a controlled unresolved validator result for dispatch failures."""

    return _ensure_classifiable_validator_result(
        _unresolved_result_for_dispatch_problem(
            request,
            reason=reason,
            explanation=explanation,
        ),
        request=request,
    )


def run_package_scoped_validator_agent(
    request: DomainValidationRequest,
    *,
    binding: ValidatorBinding,
) -> Any:
    """Execute the package-owned validator through the unified agent runtime."""

    from agents import AgentOutputSchema, Runner, function_tool

    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.config.agent_loader import (
        canonical_system_agent_key,
        get_agent_definition_for_package,
    )

    agent_definition = get_agent_definition_for_package(
        request.validator_agent.package_id,
        request.validator_agent.agent_id,
    )
    if agent_definition is None:
        raise ValueError(
            "Unknown package-scoped validator agent "
            f"{request.validator_agent.package_id}:{request.validator_agent.agent_id}"
        )

    agent = get_agent_by_id(canonical_system_agent_key(agent_definition))
    finalization_state = _ValidatorFinalizationState()
    agent = _copy_agent_for_validator_runtime(agent)
    output_type = getattr(agent, "output_type", None)
    if is_domain_validator_result_schema(output_type):
        agent.output_type = AgentOutputSchema(
            output_type,
            strict_json_schema=False,
        )
    agent.tools = [
        *list(getattr(agent, "tools", []) or []),
        _build_finalize_validator_result_tool(
            request,
            finalization_state=finalization_state,
            function_tool_factory=function_tool,
        ),
    ]
    _append_validator_finalization_instructions(agent, batch=False)

    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    if hasattr(Runner, "run_sync"):
        run_kwargs: dict[str, Any] = {"input": payload}
        if binding.max_tool_calls is not None:
            run_kwargs["max_turns"] = _max_turns_with_validator_finalization(
                binding.max_tool_calls,
                minimum=4,
            )
        run_started_at = time.monotonic()
        try:
            result = Runner.run_sync(agent, **run_kwargs)
        except Exception:
            LOGGER.warning(
                "Package-scoped validator Runner.run_sync failed for %s:%s "
                "binding %s request %s after %.3fs",
                request.validator_agent.package_id,
                request.validator_agent.agent_id,
                request.validator_binding_id,
                request.request_id,
                time.monotonic() - run_started_at,
                exc_info=True,
            )
            raise
        LOGGER.info(
            "Package-scoped validator Runner.run_sync completed for %s:%s "
            "binding %s request %s in %.3fs (payload_bytes=%s)",
            request.validator_agent.package_id,
            request.validator_agent.agent_id,
            request.validator_binding_id,
            request.request_id,
            time.monotonic() - run_started_at,
            len(payload),
        )
        if finalization_state.accepted_result is not None:
            return _ValidatorAgentRunOutput(
                raw_output=result,
                accepted_result=finalization_state.accepted_result,
            )
        raise ValueError(
            "Validator agent did not complete mandatory finalize_validator_result "
            "tool call with status accepted."
        )
    raise RuntimeError("OpenAI Agents Runner.run_sync is unavailable")


def run_package_scoped_validator_agent_batch(
    jobs: list[_DispatchJob],
    *,
    binding: ValidatorBinding,
) -> Any:
    """Execute one package validator batch through the unified agent runtime."""

    from agents import AgentOutputSchema, Runner, function_tool

    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.config.agent_loader import (
        canonical_system_agent_key,
        get_agent_definition_for_package,
    )

    representative_request = jobs[0].request
    agent_definition = get_agent_definition_for_package(
        representative_request.validator_agent.package_id,
        representative_request.validator_agent.agent_id,
    )
    if agent_definition is None:
        raise ValueError(
            "Unknown package-scoped validator agent "
            f"{representative_request.validator_agent.package_id}:"
            f"{representative_request.validator_agent.agent_id}"
        )
    if "domain_validator_batch" not in set(agent_definition.batch_capabilities):
        raise ValueError(
            "Package-scoped validator agent has not opted into domain validator "
            f"batch execution: {representative_request.validator_agent.package_id}:"
            f"{representative_request.validator_agent.agent_id}"
        )

    agent = get_agent_by_id(canonical_system_agent_key(agent_definition))
    finalization_state = _ValidatorFinalizationState()
    agent = _copy_agent_for_validator_runtime(agent)
    output_type = getattr(agent, "output_type", None)
    batch_output_type = _batch_output_schema_for_agent_output(output_type)
    if batch_output_type is not None:
        agent.output_type = AgentOutputSchema(
            batch_output_type,
            strict_json_schema=False,
        )
    agent.tools = [
        *list(getattr(agent, "tools", []) or []),
        _build_finalize_validator_batch_results_tool(
            jobs,
            finalization_state=finalization_state,
            function_tool_factory=function_tool,
        ),
    ]
    _append_validator_finalization_instructions(agent, batch=True)

    payload = json.dumps(
        {
            "mode": "domain_validator_batch",
            "instructions": (
                "Validate every DomainValidationRequest in requests. Return a "
                "JSON object with a results array containing exactly one "
                "DomainValidatorResultBase-compatible result per request_id. "
                "Copy dispatcher-owned identity fields from each request. Use "
                "one bulk lookup tool call per compatible shared lookup group "
                "when a bulk method exists, using list inputs such as "
                "gene_symbols or allele_symbols. Map the returned items back to "
                "their request_ids, and do not loop one lookup call per request "
                "when one shared bulk call can answer the group."
            ),
            "requests": [
                job.request.model_dump(mode="json")
                for job in jobs
            ],
        },
        sort_keys=True,
    )
    if hasattr(Runner, "run_sync"):
        run_kwargs: dict[str, Any] = {"input": payload}
        if binding.max_tool_calls is not None:
            run_kwargs["max_turns"] = _max_turns_with_validator_finalization(
                max(binding.max_tool_calls, len(jobs) + 1),
                minimum=len(jobs) + 3,
            )
        run_started_at = time.monotonic()
        try:
            result = Runner.run_sync(agent, **run_kwargs)
        except Exception:
            LOGGER.warning(
                "Package-scoped validator batch Runner.run_sync failed for %s:%s "
                "binding %s request_count=%s after %.3fs",
                representative_request.validator_agent.package_id,
                representative_request.validator_agent.agent_id,
                representative_request.validator_binding_id,
                len(jobs),
                time.monotonic() - run_started_at,
                exc_info=True,
            )
            raise
        LOGGER.info(
            "Package-scoped validator batch Runner.run_sync completed for %s:%s "
            "binding %s request_count=%s in %.3fs (payload_bytes=%s)",
            representative_request.validator_agent.package_id,
            representative_request.validator_agent.agent_id,
            representative_request.validator_binding_id,
            len(jobs),
            time.monotonic() - run_started_at,
            len(payload),
        )
        if finalization_state.accepted_results:
            return _ValidatorBatchAgentRunOutput(
                raw_output=result,
                accepted_results=finalization_state.accepted_results,
            )
        raise ValueError(
            "Validator batch agent did not complete mandatory "
            "finalize_validator_batch_results tool call with status accepted."
        )
    raise RuntimeError("OpenAI Agents Runner.run_sync is unavailable")


def _copy_agent_for_validator_runtime(agent: Any) -> Any:
    runtime_agent = copy.copy(agent)
    runtime_agent.tools = list(getattr(agent, "tools", []) or [])
    return runtime_agent


def _append_validator_finalization_instructions(agent: Any, *, batch: bool) -> None:
    tool_name = (
        "finalize_validator_batch_results" if batch else "finalize_validator_result"
    )
    result_shape = (
        "the complete results array" if batch else "the complete validator result"
    )
    instruction_block = (
        "Validator finalization is mandatory. Before your final answer, call "
        f"`{tool_name}` with {result_shape} you intend to return. If the tool "
        "returns `status: rejected`, repair only the reported issue(s), call the "
        "finalization tool again, and do not send the final answer until the "
        "tool returns `status: accepted`. After acceptance, return the accepted "
        "validator result payload as your final structured output. The backend "
        "rejects validator runs that do not complete this tool with "
        "`status: accepted`."
    )
    instructions = getattr(agent, "instructions", None)
    if instructions is None:
        agent.instructions = instruction_block
    elif isinstance(instructions, str):
        agent.instructions = f"{instructions.rstrip()}\n\n{instruction_block}"
    else:
        LOGGER.debug(
            "Skipping validator finalization instruction append for non-string "
            "instructions on agent %r",
            getattr(agent, "name", None),
        )


def _max_turns_with_validator_finalization(
    max_tool_calls: int,
    *,
    minimum: int,
) -> int:
    return max(max_tool_calls + 2, minimum)


def _build_finalize_validator_result_tool(
    request: DomainValidationRequest,
    *,
    finalization_state: _ValidatorFinalizationState,
    function_tool_factory: Any,
) -> Any:
    @function_tool_factory(name_override="finalize_validator_result", strict_mode=False)
    def finalize_validator_result(result: dict[str, Any]) -> dict[str, Any]:
        """Validate the final DomainValidatorResultBase before answering."""

        feedback = _validator_result_finalization_feedback(
            result,
            request=request,
        )
        if feedback.accepted_result is not None:
            finalization_state.accepted_result = feedback.accepted_result
        else:
            finalization_state.accepted_result = None
        return _validator_finalization_tool_payload(feedback)

    return finalize_validator_result


def _build_finalize_validator_batch_results_tool(
    jobs: list[_DispatchJob],
    *,
    finalization_state: _ValidatorFinalizationState,
    function_tool_factory: Any,
) -> Any:
    @function_tool_factory(
        name_override="finalize_validator_batch_results",
        strict_mode=False,
    )
    def finalize_validator_batch_results(
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Validate the final batch results before answering."""

        feedback = _validator_batch_results_finalization_feedback(
            results,
            jobs=jobs,
        )
        if feedback.accepted_results:
            finalization_state.accepted_results = feedback.accepted_results
        else:
            finalization_state.accepted_results = ()
        return _validator_finalization_tool_payload(feedback)

    return finalize_validator_batch_results


def _validator_result_finalization_feedback(
    raw_result: Any,
    *,
    request: DomainValidationRequest,
) -> _ValidatorFinalizationFeedback:
    try:
        payload = _extract_structured_output(raw_result)
        result = DomainValidatorResultBase.model_validate(payload)
    except ValidationError as exc:
        message = (
            "Validator result rejected: incompatible DomainValidatorResultBase "
            f"schema ({_validation_error_summary(exc)})."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message),
        )

    expected_agent = request.validator_agent.model_dump(mode="json")
    if (
        result.request_id != request.request_id
        or result.validator_binding_id != request.validator_binding_id
        or result.validator_agent.model_dump(mode="json") != expected_agent
        or not _validator_result_target_matches_request_identity(
            result,
            request=request,
        )
    ):
        message = (
            "Validator result rejected: request_id, validator_binding_id, "
            "validator_agent, and target must exactly match this "
            "DomainValidationRequest."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message),
        )

    normalized = result.model_copy(
        update={
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
        }
    )
    expected_result = _enforce_expected_result_fields(
        normalized,
        request=request,
    )
    if normalized.status == "resolved" and expected_result.status != "resolved":
        message = f"Validator result rejected: {expected_result.explanation}."
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message),
        )

    classifiability_error = _validator_result_classifiability_error(expected_result)
    if classifiability_error is not None:
        message = (
            "Validator result rejected: incompatible validator outcome "
            f"({classifiability_error})."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message),
        )

    return _ValidatorFinalizationFeedback(
        accepted_result=expected_result,
        message="Validator result accepted.",
    )


def _validator_batch_results_finalization_feedback(
    raw_results: Any,
    *,
    jobs: list[_DispatchJob],
) -> _ValidatorFinalizationFeedback:
    if not isinstance(raw_results, list):
        message = (
            "Validator batch rejected: pass a results list with exactly one "
            "DomainValidatorResultBase-compatible result per request."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message),
        )

    expected_request_ids = {job.request.request_id for job in jobs}
    raw_result_by_request_id: dict[str, Any] = {}
    duplicate_request_ids: set[str] = set()
    unexpected_request_ids: list[str] = []
    for raw_result in raw_results:
        request_id = _raw_result_request_id(raw_result)
        if request_id is None:
            unexpected_request_ids.append("<missing>")
            continue
        if request_id not in expected_request_ids:
            unexpected_request_ids.append(request_id)
            continue
        if request_id in raw_result_by_request_id:
            duplicate_request_ids.add(request_id)
            continue
        raw_result_by_request_id[request_id] = raw_result

    result_errors: list[dict[str, Any]] = []
    if unexpected_request_ids:
        result_errors.append(
            {
                "request_id": None,
                "message": (
                    "Unexpected request_id value(s): "
                    + ", ".join(sorted(unexpected_request_ids))
                ),
            }
        )

    accepted_results: list[DomainValidatorResultBase] = []
    for job in jobs:
        request = job.request
        raw_result = raw_result_by_request_id.get(request.request_id)
        if raw_result is None:
            result_errors.append(
                {
                    "request_id": request.request_id,
                    "message": "Missing result for this request_id.",
                }
            )
            continue
        if request.request_id in duplicate_request_ids:
            result_errors.append(
                {
                    "request_id": request.request_id,
                    "message": "Duplicate results for this request_id.",
                }
            )
            continue
        feedback = _validator_result_finalization_feedback(
            raw_result,
            request=request,
        )
        if feedback.accepted_result is None:
            result_errors.append(
                {
                    "request_id": request.request_id,
                    "message": feedback.message,
                    "repair_instructions": list(feedback.repair_instructions),
                }
            )
            continue
        accepted_results.append(feedback.accepted_result)

    if result_errors:
        message = (
            "Validator batch rejected: repair the listed result(s), then call "
            "finalize_validator_batch_results again."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message),
            result_errors=tuple(result_errors),
        )

    return _ValidatorFinalizationFeedback(
        accepted_result=None,
        message="Validator batch results accepted.",
        accepted_results=tuple(accepted_results),
    )


def _validator_finalization_tool_payload(
    feedback: _ValidatorFinalizationFeedback,
) -> dict[str, Any]:
    accepted = feedback.accepted_result is not None or bool(feedback.accepted_results)
    payload: dict[str, Any] = {
        "status": "accepted" if accepted else "rejected",
        "message": feedback.message,
    }
    if feedback.accepted_result is not None:
        payload["validator_result"] = feedback.accepted_result.model_dump(mode="json")
    elif feedback.accepted_results:
        payload["validator_results"] = [
            result.model_dump(mode="json") for result in feedback.accepted_results
        ]
    else:
        payload["repair_instructions"] = list(feedback.repair_instructions)
        if feedback.result_errors:
            payload["result_errors"] = list(feedback.result_errors)
    return payload


def _validator_repair_instructions(message: str) -> tuple[str, ...]:
    instructions = [
        "Return exactly the DomainValidatorResultBase fields required by the schema.",
        (
            "Copy request_id, validator_binding_id, validator_agent, and target "
            "exactly from the DomainValidationRequest."
        ),
    ]
    if "successful lookup_attempt" in message or "lookup_attempt" in message:
        instructions.append(
            "For status resolved, include at least one lookup_attempt with "
            'outcome "success" that records the supporting lookup call.'
        )
        instructions.append(
            "If no successful lookup supports the resolution, change status to "
            "unresolved and preserve the failed or ambiguous lookup attempt."
        )
    if "expected resolved field" in message or "expected field" in message:
        instructions.append(
            "If the evidence resolves the expected field, add the missing "
            "resolved_values entry; otherwise return status unresolved and list "
            "the field in missing_expected_fields."
        )
    return tuple(instructions)


def _validation_error_summary(exc: ValidationError) -> str:
    errors = []
    for error in exc.errors()[:5]:
        loc = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        errors.append(f"{loc}: {error.get('msg')}")
    if len(exc.errors()) > 5:
        errors.append(f"{len(exc.errors()) - 5} more error(s)")
    return "; ".join(errors)


def _batch_output_schema_for_agent_output(output_type: Any) -> type[BaseModel] | None:
    if not is_domain_validator_result_schema(output_type):
        return None
    if not isinstance(output_type, type) or not issubclass(output_type, BaseModel):
        return None
    schema_name = f"{output_type.__name__}BatchEnvelope"
    return create_model(
        schema_name,
        __base__=BaseModel,
        results=(
            list[output_type],
            Field(
                description=(
                    "One validator result per DomainValidationRequest in the "
                    "batch, keyed by request_id"
                )
            ),
        ),
    )


def run_package_scoped_validator_agent_in_worker_thread(
    request: DomainValidationRequest,
    *,
    binding: ValidatorBinding,
) -> Any:
    """Execute a package validator from sync code that is already in an event loop."""

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="domain-validator-agent",
    ) as executor:
        future = executor.submit(
            run_package_scoped_validator_agent,
            request,
            binding=binding,
        )
        return future.result()


def run_package_scoped_validator_agent_batch_in_worker_thread(
    jobs: list[_DispatchJob],
    *,
    binding: ValidatorBinding,
) -> Any:
    """Execute a package validator batch from sync code inside an event loop."""

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="domain-validator-agent-batch",
    ) as executor:
        future = executor.submit(
            run_package_scoped_validator_agent_batch,
            jobs,
            binding=binding,
        )
        return future.result()


def _default_package_scoped_validator_runner() -> DomainValidatorAgentRunner:
    if _running_event_loop_exists():
        return run_package_scoped_validator_agent_in_worker_thread
    return run_package_scoped_validator_agent


def _default_package_scoped_validator_batch_runner() -> DomainValidatorBatchAgentRunner:
    if _running_event_loop_exists():
        return run_package_scoped_validator_agent_batch_in_worker_thread
    return run_package_scoped_validator_agent_batch


def _running_event_loop_exists() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _validated_result_from_agent_output(
    raw_output: Any,
    *,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    if isinstance(raw_output, _ValidatorAgentRunOutput):
        return raw_output.accepted_result
    payload = _extract_structured_output(raw_output)
    try:
        result = DomainValidatorResultBase.model_validate(payload)
    except ValidationError as exc:
        return _unresolved_result_for_dispatch_problem(
            request,
            reason="invalid_schema",
            explanation=f"Validator agent returned incompatible output: {exc}",
        )

    expected_agent = request.validator_agent.model_dump(mode="json")
    if (
        result.request_id != request.request_id
        or result.validator_binding_id != request.validator_binding_id
        or result.validator_agent.model_dump(mode="json") != expected_agent
        or not _validator_result_target_matches_request_identity(
            result,
            request=request,
        )
    ):
        LOGGER.info(
            "Rejecting validator output identity mismatch for binding %s request %s",
            request.validator_binding_id,
            request.request_id,
        )
        return _unresolved_result_for_dispatch_problem(
            request,
            reason="invalid_schema",
            explanation=(
                "Validator agent returned output for a different request, "
                "binding, validator agent, or target."
            ),
        )
    return result.model_copy(
        update={
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
        }
    )


def _validated_results_from_agent_batch_output(
    raw_output: Any,
    *,
    jobs: list[_DispatchJob],
) -> list[DomainValidatorResultBase]:
    if isinstance(raw_output, _ValidatorBatchAgentRunOutput):
        return list(raw_output.accepted_results)
    raw_results = _extract_batch_structured_outputs(raw_output)
    if not isinstance(raw_results, list):
        return [
            _finalize_validator_result(
                _unresolved_result_for_dispatch_problem(
                    job.request,
                    reason="invalid_schema",
                    explanation=(
                        "Validator batch returned incompatible output: expected "
                        "a results list with one result per request."
                    ),
                ),
                request=job.request,
            )
            for job in jobs
        ]

    expected_request_ids = {job.request.request_id for job in jobs}
    raw_result_by_request_id: dict[str, Any] = {}
    duplicate_request_ids: set[str] = set()
    unexpected_request_ids: list[str] = []
    for raw_result in raw_results:
        request_id = _raw_result_request_id(raw_result)
        if request_id is None:
            unexpected_request_ids.append("<missing>")
            continue
        if request_id not in expected_request_ids:
            unexpected_request_ids.append(request_id)
            continue
        if request_id in raw_result_by_request_id:
            duplicate_request_ids.add(request_id)
            continue
        raw_result_by_request_id[request_id] = raw_result

    if unexpected_request_ids:
        unexpected_text = ", ".join(sorted(unexpected_request_ids))
        return [
            _finalize_validator_result(
                _unresolved_result_for_dispatch_problem(
                    job.request,
                    reason="invalid_schema",
                    explanation=(
                        "Validator batch returned result(s) for unexpected "
                        f"request IDs: {unexpected_text}."
                    ),
                ),
                request=job.request,
            )
            for job in jobs
        ]

    validator_results: list[DomainValidatorResultBase] = []
    for job in jobs:
        request = job.request
        raw_result = raw_result_by_request_id.get(request.request_id)
        if raw_result is None:
            explanation = (
                "Validator batch did not return exactly one result for request "
                f"{request.request_id}."
            )
            validator_result = _unresolved_result_for_dispatch_problem(
                request,
                reason="invalid_schema",
                explanation=explanation,
            )
        elif request.request_id in duplicate_request_ids:
            validator_result = _unresolved_result_for_dispatch_problem(
                request,
                reason="invalid_schema",
                explanation=(
                    "Validator batch returned duplicate results for request "
                    f"{request.request_id}."
                ),
            )
        else:
            validator_result = _validated_result_from_agent_output(
                raw_result,
                request=request,
            )
        validator_results.append(
            _finalize_validator_result(validator_result, request=request)
        )
    return validator_results


def _validator_result_target_matches_request_identity(
    result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> bool:
    """Return whether the validator result targets the request's object/field.

    ``target.input_values`` is request context for the validator, not target
    identity. The model must copy object and field identity exactly, but copied
    context text can drift through JSON escaping or normalization without
    changing where the result should materialize.
    """

    return _target_identity_payload(result.target) == _target_identity_payload(
        request.target
    )


def _target_identity_payload(target: Any) -> dict[str, Any]:
    if hasattr(target, "model_dump"):
        return target.model_dump(mode="json", exclude={"input_values"})
    if isinstance(target, dict):
        return {key: value for key, value in target.items() if key != "input_values"}
    return {}


def _extract_structured_output(raw_output: Any) -> Any:
    output = raw_output
    if isinstance(output, _ValidatorAgentRunOutput):
        output = output.raw_output
    if isinstance(output, _ValidatorBatchAgentRunOutput):
        output = output.raw_output
    # Support OpenAI SDK run results, Pydantic models, and lightweight fake runners.
    if hasattr(output, "final_output"):
        output = output.final_output
    if isinstance(output, DomainValidatorResultBase):
        return output.model_dump(
            mode="json",
            include=set(DomainValidatorResultBase.model_fields),
        )
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json")
    return output


def _extract_batch_structured_outputs(raw_output: Any) -> Any:
    output = raw_output
    if isinstance(output, _ValidatorAgentRunOutput):
        output = output.raw_output
    if isinstance(output, _ValidatorBatchAgentRunOutput):
        output = output.raw_output
    if hasattr(output, "final_output"):
        output = output.final_output
    if isinstance(output, list):
        return [_extract_structured_output(item) for item in output]
    if isinstance(output, tuple):
        return [_extract_structured_output(item) for item in output]
    if isinstance(output, BaseModel):
        results = getattr(output, "results", None)
        if isinstance(results, list):
            return [_extract_structured_output(item) for item in results]
        return output.model_dump(mode="json")
    if isinstance(output, dict):
        results = output.get("results")
        if isinstance(results, list):
            return results
    return output


def _raw_result_request_id(raw_result: Any) -> str | None:
    if isinstance(raw_result, DomainValidatorResultBase):
        return raw_result.request_id
    if hasattr(raw_result, "request_id"):
        request_id = getattr(raw_result, "request_id", None)
        return str(request_id) if request_id is not None else None
    if isinstance(raw_result, dict):
        request_id = raw_result.get("request_id")
        return str(request_id) if request_id is not None else None
    return None


def _validator_batch_summary(jobs: list[_DispatchJob]) -> dict[str, Any]:
    representative = jobs[0]
    binding = representative.match.binding
    validator_agent = (
        binding.validator_agent.to_dict()
        if binding.validator_agent is not None
        else representative.request.validator_agent.model_dump(mode="json")
    )
    return {
        "validator_binding_id": binding.binding_id,
        "validator_agent": validator_agent,
        "batch_family": binding.batch_family or binding.binding_id,
        "request_count": len(jobs),
        "request_ids": [job.request.request_id for job in jobs],
        "first_request_id": jobs[0].request.request_id,
    }


def _emit_validator_batch_event(
    event_emitter: ValidatorDispatchEventEmitter | None,
    *,
    phase: str,
    summary: dict[str, Any],
) -> None:
    if event_emitter is None:
        return
    try:
        event_emitter(
            {
                "event": f"validator_batch_{phase}",
                **summary,
            }
        )
    except Exception:
        LOGGER.debug("Validator dispatch event emitter failed", exc_info=True)


def _enforce_expected_result_fields(
    result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    if result.status != "resolved":
        return result

    missing_fields = [
        field_name
        for field_name in request.expected_result_fields
        if missing_resolved_value(result.resolved_values.get(field_name))
    ]
    invalid_array_fields = _invalid_array_resolved_fields(result, request=request)
    policy_violations = allowed_term_policy_violations(result, request=request)
    invalid_policy_fields = [violation.field_name for violation in policy_violations]
    invalid_fields: list[str] = []
    for field_name in [*invalid_array_fields, *invalid_policy_fields]:
        if field_name not in missing_fields and field_name not in invalid_fields:
            invalid_fields.append(field_name)
    if not missing_fields and not invalid_fields:
        return result

    unresolved_fields = [*missing_fields, *invalid_fields]
    explanation = "Validator result omitted expected resolved field(s): "
    if invalid_policy_fields and not missing_fields and not invalid_array_fields:
        explanation = "; ".join(
            violation.message for violation in policy_violations
        ) + ". Expected field(s): "
    elif invalid_fields and not missing_fields:
        explanation = (
            "Validator result did not return one resolved value per selected "
            "array item for expected field(s): "
        )
    elif invalid_fields:
        explanation = (
            "Validator result omitted or underfilled expected resolved field(s): "
        )

    return result.model_copy(
        update={
            "status": "unresolved",
            "missing_expected_fields": unresolved_fields,
            "explanation": explanation + ", ".join(unresolved_fields),
        },
    )


def _invalid_array_resolved_fields(
    result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> list[str]:
    invalid_fields: list[str] = []
    for field_name in request.expected_result_fields:
        selected_value = request.selected_inputs.get(field_name)
        if not isinstance(selected_value, list):
            continue

        resolved_value = result.resolved_values.get(field_name)
        if missing_resolved_value(resolved_value):
            continue
        if not isinstance(resolved_value, list):
            invalid_fields.append(field_name)
            continue
        if len(resolved_value) != len(selected_value):
            invalid_fields.append(field_name)
            continue
        if any(missing_resolved_value(item) for item in resolved_value):
            invalid_fields.append(field_name)

    return invalid_fields


def _unresolved_result_for_dispatch_problem(
    request: DomainValidationRequest,
    *,
    reason: str,
    explanation: str,
) -> DomainValidatorResultBase:
    return DomainValidatorResultBase(
        status="unresolved",
        request_id=request.request_id,
        validator_binding_id=request.validator_binding_id,
        validator_agent=request.validator_agent,
        target=request.target,
        resolved_values={},
        resolved_objects=[],
        missing_expected_fields=list(request.expected_result_fields),
        candidates=[],
        lookup_attempts=[
            ValidatorLookupAttempt(
                provider="domain_validator_dispatch",
                method=reason,
                query={
                    "request_id": request.request_id,
                    "selected_inputs": dict(request.selected_inputs),
                },
                result_count=0,
                outcome="error",
                message=explanation,
            )
        ],
        curator_message=explanation,
        explanation=explanation,
    )


def preflight_unresolved_validator_result(
    request: DomainValidationRequest,
) -> DomainValidatorResultBase | None:
    """Return deterministic unresolved results for requests that should not run."""

    explanation = _unsupported_phenotype_provider_taxon_explanation(request)
    if explanation is None:
        return None

    query = {
        "ontology_family": request.selected_inputs.get("ontology_family"),
        "label": request.selected_inputs.get("label"),
        "name": request.selected_inputs.get("name"),
        "data_provider": request.selected_inputs.get("data_provider"),
        "taxon_id": request.selected_inputs.get("taxon_id"),
        "accepted_prefixes": request.selected_inputs.get("accepted_prefixes"),
        "active_provider_taxon_ontology_mappings": _mapping_summaries(
            request.selected_inputs.get("provider_taxon_ontology_mappings")
        ),
    }
    return DomainValidatorResultBase(
        status="unresolved",
        request_id=request.request_id,
        validator_binding_id=request.validator_binding_id,
        validator_agent=request.validator_agent,
        target=request.target,
        resolved_values={},
        resolved_objects=[],
        missing_expected_fields=[],
        candidates=[],
        lookup_attempts=[
            ValidatorLookupAttempt(
                provider="domain_validator_dispatch",
                method="unsupported_provider_taxon_mapping",
                query=query,
                result_count=0,
                outcome="blocked",
                message=explanation,
            )
        ],
        curator_message=explanation,
        explanation=explanation,
    )


def _unsupported_phenotype_provider_taxon_explanation(
    request: DomainValidationRequest,
) -> str | None:
    selected_inputs = request.selected_inputs
    if selected_inputs.get("ontology_family") != "phenotype":
        return None
    if _present(selected_inputs.get("curie")) or _present(
        selected_inputs.get("ontology_term_type")
    ):
        return None
    if not (
        _present(selected_inputs.get("label"))
        or _present(selected_inputs.get("name"))
    ):
        return None

    mappings = selected_inputs.get("provider_taxon_ontology_mappings")
    if not isinstance(mappings, list) or not mappings:
        return None

    data_provider = _optional_string(selected_inputs.get("data_provider"))
    taxon_id = _optional_string(selected_inputs.get("taxon_id"))
    if _provider_taxon_mapping_matches(
        mappings,
        data_provider=data_provider,
        taxon_id=taxon_id,
    ):
        return None

    mapping_summary = _active_mapping_summary_text(mappings)
    context = (
        f"data_provider={data_provider or '<missing>'}, "
        f"taxon_id={taxon_id or '<missing>'}"
    )
    return (
        "Phenotype ontology label lookup is blocked because no active "
        f"provider/taxon ontology mapping matched {context}. "
        "The dispatcher will not infer a phenotype ontology term type from "
        f"accepted prefixes or free text. Active mappings: {mapping_summary}."
    )


def _provider_taxon_mapping_matches(
    mappings: list[Any],
    *,
    data_provider: str | None,
    taxon_id: str | None,
) -> bool:
    if data_provider is None or taxon_id is None:
        return False
    expected_provider = data_provider.upper()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        mapping_provider = _optional_string(mapping.get("data_provider"))
        mapping_taxon = _optional_string(mapping.get("taxon_id"))
        if (
            mapping_provider is not None
            and mapping_provider.upper() == expected_provider
            and mapping_taxon == taxon_id
        ):
            return True
    return False


def _mapping_summaries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    summaries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        summary = {
            key: item.get(key)
            for key in (
                "data_provider",
                "taxon_id",
                "ontology_term_type",
                "accepted_prefixes",
            )
            if item.get(key) is not None
        }
        if summary:
            summaries.append(summary)
    return summaries


def _active_mapping_summary_text(mappings: list[Any]) -> str:
    summaries = []
    for mapping in _mapping_summaries(mappings):
        provider = mapping.get("data_provider") or "<unknown-provider>"
        taxon = mapping.get("taxon_id") or "<unknown-taxon>"
        term_type = mapping.get("ontology_term_type") or "<unknown-term-type>"
        summaries.append(f"{provider}/{taxon}->{term_type}")
    return ", ".join(summaries) if summaries else "<none>"


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return value not in ({}, [])


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _ensure_classifiable_validator_result(
    result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    classifiability_error = _validator_result_classifiability_error(result)
    if classifiability_error is not None:
        return _unresolved_result_for_dispatch_problem(
            request,
            reason="invalid_schema",
            explanation=(
                "Validator agent returned incompatible output: "
                f"{classifiability_error}"
            ),
        )
    return result


def _validator_result_classifiability_error(
    result: DomainValidatorResultBase,
) -> str | None:
    try:
        for attempt in result.lookup_attempts:
            lookup_status_for_validator_outcome(attempt.outcome)
        if result.status == "resolved" and not any(
            attempt.outcome == "success" for attempt in result.lookup_attempts
        ):
            raise ValueError(
                "Resolved validator result must include at least one successful "
                "lookup_attempt"
            )
        if result.status == "unresolved":
            validator_failure_classification(result)
    except ValueError as exc:
        return str(exc)
    return None


def _ordered_matches(
    matches: tuple[ValidatorBindingMatch, ...],
) -> tuple[ValidatorBindingMatch, ...]:
    return tuple(
        sorted(
            matches,
            key=lambda match: (
                match.binding.binding_id,
                json.dumps(match.target_details(), sort_keys=True),
            ),
        )
    )


def _binding_has_dispatch_contract(binding: ValidatorBinding) -> bool:
    return bool(binding.input_fields or binding.expected_result_fields)


__all__ = [
    "ActiveValidatorDispatchResult",
    "DomainValidatorAgentRunner",
    "DomainValidatorBatchAgentRunner",
    "ValidatorDispatchEventEmitter",
    "dispatch_active_validator_bindings",
    "preflight_unresolved_validator_result",
    "run_package_scoped_validator_agent_batch",
    "run_package_scoped_validator_agent_batch_in_worker_thread",
    "run_package_scoped_validator_agent_in_worker_thread",
    "run_package_scoped_validator_agent",
    "unresolved_validator_result_for_dispatch_problem",
    "validator_result_from_agent_output",
]
