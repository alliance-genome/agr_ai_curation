"""Active domain-pack validator binding dispatch.

This service owns execution for package-scoped active validator bindings.  It
keeps biological validation on the validator-dispatch path while reusing the
shared selector and envelope finding contracts.
"""

from __future__ import annotations

from src.lib.openai_agents.provider_usage import BenchmarkInvocationBudgetExceeded, raise_if_provider_budget_exhausted

import asyncio
import concurrent.futures
import contextvars
import copy
import dataclasses
import json
import logging
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol, Sequence, cast

from src.lib.packages.tool_roles import ValidatorToolPolicyError

if TYPE_CHECKING:
    from .profile_validation import ProfileValidationContext

from pydantic import BaseModel, Field, ValidationError, create_model

from src.schemas.domain_envelope import (
    DomainEnvelope,
    FieldRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
    ValidatorLookupAttempt,
    is_domain_validator_result_schema,
)
from src.lib.runtime_payload_budget import (
    json_size,
    large_scalar_paths,
    provider_context_preflight,
)
from src.lib.openai_agents.config import (
    get_max_parallel_validators,
    get_validator_batch_max_size,
    get_validator_max_tool_calls,
)
from src.lib.observability.sentry import (
    gen_ai_conversation_scope,
    gen_ai_invoke_agent_span,
    set_redacted_ai_span_data,
)
from src.lib.observability.runtime import report_runtime_exception

from .input_selectors import build_domain_validation_request
from .flow_validator_selection import current_flow_validator_selections
from .materialization import (
    ValidatorResultMaterializationInput,
)
from .registry import LoadedDomainPack
from .validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBinding,
    ValidatorBindingMatch,
    validator_dispatch_waves,
)
from .validator_result_classification import (
    lookup_status_for_validator_outcome,
    validator_failure_classification,
)
from .not_validatable import is_not_validatable, not_validatable_object_keys
from .value_presence import missing_resolved_value
from .validation_findings import append_validation_findings_to_envelope


LOGGER = logging.getLogger(__name__)

# These operational caps are env-configurable (defaults unchanged). See
# src/lib/openai_agents/config.py getters and .env.example for documentation:
#   MAX_PARALLEL_VALIDATORS, VALIDATOR_BATCH_MAX_SIZE, VALIDATOR_MAX_TOOL_CALLS.
DEFAULT_MAX_PARALLEL_VALIDATORS = get_max_parallel_validators()
# Cap on how many deduped validator jobs a single batch run may carry when a
# binding does not configure ``batch_max_size``. Large extractions (hundreds of
# annotations) collapse into one batch_key; without a cap the batch agent would
# receive every job in one payload and need hundreds of turns to finish, which
# is both the "Max turns exceeded" failure mode and an unbounded-cost risk.
# Kept small because the per-run ``max_turns`` budget scales with the number of
# jobs in the batch (see the batch runner): a COMPOSITE validator like
# experimental_condition does several per-component lookups PER job, so a run of
# N jobs costs roughly ``N * _DEFAULT_VALIDATOR_MAX_TOOL_CALLS`` turns. A small
# cap keeps each run's turn count (and wall-clock) bounded and lets the chunks
# run in parallel up to ``DEFAULT_MAX_PARALLEL_VALIDATORS`` instead of one long
# serial run.
_DEFAULT_VALIDATOR_BATCH_MAX_SIZE = get_validator_batch_max_size()
# Per-job tool-call budget assumed for a validator binding that does not
# configure ``max_tool_calls``. The batch ``max_turns`` is derived as
# ``len(jobs) * this`` (each job may make this many tool calls before the single
# batch finalize), so the runtime never relies on the Agents SDK default of 10
# turns and never starves a multi-lookup composite validator mid-batch.
_DEFAULT_VALIDATOR_MAX_TOOL_CALLS = get_validator_max_tool_calls()
_VALIDATOR_DEDUPE_CONTEXT_INPUT_FIELDS = frozenset(
    {
        "evidence_quote",
        "verified_quote",
        "evidence_record_id",
    }
)
_VALIDATOR_EXECUTION_FAILURE_EXPLANATION = (
    "Validator execution could not be completed. Curator review is required."
)


class _ValidatorDispatchRuntimeError(RuntimeError):
    """Sanitized validator dispatch failure safe for operational reporting."""


def _sanitized_validator_dispatch_error() -> _ValidatorDispatchRuntimeError:
    try:
        raise _ValidatorDispatchRuntimeError(
            "Package validator execution failed"
        ) from None
    except _ValidatorDispatchRuntimeError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _report_validator_dispatch_failure(
    request: DomainValidationRequest,
    *,
    phase: Literal["single", "batch"],
    request_count: int,
) -> None:
    try:
        report_runtime_exception(
            _sanitized_validator_dispatch_error(),
            component="domain_pack_validator_dispatch",
            operation=f"{phase}_validator_run_failed",
            tags={"phase": phase},
            context={
                "domain_pack_id": request.target.domain_pack_id,
                "validator_package_id": request.validator_agent.package_id,
                "validator_agent_id": request.validator_agent.agent_id,
                "validator_binding_id": request.validator_binding_id,
                "request_count": request_count,
            },
            level="error",
        )
    except Exception:
        LOGGER.warning(
            "Runtime reporting failed for package-scoped validator %s failure",
            phase,
            exc_info=True,
        )


def _provider_id_for_model(model: Any) -> str | None:
    model_name = str(model or "").strip()
    if not model_name:
        return None
    try:
        from src.lib.openai_agents.config import resolve_model_provider

        return resolve_model_provider(model_name)
    except (LookupError, RuntimeError, ValueError):
        LOGGER.warning(
            "Unable to resolve validator provider for model=%s",
            model_name,
            exc_info=True,
        )
        return None


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
        jobs: list["ValidatorDispatchJob"],
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
    binding_audit: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ValidatorRuntimeContext:
    """Runtime document/user context available to validator agent tools."""

    document_id: str | None = None
    user_id: str | None = None
    authenticated_groups: tuple[str, ...] | None = None
    curator_revalidation: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None
    explicit_validation_requested: bool = False
    refresh_scopes: Sequence[tuple[str, Sequence[str]]] | None = None
    dispatch_receipt: dict[str, bool] | None = None


@dataclass(frozen=True)
class ValidatorDispatchJob:
    """One prepared binding request for the shared dispatch planner."""

    match: ValidatorBindingMatch
    request: DomainValidationRequest
    dispatch_context: Mapping[str, Any] | None = None
    history_wave: int | None = None
    history_episode_id: str | None = None
    history_prior_completions: tuple[str, ...] = ()
    history_prerequisites: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ValidatorJobDispatchResult:
    """Canonical execution result for prepared validator jobs."""

    materialization_inputs: tuple[ValidatorResultMaterializationInput, ...]
    validator_results: tuple[DomainValidatorResultBase, ...]
    validator_agent_run_count: int
    batch_validator_run_count: int
    validator_batch_groups: tuple[dict[str, Any], ...]


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
    incomplete_result: DomainValidatorResultBase | None = None
    incomplete_results: tuple[DomainValidatorResultBase, ...] = ()
    retained_results: dict[str, DomainValidatorResultBase] = dataclasses.field(default_factory=dict)
    accepted_result: DomainValidatorResultBase | None = None
    accepted_results: tuple[DomainValidatorResultBase, ...] = ()


@dataclass(frozen=True)
class _ValidatorFinalizationFeedback:
    accepted_result: DomainValidatorResultBase | None
    message: str
    accepted_results: tuple[DomainValidatorResultBase, ...] = ()
    repair_instructions: tuple[str, ...] = ()
    result_errors: tuple[dict[str, Any], ...] = ()
    incomplete_result: DomainValidatorResultBase | None = None
    incomplete_results: tuple[DomainValidatorResultBase, ...] = ()
    retained_results: tuple[DomainValidatorResultBase, ...] = ()


@dataclass(frozen=True)
class _ValidatorAgentRunOutput:
    raw_output: Any
    accepted_result: DomainValidatorResultBase


@dataclass(frozen=True)
class _ValidatorBatchAgentRunOutput:
    raw_output: Any
    accepted_results: tuple[DomainValidatorResultBase, ...]


def dispatch_validator_jobs(
    jobs: list[ValidatorDispatchJob],
    *,
    history_binding,
    runner: DomainValidatorAgentRunner | None = None,
    batch_runner: DomainValidatorBatchAgentRunner | None = None,
    event_emitter: ValidatorDispatchEventEmitter | None = None,
    max_parallel_validators: int = DEFAULT_MAX_PARALLEL_VALIDATORS,
    runtime_context: ValidatorRuntimeContext | None = None,
) -> ValidatorJobDispatchResult:
    """Run prepared jobs through the canonical preflight, dedupe, and batch path."""

    if runner is None:
        default_runner = _default_package_scoped_validator_runner()

        def agent_runner(
            request: DomainValidationRequest,
            *,
            binding: ValidatorBinding,
        ) -> Any:
            if runtime_context is None:
                return default_runner(request, binding=binding)
            return cast(Any, default_runner)(
                request,
                binding=binding,
                runtime_context=runtime_context,
            )

    else:
        agent_runner = runner

    if batch_runner is None:
        default_batch_runner = _default_package_scoped_validator_batch_runner()

        def agent_batch_runner(
            jobs: list[ValidatorDispatchJob],
            *,
            binding: ValidatorBinding,
        ) -> Any:
            if runtime_context is None:
                return default_batch_runner(jobs, binding=binding)
            return cast(Any, default_batch_runner)(
                jobs,
                binding=binding,
                runtime_context=runtime_context,
            )

    else:
        agent_batch_runner = batch_runner

    executable_jobs: list[ValidatorDispatchJob] = []
    ordered_units: list[ValidatorDispatchJob | ValidatorResultMaterializationInput] = []
    for job in jobs:
        validator_result = preflight_unresolved_validator_result(
            job.request,
            policy=job.match.binding.preflight_policy,
        )
        if validator_result is None:
            executable_jobs.append(job)
            ordered_units.append(job)
            continue
        from uuid import uuid4
        from src.lib.validation_history.runtime import record_preflight
        record_preflight(history_binding, job, validator_result, operation_id="preflight:" + str(uuid4()))
        ordered_units.append(
            ValidatorResultMaterializationInput(
                match=job.match,
                request=job.request,
                result=_finalize_validator_result(
                    validator_result,
                    request=job.request,
                ),
                dispatch_context=job.dispatch_context,
            )
        )

    execution_started_at = time.monotonic()
    _, executed_items, run_metadata = _run_validator_jobs(
        executable_jobs,
        agent_runner=agent_runner,
        batch_runner=agent_batch_runner,
        event_emitter=event_emitter,
        max_parallel_validators=max_parallel_validators,
        history_binding=history_binding,
    )
    LOGGER.info(
        "Executed %s prepared validator dispatch job(s) in %.3fs",
        len(executable_jobs),
        time.monotonic() - execution_started_at,
    )
    executed_items_by_request_id = {
        item.request.request_id: item for item in executed_items
    }
    materialization_inputs = tuple(
        _with_returned_optional_fields(
            executed_items_by_request_id[unit.request.request_id]
            if isinstance(unit, ValidatorDispatchJob)
            else unit
        )
        for unit in ordered_units
    )
    return ValidatorJobDispatchResult(
        materialization_inputs=materialization_inputs,
        validator_results=tuple(item.result for item in materialization_inputs),
        validator_agent_run_count=int(run_metadata["validator_agent_run_count"]),
        batch_validator_run_count=int(run_metadata["batch_validator_run_count"]),
        validator_batch_groups=tuple(run_metadata["validator_batch_groups"]),
    )


def _with_returned_optional_fields(
    item: ValidatorResultMaterializationInput,
) -> ValidatorResultMaterializationInput:
    """Write the optional result fields a resolved result returned, like its expected ones.

    An optional field the validator left out is not written and never makes the
    result unresolved; a resolvable value's other identity keys are cleared as usual.
    """

    optional = item.request.optional_result_fields
    if not optional or not item.result.is_resolved:
        return item
    returned = {
        result_field: field_path
        for result_field, field_path in optional.items()
        if not missing_resolved_value(item.result.resolved_values.get(result_field))
    }
    if not returned:
        return item
    return dataclasses.replace(
        item,
        request=item.request.model_copy(
            update={"expected_result_fields": {**item.request.expected_result_fields, **returned}}
        ),
    )


@dataclass(frozen=True)
class PreparedValidatorDispatch:
    """Declared coverage and eligible work, before any provider invocation."""

    envelope: DomainEnvelope
    history_binding: Any
    registry: DomainPackValidationRegistry
    matches: tuple[ValidatorBindingMatch, ...]
    initial_jobs: tuple[ValidatorDispatchJob, ...]
    findings: tuple[ValidationFinding, ...]
    binding_audit: tuple[dict[str, Any], ...]
    suppressed_bindings: tuple[dict[str, Any], ...]


def prepare_validator_dispatch(
    envelope, domain_pack, *, registry=None, runtime_context=None, profile_context=None,
):
    """Capture all obligations and selectors without starting validation."""
    from src.lib.validation_history.runtime import bind_artifact
    history_binding = bind_artifact(envelope, runtime_context)
    from src.lib.validation_history.projection import read_dispatch_source
    dispatch_source = read_dispatch_source(history_binding, envelope)
    authenticated_groups = _normalized_authenticated_groups(runtime_context)
    if profile_context is None:
        from .profile_validation import resolve_envelope_profile_validation
        profile_context = resolve_envelope_profile_validation(
            envelope, domain_pack, active_group_ids=authenticated_groups or (),
            user_id=runtime_context.user_id if runtime_context else None,
        )
    if profile_context is not None:
        from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance
        from src.lib.agent_studio.profile_conformance import ProfileIdentityError
        if envelope.metadata.get("execution_receipt") != profile_context.receipt.model_dump(mode="json"):
            raise ProfileIdentityError("Validation context does not match the authoritative execution receipt")
        require_resolved_profile_conformance(profile_context.profile, profile_context.receipt, envelope.model_dump(mode="json"))
        validation_registry = profile_context.registry
        domain_pack = validation_registry.domain_pack
    else:
        validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(domain_pack)
    matches = validation_registry.match_bindings(
        dispatch_source,
        states=[ValidationBindingState.ACTIVE],
    )
    from src.lib.validation_history.selections import declare_envelope_episode
    envelope = declare_envelope_episode(history_binding, envelope, matches,
        authenticated_groups=authenticated_groups, flow_selections=current_flow_validator_selections(),
        curator_revalidation=runtime_context.curator_revalidation if runtime_context else None,
        unavailable_bindings=[item.mapping.mapping_id for item in profile_context.unavailable if item.mapping.policy.blocks_readiness] if profile_context else ())
    if profile_context is not None:
        from .profile_validation import profile_dispatch_matches
        eligible_matches, eligibility_findings, binding_audit = profile_dispatch_matches(
            dispatch_source, profile_context, authenticated_groups=authenticated_groups,
        )
    else:
        eligible_matches, eligibility_findings, binding_audit = (
            resolve_group_scoped_validator_matches(
                list(_ordered_matches(matches)), authenticated_groups=authenticated_groups,
            )
        )
    dispatch_context = group_dispatch_context(authenticated_groups)
    flow_selections = current_flow_validator_selections()
    from src.lib.validation_history.runtime import record_match_scheduling
    for match in validation_registry.match_bindings(envelope, states=[ValidationBindingState.UNDER_DEVELOPMENT]):
        record_match_scheduling(history_binding, match, "inactive")
    for match in matches:
        if match not in eligible_matches:
            record_match_scheduling(history_binding, match, "ineligible")
    suppressed_bindings = []
    not_validatable = not_validatable_object_keys(envelope)

    def wave_jobs(
        wave_matches: list[ValidatorBindingMatch],
    ) -> tuple[list[ValidatorDispatchJob], list[ValidationFinding]]:
        jobs: list[ValidatorDispatchJob] = []
        findings: list[ValidationFinding] = []
        for match in wave_matches:
            if is_not_validatable(match.object_envelope, not_validatable):
                record_match_scheduling(history_binding, match, "not_validatable")
                # A package validator's one "not validatable" finding speaks for the object.
                continue
            selection = flow_selections.get(match.binding.binding_id)
            if selection is not None:
                record_match_scheduling(history_binding, match,
                    "opted_out" if selection["state"] == "skipped" else "custom_replacement", details={"selection": selection})
                suppressed_bindings.append({
                    "validator_binding_id": match.binding.binding_id,
                    "target": match.target_details(),
                    **selection,
                    "reason": "flow_skip" if selection["state"] == "skipped" else "custom_replacement",
                })
                continue
            if not _binding_has_dispatch_contract(match.binding):
                record_match_scheduling(history_binding, match, "no_contract")
                LOGGER.info(
                    "Skipping active validator binding %s because it declares no "
                    "input_fields or expected_result_fields",
                    match.binding.binding_id,
                )
                continue
            selector_result = build_domain_validation_request(match)
            if selector_result.findings:
                record_match_scheduling(history_binding, match, "missing_input", details={"finding_codes": [f.code for f in selector_result.findings]})
                if profile_context is not None:
                    from .profile_validation import profile_policy_finding
                    mapping_id = match.binding.raw["profile_validation"]["mapping"]["mapping_id"]
                    mapping = next(m for m in profile_context.profile.contract.validator_mappings if m.mapping_id == mapping_id)
                    findings.extend(profile_policy_finding(profile_context, mapping,
                        code=finding.code, message=finding.message, object_ref=finding.object_ref,
                        details=finding.details) for finding in selector_result.findings)
                else:
                    findings.extend(selector_result.findings)
                continue
            if selector_result.request is not None and runtime_context and runtime_context.curator_revalidation:
                from src.lib.validation_history.curator_authority import revalidation_intents
                for intent in revalidation_intents(runtime_context.curator_revalidation):
                    guidance = intent.get("guidance")
                    from src.lib.validation_history.curator_authority import _overlap
                    paths = [*selector_result.request.expected_result_fields.values(), *(selector_result.request.optional_result_fields or {}).values()]
                    target_matches = match.object_envelope is not None and intent["object_id"] in {match.object_envelope.object_id, match.object_envelope.pending_ref_id}
                    fields_match = not intent["field_paths"] or any(isinstance(path, str) and _overlap(path, requested) for path in paths for requested in intent["field_paths"])
                    if guidance and target_matches and fields_match:
                        previous = selector_result.request.validation_guidance
                        selector_result.request.validation_guidance = "\n\n".join(value for value in (previous, "Curator revalidation guidance: " + guidance) if value)
            if selector_result.request is None:
                record_match_scheduling(history_binding, match, "missing_input")
                continue

            jobs.append(
                ValidatorDispatchJob(
                    match=match,
                    request=selector_result.request,
                    dispatch_context=(
                        dispatch_context
                        if match.binding.required_any_active_group
                        else None
                    ),
                )
            )
        return jobs, findings


    jobs = []
    findings = list(eligibility_findings)
    for wave_index, wave in enumerate(
        validator_dispatch_waves({match.binding.binding_id: match.binding for match in eligible_matches}.values())
    ):
        wave_matches = [match for match in eligible_matches if match.binding.binding_id in wave]
        if wave_index:
            for match in wave_matches:
                record_match_scheduling(history_binding, match, "awaiting_review",
                    details={"review_episode_id": envelope.metadata["review_episode_id"]})
            continue
        initial, selector_findings = wave_jobs(wave_matches)
        jobs.extend(replace(job, history_wave=wave_index,
            history_episode_id=envelope.metadata["review_episode_id"]) for job in initial)
        findings.extend(selector_findings)
    if not matches:
        from src.lib.validation_history.store import record_scheduling
        record_scheduling(history_binding.scope, history_binding.artifact_id,
            operation_id="empty-scheduling:" + envelope.metadata["review_episode_id"],
            outcome="zero_item" if not envelope.extracted_objects else "no_validator",
            details={"matched_binding_count": 0})
    return PreparedValidatorDispatch(envelope, history_binding, validation_registry,
        tuple(eligible_matches), tuple(jobs), tuple(findings), tuple(binding_audit),
        tuple(suppressed_bindings))


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
    runtime_context: ValidatorRuntimeContext | None = None,
    profile_context: ProfileValidationContext | None = None,
) -> ActiveValidatorDispatchResult:
    """Dispatch active validator bindings and append result findings."""


    plan = prepare_validator_dispatch(envelope, domain_pack, registry=registry,
        runtime_context=runtime_context, profile_context=profile_context)
    dispatched = dispatch_validator_jobs(list(plan.initial_jobs), history_binding=plan.history_binding,
        runner=runner, batch_runner=batch_runner, event_emitter=event_emitter,
        max_parallel_validators=max_parallel_validators, runtime_context=runtime_context)
    updated, findings = append_validation_findings_to_envelope(
        plan.envelope, list(plan.findings), actor_id=actor_id)
    appended = list(findings)
    if dispatched.materialization_inputs:
        from src.lib.validation_history.candidates import retain_candidates
        retained = retain_candidates(updated, list(dispatched.materialization_inputs),
            scope=plan.history_binding.scope, source_envelope_revision=source_envelope_revision)
        updated = retained.envelope
        appended.extend(retained.appended_findings)
    if plan.suppressed_bindings:
        updated = updated.model_copy(update={"metadata": {
            **updated.metadata, "suppressed_upstream_validators": list(plan.suppressed_bindings)}})
    if any(entry.get("group_scope") is not None for entry in plan.binding_audit):
        updated = updated.model_copy(update={"metadata": {
            **updated.metadata, "validator_binding_audit": list(plan.binding_audit)}})
    return ActiveValidatorDispatchResult(
        envelope=updated, registry=plan.registry, matched_bindings=plan.matches,
        appended_findings=tuple(appended), validator_results=dispatched.validator_results,
        validator_agent_run_count=dispatched.validator_agent_run_count,
        batch_validator_run_count=dispatched.batch_validator_run_count,
        validator_batch_groups=dispatched.validator_batch_groups, binding_audit=plan.binding_audit)


def _normalized_authenticated_groups(
    runtime_context: ValidatorRuntimeContext | None,
) -> tuple[str, ...] | None:
    if runtime_context is None or runtime_context.authenticated_groups is None:
        return None
    return tuple(
        sorted(
            {
                group.strip()
                for group in runtime_context.authenticated_groups
                if isinstance(group, str) and group.strip()
            }
        )
    )


def group_dispatch_context(
    authenticated_groups: tuple[str, ...] | None,
) -> dict[str, Any]:
    """Return the canonical persisted identity for authenticated groups."""

    return {
        "authenticated_groups": (
            list(authenticated_groups) if authenticated_groups is not None else None
        ),
        "group_context_identity": json.dumps(
            list(authenticated_groups) if authenticated_groups is not None else None,
            separators=(",", ":"),
        ),
    }


def resolve_group_scoped_validator_matches(
    matches: list[ValidatorBindingMatch],
    *,
    authenticated_groups: tuple[str, ...] | None,
) -> tuple[
    list[ValidatorBindingMatch],
    list[ValidationFinding],
    tuple[dict[str, Any], ...],
]:
    active_set = set(authenticated_groups or ())
    eligible: list[ValidatorBindingMatch] = []
    findings: list[ValidationFinding] = []
    audit: list[dict[str, Any]] = []

    for match in matches:
        required = match.binding.required_any_active_group
        if not required:
            eligible.append(match)
            audit.append(_binding_group_audit(match, authenticated_groups, True, "generic"))
            continue
        if authenticated_groups is None:
            audit.append(
                _binding_group_audit(match, authenticated_groups, False, "missing_context")
            )
            findings.append(
                _group_scope_finding(
                    match,
                    authenticated_groups=authenticated_groups,
                    code="domain_pack.validator_group_context_missing",
                    message=(
                        f"Validator binding '{match.binding.binding_id}' requires an "
                        "authenticated curator group, but no trusted group context was available."
                    ),
                )
            )
            continue
        if not active_set.intersection(required):
            audit.append(
                _binding_group_audit(match, authenticated_groups, False, "group_not_satisfied")
            )
            continue
        eligible.append(match)
        audit.append(
            _binding_group_audit(match, authenticated_groups, True, "group_scope_satisfied")
        )
        findings.extend(
            _provider_context_mismatch_findings(
                match,
                authenticated_groups=authenticated_groups,
            )
        )

    scoped_by_target = _scoped_matches_by_target(eligible)
    ambiguous_keys = {
        key for key, scoped_matches in scoped_by_target.items() if len(scoped_matches) > 1
    }
    if ambiguous_keys:
        for key in sorted(ambiguous_keys):
            competing = scoped_by_target[key]
            match = competing[0]
            findings.append(
                _group_scope_finding(
                    match,
                    authenticated_groups=authenticated_groups,
                    code="domain_pack.validator_group_scope_ambiguous",
                    message=(
                        "Multiple authenticated-group validator bindings claim this field: "
                        + ", ".join(sorted(item.binding.binding_id for item in competing))
                        + "."
                    ),
                    extra_details={
                        "competing_binding_ids": sorted(
                            item.binding.binding_id for item in competing
                        )
                    },
                )
            )
        eligible = [
            match
            for match in eligible
            if not match.binding.required_any_active_group
            or _match_target_key(match) not in ambiguous_keys
        ]
        audit = [
            {
                **entry,
                "eligible": False,
                "eligibility_reason": "same_field_ambiguity",
            }
            if entry["group_scope"] is not None
            and entry["eligible"]
            and json.dumps(entry["target"], sort_keys=True, default=str)
            in ambiguous_keys
            else entry
            for entry in audit
        ]

    return eligible, findings, tuple(audit)


def _binding_group_audit(
    match: ValidatorBindingMatch,
    authenticated_groups: tuple[str, ...] | None,
    eligible: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "binding_id": match.binding.binding_id,
        "target": match.target_details(),
        "group_scope": (
            {
                "required_any_active_group": list(
                    match.binding.required_any_active_group
                ),
                "provider_value_field_paths": list(
                    match.binding.provider_value_field_paths
                ),
                "allowed_provider_values": list(
                    match.binding.allowed_provider_values
                ),
                "allow_cross_provider": match.binding.allow_cross_provider,
            }
            if match.binding.required_any_active_group
            else None
        ),
        "authenticated_groups": (
            list(authenticated_groups) if authenticated_groups is not None else None
        ),
        "eligible": eligible,
        "eligibility_reason": reason,
        "group_context_identity": group_dispatch_context(authenticated_groups)[
            "group_context_identity"
        ],
    }


def _scoped_matches_by_target(
    matches: list[ValidatorBindingMatch],
) -> dict[str, list[ValidatorBindingMatch]]:
    grouped: dict[str, list[ValidatorBindingMatch]] = {}
    for match in matches:
        if match.binding.required_any_active_group:
            grouped.setdefault(_match_target_key(match), []).append(match)
    return grouped


def _match_on_envelope(match: ValidatorBindingMatch, envelope: DomainEnvelope) -> ValidatorBindingMatch:
    """The same binding target read from ``envelope``, using its stable object identity."""

    if match.object_envelope is None:
        return dataclasses.replace(match, envelope=envelope)
    keys = set(match.object_envelope.ref_keys())
    current = next(
        (
            domain_object
            for domain_object in envelope.extracted_objects
            if keys.intersection(domain_object.ref_keys())
        ),
        None,
    )
    if current is None:
        # Materialization writes into objects and never drops one, so this is a broken envelope.
        raise ValueError(
            f"Validator binding {match.binding.binding_id!r} targets object "
            f"{sorted(keys)!r}, which the envelope its earlier validators wrote into no longer holds."
        )
    return dataclasses.replace(match, envelope=envelope, object_envelope=current)


def _match_target_key(match: ValidatorBindingMatch) -> str:
    return json.dumps(match.target_details(), sort_keys=True, default=str)


def _group_scope_finding(
    match: ValidatorBindingMatch,
    *,
    authenticated_groups: tuple[str, ...] | None,
    code: str,
    message: str,
    field_path: str | None = None,
    extra_details: Mapping[str, Any] | None = None,
) -> ValidationFinding:
    object_ref = (
        match.object_envelope.to_object_ref()
        if match.object_envelope is not None
        else None
    )
    resolved_path = field_path or match.field_path
    return ValidationFinding(
        severity=(
            ValidationFindingSeverity.BLOCKER
            if match.binding.blocking
            else ValidationFindingSeverity.WARNING
        ),
        status=ValidationFindingStatus.OPEN,
        code=code,
        message=message,
        object_ref=object_ref,
        field_ref=(
            FieldRef(object_ref=object_ref, field_path=resolved_path)
            if object_ref is not None and resolved_path is not None
            else None
        ),
        details={
            **match.binding.identity_details(),
            "target": match.target_details(),
            **group_dispatch_context(authenticated_groups),
            **dict(extra_details or {}),
        },
    )


def _provider_context_mismatch_findings(
    match: ValidatorBindingMatch,
    *,
    authenticated_groups: tuple[str, ...],
) -> list[ValidationFinding]:
    binding = match.binding
    if binding.allow_cross_provider or not binding.provider_value_field_paths:
        return []
    if match.object_envelope is None:
        return []
    allowed = {value.casefold() for value in binding.allowed_provider_values}
    findings: list[ValidationFinding] = []
    for declared_path in binding.provider_value_field_paths:
        field_path = match.resolve_input_path(declared_path)
        value = _payload_value(match.object_envelope.payload, field_path)
        normalized_value = _provider_value(value)
        if normalized_value is None or normalized_value.casefold() in allowed:
            continue
        findings.append(
            _group_scope_finding(
                match,
                authenticated_groups=authenticated_groups,
                code="domain_pack.validator_provider_group_mismatch",
                message=(
                    f"Extracted provider value '{normalized_value}' conflicts with the "
                    "authenticated curator group context. The extracted value was preserved."
                ),
                field_path=field_path,
                extra_details={
                    "provider_value": normalized_value,
                    "provider_value_field_path": field_path,
                    "allowed_provider_values": list(binding.allowed_provider_values),
                },
            )
        )
    return findings


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for raw_part in field_path.replace("]", "").replace("[", ".").split("."):
        if not raw_part:
            continue
        if raw_part.isdigit():
            if not isinstance(current, list) or int(raw_part) >= len(current):
                return None
            current = current[int(raw_part)]
        elif isinstance(current, Mapping):
            current = current.get(raw_part)
        else:
            return None
    return current


def _provider_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        for key in ("abbreviation", "id", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _run_validator_jobs(
    jobs: list[ValidatorDispatchJob],
    *,
    history_binding,
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
                history_binding=history_binding,
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
            future_by_run_group = {}
            for run_group in run_groups:
                dispatch_context = contextvars.copy_context()
                future = executor.submit(
                    dispatch_context.run,
                    _execute_validator_run_group,
                    run_group,
                    grouped_jobs=grouped_jobs,
                    history_binding=history_binding,
                    agent_runner=agent_runner,
                    batch_runner=batch_runner,
                    event_emitter=event_emitter,
                )
                future_by_run_group[future] = run_group
            for future in concurrent.futures.as_completed(future_by_run_group):
                try:
                    result = future.result()
                except Exception as exc:
                    from src.lib.validation_history.store import HistoryRecordingError
                    if isinstance(exc, HistoryRecordingError):
                        raise
                    from agr_ai_curation_runtime.document_errors import document_read_failure
                    if not isinstance(exc, BenchmarkInvocationBudgetExceeded) and document_read_failure(exc) is None:
                        raise
                    for pending in future_by_run_group:
                        pending.cancel()
                    # Executor exit drains already-running work and its usage records.
                    raise
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
                dispatch_context=job.dispatch_context,
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
    grouped_jobs: list[list[ValidatorDispatchJob]],
) -> list[_ValidatorRunGroup]:
    batch_indexes_by_key: dict[str, list[int]] = {}
    # ``slots`` preserves first-appearance order. Each slot is either a ready
    # standalone run group or a ``batch_key`` placeholder string to expand once
    # every member of that batch has been collected.
    slots: list[_ValidatorRunGroup | str] = []

    for group_index, group in enumerate(grouped_jobs):
        key = _batch_group_key_for_deduped_job_group(group)
        if key is None:
            slots.append(_ValidatorRunGroup(dedupe_group_indexes=(group_index,)))
            continue

        indexes = batch_indexes_by_key.get(key)
        if indexes is None:
            indexes = []
            batch_indexes_by_key[key] = indexes
            slots.append(key)
        indexes.append(group_index)

    ordered_run_groups: list[_ValidatorRunGroup] = []
    for slot in slots:
        if isinstance(slot, _ValidatorRunGroup):
            ordered_run_groups.append(slot)
            continue

        indexes = batch_indexes_by_key[slot]
        max_size = _effective_validator_batch_max_size(grouped_jobs, indexes)
        for chunk in _chunk_indexes(indexes, max_size):
            if len(chunk) == 1:
                ordered_run_groups.append(
                    _ValidatorRunGroup(dedupe_group_indexes=(chunk[0],))
                )
            else:
                ordered_run_groups.append(
                    _ValidatorRunGroup(
                        dedupe_group_indexes=tuple(chunk),
                        batch_key=slot,
                    )
                )
    return ordered_run_groups


def _effective_validator_batch_max_size(
    grouped_jobs: list[list[ValidatorDispatchJob]],
    indexes: list[int],
) -> int:
    binding = grouped_jobs[indexes[0]][0].match.binding
    configured = binding.batch_max_size
    if configured is not None and configured > 0:
        return configured
    return _DEFAULT_VALIDATOR_BATCH_MAX_SIZE


def _chunk_indexes(indexes: list[int], size: int) -> list[list[int]]:
    return [indexes[start : start + size] for start in range(0, len(indexes), size)]


def _batch_group_key_for_deduped_job_group(group: list[ValidatorDispatchJob]) -> str | None:
    representative = group[0]
    binding = representative.match.binding
    if not binding.batch_enabled or binding.validator_agent is None:
        return None
    if any(_request_has_scoped_evidence_context(job.request) for job in group):
        return None
    family = binding.batch_family or binding.binding_id
    return json.dumps(
        {
            "validator_agent": binding.validator_agent.to_dict(),
            "batch_family": family,
        },
        sort_keys=True,
    )


def _execute_validator_run_group(run_group, *, grouped_jobs, agent_runner, batch_runner, event_emitter, history_binding):
    from uuid import uuid4
    from src.lib.validation_history.runtime import begin_jobs, complete_jobs, fail_jobs, ACTIVE_VALIDATION_EXECUTION
    jobs = [job for index in run_group.dedupe_group_indexes for job in grouped_jobs[index]]
    invocation = begin_jobs(history_binding, jobs, operation_id=str(uuid4()), receipt={
        "batch": run_group.batch_key is not None,
        "agent": jobs[0].request.validator_agent.model_dump(mode="json"),
    })
    token = ACTIVE_VALIDATION_EXECUTION.set((history_binding, invocation))
    try:
        result = _execute_validator_run_group_uncaptured(run_group, grouped_jobs=grouped_jobs,
            agent_runner=agent_runner, batch_runner=batch_runner, event_emitter=event_emitter)
    except BaseException as error:
        fail_jobs(history_binding, invocation, error)
        raise
    finally:
        ACTIVE_VALIDATION_EXECUTION.reset(token)
    complete_jobs(history_binding, invocation, [answer for index in run_group.dedupe_group_indexes
        for answer in result.dedupe_group_results[index]])
    return result


def _execute_validator_run_group_uncaptured(
    run_group: _ValidatorRunGroup,
    *,
    grouped_jobs: list[list[ValidatorDispatchJob]],
    agent_runner: DomainValidatorAgentRunner,
    batch_runner: DomainValidatorBatchAgentRunner,
    event_emitter: ValidatorDispatchEventEmitter | None,
) -> _ValidatorRunGroupResult:
    raise_if_provider_budget_exhausted()
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


def _dedupe_validator_jobs(jobs: list[ValidatorDispatchJob]) -> list[list[ValidatorDispatchJob]]:
    groups_by_key: dict[str, list[ValidatorDispatchJob]] = {}
    ordered_groups: list[list[ValidatorDispatchJob]] = []
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

    target_identity: dict[str, Any] = {
        "domain_pack_id": request.target.domain_pack_id,
        "object_type": request.target.object_type,
        "object_role": request.target.object_role,
        "expected_fields": list(request.target.expected_fields),
    }
    evidence_context = _validator_evidence_context_identity(request)
    if evidence_context is not None:
        target_identity["object_id"] = request.target.object_id
        target_identity["field_path"] = request.target.field_path

    return json.dumps(
        {
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": target_identity,
            "selected_inputs": selected_identity_inputs,
            "validation_guidance": request.validation_guidance,
            "evidence_context": evidence_context,
            "expected_result_fields": request.expected_result_fields,
            **(
                {"optional_result_fields": request.optional_result_fields}
                if request.optional_result_fields
                else {}
            ),
        },
        sort_keys=True,
        default=str,
    )


def _run_validator_job_group(
    jobs: list[ValidatorDispatchJob],
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
    jobs: list[ValidatorDispatchJob],
    *,
    batch_runner: DomainValidatorBatchAgentRunner,
    event_emitter: ValidatorDispatchEventEmitter | None,
) -> tuple[list[DomainValidatorResultBase], dict[str, Any]]:
    from src.lib.benchmarks.stage_measurements import measure_bound_validator

    with measure_bound_validator(jobs[0].match.binding.binding_id):
        return _execute_validator_job_batch(
            jobs, batch_runner=batch_runner, event_emitter=event_emitter,
        )


def _execute_validator_job_batch(
    jobs: list[ValidatorDispatchJob],
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
        raise_if_provider_budget_exhausted()
        raw_output = batch_runner(jobs, binding=binding)
        raise_if_provider_budget_exhausted()
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
                1 for result in validator_results if result.is_resolved
            ),
            "unresolved_count": sum(
                1 for result in validator_results if not result.is_resolved
            ),
        }
        _emit_validator_batch_event(
            event_emitter,
            phase="complete",
            summary=summary,
        )
        return validator_results, summary
    except ValidatorToolPolicyError:
        results = []
        for job in jobs:
            result = _unresolved_result_for_dispatch_problem(
                job.request, reason="validator_tool_policy",
                explanation="Validator configuration includes paper or evidence tools; validation did not run.",
            )
            result._execution_failure = "validator_tool_policy"
            results.append(_finalize_validator_result(result, request=job.request))
        summary = {**summary, "status": "error", "error": "validator_tool_policy",
                   "resolved_count": 0, "unresolved_count": len(results)}
        _emit_validator_batch_event(event_emitter, phase="complete", summary=summary)
        return results, summary
    except BenchmarkInvocationBudgetExceeded:
        raise
    except Exception as exc:
        from src.lib.validation_review.provider import ACTIVE_BUDGET
        from src.lib.validation_review.stops import review_stop
        if ACTIVE_BUDGET.get() is not None and review_stop(exc) is not None:
            raise
        from src.lib.validation_history.store import HistoryRecordingError
        if isinstance(exc, HistoryRecordingError):
            raise
        from src.lib.benchmarks.stage_measurements import record_handled_stage_failure

        from agr_ai_curation_runtime.document_errors import raise_if_document_read_failed
        raise_if_document_read_failed(exc)
        record_handled_stage_failure(exc)
        raise_if_provider_budget_exhausted()
        if runner_duration_seconds == 0.0:
            runner_duration_seconds = time.monotonic() - runner_started_at
        LOGGER.warning(
            "Package-scoped validator batch failed for binding %s request(s) %s",
            binding.binding_id,
            [job.request.request_id for job in jobs],
            exc_info=exc,
        )
        _report_validator_dispatch_failure(
            representative.request,
            phase="batch",
            request_count=len(jobs),
        )
        validator_results = [
            _finalize_validator_result(
                _unresolved_result_for_dispatch_problem(
                    job.request,
                    reason="validator_agent_error",
                    explanation=_VALIDATOR_EXECUTION_FAILURE_EXPLANATION,
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
            "error": _VALIDATOR_EXECUTION_FAILURE_EXPLANATION,
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
    job: ValidatorDispatchJob,
    *,
    agent_runner: DomainValidatorAgentRunner,
) -> DomainValidatorResultBase:
    from src.lib.benchmarks.stage_measurements import measure_bound_validator

    with measure_bound_validator(job.match.binding.binding_id):
        return _execute_single_validator_job(job, agent_runner=agent_runner)


def _execute_single_validator_job(
    job: ValidatorDispatchJob,
    *,
    agent_runner: DomainValidatorAgentRunner,
) -> DomainValidatorResultBase:
    request = job.request
    try:
        runner_started_at = time.monotonic()
        raise_if_provider_budget_exhausted()
        raw_output = agent_runner(request, binding=job.match.binding)
        raise_if_provider_budget_exhausted()
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
    except ValidatorToolPolicyError:
        validator_result = _unresolved_result_for_dispatch_problem(
            request, reason="validator_tool_policy", explanation=(
                "Validator configuration includes paper or evidence tools. Remove those tools "
                "and save a new revision; validation did not run."
            ),
        )
        validator_result._execution_failure = "validator_tool_policy"
    except BenchmarkInvocationBudgetExceeded:
        raise
    except Exception as exc:
        from src.lib.validation_review.provider import ACTIVE_BUDGET
        from src.lib.validation_review.stops import review_stop
        if ACTIVE_BUDGET.get() is not None and review_stop(exc) is not None:
            raise
        from src.lib.validation_history.store import HistoryRecordingError
        if isinstance(exc, HistoryRecordingError):
            raise
        from src.lib.benchmarks.stage_measurements import record_handled_stage_failure

        from agr_ai_curation_runtime.document_errors import raise_if_document_read_failed
        raise_if_document_read_failed(exc)
        record_handled_stage_failure(exc)
        raise_if_provider_budget_exhausted()
        LOGGER.warning(
            "Package-scoped validator agent failed for binding %s request %s",
            request.validator_binding_id,
            request.request_id,
            exc_info=exc,
        )
        _report_validator_dispatch_failure(
            request,
            phase="single",
            request_count=1,
        )
        validator_result = _unresolved_result_for_dispatch_problem(
            request,
            reason="validator_agent_error",
            explanation=_VALIDATOR_EXECUTION_FAILURE_EXPLANATION,
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
    runtime_context: ValidatorRuntimeContext | None = None,
) -> Any:
    """Execute the package-owned validator through the unified agent runtime."""

    from agents import AgentOutputSchema, function_tool

    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.config.agent_loader import (
        canonical_system_agent_key,
        get_agent_definition_for_package,
    )
    from src.lib.openai_agents.runner import (
        run_agent_sync_with_owned_openai_resources,
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

    authenticated_groups = _normalized_authenticated_groups(runtime_context)
    from src.lib.openai_agents.benchmark_routing import benchmark_route_kwargs

    benchmark_slot = f"validator:{request.validator_binding_id}"
    custom_pin = binding.raw.get("custom_validator")
    flow_pin = binding.raw.get("flow_validator")
    if flow_pin:
        from src.lib.validation_review.flow_plan import build_validator
        agent = build_validator(flow_pin, runtime_context)
    elif custom_pin:
        from src.lib.agent_studio.custom_profile_validators import build_custom_validator_agent
        agent = build_custom_validator_agent(custom_pin, runtime_context, benchmark_slot=benchmark_slot)
    else:
        agent = get_agent_by_id(
            canonical_system_agent_key(agent_definition),
            **benchmark_route_kwargs(benchmark_slot),
            **(
                {"authenticated_groups": list(authenticated_groups)}
                if authenticated_groups is not None
                else {}
            ),
        )
    finalization_state = _ValidatorFinalizationState()
    agent = _copy_agent_for_validator_runtime(agent)
    from src.lib.validation_review.validator_adapter import pin_validator_agent
    pin_validator_agent(agent, request.validator_binding_id)
    if runtime_context is not None:
        agent.cost_boundary = {
            "document_id": runtime_context.document_id,
            "user_id": runtime_context.user_id,
        }
    _configure_accepted_finalization_stop(agent, finalization_state, batch=False)
    output_type = getattr(agent, "output_type", None)
    from src.lib.domain_packs.compact_runtime import runtime_for_schema, prepare_compact_tools
    compact_runtime = runtime_for_schema(
        [request], result_schema=output_type,
        profile_request_ids=(request.request_id,) if binding.raw.get("profile_validation") else (),
    )
    if is_domain_validator_result_schema(output_type):
        agent.output_type = AgentOutputSchema(
            output_type,
            strict_json_schema=False,
        )
    agent.tools = [
        *list(getattr(agent, "tools", []) or []),
        _build_finalize_validator_result_tool(
            request,
            compact_runtime=compact_runtime,
            finalization_state=finalization_state,
            function_tool_factory=function_tool,
            profile_mapped=bool(binding.raw.get("profile_validation")),
            result_schema=(
                cast(type[DomainValidatorResultBase], output_type)
                if is_domain_validator_result_schema(output_type)
                else DomainValidatorResultBase
            ),
        ),
    ]
    if compact_runtime is not None:
        prepare_compact_tools(agent, compact_runtime)
    _append_validator_source_context_instructions(agent)
    _append_validator_finalization_instructions(
        agent, batch=False,
        compact_runtime=compact_runtime,
        profile_request_ids=(request.request_id,) if binding.raw.get("profile_validation") else (),
    )

    provider_payload = validator_request_payload_for_agent(request)
    from src.lib.validation_review.validator_adapter import attach_reconsideration_context
    provider_payload = attach_reconsideration_context(agent, provider_payload)
    validator_model = str(getattr(agent, "model", "") or "")
    provider_context_preflight(
        surface="validator",
        operation="domain_validator_single",
        provider=_provider_id_for_model(validator_model),
        model=validator_model,
        payload=provider_payload,
        metadata={
            "validator_binding_id": request.validator_binding_id,
            "request_id": request.request_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "selected_input_large_scalar_paths": large_scalar_paths(
                request.selected_inputs,
                root_path="selected_inputs",
            ),
        },
        emit_runtime_event=True,
    )
    payload = json.dumps(provider_payload, sort_keys=True)
    run_kwargs: dict[str, Any] = {"input": payload}
    effective_max_tool_calls = _effective_validator_max_tool_calls(binding)
    run_kwargs["max_turns"] = _max_turns_with_validator_finalization(
        effective_max_tool_calls,
        minimum=4,
    )
    # ALL-1280: compile the provider-facing tool surface last, after the
    # compact finalize schema replacement.
    from src.lib.packages.tool_roles import require_validator_tool_boundary
    require_validator_tool_boundary(
        [tool.name for tool in agent.tools], validator_role=True, agent_label="Validator",
    )
    from src.lib.openai_agents.tool_surface import apply_tool_surface

    apply_tool_surface(
        agent,
        runtime="validator",
        required_tool_names=("finalize_validator_result",),
    )
    run_started_at = time.monotonic()
    conversation_context_manager = gen_ai_conversation_scope(request.request_id)
    sentry_span_context_manager = gen_ai_invoke_agent_span(
        agent_name=str(getattr(agent, "name", None) or request.validator_agent.agent_id),
        model=validator_model,
        conversation_id=request.request_id,
        workflow="domain_validator",
        tool_name="finalize_validator_result",
        agent_key=request.validator_agent.agent_id,
        agent_source="package_validator",
        document_id=runtime_context.document_id if runtime_context else None,
        document_present=bool(runtime_context and runtime_context.document_id),
        finalization_required=True,
        finalization_tool="finalize_validator_result",
        input_preview=provider_payload,
        span_data={
            "ai_curation.validator.package_id": request.validator_agent.package_id,
            "ai_curation.validator.agent_id": request.validator_agent.agent_id,
            "ai_curation.validator.binding_id": request.validator_binding_id,
            "ai_curation.validator.request_id": request.request_id,
        },
    )
    conversation_context_manager.__enter__()
    sentry_span = sentry_span_context_manager.__enter__()
    run_failed = False
    try:
        from src.lib.validation_history.runtime import record_runner_receipt
        record_runner_receipt(agent, model=validator_model, input_payload=provider_payload)
        from src.lib.validation_review.phase_observability import emit_phase
        emit_phase(agent)
        result = run_agent_sync_with_owned_openai_resources(agent, **run_kwargs)
    except Exception as exc:
        from src.lib.validation_review.provider import ACTIVE_BUDGET
        from src.lib.validation_review.stops import review_stop
        if ACTIVE_BUDGET.get() is not None and review_stop(exc) is not None:
            raise
        from src.lib.validation_history.store import HistoryRecordingError
        if isinstance(exc, HistoryRecordingError):
            raise
        run_failed = True
        set_redacted_ai_span_data(
            sentry_span,
            "ai_curation.validation.status",
            "error",
        )
        LOGGER.warning(
            "Package-scoped owned validator run failed for %s:%s "
            "binding %s request %s after %.3fs",
            request.validator_agent.package_id,
            request.validator_agent.agent_id,
            request.validator_binding_id,
            request.request_id,
            time.monotonic() - run_started_at,
            exc_info=True,
            extra={"sentry_skip_event": True},
        )
        from agents.exceptions import MaxTurnsExceeded
        if isinstance(exc, MaxTurnsExceeded) and finalization_state.incomplete_result:
            from src.lib.benchmarks.stage_measurements import record_handled_stage_failure

            record_handled_stage_failure(exc)
            _report_validator_dispatch_failure(request, phase="single", request_count=1)
            return _ValidatorAgentRunOutput(raw_output=None, accepted_result=finalization_state.incomplete_result)
        raise
    finally:
        if not run_failed:
            if finalization_state.accepted_result is not None:
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.validation.status",
                    "accepted",
                )
            else:
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.validation.status",
                    "rejected",
                )
        sentry_span_context_manager.__exit__(None, None, None)
        conversation_context_manager.__exit__(None, None, None)
    LOGGER.info(
        "Package-scoped owned validator run completed for %s:%s "
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
    if finalization_state.incomplete_result:
        return _ValidatorAgentRunOutput(raw_output=result, accepted_result=finalization_state.incomplete_result)
    return _ValidatorAgentRunOutput(raw_output=result, accepted_result=_unresolved_result_for_dispatch_problem(
        request, reason="invalid_schema", raw_output=_extract_structured_output(result),
        explanation="Validator agent did not complete mandatory finalize_validator_result tool call with status accepted."))


def run_package_scoped_validator_agent_batch(
    jobs: list[ValidatorDispatchJob],
    *,
    binding: ValidatorBinding,
    runtime_context: ValidatorRuntimeContext | None = None,
) -> Any:
    """Execute one package validator batch through the unified agent runtime."""

    from agents import AgentOutputSchema, function_tool

    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.config.agent_loader import (
        canonical_system_agent_key,
        get_agent_definition_for_package,
    )
    from src.lib.openai_agents.runner import (
        run_agent_sync_with_owned_openai_resources,
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

    authenticated_groups = _normalized_authenticated_groups(runtime_context)
    from src.lib.openai_agents.benchmark_routing import benchmark_route_kwargs

    benchmark_kwargs = benchmark_route_kwargs(
        f"validator:{representative_request.validator_binding_id}"
    )
    agent = get_agent_by_id(
        canonical_system_agent_key(agent_definition),
        **benchmark_kwargs,
        **(
            {"authenticated_groups": list(authenticated_groups)}
            if authenticated_groups is not None
            else {}
        ),
    )
    finalization_state = _ValidatorFinalizationState()
    agent = _copy_agent_for_validator_runtime(agent)
    from src.lib.validation_review.validator_adapter import pin_validator_agent
    pin_validator_agent(agent, representative_request.validator_binding_id)
    if runtime_context is not None:
        agent.cost_boundary = {
            "document_id": runtime_context.document_id,
            "user_id": runtime_context.user_id,
        }
    output_type = getattr(agent, "output_type", None)
    batch_output_type = _batch_output_schema_for_agent_output(output_type)
    from src.lib.domain_packs.compact_runtime import runtime_for_schema, prepare_compact_tools
    compact_runtime = runtime_for_schema(
        [job.request for job in jobs], result_schema=output_type,
        profile_request_ids=tuple(job.request.request_id for job in jobs if job.match.binding.raw.get("profile_validation")),
    )
    if batch_output_type is not None:
        agent.output_type = AgentOutputSchema(
            batch_output_type,
            strict_json_schema=False,
        )
    _configure_accepted_finalization_stop(
        agent, finalization_state, batch=True, batch_output_type=batch_output_type,
    )
    agent.tools = [
        *list(getattr(agent, "tools", []) or []),
        _build_finalize_validator_batch_results_tool(
            jobs,
            compact_runtime=compact_runtime,
            finalization_state=finalization_state,
            function_tool_factory=function_tool,
        ),
    ]
    if compact_runtime is not None:
        prepare_compact_tools(agent, compact_runtime)
    _append_validator_source_context_instructions(agent)
    _append_validator_finalization_instructions(
        agent, batch=True,
        compact_runtime=compact_runtime,
        profile_request_ids=tuple(
            job.request.request_id for job in jobs
            if job.match.binding.raw.get("profile_validation")
        ),
    )

    provider_payload = {
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
            validator_request_payload_for_agent(job.request)
            for job in jobs
        ],
    }
    if compact_runtime is not None:
        provider_payload["instructions"] = (
            "Assess every request scientifically and finalize exactly one compact decision per request_id. "
            "Use one bulk lookup tool call per compatible shared lookup group when a bulk method exists, "
            "using the tool's declared bulk/list input. Do not loop one lookup per request "
            "when a shared bulk call answers the group. The program partitions records and assembles canonical results."
        )
    from src.lib.validation_review.validator_adapter import attach_reconsideration_context
    provider_payload = attach_reconsideration_context(agent, provider_payload)
    validator_model = str(getattr(agent, "model", "") or "")
    provider_context_preflight(
        surface="validator",
        operation="domain_validator_batch",
        provider=_provider_id_for_model(validator_model),
        model=validator_model,
        payload=provider_payload,
        metadata={
            "validator_binding_id": representative_request.validator_binding_id,
            "request_count": len(jobs),
            "request_ids": [job.request.request_id for job in jobs],
            "validator_agent": representative_request.validator_agent.model_dump(mode="json"),
            "selected_input_large_scalar_paths": [
                {
                    "request_id": job.request.request_id,
                    "paths": large_scalar_paths(
                        job.request.selected_inputs,
                        root_path="selected_inputs",
                    ),
                }
                for job in jobs
            ],
        },
        emit_runtime_event=True,
    )
    payload = json.dumps(provider_payload, sort_keys=True)
    run_kwargs: dict[str, Any] = {"input": payload}
    effective_max_tool_calls = _effective_validator_max_tool_calls(binding)
    # Per-JOB-aware budget: the batch agent validates every job in one run and
    # a composite validator (e.g. experimental_condition) makes several
    # per-component lookups per job, so the turn budget must scale with
    # len(jobs) * per-job tool calls, not just max(per-job, job-count). The
    # batch size is capped (``_DEFAULT_VALIDATOR_BATCH_MAX_SIZE`` /
    # binding.batch_max_size) so this product stays bounded per run.
    run_kwargs["max_turns"] = _max_turns_with_validator_finalization(
        len(jobs) * effective_max_tool_calls,
        minimum=len(jobs) + 3,
    )
    # ALL-1280: compile the provider-facing tool surface last, after the
    # compact finalize schema replacement.
    from src.lib.packages.tool_roles import require_validator_tool_boundary
    require_validator_tool_boundary(
        [tool.name for tool in agent.tools], validator_role=True, agent_label="Validator",
    )
    from src.lib.openai_agents.tool_surface import apply_tool_surface

    apply_tool_surface(
        agent,
        runtime="validator",
        required_tool_names=("finalize_validator_batch_results",),
    )
    run_started_at = time.monotonic()
    first_request_id = jobs[0].request.request_id if jobs else None
    conversation_context_manager = gen_ai_conversation_scope(first_request_id)
    sentry_span_context_manager = gen_ai_invoke_agent_span(
        agent_name=str(getattr(agent, "name", None) or representative_request.validator_agent.agent_id),
        model=validator_model,
        conversation_id=first_request_id,
        workflow="domain_validator_batch",
        tool_name="finalize_validator_batch_results",
        agent_key=representative_request.validator_agent.agent_id,
        agent_source="package_validator",
        document_id=runtime_context.document_id if runtime_context else None,
        document_present=bool(runtime_context and runtime_context.document_id),
        finalization_required=True,
        finalization_tool="finalize_validator_batch_results",
        input_preview=provider_payload,
        span_data={
            "ai_curation.validator.package_id": representative_request.validator_agent.package_id,
            "ai_curation.validator.agent_id": representative_request.validator_agent.agent_id,
            "ai_curation.validator.binding_id": representative_request.validator_binding_id,
            "ai_curation.validator.request_id": first_request_id,
            "ai_curation.validator.batch_size": len(jobs),
        },
    )
    conversation_context_manager.__enter__()
    sentry_span = sentry_span_context_manager.__enter__()
    run_failed = False
    try:
        from src.lib.validation_history.runtime import record_runner_receipt
        record_runner_receipt(agent, model=validator_model, input_payload=provider_payload)
        from src.lib.validation_review.phase_observability import emit_phase
        emit_phase(agent)
        result = run_agent_sync_with_owned_openai_resources(agent, **run_kwargs)
    except Exception as exc:
        from src.lib.validation_review.provider import ACTIVE_BUDGET
        from src.lib.validation_review.stops import review_stop
        if ACTIVE_BUDGET.get() is not None and review_stop(exc) is not None:
            raise
        from src.lib.validation_history.store import HistoryRecordingError
        if isinstance(exc, HistoryRecordingError):
            raise
        run_failed = True
        set_redacted_ai_span_data(
            sentry_span,
            "ai_curation.validation.status",
            "error",
        )
        LOGGER.warning(
            "Package-scoped owned validator batch run failed for %s:%s "
            "binding %s request_count=%s after %.3fs",
            representative_request.validator_agent.package_id,
            representative_request.validator_agent.agent_id,
            representative_request.validator_binding_id,
            len(jobs),
            time.monotonic() - run_started_at,
            exc_info=True,
            extra={"sentry_skip_event": True},
        )
        from agents.exceptions import MaxTurnsExceeded
        if isinstance(exc, MaxTurnsExceeded) and finalization_state.incomplete_results:
            from src.lib.benchmarks.stage_measurements import record_handled_stage_failure

            record_handled_stage_failure(exc)
            _report_validator_dispatch_failure(
                representative_request, phase="batch", request_count=len(jobs),
            )
            return _ValidatorBatchAgentRunOutput(raw_output=None, accepted_results=finalization_state.incomplete_results)
        raise
    finally:
        if not run_failed:
            if finalization_state.accepted_results:
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.validation.status",
                    "accepted",
                )
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.candidate.count",
                    len(finalization_state.accepted_results),
                )
            else:
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.validation.status",
                    "rejected",
                )
        sentry_span_context_manager.__exit__(None, None, None)
        conversation_context_manager.__exit__(None, None, None)
    LOGGER.info(
        "Package-scoped owned validator batch run completed for %s:%s "
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
    if finalization_state.incomplete_results:
        return _ValidatorBatchAgentRunOutput(raw_output=result, accepted_results=finalization_state.incomplete_results)
    return _ValidatorBatchAgentRunOutput(raw_output=result, accepted_results=tuple(
        _unresolved_result_for_dispatch_problem(job.request, reason="invalid_schema",
            raw_output=_extract_structured_output(result),
            explanation="Validator batch agent did not complete mandatory finalize_validator_batch_results tool call with status accepted.")
        for job in jobs))


def _copy_agent_for_validator_runtime(agent: Any) -> Any:
    runtime_agent = copy.copy(agent)
    runtime_agent.tools = list(getattr(agent, "tools", []) or [])
    return runtime_agent


def _selected_evidence_quote_bundles(selected_inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            evidence_record_id = _optional_string(value.get("evidence_record_id"))
            verified_quote = _optional_string(value.get("verified_quote"))
            if evidence_record_id and verified_quote:
                bundles.append(dict(value))
                return
            for nested_value in value.values():
                visit(nested_value)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)

    for value in selected_inputs.values():
        visit(value)
    return bundles


def _request_has_scoped_evidence_context(request: DomainValidationRequest) -> bool:
    # Only selected-input quote bundles force individual validator runs. Bare
    # request.evidence is supplied directly, and ordinary request evidence
    # remains batchable. This preserves the existing scheduling policy.
    target_field_path = _optional_string(request.target.field_path)
    for bundle in _selected_evidence_quote_bundles(request.selected_inputs):
        bundle_field_path = _optional_string(bundle.get("field_path"))
        if target_field_path and bundle_field_path and bundle_field_path != target_field_path:
            continue
        if _optional_string(bundle.get("evidence_record_id")):
            return True
    return False


def _validator_evidence_context_identity(
    request: DomainValidationRequest,
) -> dict[str, Any] | None:
    bundles = _selected_evidence_quote_bundles(request.selected_inputs)
    if not bundles:
        return None
    return {
        "target_object_id": request.target.object_id,
        "target_field_path": request.target.field_path,
        "evidence_quote_bundles": [
            {
                "evidence_record_id": _optional_string(bundle.get("evidence_record_id")),
                "field_path": _optional_string(bundle.get("field_path")),
                "verified_quote": _optional_string(bundle.get("verified_quote")),
            }
            for bundle in bundles
        ],
    }


def validator_request_payload_for_agent(
    request: DomainValidationRequest,
) -> dict[str, Any]:
    """Return the compact request shape sent to validator agents.

    The canonical ``DomainValidationRequest`` intentionally keeps dispatcher
    metadata for persistence and materialization. The runtime model input should
    avoid repeating that same data, especially ``target.input_values`` which is
    a copy of ``selected_inputs`` for most bindings.
    """

    payload = request.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"input_selectors"},
    )
    target = payload.get("target")
    if isinstance(target, dict):
        target.pop("input_values", None)
    payload["runtime_compaction"] = {
        "omitted_fields": [
            "input_selectors",
            "target.input_values",
        ],
        "input_values_source": "selected_inputs",
        "canonical_identity_restored_by": "finalize_validator_result",
    }
    return payload


def _append_validator_finalization_instructions(
    agent: Any, *, batch: bool, profile_request_ids: tuple[str, ...] = (),
    compact_runtime: Any = None,
) -> None:
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
    if compact_runtime is not None:
        from src.lib.domain_packs.compact_runtime import compact_finalization_instruction
        instruction_block = compact_finalization_instruction(compact_runtime, tool_name=tool_name, batch=batch)
    elif profile_request_ids:
        instruction_block += (
            "\nCustom-profile output contract for request_ids "
            + json.dumps(profile_request_ids)
            + ": this contract takes precedence over package instructions to populate "
            "resolved_objects. Return resolved_objects as an empty list. Put only "
            "tool-grounded values in resolved_values, using only the keys in that "
            "request's expected_result_fields. Preserve lookup_attempts and candidate "
            "evidence; do not invent values to fill mapped slots. Unresolved or "
            "ambiguous identities must remain unresolved."
        )
    if compact_runtime is None:
        from src.lib.domain_packs.validator_guidance import VALIDATOR_FOLLOW_UP_GUIDANCE
        instruction_block += "\n" + VALIDATOR_FOLLOW_UP_GUIDANCE
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


def _append_validator_source_context_instructions(
    agent: Any,
) -> None:
    instruction_block = (
        "Validate the supplied values using the extractor-provided context and appropriate "
        "database/ontology tools. The supplied evidence is read-only source context, not a "
        "validation decision. You cannot search/read the paper, record or change source "
        "evidence, or perform extraction. Return your resolution, explanation and any "
        "questions separately. If context is missing, describe what is needed for the "
        "supervisor; do not initiate extraction. A lookup-only task does not require a "
        "paper quote. Use tools when needed for the task; do not fabricate lookup results. "
        "Treat validation_guidance as advisory, not verified evidence or a decision. "
        "Never resolve an identity solely from guidance; follow higher-priority policy. "
        "Do not apply one request's guidance to another request."
    )
    instructions = getattr(agent, "instructions", None)
    if instructions is None:
        agent.instructions = instruction_block
    elif isinstance(instructions, str):
        agent.instructions = f"{instructions.rstrip()}\n\n{instruction_block}"
    else:
        LOGGER.debug(
            "Skipping validator source-context instruction append for non-string "
            "instructions on agent %r",
            getattr(agent, "name", None),
        )


def _max_turns_with_validator_finalization(
    max_tool_calls: int,
    *,
    minimum: int,
) -> int:
    return max(max_tool_calls + 2, minimum)


def _effective_validator_max_tool_calls(binding: ValidatorBinding) -> int:
    if binding.max_tool_calls is not None:
        return binding.max_tool_calls
    return _DEFAULT_VALIDATOR_MAX_TOOL_CALLS


def _configure_accepted_finalization_stop(
    agent: Any,
    state: _ValidatorFinalizationState,
    *,
    batch: bool,
    batch_output_type: type[BaseModel] | None = None,
) -> None:
    """Let the SDK finish with the result accepted by our mandatory finalizer."""
    from agents.agent import ToolsToFinalOutputResult
    from src.lib.openai_agents.config import get_validator_stop_after_accepted_finalization

    if not get_validator_stop_after_accepted_finalization():
        return

    def finish_when_accepted(_context, _tool_results):
        if batch:
            if not state.accepted_results:
                return ToolsToFinalOutputResult(is_final_output=False)
            payload = {"results": [result.model_dump(mode="json") for result in state.accepted_results]}
            output = batch_output_type.model_validate(payload) if batch_output_type else payload
        else:
            if state.accepted_result is None:
                return ToolsToFinalOutputResult(is_final_output=False)
            output = state.accepted_result
        return ToolsToFinalOutputResult(is_final_output=True, final_output=output)

    agent.tool_use_behavior = finish_when_accepted


def _build_finalize_validator_result_tool(
    request: DomainValidationRequest,
    *,
    finalization_state: _ValidatorFinalizationState,
    function_tool_factory: Any,
    result_schema: type[DomainValidatorResultBase] = DomainValidatorResultBase,
    profile_mapped: bool = False,
    compact_runtime: Any = None,
) -> Any:
    @function_tool_factory(name_override="finalize_validator_result", strict_mode=False,
                          **({"failure_error_function": None} if compact_runtime is not None else {}))
    def finalize_validator_result(result: dict[str, Any]) -> dict[str, Any]:
        """Validate the final DomainValidatorResultBase before answering."""

        if compact_runtime is not None:
            try:
                result = compact_runtime.assemble(result)
            except (TypeError, KeyError):
                from src.lib.domain_packs.compact_runtime import fail_compact_assembly
                finalization_state.accepted_result = None
                fail_compact_assembly()
            except ValueError as exc:
                finalization_state.accepted_result = None
                return {"status": "rejected", "message": str(exc),
                        "repair_instructions": list(_validator_repair_instructions(
                            str(exc), compact_runtime=compact_runtime,
                        ))}
        feedback = _validator_result_finalization_feedback(
            result,
            request=request,
            result_schema=result_schema,
            profile_mapped=profile_mapped,
            compact_runtime=compact_runtime,
        )
        if feedback.accepted_result is not None:
            finalization_state.accepted_result = feedback.accepted_result
        else:
            finalization_state.accepted_result = None
        if feedback.incomplete_result is not None or feedback.accepted_result is not None:
            finalization_state.incomplete_result = feedback.incomplete_result
        payload = _validator_finalization_tool_payload(feedback)
        return payload

    if compact_runtime is not None:
        from src.lib.domain_packs.compact_runtime import compact_finalization_schema
        return compact_finalization_schema(finalize_validator_result, compact_runtime)
    return finalize_validator_result


def _build_finalize_validator_batch_results_tool(
    jobs: list[ValidatorDispatchJob],
    *,
    finalization_state: _ValidatorFinalizationState,
    function_tool_factory: Any,
    compact_runtime: Any = None,
) -> Any:
    @function_tool_factory(
        name_override="finalize_validator_batch_results",
        strict_mode=False,
        **({"failure_error_function": None} if compact_runtime is not None else {}),
    )
    def finalize_validator_batch_results(
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Validate the final batch results before answering."""

        try:
            feedback = _validator_batch_results_finalization_feedback(
                results, jobs=jobs,
                result_schemas={key: contract.result_schema for key, contract in compact_runtime.contracts.items()}
                if compact_runtime is not None else None,
                compact_runtime=compact_runtime,
            )
        except (TypeError, KeyError):
            if compact_runtime is None:
                raise
            from src.lib.domain_packs.compact_runtime import fail_compact_assembly
            finalization_state.accepted_results = ()
            fail_compact_assembly()
        # Only independently safe results may replace a previously retained
        # judgment. Missing/malformed peers and synthetic failures never do.
        finalization_state.retained_results.update(
            (result.request_id, result) for result in feedback.retained_results
        )
        if feedback.accepted_results:
            finalization_state.accepted_results = feedback.accepted_results
            finalization_state.incomplete_results = ()
            finalization_state.retained_results.clear()
        else:
            finalization_state.accepted_results = ()
            if finalization_state.retained_results:
                finalization_state.incomplete_results = _retained_batch_results(
                    jobs, finalization_state.retained_results,
                )
        return _validator_finalization_tool_payload(feedback)

    if compact_runtime is not None:
        from src.lib.domain_packs.compact_runtime import compact_finalization_schema
        return compact_finalization_schema(finalize_validator_batch_results, compact_runtime, batch=True)
    return finalize_validator_batch_results


def _validator_result_finalization_feedback(
    raw_result: Any,
    *,
    request: DomainValidationRequest,
    result_schema: type[DomainValidatorResultBase] = DomainValidatorResultBase,
    profile_mapped: bool = False,
    compact_runtime: Any = None,
    batch: bool = False,
) -> _ValidatorFinalizationFeedback:
    try:
        # Keep the assembler's private completeness provenance on typed results;
        # raw model-authored dictionaries still undergo ordinary schema validation.
        payload = raw_result if type(raw_result) is result_schema else _extract_structured_output(raw_result)
        result = result_schema.model_validate(
            payload,
            context={"domain_validation_request": request},
        )
    except ValidationError as exc:
        message = (
            "Validator result rejected: incompatible DomainValidatorResultBase "
            f"schema ({_validation_error_summary(exc)})."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=batch),
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
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=batch),
        )

    if profile_mapped and (
        result.resolved_objects
        or set(result.resolved_values) - request.expected_result_fields.keys()
    ):
        message = (
            "Validator result rejected: custom-profile results require resolved_objects "
            "to be an empty list and resolved_values to contain only approved "
            "expected_result_fields keys. Keep grounded mapped values and lookup "
            "evidence; remove the unmapped output channels."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=batch),
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
    if not expected_result.is_complete:
        message = "Validator result rejected: " + "; ".join(expected_result.output_issues)
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=batch),
            incomplete_result=expected_result,
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
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=batch),
        )

    return _ValidatorFinalizationFeedback(
        accepted_result=expected_result,
        message="Validator result accepted.",
    )


def _validator_batch_results_finalization_feedback(
    raw_results: Any,
    *,
    jobs: list[ValidatorDispatchJob],
    result_schemas: dict[str, type[DomainValidatorResultBase]] | None = None,
    compact_runtime: Any = None,
) -> _ValidatorFinalizationFeedback:
    if not isinstance(raw_results, list):
        message = (
            "Validator batch rejected: pass a results list with exactly one "
            "result matching the active submission schema per request."
        )
        return _ValidatorFinalizationFeedback(
            accepted_result=None,
            message=message,
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=True),
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
    retained_results: dict[str, DomainValidatorResultBase] = {}
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
        if compact_runtime is not None:
            try:
                raw_result = compact_runtime.assemble(raw_result)
            except ValueError as exc:
                result_errors.append({"request_id": request.request_id, "message": str(exc),
                                      "repair_instructions": list(_validator_repair_instructions(
                                          str(exc), compact_runtime=compact_runtime, batch=True,
                                      ))})
                continue
        feedback = _validator_result_finalization_feedback(
            raw_result,
            request=request,
            result_schema=(result_schemas or {}).get(request.request_id, DomainValidatorResultBase),
            profile_mapped=bool(job.match.binding.raw.get("profile_validation")),
            compact_runtime=compact_runtime,
            batch=True,
        )
        retained = feedback.accepted_result or feedback.incomplete_result
        if retained is not None:
            retained_results[request.request_id] = retained
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
            repair_instructions=_validator_repair_instructions(message, compact_runtime=compact_runtime, batch=True),
            result_errors=tuple(result_errors),
            incomplete_results=_retained_batch_results(jobs, retained_results) if retained_results else (),
            retained_results=tuple(retained_results.values()),
        )

    return _ValidatorFinalizationFeedback(
        accepted_result=None,
        message="Validator batch results accepted.",
        accepted_results=tuple(accepted_results),
    )


def _retained_batch_results(
    jobs: list[ValidatorDispatchJob],
    retained_results: Mapping[str, DomainValidatorResultBase],
) -> tuple[DomainValidatorResultBase, ...]:
    return tuple(
        retained_results.get(job.request.request_id) or _unresolved_result_for_dispatch_problem(
            job.request, reason="invalid_schema",
            explanation="Validator batch did not supply a structurally valid result for this request.",
        ) for job in jobs
    )


def _validator_finalization_tool_payload(
    feedback: _ValidatorFinalizationFeedback,
) -> dict[str, Any]:
    accepted = feedback.accepted_result is not None or bool(feedback.accepted_results)
    if accepted:
        # The canonical result is already stored in finalization_state. The
        # validator supplied it in this call; echoing it adds no information.
        return {"status": "accepted", "message": "Validator result accepted."}
    payload: dict[str, Any] = {
        "status": "rejected",
        "message": feedback.message,
        "repair_instructions": list(feedback.repair_instructions),
    }
    if feedback.result_errors:
        payload["result_errors"] = list(feedback.result_errors)
    return payload


def _validator_repair_instructions(
    message: str, *, compact_runtime: Any = None, batch: bool = False,
) -> tuple[str, ...]:
    if compact_runtime is not None:
        from src.lib.domain_packs.compact_runtime import compact_repair_instructions
        return compact_repair_instructions(compact_runtime, batch=batch)
    instructions = [
        "Return exactly the DomainValidatorResultBase fields required by the schema.",
        (
            "Copy request_id, validator_binding_id, validator_agent, and target "
            "exactly from the DomainValidationRequest."
        ),
    ]
    if "lookup_attempt" in message:
        instructions.append(
            "Describe only lookup calls actually performed, using the declared "
            "lookup outcome vocabulary. Do not invent a lookup to support a judgment."
        )
    if "expected resolved field" in message or "expected field" in message:
        instructions.append(
            "Supply the missing expected field from supported data, or correct "
            "the reported shape or contradiction. Keep valid values and explain "
            "any uncertainty. Change the scientific judgment only if your "
            "assessment changes; do not invent a value to complete the output."
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
    runtime_context: ValidatorRuntimeContext | None = None,
) -> Any:
    """Execute a package validator from sync code that is already in an event loop."""

    dispatch_context = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="domain-validator-agent",
    ) as executor:
        if runtime_context is None:
            future = executor.submit(
                dispatch_context.run,
                run_package_scoped_validator_agent,
                request,
                binding=binding,
            )
            return future.result()
        future = executor.submit(
            dispatch_context.run,
            run_package_scoped_validator_agent,
            request,
            binding=binding,
            runtime_context=runtime_context,
        )
        return future.result()


def run_package_scoped_validator_agent_batch_in_worker_thread(
    jobs: list[ValidatorDispatchJob],
    *,
    binding: ValidatorBinding,
    runtime_context: ValidatorRuntimeContext | None = None,
) -> Any:
    """Execute a package validator batch from sync code inside an event loop."""

    dispatch_context = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="domain-validator-agent-batch",
    ) as executor:
        if runtime_context is None:
            future = executor.submit(
                dispatch_context.run,
                run_package_scoped_validator_agent_batch,
                jobs,
                binding=binding,
            )
            return future.result()
        future = executor.submit(
            dispatch_context.run,
            run_package_scoped_validator_agent_batch,
            jobs,
            binding=binding,
            runtime_context=runtime_context,
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
            raw_output=payload,
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
            raw_output=payload,
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
    jobs: list[ValidatorDispatchJob],
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
                    raw_output=raw_results,
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
                    raw_output=raw_results,
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
                    raw_output=raw_results,
                explanation=explanation,
            )
        elif request.request_id in duplicate_request_ids:
            validator_result = _unresolved_result_for_dispatch_problem(
                request,
                reason="invalid_schema",
                    raw_output=raw_results,
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


def _validator_batch_summary(jobs: list[ValidatorDispatchJob]) -> dict[str, Any]:
    representative = jobs[0]
    binding = representative.match.binding
    validator_agent = (
        binding.validator_agent.to_dict()
        if binding.validator_agent is not None
        else representative.request.validator_agent.model_dump(mode="json")
    )
    request_payloads = [
        validator_request_payload_for_agent(job.request)
        for job in jobs
    ]
    request_payload_sizes = [json_size(payload) for payload in request_payloads]
    selected_input_large_scalar_paths = [
        {
            "request_id": job.request.request_id,
            "paths": large_scalar_paths(
                job.request.selected_inputs,
                root_path="selected_inputs",
            ),
        }
        for job in jobs
    ]
    return {
        "validator_binding_id": binding.binding_id,
        "validator_agent": validator_agent,
        "batch_family": binding.batch_family or binding.binding_id,
        "request_count": len(jobs),
        "request_ids": [job.request.request_id for job in jobs],
        "first_request_id": jobs[0].request.request_id,
        "payload_summary": {
            "request_payload_json_chars": sum(size.json_chars for size in request_payload_sizes),
            "request_payload_estimated_tokens": sum(size.estimated_tokens for size in request_payload_sizes),
            "largest_request_json_chars": max(
                (size.json_chars for size in request_payload_sizes),
                default=0,
            ),
            "omitted_target_input_values_count": len(jobs),
            "large_selected_input_scalar_paths": selected_input_large_scalar_paths,
        },
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

    missing_fields = list(result.missing_expected_fields) if (
        result._assembled_field_completeness and result.field_resolutions
    ) else [
        field_name
        for field_name in request.expected_result_fields
        if missing_resolved_value(result.resolved_values.get(field_name))
    ]
    invalid_array_fields = _invalid_array_resolved_fields(result, request=request)
    invalid_fields: list[str] = []
    for field_name in invalid_array_fields:
        if field_name not in missing_fields and field_name not in invalid_fields:
            invalid_fields.append(field_name)
    if not missing_fields and not invalid_fields:
        return result

    unresolved_fields = [*missing_fields, *invalid_fields]
    explanation = "Validator result omitted expected resolved field(s): "
    if invalid_fields and not missing_fields:
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
            "missing_expected_fields": unresolved_fields,
            "output_issues": list(dict.fromkeys([
                *result.output_issues, explanation + ", ".join(unresolved_fields),
            ])),
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
    raw_output: Any = None,
) -> DomainValidatorResultBase:
    result = DomainValidatorResultBase(
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

    if reason == "validator_agent_error":
        result._execution_failure = reason
    else:
        result.output_issues = [explanation]
        result._raw_output = {"raw_output": raw_output}
    return result


def preflight_unresolved_validator_result(
    request: DomainValidationRequest,
    *,
    policy: str | None = None,
) -> DomainValidatorResultBase | None:
    """Return deterministic unresolved results for requests that should not run."""

    if policy != "provider_taxon_mapping_required":
        return None

    explanation = _provider_taxon_mapping_policy_explanation(request)
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


def _provider_taxon_mapping_policy_explanation(
    request: DomainValidationRequest,
) -> str | None:
    selected_inputs = request.selected_inputs
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
        "Ontology label lookup is blocked because no active "
        f"provider/taxon ontology mapping matched {context}. "
        "The dispatcher will not infer an ontology term type from "
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
            raw_output=result.model_dump(mode="json"),
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
    return binding.has_dispatch_contract


__all__ = [
    "ActiveValidatorDispatchResult",
    "DomainValidatorAgentRunner",
    "DomainValidatorBatchAgentRunner",
    "ValidatorDispatchJob",
    "ValidatorDispatchEventEmitter",
    "ValidatorJobDispatchResult",
    "ValidatorRuntimeContext",
    "dispatch_active_validator_bindings",
    "dispatch_validator_jobs",
    "group_dispatch_context",
    "preflight_unresolved_validator_result",
    "resolve_group_scoped_validator_matches",
    "run_package_scoped_validator_agent_batch",
    "run_package_scoped_validator_agent_batch_in_worker_thread",
    "run_package_scoped_validator_agent_in_worker_thread",
    "run_package_scoped_validator_agent",
    "unresolved_validator_result_for_dispatch_problem",
    "validator_request_payload_for_agent",
    "validator_result_from_agent_output",
]
