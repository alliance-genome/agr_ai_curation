"""Flow steps the flow policy refuses, and what to tell the curator about each.

One description serves every place that meets such a step: saving, opening,
duplicating and exporting a flow, starting a batch, Agent Studio's flow checks,
and the run refusal. The kind of step is read from structure only (the internal
agent IDs, the custom-agent key prefix, category and supervisor routing); the
extraction agents that run a validator come from the domain-pack bindings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.lib.agent_studio.flow_agent_policy import (
    INTERNAL_FLOW_AGENT_IDS,
    agent_allows_ordinary_flow_step,
)

RefusedStepKind = Literal["validator", "custom_validator", "internal"]

# Authoring (save/open/batch/Studio) codes and the run refusal's reason codes.
SAVE_CODES: dict[RefusedStepKind, str] = {
    "validator": "attachment_only_agent_in_control_flow",
    "custom_validator": "custom_validator_in_control_flow",
    "internal": "internal_agent_in_control_flow",
}
RUN_CODES: dict[RefusedStepKind, str] = {
    "validator": "attachment_only_validator",
    "custom_validator": "custom_validator_step",
    "internal": "internal_agent_step",
}

CUSTOM_AGENT_KEY_PREFIX = "ca_"


def join_names(names: Sequence[str]) -> str:
    """"A", "A and B", "A, B and C"."""

    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def checking_sentence(validator_name: str, checking_extractors: Sequence[str]) -> str:
    """Which extraction steps run a validator's check, or that none does."""

    if not checking_extractors:
        return f"No extraction step runs the {validator_name} check at present."
    names = join_names(list(checking_extractors))
    if len(checking_extractors) == 1:
        return f"{names} runs the {validator_name} check automatically; keep or add it in the flow."
    return (
        f"{names} run the {validator_name} check automatically; keep or add the one for "
        "your records."
    )


@dataclass(frozen=True)
class RefusedFlowStep:
    kind: RefusedStepKind
    agent_name: str
    # Validator kind: the extraction agents that run this check automatically.
    checking_extractors: tuple[str, ...] = ()
    # Custom kind: a copy of a validator whose bindings allow custom-detail reuse.
    custom_detail: bool = False

    @property
    def save_code(self) -> str:
        return SAVE_CODES[self.kind]

    @property
    def run_code(self) -> str:
        return RUN_CODES[self.kind]

    @property
    def message(self) -> str:
        if self.kind == "validator":
            return (
                f"{self.agent_name} is a validator. Validators check an extraction step's "
                "records automatically and cannot be flow steps."
            )
        if self.kind == "custom_validator":
            return (
                f"{self.agent_name} is your copy of a validator, and a copy of a validator "
                "cannot run in a flow."
            )
        return f"{self.agent_name} is an internal agent and cannot be a flow step."

    @property
    def fix_hint(self) -> str:
        if self.kind == "validator":
            return f"Remove this step. {self.checking_sentence}"
        if self.kind == "custom_validator" and self.custom_detail:
            return (
                "Remove this step. You can attach your copy as the validator on a detail of "
                "your own Custom Output Structure, as long as it keeps the answer format of "
                "the validator it was copied from."
            )
        return "Remove this step."

    @property
    def checking_sentence(self) -> str:
        return checking_sentence(self.agent_name, self.checking_extractors)

    @property
    def explanation(self) -> str:
        """The message and what to do, as one text (open warnings, duplicate, batch)."""

        return f"{self.message} {self.fix_hint}"


def refused_flow_step(
    agent_id: str,
    entry: Mapping[str, Any] | None,
    *,
    active_group_ids: Sequence[str] = (),
) -> RefusedFlowStep | None:
    """Describe a step the flow policy refuses; ``None`` when it may be a flow step."""

    if entry is None or agent_allows_ordinary_flow_step(agent_id, entry):
        return None
    from src.lib.flows.validation_attachments import (
        extraction_agents_running_validator,
        validator_supports_custom_detail,
    )

    name = str(entry.get("name") or entry.get("display_name") or agent_id)
    if agent_id in INTERNAL_FLOW_AGENT_IDS:
        return RefusedFlowStep("internal", name)
    if agent_id.startswith(CUSTOM_AGENT_KEY_PREFIX):
        return RefusedFlowStep(
            "custom_validator", name,
            custom_detail=validator_supports_custom_detail(entry.get("template_source")),
        )
    return RefusedFlowStep(
        "validator", name,
        checking_extractors=extraction_agents_running_validator(
            agent_id, active_group_ids=list(active_group_ids),
        ),
    )


__all__ = [
    "CUSTOM_AGENT_KEY_PREFIX",
    "RUN_CODES",
    "RefusedFlowStep",
    "SAVE_CODES",
    "checking_sentence",
    "join_names",
    "refused_flow_step",
]
