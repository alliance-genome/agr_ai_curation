"""Additional unit tests for prompt context tracking."""

import uuid

from src.lib.prompts.context import (
    PromptOverride,
    clear_prompt_context,
    clear_prompt_override,
    commit_pending_prompts,
    get_pending_for_agent,
    get_prompt_override,
    get_used_prompt_runs,
    get_used_prompts,
    set_pending_prompts,
    set_prompt_override,
)
from src.models.sql.prompts import PromptTemplate


def _prompt(content: str) -> PromptTemplate:
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content=content,
        version=1,
        is_active=True,
    )


def test_pending_prompts_commit_and_used_tracking():
    clear_prompt_context()
    prompt_a = _prompt("base")
    prompt_b = _prompt("rules")
    original = [prompt_a, prompt_b]

    layer_manifest = {"agent_id": "gene", "layers": [], "hash": "hash-1"}
    set_pending_prompts(
        "Gene Specialist",
        original,
        effective_prompt_hash="hash-1",
        layer_manifest=layer_manifest,
    )
    pending = get_pending_for_agent("Gene Specialist")
    assert pending == [prompt_a, prompt_b]

    # Ensure we stored a copy, not the caller's mutable list.
    original.clear()
    assert len(get_pending_for_agent("Gene Specialist")) == 2

    commit_pending_prompts("Gene Specialist")
    used = get_used_prompts()
    assert used == [prompt_a, prompt_b]
    used_runs = get_used_prompt_runs()
    assert len(used_runs) == 1
    assert used_runs[0].prompts == [prompt_a, prompt_b]
    assert used_runs[0].assembly is not None
    assert used_runs[0].assembly.effective_prompt_hash == "hash-1"
    assert used_runs[0].assembly.layer_manifest == layer_manifest

    # Strict audit trail: committing again logs again (no de-dupe).
    commit_pending_prompts("Gene Specialist")
    assert get_used_prompts() == [prompt_a, prompt_b, prompt_a, prompt_b]


def test_commit_pending_prompts_noop_for_unknown_agent():
    clear_prompt_context()
    commit_pending_prompts("Unknown Agent")
    assert get_used_prompts() == []


def test_prompt_override_set_get_and_clear():
    clear_prompt_context()
    assert get_prompt_override() is None

    override = PromptOverride(
        content="custom prompt",
        agent_name="gene",
        custom_agent_id=str(uuid.uuid4()),
        group_overrides={"WB": "wb rules"},
    )
    set_prompt_override(override)
    assert get_prompt_override() == override
    assert override.group_overrides == {"WB": "wb rules"}
    assert override.mod_overrides == {"WB": "wb rules"}

    clear_prompt_override()
    assert get_prompt_override() is None
