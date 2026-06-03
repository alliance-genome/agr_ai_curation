"""Soft budget so the generated contract can't silently re-bloat."""
from src.lib.prompts.size_report import core_layer_sizes

# Post-slim ceiling (chars) for core_generated. Generous headroom over the
# expected ~1-1.5K compact contract; tighten in a later pass if desired.
CORE_GENERATED_BUDGET = 2500


def test_core_generated_within_budget_for_all_agents():
    report = core_layer_sizes()
    assert report, "expected at least one agent with core layers"
    over = {
        agent_id: sizes["core_generated"]
        for agent_id, sizes in report.items()
        if sizes.get("core_generated", 0) > CORE_GENERATED_BUDGET
    }
    assert not over, f"core_generated over budget ({CORE_GENERATED_BUDGET} chars): {over}"
