"""Flow steps the flow policy refuses, and what to tell the curator about each.

One description serves every place that meets such a step: saving, opening,
duplicating and exporting a flow, starting a batch, Agent Studio's flow checks,
and the run refusal. The kind of step is read from structure only (the
custom-agent key prefix, category and supervisor routing); the extraction
agents that run a validator come from the domain-pack bindings.

Internal agents (``supervisor``, ``task_input``) are refused by ``FlowDefinition``
itself and never reach a flow step, so they have no description here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.lib.agent_studio.flow_agent_policy import (
    INTERNAL_FLOW_AGENT_IDS,
    agent_allows_ordinary_flow_step,
)

RefusedStepKind = Literal["validator", "custom_validator"]

# Authoring (save/open/batch/Studio) codes and the run refusal's reason codes.
SAVE_CODES: dict[RefusedStepKind, str] = {
    "validator": "attachment_only_agent_in_control_flow",
    "custom_validator": "custom_validator_in_control_flow",
}
RUN_CODES: dict[RefusedStepKind, str] = {
    "validator": "attachment_only_validator",
    "custom_validator": "custom_validator_step",
}

CUSTOM_AGENT_KEY_PREFIX = "ca_"

# Where a curator can see the checks an extraction step does run.
AUTOMATIC_CHECKS_PLACE = "under Automatic checks on that step in Flow Builder"


def join_names(names: Sequence[str]) -> str:
    """"A", "A and B", "A, B and C"."""

    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def checking_sentence(validator_name: str, checking_extractors: Sequence[str]) -> str:
    """Which extraction steps run a validator's check, or that none does."""

    if not checking_extractors:
        return (
            f"No extraction step runs the {validator_name} check at present; the checks each "
            f"extraction step does run are listed {AUTOMATIC_CHECKS_PLACE}."
        )
    names = join_names(list(checking_extractors))
    if len(checking_extractors) == 1:
        return f"{names} runs the {validator_name} check automatically; keep or add it in the flow."
    return (
        f"{names} run the {validator_name} check automatically; keep or add the one for "
        "your records."
    )


def custom_detail_sentence(copy_name: str) -> str:
    return (
        f"You can attach {copy_name} as the validator on a detail of your own Custom Output "
        "Structure, as long as it keeps the answer format of the validator it was copied from."
    )


@dataclass(frozen=True)
class RefusedFlowStep:
    kind: RefusedStepKind
    agent_name: str
    # Validator kind: the extraction agents that run this check automatically.
    checking_extractors: tuple[str, ...] = ()
    # Custom kind: a copy of a validator whose bindings allow custom-detail reuse.
    custom_detail: bool = False
    # The person reading is the flow's owner (only the owner can change it).
    owner: bool = True

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
        whose = "your copy" if self.owner else "a copy, saved by the flow's owner,"
        return f"{self.agent_name} is {whose} of a validator, and a copy of a validator cannot run in a flow."

    @property
    def fix_hint(self) -> str:
        action = "Remove this step." if self.owner else "Only the flow's owner can remove this step."
        if self.kind == "validator":
            return f"{action} {checking_sentence(self.agent_name, self.checking_extractors)}"
        if self.custom_detail and self.owner:
            return f"{action} {custom_detail_sentence('your copy')}"
        return action

    @property
    def explanation(self) -> str:
        """The message and what to do, as one text (open warnings, duplicate, batch)."""

        return f"{self.message} {self.fix_hint}"

    def as_unavailable_step(self, step: int, agent_id: str) -> dict[str, Any]:
        """The executor's record of this step; the run text is built from it."""

        return {
            "step": step,
            "agent_id": agent_id,
            "agent_name": self.agent_name,
            "reason": self.explanation,
            "reason_code": self.run_code,
            "checking_extractors": list(self.checking_extractors),
            "custom_detail": self.custom_detail,
            "owner": self.owner,
        }


def refused_flow_step(
    agent_id: str,
    entry: Mapping[str, Any] | None,
    *,
    active_group_ids: Sequence[str] = (),
    owner: bool = True,
) -> RefusedFlowStep | None:
    """Describe a step the flow policy refuses; ``None`` when it may be a flow step."""

    if entry is None or agent_allows_ordinary_flow_step(agent_id, entry):
        return None
    if agent_id in INTERNAL_FLOW_AGENT_IDS:
        raise ValueError(f"Internal agent '{agent_id}' reached a flow step; FlowDefinition refuses it")
    from src.lib.flows.validation_attachments import (
        extraction_agents_running_validator,
        validator_supports_custom_detail,
    )

    name = str(entry.get("name") or entry.get("display_name") or agent_id)
    if agent_id.startswith(CUSTOM_AGENT_KEY_PREFIX):
        return RefusedFlowStep(
            "custom_validator", name,
            custom_detail=validator_supports_custom_detail(entry.get("template_source")),
            owner=owner,
        )
    return RefusedFlowStep(
        "validator", name,
        checking_extractors=extraction_agents_running_validator(
            agent_id, active_group_ids=list(active_group_ids),
        ),
        owner=owner,
    )


__all__ = [
    "AUTOMATIC_CHECKS_PLACE",
    "CUSTOM_AGENT_KEY_PREFIX",
    "RUN_CODES",
    "RefusedFlowStep",
    "SAVE_CODES",
    "checking_sentence",
    "custom_detail_sentence",
    "join_names",
    "refused_flow_step",
]
