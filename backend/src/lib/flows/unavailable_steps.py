"""Safe reason codes and curator messages for flow steps that cannot start.

The executor keeps a free-text ``reason`` for logs, but that text can embed
curator-written names or private configuration from exceptions. Only the
stable codes below are stored in chat records or returned to Agent Studio.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

FLOW_STEP_UNAVAILABLE_REASON_CODES = frozenset({
    "document_required",
    "attachment_only_validator",
    "agent_unresolvable",
    "missing_agent_id",
    "provider_disabled",
    "agent_unavailable",
})
_FALLBACK_REASON_CODE = "agent_unavailable"


def flow_step_unavailable_reason_code(step: Mapping[str, Any]) -> str:
    """Return the step's known reason code, or the generic fallback."""

    code = step.get("reason_code")
    if code in FLOW_STEP_UNAVAILABLE_REASON_CODES:
        return str(code)
    if step.get("error_code") == "provider_disabled":
        return "provider_disabled"
    return _FALLBACK_REASON_CODE


def stored_flow_step_reason_codes(value: Any) -> list[dict[str, Any]] | None:
    """Read persisted per-step codes; ``None`` marks records without codes."""

    if not isinstance(value, list):
        return None
    codes: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        step = item.get("step")
        codes.append({
            "step": step if isinstance(step, int) and not isinstance(step, bool) else None,
            "reason_code": flow_step_unavailable_reason_code({"reason_code": item.get("reason_code")}),
        })
    return codes


def _label(step: Mapping[str, Any]) -> str:
    return f"{step.get('step')} ({step.get('agent_name') or 'Unknown Agent'})"


def _subject(steps: Sequence[Mapping[str, Any]]) -> str:
    labels = [_label(step) for step in steps]
    if len(labels) == 1:
        return f"Step {labels[0]}"
    return f"Steps {', '.join(labels[:-1])} and {labels[-1]}"


def _cause_sentence(code: str, steps: Sequence[Mapping[str, Any]]) -> str:
    subject = _subject(steps)
    one = len(steps) == 1
    if code == "document_required":
        return (
            f"{subject} {'needs' if one else 'need'} a PDF. Open Documents in the top "
            "navigation and load a paper into chat, then run the flow again."
        )
    if code == "attachment_only_validator":
        return (
            f"{subject} {'is an attachment-only validator' if one else 'are attachment-only validators'}. "
            f"In Flow Builder, remove {'it' if one else 'them'} from the ordinary steps and add "
            f"{'it' if one else 'them'} as a validation attachment on an extraction step."
        )
    if code == "agent_unresolvable":
        return (
            f"{subject} {'uses an agent that is' if one else 'use agents that are'} no longer available. "
            f"In Flow Builder, choose an available agent for {'that step' if one else 'those steps'}."
        )
    if code == "missing_agent_id":
        return (
            f"{subject} {'has' if one else 'have'} no agent selected. "
            f"In Flow Builder, choose an agent for {'that step' if one else 'those steps'}."
        )
    if code == "provider_disabled":
        return (
            f"{subject}: a model provider is disabled by policy. Explicitly choose an approved "
            "model, save a new agent revision, and update the flow's pinned reference. "
            "Credential setup, PDFs, and validator placement do not resolve this policy block."
        )
    return (
        f"{subject} could not be prepared. In Flow Builder, check that "
        f"{'the step uses' if one else 'those steps use'} an available agent; if this keeps "
        "happening, report the problem using the feedback button."
    )


def build_flow_step_unavailable_message(steps: Sequence[Mapping[str, Any]]) -> str:
    """Build the curator-facing refusal, naming each step's cause."""

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for step in steps:
        grouped.setdefault(flow_step_unavailable_reason_code(step), []).append(step)
    if set(grouped) == {"provider_disabled"}:
        step_labels = ", ".join(_label(step) for step in steps)
        return (
            f"Flow cannot start: a model provider for these steps is disabled by policy: {step_labels}. "
            "Explicitly choose an approved model, save a new agent revision, and update the flow's "
            "pinned reference. Credential setup, PDFs, and validator placement do not resolve this policy block."
        )
    return " ".join(
        ["Flow cannot start."]
        + [_cause_sentence(code, grouped_steps) for code, grouped_steps in grouped.items()]
    )
