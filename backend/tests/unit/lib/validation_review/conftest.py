"""Trace emission fixture: keep real phase queues, prompt binding and flush logic."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def phase_trace(monkeypatch):
    from src.lib.openai_agents import langfuse_client
    from src.lib.prompts.context import clear_prompt_context
    clear_prompt_context()
    langfuse_client.clear_pending_configs()
    captured = SimpleNamespace(events=[], configs=[])
    def create_event(**kwargs):
        captured.events.append(kwargs)
        captured.configs.append(kwargs['metadata']['agent_config'])
    monkeypatch.setattr(langfuse_client, '_langfuse_client', SimpleNamespace(create_event=create_event))
    monkeypatch.setattr('src.lib.openai_agents.extraction_trace_events.get_current_extraction_trace_run',
                        lambda: SimpleNamespace(trace_id='phase-trace', observation_id='phase-parent'))
    monkeypatch.setattr('src.lib.prompts.assembly.get_all_active_prompts', lambda: {})
    yield captured
    clear_prompt_context()
    langfuse_client.clear_pending_configs()
