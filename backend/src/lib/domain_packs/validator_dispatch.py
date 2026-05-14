"""Active domain-pack validator binding dispatch.

This service owns execution for package-scoped active validator bindings.  It
keeps biological validation out of the legacy validation supervisor path while
reusing the shared selector and envelope finding contracts.
"""

from __future__ import annotations

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
from .validation_supervisor import append_validation_findings_to_envelope


LOGGER = logging.getLogger(__name__)

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


def dispatch_active_validator_bindings(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    *,
    actor_id: str = "domain_validator_dispatch",
    registry: DomainPackValidationRegistry | None = None,
    runner: DomainValidatorAgentRunner | None = None,
) -> ActiveValidatorDispatchResult:
    """Dispatch active validator bindings and append result findings."""

    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    matches = validation_registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )
    agent_runner = runner or _run_package_scoped_validator_agent

    selector_findings: list[ValidationFinding] = []
    materialization_items: list[ValidatorResultMaterializationInput] = []
    validator_results: list[DomainValidatorResultBase] = []
    for match in _ordered_matches(matches):
        selector_result = build_domain_validation_request(match)
        if selector_result.findings:
            selector_findings.extend(selector_result.findings)
            continue
        if selector_result.request is None:
            continue

        request = selector_result.request
        try:
            raw_output = agent_runner(request, binding=match.binding)
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

        validator_result = _enforce_expected_result_fields(
            validator_result,
            request=request,
        )
        validator_result = _ensure_classifiable_validator_result(
            validator_result,
            request=request,
        )
        validator_results.append(validator_result)
        materialization_items.append(
            ValidatorResultMaterializationInput(
                match=match,
                request=request,
                result=validator_result,
            )
        )

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


def _run_package_scoped_validator_agent(
    request: DomainValidationRequest,
    *,
    binding: ValidatorBinding,
) -> Any:
    """Execute the package-owned validator through the unified agent runtime."""

    from agents import Runner

    from src.lib.agent_studio.catalog_service import get_agent_by_id
    from src.lib.agent_studio.system_agent_sync import canonical_system_agent_key
    from src.lib.config.agent_loader import get_agent_definition_for_package

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
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    if hasattr(Runner, "run_sync"):
        run_kwargs: dict[str, Any] = {"input": payload}
        if binding.max_tool_calls is not None:
            run_kwargs["max_turns"] = binding.max_tool_calls
        return Runner.run_sync(agent, **run_kwargs)
    raise RuntimeError("OpenAI Agents Runner.run_sync is unavailable")


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
        or result.target.model_dump(mode="json") != request.target.model_dump(
            mode="json"
        )
    ):
        return _unresolved_result_for_dispatch_problem(
            request,
            reason="invalid_schema",
            explanation=(
                "Validator agent output did not match the dispatched request "
                "identity or target."
            ),
        )
    return result


def _extract_structured_output(raw_output: Any) -> Any:
    output = raw_output
    # Support OpenAI SDK run results, Pydantic models, and lightweight fake runners.
    if hasattr(output, "final_output"):
        output = output.final_output
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


def _ensure_classifiable_validator_result(
    result: DomainValidatorResultBase,
    *,
    request: DomainValidationRequest,
) -> DomainValidatorResultBase:
    try:
        for attempt in result.lookup_attempts:
            lookup_status_for_validator_outcome(attempt.outcome)
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


__all__ = [
    "ActiveValidatorDispatchResult",
    "DomainValidatorAgentRunner",
    "dispatch_active_validator_bindings",
    "unresolved_validator_result_for_dispatch_problem",
    "validator_result_from_agent_output",
]
