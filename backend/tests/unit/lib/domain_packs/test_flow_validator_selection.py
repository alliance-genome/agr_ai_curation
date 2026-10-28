"""Effective source binding choices and task isolation."""
import asyncio

import pytest

from src.lib.domain_packs.flow_validator_selection import (
    current_flow_validator_selections, effective_flow_validation_groups,
    reset_flow_validator_selections, set_flow_validator_selections,
)


def group(binding="same", state="replaced", node="custom"):
    return {"binding_id": binding, "state": state, "validator_node_id": node}


def test_automatic_and_optout_choices_are_preserved():
    groups = [group(state="automatic"), group("other", "skipped")]
    assert effective_flow_validation_groups(groups) == groups


@pytest.mark.parametrize("groups", [
    [group(), group(node="other-custom")],
    [group(), group(state="skipped")],
    [group(node=None)],
])
def test_conflicting_choices_fail_before_dispatch(groups):
    with pytest.raises(ValueError):
        set_flow_validator_selections(groups)
    assert current_flow_validator_selections() == {}


@pytest.mark.asyncio
async def test_choices_are_task_local_and_reset_on_failure():
    async def run(binding):
        token = set_flow_validator_selections([group(binding, "skipped")])
        try:
            await asyncio.sleep(0)
            assert set(current_flow_validator_selections()) == {binding}
            raise RuntimeError("custom failure")
        except RuntimeError:
            pass
        finally:
            reset_flow_validator_selections(token)
        assert current_flow_validator_selections() == {}
    await asyncio.gather(run("first"), run("second"))
