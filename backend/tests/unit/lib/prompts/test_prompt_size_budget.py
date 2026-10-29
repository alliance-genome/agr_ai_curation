"""Soft budget so the generated contract can't silently re-bloat."""
from src.lib.prompts.size_report import core_layer_sizes

# Post-slim ceiling (chars) for core_generated. Generous headroom over the
# expected ~1-1.5K compact contract; tighten in a later pass if desired.
CORE_GENERATED_BUDGET = 2500
# The reviewer carries the locked multi-phase protocol: exact history, explicit
# decisions, targeted extraction, source selection, and shared retry budgets.
# Preserve those rules rather than clipping them to the single-specialist budget.
# ALL-1445 adds locked curator authority, selected-record scope, and retained
# sibling/dependency review coverage. ALL-1446 adds adaptive exact source reads,
# original/contribution provenance, and complete-page evidence rules. The
# incremental source/answer navigation and runtime compaction guidance bring the
# measured contract to 6242 chars. Explicit whole-target/component decision routing
# brings it to 6788 chars. Explicit whole-record retirement routing adds one
# reviewed sentence (149 chars), bringing it to 6937; keep the ceiling close.
# Outstanding-work navigation, explicit optional-absence assessment, and awaited
# execution guidance add 592 reviewed characters, bringing it to 7529. Keep
# only 71 characters of headroom for accidental source growth.
# The reviewed unsupported-lookup guidance adds 171 characters (7700 total),
# distinguishing accepted unresolved applicability from a confirmed identity.
# Keep 100 characters of headroom; the protocol itself is never truncated.
# This is a source-growth regression ceiling, not a runtime truncation limit.
AGENT_CORE_GENERATED_BUDGETS = {"validation_reviewer": 7800}


def test_core_generated_within_budget_for_all_agents():
    report = core_layer_sizes()
    assert report, "expected at least one agent with core layers"
    over = {
        agent_id: sizes["core_generated"]
        for agent_id, sizes in report.items()
        if sizes.get("core_generated", 0) > AGENT_CORE_GENERATED_BUDGETS.get(agent_id, CORE_GENERATED_BUDGET)
    }
    assert not over, f"core_generated over budget ({CORE_GENERATED_BUDGET} chars): {over}"
