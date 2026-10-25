"""Recover a selected execution's configured route without freezing source data."""

from copy import deepcopy
from dataclasses import replace

from . import store


def restore_execution_binding(db, artifact_id, attempt_fact, attempt, match):
    execution = store._fact(db, artifact_id, attempt_fact.parent_id, kind="execution")
    receipt = store._content(db, execution)["receipt"]
    if "plan_ref" not in receipt:
        # Direct dispatch has no review plan. The caller still checks the full
        # recipe fingerprint, so this cannot authorize a saved flow override.
        return match
    plan_fact = store._fact(db, artifact_id, receipt["plan_ref"], kind="review_chat_plan")
    plan = store._content(db, plan_fact)
    episode_id = attempt["input"].get("review_episode_id")
    if (not episode_id or plan_fact.parent_id != episode_id
            or plan["envelope"]["metadata"].get("review_episode_id") != episode_id):
        raise store.HistoryConflict("Selected execution plan belongs to another episode")
    binding_id = match.binding.binding_id
    definitions = [item for item in plan["registry"]["bindings"]
                   if item["binding_id"] == binding_id]
    if len(definitions) != 1:
        raise store.HistoryConflict("Selected execution plan has no unique binding")
    contract = plan["contracts"].get(binding_id)
    if contract is None or receipt.get("contracts", {}).get(binding_id) != contract:
        raise store.HistoryConflict("Selected execution differs from its frozen plan contract")
    saved_raw = definitions[0]["raw"]
    current_raw = match.binding.raw
    saved_flow = saved_raw.get("flow_validator")
    if (current_raw.get("flow_validator") is not None
            and current_raw["flow_validator"] != saved_flow):
        raise store.HistoryConflict("Selected flow validator contract changed")
    if ({key: value for key, value in saved_raw.items() if key != "flow_validator"}
            != {key: value for key, value in current_raw.items() if key != "flow_validator"}):
        raise store.HistoryConflict("Selected underlying validator binding changed")
    if saved_flow is None:
        return match
    # Retain current object identity, source values and address. Only the exact
    # configured execution route comes from the immutable plan.
    return replace(match, binding=replace(match.binding, batch_enabled=False,
        raw={**current_raw, "flow_validator": deepcopy(saved_flow)}))
