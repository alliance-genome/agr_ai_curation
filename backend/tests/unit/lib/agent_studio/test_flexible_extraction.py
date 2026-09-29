import pytest

from src.lib.agent_studio.flexible_extraction import FLEXIBLE_RETIRED, require_flexible_not_new
from src.schemas.agent_execution_revision import AgentOutputContract

FLEXIBLE = AgentOutputContract(output_state="structured_extraction", output_mode="unprofiled_generic")
NONE = AgentOutputContract(output_state="none")


def test_other_modes_are_untouched():
    require_flexible_not_new(NONE, None)
    require_flexible_not_new(NONE, FLEXIBLE)


def test_an_existing_flexible_head_may_keep_flexible():
    require_flexible_not_new(FLEXIBLE, FLEXIBLE)


@pytest.mark.parametrize("previous", [None, NONE])
def test_new_flexible_is_refused(previous):
    with pytest.raises(ValueError) as refused:
        require_flexible_not_new(FLEXIBLE, previous)
    assert str(refused.value) == FLEXIBLE_RETIRED == (
        "Flexible extraction is retired. Choose Custom Output Structure for this agent.")
