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
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from src.schemas.domain_envelope import (
    DomainEnvelope,
    ValidationFinding,
)
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
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


@dataclass(frozen=True)
class ActiveValidatorDispatchResult:
    """Result of dispatching active validator bindings for one envelope."""

    envelope: DomainEnvelope
    registry: DomainPackValidationRegistry
    matched_bindings: tuple[ValidatorBindingMatch, ...]
    appended_findings: tuple[ValidationFinding, ...]
    validator_results: tuple[DomainValidatorResultBase, ...]


@dataclass(frozen=True)
class _DispatchJob:
    match: ValidatorBindingMatch
    request: DomainValidationRequest


def dispatch_active_validator_bindings(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    *,
    actor_id: str = "domain_validator_dispatch",
    registry: DomainPackValidationRegistry | None = None,
    runner: DomainValidatorAgentRunner | None = None,
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

    _, executed_items = _run_validator_jobs(
        jobs,
        agent_runner=agent_runner,
        max_parallel_validators=max_parallel_validators,
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
        materialization_result = materialize_validator_results_into_envelope(
            updated_envelope,
            domain_pack.metadata,
            materialization_items,
            actor_id=actor_id,
            source_envelope_revision=source_envelope_revision,
        )
        updated_envelope = materialization_result.envelope
        appended_findings.extend(materialization_result.appended_findings)

    return ActiveValidatorDispatchResult(
        envelope=updated_envelope,
        registry=validation_registry,
        matched_bindings=matches,
        appended_findings=tuple(appended_findings),
        validator_results=tuple(validator_results),
    )


def _run_validator_jobs(
    jobs: list[_DispatchJob],
    *,
    agent_runner: DomainValidatorAgentRunner,
    max_parallel_validators: int,
) -> tuple[
    list[DomainValidatorResultBase],
    list[ValidatorResultMaterializationInput],
]:
    if not jobs:
        return [], []

    grouped_jobs = _dedupe_validator_jobs(jobs)
    if len(grouped_jobs) < len(jobs):
        LOGGER.info(
            "Deduplicated %s validator dispatch job(s) into %s unique request(s)",
            len(jobs),
            len(grouped_jobs),
        )

    group_results: dict[int, list[DomainValidatorResultBase]] = {}
    worker_count = max(1, min(max_parallel_validators, len(grouped_jobs)))
    if worker_count == 1:
        for group_index, group in enumerate(grouped_jobs):
            group_results[group_index] = _run_validator_job_group(
                group,
                agent_runner=agent_runner,
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="domain-validator-dispatch",
        ) as executor:
            future_by_group_index = {
                executor.submit(
                    _run_validator_job_group,
                    group,
                    agent_runner=agent_runner,
                ): group_index
                for group_index, group in enumerate(grouped_jobs)
            }
            for future in concurrent.futures.as_completed(future_by_group_index):
                group_results[future_by_group_index[future]] = future.result()

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
    return validator_results, materialization_items


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
                "field_path": request.target.field_path,
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


def _run_single_validator_job(
    job: _DispatchJob,
    *,
    agent_runner: DomainValidatorAgentRunner,
) -> DomainValidatorResultBase:
    request = job.request
    try:
        raw_output = agent_runner(request, binding=job.match.binding)
        validator_result = _validated_result_from_agent_output(
            raw_output,
            request=request,
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

    from agents import AgentOutputSchema, Runner

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
    output_type = getattr(agent, "output_type", None)
    if is_domain_validator_result_schema(output_type):
        runtime_agent = copy.copy(agent)
        runtime_agent.output_type = AgentOutputSchema(
            output_type,
            strict_json_schema=False,
        )
        agent = runtime_agent

    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    if hasattr(Runner, "run_sync"):
        run_kwargs: dict[str, Any] = {"input": payload}
        if binding.max_tool_calls is not None:
            run_kwargs["max_turns"] = binding.max_tool_calls
        return Runner.run_sync(agent, **run_kwargs)
    raise RuntimeError("OpenAI Agents Runner.run_sync is unavailable")


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


def _default_package_scoped_validator_runner() -> DomainValidatorAgentRunner:
    if _running_event_loop_exists():
        return run_package_scoped_validator_agent_in_worker_thread
    return run_package_scoped_validator_agent


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
        if _missing_resolved_value(result.resolved_values.get(field_name))
    ]
    if not missing_fields:
        return result

    return result.model_copy(
        update={
            "status": "unresolved",
            "missing_expected_fields": missing_fields,
            "explanation": (
                "Validator result omitted expected resolved field(s): "
                + ", ".join(missing_fields)
            ),
        },
    )


def _missing_resolved_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


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
            {
                "provider": "domain_validator_dispatch",
                "method": reason,
                "query": {
                    "request_id": request.request_id,
                    "selected_inputs": dict(request.selected_inputs),
                },
                "result_count": 0,
                "outcome": "error",
                "message": explanation,
            }
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
            {
                "provider": "domain_validator_dispatch",
                "method": "unsupported_provider_taxon_mapping",
                "query": query,
                "result_count": 0,
                "outcome": "blocked",
                "message": explanation,
            }
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
    try:
        for attempt in result.lookup_attempts:
            lookup_status_for_validator_outcome(attempt.outcome)
        if result.status == "resolved" and not any(
            attempt.outcome == "success" for attempt in result.lookup_attempts
        ):
            raise ValueError(
                "Resolved validator result must include at least one successful "
                "lookup_attempt or explicit non-lookup validation attempt"
            )
        if result.status == "unresolved":
            validator_failure_classification(result)
    except ValueError as exc:
        return _unresolved_result_for_dispatch_problem(
            request,
            reason="invalid_schema",
            explanation=f"Validator agent returned incompatible output: {exc}",
        )
    return result


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
    "dispatch_active_validator_bindings",
    "preflight_unresolved_validator_result",
    "run_package_scoped_validator_agent_in_worker_thread",
    "run_package_scoped_validator_agent",
    "unresolved_validator_result_for_dispatch_problem",
    "validator_result_from_agent_output",
]
