"""Soft budget so the generated contract can't silently re-bloat."""
from src.lib.prompts.size_report import core_layer_sizes

# Post-slim ceiling (chars) for core_generated. Generous headroom over the
# expected ~1-1.5K compact contract; tighten in a later pass if desired.
CORE_GENERATED_BUDGET = 2500
# The reviewer carries the locked multi-phase protocol: exact history, explicit
# decisions, targeted extraction, source selection, and shared retry budgets.
# Preserve those rules rather than clipping them to the single-specialist budget.
# ALL-1445 adds locked curator authority, selected-record scope, and retained
# sibling/dependency review coverage. The measured contract is 4349 chars.
# This is a source-growth regression ceiling, not a runtime truncation limit.
AGENT_CORE_GENERATED_BUDGETS = {"validation_reviewer": 4500}


def test_core_generated_within_budget_for_all_agents():
    report = core_layer_sizes()
    assert report, "expected at least one agent with core layers"
    over = {
        agent_id: sizes["core_generated"]
        for agent_id, sizes in report.items()
        if sizes.get("core_generated", 0) > AGENT_CORE_GENERATED_BUDGETS.get(agent_id, CORE_GENERATED_BUDGET)
    }
    assert not over, f"core_generated over budget ({CORE_GENERATED_BUDGET} chars): {over}"
