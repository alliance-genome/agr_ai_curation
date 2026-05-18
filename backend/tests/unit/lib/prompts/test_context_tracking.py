"""Additional unit tests for prompt context tracking."""

import uuid
from types import SimpleNamespace

import pytest
from src.lib.prompts.context import (
    PromptOverride,
    append_pending_prompt_runtime_context,
    bind_prompt_run,
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


def _manifest(agent_id: str, content: str, hash_value: str) -> dict:
    return {
        "agent_id": agent_id,
        "layers": [
            {
                "id": f"{agent_id}:base_prompt",
                "kind": "base_prompt",
                "title": "Editable base prompt",
                "content": content,
                "provenance": "prompt_template:system",
                "editable": True,
                "locked": False,
                "source_ref": f"prompt_templates:active:{agent_id}:system:base:v1",
                "hash": f"{hash_value}:layer",
            }
        ],
        "hash": hash_value,
    }


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


def test_pending_prompt_runtime_context_updates_assembly_before_commit():
    clear_prompt_context()
    prompt = _prompt("base")
    layer_manifest = {
        "agent_id": "gene",
        "layers": [
            {
                "id": "gene:base_prompt",
                "kind": "base_prompt",
                "title": "Editable base prompt",
                "content": "base",
                "provenance": "prompt_template:system",
                "editable": True,
                "locked": False,
                "source_ref": "prompt_templates:active:gene:system:base:v1",
                "hash": "base-layer-hash",
            }
        ],
        "hash": "hash-1",
    }
    set_pending_prompts(
        "Gene Specialist",
        [prompt],
        effective_prompt_hash="hash-1",
        layer_manifest=layer_manifest,
    )

    append_pending_prompt_runtime_context(
        "Gene Specialist",
        layer_id_suffix="tool_efficiency",
        title="Tool efficiency runtime instruction",
        content="Batch large lookup lists in one tool call.",
        source_ref="test:tool_efficiency",
    )
    commit_pending_prompts("Gene Specialist")

    used_run = get_used_prompt_runs()[0]
    assert used_run.assembly is not None
    assert used_run.assembly.effective_prompt_hash != "hash-1"
    layers = used_run.assembly.layer_manifest["layers"]
    assert [layer["id"] for layer in layers] == [
        "gene:base_prompt",
        "gene:runtime_context:tool_efficiency",
    ]
    assert layers[-1]["content"] == "Batch large lookup lists in one tool call."


def test_duplicate_agent_names_commit_by_bound_prompt_run_id():
    clear_prompt_context()
    prompt_a = _prompt("step 1")
    prompt_b = _prompt("step 2")
    agent_a = SimpleNamespace(name="Gene Specialist")
    agent_b = SimpleNamespace(name="Gene Specialist")

    bind_prompt_run(
        agent_a,
        set_pending_prompts(
            agent_a.name,
            [prompt_a],
            effective_prompt_hash="hash-step-1",
            layer_manifest=_manifest("gene", "step 1", "hash-step-1"),
        ),
    )
    bind_prompt_run(
        agent_b,
        set_pending_prompts(
            agent_b.name,
            [prompt_b],
            effective_prompt_hash="hash-step-2",
            layer_manifest=_manifest("gene", "step 2", "hash-step-2"),
        ),
    )

    with pytest.raises(ValueError, match="ambiguous"):
        commit_pending_prompts("Gene Specialist")

    commit_pending_prompts(agent_a)
    commit_pending_prompts(agent_b)

    used_runs = get_used_prompt_runs()
    assert [run.prompts for run in used_runs] == [[prompt_a], [prompt_b]]
    assert [run.assembly.effective_prompt_hash for run in used_runs if run.assembly] == [
        "hash-step-1",
        "hash-step-2",
    ]
    assert [
        run.assembly.layer_manifest["layers"][0]["content"]
        for run in used_runs
        if run.assembly
    ] == ["step 1", "step 2"]


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
