"""Execution-path coverage for saved-flow catalog migrations."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.flows.persisted_flow_migrations import (
    RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS,
    RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
)


@pytest.mark.asyncio
async def test_execute_flow_uses_migrated_copy_without_mutating_stored_definition(
    monkeypatch,
):
    executor = importlib.import_module("src.lib.flows.executor")
    retired_attachment_id = next(
        attachment_id
        for attachment_id in RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS
        if ":binding:" in attachment_id
    )
    stored_definition = {
        "version": "1.1",
        "nodes": [
            {
                "id": "extract",
                "type": "agent",
                "data": {
                    "agent_id": "allele_extractor",
                    "validation_attachments": [
                        {
                            "attachment_id": retired_attachment_id,
                            "validator_binding_id": RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID,
                        },
                        {"attachment_id": "current"},
                    ],
                    "validation_groups": [],
                },
            }
        ],
        "edges": [],
        "entry_node_id": "extract",
    }
    stored_flow = SimpleNamespace(
        id=uuid4(),
        name="Legacy allele flow",
        flow_definition=stored_definition,
    )
    captured = {}

    def _capture_supervisor(*, flow, **_kwargs):
        captured["flow"] = flow
        return SimpleNamespace(
            _flow_unavailable_steps=[],
            _flow_execution_state={"completed_steps": [], "evidence_registry": {}},
        )

    monkeypatch.setattr(executor, "clear_pending_configs", lambda: None)
    monkeypatch.setattr(executor, "create_flow_supervisor", _capture_supervisor)
    monkeypatch.setattr(executor, "build_flow_prompt", lambda *_args: "prompt")

    event_stream = executor.execute_flow(
        stored_flow,
        user_id="curator",
        session_id="session",
    )
    first_event = await anext(event_stream)
    await event_stream.aclose()

    assert first_event["type"] == "FLOW_STARTED"
    runtime_attachments = captured["flow"].flow_definition["nodes"][0]["data"][
        "validation_attachments"
    ]
    assert runtime_attachments == [{"attachment_id": "current"}]
    assert len(stored_definition["nodes"][0]["data"]["validation_attachments"]) == 2
