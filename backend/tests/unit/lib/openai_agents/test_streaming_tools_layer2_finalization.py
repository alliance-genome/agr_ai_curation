"""Layer 2 forced-tool finalization tests for streaming_tools.

Layer 2 makes a structured extraction/validation specialist physically unable to
deliver output as a bare final message and ends the run the instant the mandatory
finalize tool is accepted. It does this by:
  - tool_use_behavior = a conditional ToolsToFinalOutputFunction that ends the run
    on accepted finalize and continues otherwise (preserving reject/repair).
  - model_settings.tool_choice = "required" (cloned, source never mutated).
  - reset_tool_choice = False so tool_choice stays required across turns.

These tests deterministically cover what does NOT need a live model: the callback
decision and the apply-helper wiring (including the kill-switch). The real
forced-tool-choice loop dynamics across the live agents cannot be unit-tested.
"""

import json
from types import SimpleNamespace

from agents import Agent, ModelSettings

from src.lib.openai_agents import streaming_tools


def _finalization_state(accepted_payload=None):
    return streaming_tools._StructuredSpecialistFinalizationState(
        required=True,
        tool_name="finalize_pdf_extraction",
        agent_name="General PDF Extraction Agent",
        output_type_name="PdfExtractionResultEnvelope",
        config={"checks": ["pdf_evidence"]},
        max_attempts=6,
        accepted_payload=accepted_payload,
    )


def _runtime_agent(model_settings=None):
    # Use a plain object rather than Agent() to avoid SDK construction
    # validation; Layer 2 only reads/sets model_settings, tool_use_behavior,
    # and reset_tool_choice.
    return SimpleNamespace(
        name="runtime",
        model_settings=model_settings,
        tool_use_behavior="run_llm_again",
        reset_tool_choice=True,
    )


def test_tool_use_behavior_returns_final_output_when_accepted():
    payload = {"objects": [{"id": "X"}], "domain_pack_id": "pdf"}
    state = _finalization_state(accepted_payload=payload)

    behavior = streaming_tools._build_structured_finalization_tool_use_behavior(state)
    result = behavior(SimpleNamespace(), [])

    assert result.is_final_output is True
    assert result.final_output == json.dumps(payload)


def test_tool_use_behavior_continues_when_not_accepted():
    state = _finalization_state(accepted_payload=None)

    behavior = streaming_tools._build_structured_finalization_tool_use_behavior(state)
    result = behavior(SimpleNamespace(), [SimpleNamespace(output="rejected")])

    assert result.is_final_output is False
    assert result.final_output is None


def test_tool_use_behavior_continues_after_a_rejection_clears_acceptance():
    # A rejected attempt leaves accepted_payload None, so the callback keeps the
    # run going and the model is allowed to repair and retry.
    state = _finalization_state(accepted_payload=None)
    state.last_rejection = {"message": "needs repair"}

    behavior = streaming_tools._build_structured_finalization_tool_use_behavior(state)
    result = behavior(SimpleNamespace(), [])

    assert result.is_final_output is False
    assert result.final_output is None


def test_apply_layer2_sets_forced_tool_choice_and_callback_when_enabled():
    state = _finalization_state(accepted_payload=None)
    source_settings = ModelSettings(temperature=0.4, tool_choice="auto")
    agent = _runtime_agent(model_settings=source_settings)

    returned = streaming_tools._apply_layer2_forced_tool_finalization(agent, state)

    assert returned is agent
    assert callable(returned.tool_use_behavior)
    assert returned.reset_tool_choice is False
    assert returned.model_settings.tool_choice == "required"
    # Other model_settings fields are preserved.
    assert returned.model_settings.temperature == 0.4


def test_apply_layer2_does_not_mutate_source_model_settings():
    state = _finalization_state(accepted_payload=None)
    source_settings = ModelSettings(temperature=0.4, tool_choice="auto")
    agent = _runtime_agent(model_settings=source_settings)

    streaming_tools._apply_layer2_forced_tool_finalization(agent, state)

    # The shared/source model_settings object is cloned, never mutated.
    assert source_settings.tool_choice == "auto"
    assert agent.model_settings is not source_settings


def test_apply_layer2_handles_missing_model_settings():
    state = _finalization_state(accepted_payload=None)
    agent = _runtime_agent(model_settings=None)

    streaming_tools._apply_layer2_forced_tool_finalization(agent, state)

    assert isinstance(agent.model_settings, ModelSettings)
    assert agent.model_settings.tool_choice == "required"
    assert agent.reset_tool_choice is False
    assert callable(agent.tool_use_behavior)


def test_apply_layer2_callback_ends_run_on_acceptance_via_shared_state():
    # The callback closes over the live finalization state: once the finalize tool
    # wrapper sets accepted_payload, the same callback now ends the run.
    state = _finalization_state(accepted_payload=None)
    agent = _runtime_agent(model_settings=ModelSettings(tool_choice="auto"))

    streaming_tools._apply_layer2_forced_tool_finalization(agent, state)
    behavior = agent.tool_use_behavior

    # Before acceptance: continue.
    assert behavior(SimpleNamespace(), []).is_final_output is False

    # After acceptance: end the run with the accepted canonical payload.
    payload = {"objects": [], "domain_pack_id": "pdf"}
    state.accepted_payload = payload
    result = behavior(SimpleNamespace(), [])
    assert result.is_final_output is True
    assert result.final_output == json.dumps(payload)


def test_apply_layer2_no_op_when_kill_switch_disabled(monkeypatch):
    monkeypatch.setattr(
        streaming_tools,
        "LAYER2_FORCE_TOOL_FINALIZATION_ENABLED",
        False,
    )
    state = _finalization_state(accepted_payload=None)
    source_settings = ModelSettings(temperature=0.4, tool_choice="auto")
    agent = _runtime_agent(model_settings=source_settings)

    returned = streaming_tools._apply_layer2_forced_tool_finalization(agent, state)

    # Layer-1 behavior: agent returned unchanged.
    assert returned is agent
    assert returned.tool_use_behavior == "run_llm_again"
    assert returned.reset_tool_choice is True
    assert returned.model_settings is source_settings
    assert returned.model_settings.tool_choice == "auto"


def test_kill_switch_is_enabled_by_default():
    assert streaming_tools.LAYER2_FORCE_TOOL_FINALIZATION_ENABLED is True
