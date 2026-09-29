"""Flexible extraction (``unprofiled_generic``) is retired for new agents and revisions.

Existing Flexible agents keep running and stay editable until they are converted;
nothing may create a Flexible revision for an agent whose current head is not
already Flexible (create, clone, or restore to an older Flexible revision).
"""

from src.schemas.agent_execution_revision import AgentOutputContract

FLEXIBLE_RETIRED = "Flexible extraction is retired. Choose Custom Output Structure for this agent."


def require_flexible_not_new(
    selected: AgentOutputContract, previous: AgentOutputContract | None,
) -> None:
    """Refuse ``selected`` when it is Flexible and ``previous`` (the current head) is not."""
    if selected.output_mode != "unprofiled_generic":
        return
    if previous is not None and previous.output_mode == "unprofiled_generic":
        return
    raise ValueError(FLEXIBLE_RETIRED)
