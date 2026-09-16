"""Explicit shallow tool-result contracts and invocation-local capture ownership."""

from dataclasses import dataclass, field
import json
from typing import Any
from uuid import uuid4

from src.lib.observability.runtime import report_runtime_exception
from src.lib.observability.sentry import hash_sentry_identifier


@dataclass(frozen=True)
class ToolOutcome:
    success: bool = True
    operational_code: str | None = None


def result_mapping(output: Any) -> dict | None:
    if hasattr(output, "model_dump"):
        output = output.model_dump(mode="json")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (ValueError, TypeError):
            return None
    return output if isinstance(output, dict) else None


def classify_tool_result(output: Any, *, studio: bool = False) -> ToolOutcome:
    value = result_mapping(output)
    if value is None:
        return ToolOutcome()
    if (
        value.get("failure_kind") == "operational"
        and value.get("code") == "flow_authoring_compile_failed"
    ):
        return ToolOutcome(False, "flow_authoring_compile_failed")
    # Lookup/builder result contract: both fields are declared in the result
    # schema. A status field alone (or nested biological data) is not a contract.
    if "lookup_status" in value and "failure_classification" in value:
        if value.get("lookup_status") == "transient":
            return ToolOutcome(False, "lookup_dependency_failure")
        return ToolOutcome(value.get("status") != "error")
    if studio:
        return ToolOutcome(
            not (value.get("success") is False or value.get("status") == "error")
        )
    return ToolOutcome()


class ReturnedToolFailure(RuntimeError):
    """Sanitized exception identity retained for linked terminal propagation."""


@dataclass
class ToolFailureState:
    """One turn/run's failure identities; never shared globally or across users."""

    failures: dict[str, tuple[ReturnedToolFailure, bool]] = field(default_factory=dict)
    invocation_ids: dict[str, str] = field(default_factory=dict)

    def capture(
        self,
        *,
        code: str,
        tool_name: str,
        invocation_id: str,
        trace_id: str | None,
        session_id: str | None
    ) -> tuple[str, bool]:
        if invocation_id in self.invocation_ids:
            identity = self.invocation_ids[invocation_id]
            return identity, self.failures[identity][1]
        identity = uuid4().hex
        exc = ReturnedToolFailure(code)
        try:
            captured = report_runtime_exception(
                exc,
                component="tool_result",
                operation=code,
                tags={
                    "tool_name": tool_name,
                    "ai_curation.trace.id_hash": hash_sentry_identifier(trace_id),
                    "ai_curation.chat.session_id_hash": hash_sentry_identifier(
                        session_id
                    ),
                },
                context={"failure_id": identity},
            )
        except Exception:
            captured = False
        if captured:
            setattr(exc, "_ai_curation_sentry_captured", True)
        self.failures[identity] = (exc, captured)
        self.invocation_ids[invocation_id] = identity
        return identity, captured
