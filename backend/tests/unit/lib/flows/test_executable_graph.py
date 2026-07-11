"""Consumer-parity coverage for canonical executable flow topology."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api import flows as flows_api
from src.lib.agent_studio import flow_tools
from src.lib.batch import validation as batch_validation
from src.lib.flows import executor
from src.lib.executable_flow_graph import (
    ExecutableFlowTopologyError,
    project_executable_flow_graph,
)
from src.models.sql import CurationFlow
from src.schemas.flows import CreateFlowRequest, FlowDefinition


def _node(node_id: str, agent_id: str, *, node_type: str = "agent") -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {
            "agent_id": agent_id,
            "agent_display_name": agent_id,
            "task_instructions": "Extract genes" if agent_id == "task_input" else None,
            "output_key": f"{node_id}_output",
        },
    }


def _edge(edge_id: str, source: str, target: str, **extra: object) -> dict:
    return {"id": edge_id, "source": source, "target": target, **extra}


def _multi_sidecar_flow() -> dict:
    return {
        "version": "1.0",
        "entry_node_id": "task",
        "nodes": [
            _node("task", "task_input", node_type="task_input"),
            _node("extract", "gene_extractor"),
            _node("validator_symbol", "custom_validator_symbol"),
            _node("validator_identifier", "custom_validator_identifier"),
            _node("output", "csv_formatter"),
        ],
        "edges": [
            _edge("control_1", "task", "extract"),
            _edge("sidecar_1", "extract", "validator_symbol", role="validation_attachment", satisfies_binding_id="symbol"),
            _edge("sidecar_2", "extract", "validator_identifier", role="validation_attachment", satisfies_binding_id="identifier"),
            _edge("control_2", "extract", "output"),
        ],
    }


def _invalid_flow(kind: str) -> dict:
    flow = _multi_sidecar_flow()
    flow["nodes"] = flow["nodes"][:2] + flow["nodes"][-1:]
    flow["edges"] = [
        _edge("e1", "task", "extract"),
        _edge("e2", "extract", "output"),
    ]
    if kind == "branch":
        flow["nodes"].append(_node("other", "json_formatter"))
        flow["edges"].append(_edge("e3", "extract", "other"))
    elif kind == "join":
        flow["nodes"].append(_node("other", "gene"))
        flow["edges"] = [
            _edge("e1", "task", "extract"),
            _edge("e2", "task", "other"),
            _edge("e3", "extract", "output"),
            _edge("e4", "other", "output"),
        ]
    elif kind == "cycle":
        flow["edges"].append(_edge("e3", "output", "extract"))
    elif kind == "disconnected":
        flow["nodes"].append(_node("orphan", "gene"))
    elif kind == "ambiguous_entry":
        flow["edges"] = [_edge("e2", "extract", "output")]
    elif kind == "ambiguous_terminal":
        flow["nodes"].append(_node("orphan_output", "json_formatter"))
    return flow


def _unavailable_step_flow() -> dict:
    return {
        "version": "1.0",
        "entry_node_id": "task",
        "nodes": [
            _node("task", "task_input", node_type="task_input"),
            _node("missing", "unavailable_agent"),
        ],
        "edges": [_edge("control_1", "task", "missing")],
    }


def test_multi_sidecar_projection_is_identical_across_consumers(monkeypatch):
    flow = _multi_sidecar_flow()
    projection = project_executable_flow_graph(flow)

    assert projection.ordered_control_node_ids == ("task", "extract", "output")
    assert projection.ordered_executable_node_ids == ("extract", "output")
    assert projection.entry_node_ids == ("task",)
    assert projection.exit_node_ids == ("output",)
    assert [sidecar.binding_id for sidecar in projection.sidecars_for("extract")] == [
        "symbol",
        "identifier",
    ]

    # Pydantic/API save contract accepts the exact same topology and preserves edges.
    saved = FlowDefinition.model_validate(flow).model_dump()
    assert FlowDefinition.model_validate(saved).model_dump() == saved
    assert [edge["satisfies_binding_id"] for edge in saved["edges"] if edge["role"] == "validation_attachment"] == [
        "symbol",
        "identifier",
    ]

    runtime_nodes = executor._get_ordered_executable_nodes(
        cast(CurationFlow, SimpleNamespace(flow_definition=saved))
    )
    assert [node["id"] for node in runtime_nodes] == ["extract", "output"]
    runtime_flow = cast(CurationFlow, SimpleNamespace(flow_definition=saved))
    assert executor.get_flow_agent_ids(runtime_flow) == {"gene_extractor", "csv_formatter"}
    assert executor._count_agent_ids(runtime_flow) == {
        "gene_extractor": 1,
        "csv_formatter": 1,
    }

    flow_tools.set_current_flow_context({"flow_name": "Parity", **saved})
    inspected = flow_tools._get_current_flow_handler()()
    assert inspected["executable_graph"] == projection.to_dict()
    assert [step["node_id"] for step in inspected["steps"]] == ["extract", "output"]
    assert [step["step"] for step in inspected["steps"]] == [1, 2]
    assert [sidecar["binding_id"] for sidecar in inspected["executable_graph"]["validation_sidecars"]] == [
        "symbol",
        "identifier",
    ]

    monkeypatch.setitem(
        batch_validation.AGENT_REGISTRY,
        "gene_extractor",
        {"batch_capabilities": ["pdf_extraction"]},
    )
    monkeypatch.setitem(
        batch_validation.AGENT_REGISTRY,
        "csv_formatter",
        {"batch_capabilities": ["file_output"]},
    )
    assert batch_validation.validate_flow_for_batch(saved).valid is True


@pytest.mark.asyncio
async def test_multi_sidecar_api_create_load_round_trip_preserves_projection(
    monkeypatch,
):
    """CRUD persistence must retain the same topology and every sidecar binding."""

    class _RoundTripDB:
        added: Any = None
        stored: Any = None

        def add(self, obj):
            self.added = obj

        def commit(self):
            now = datetime.now(timezone.utc)
            self.added.id = uuid4()
            self.added.execution_count = 0
            self.added.last_executed_at = None
            self.added.created_at = now
            self.added.updated_at = now
            self.stored = SimpleNamespace(
                id=self.added.id,
                user_id=self.added.user_id,
                name=self.added.name,
                description=self.added.description,
                flow_definition=json.loads(json.dumps(self.added.flow_definition)),
                execution_count=0,
                last_executed_at=None,
                created_at=now,
                updated_at=now,
            )

        def refresh(self, _obj):
            return None

    db = _RoundTripDB()
    monkeypatch.setattr(
        flows_api,
        "set_global_user_from_cognito",
        lambda *_args, **_kwargs: SimpleNamespace(id=17),
    )
    monkeypatch.setattr(
        flows_api,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows_api,
        "_validate_flow_agent_references",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        flows_api,
        "_validate_flow_agent_step_policy",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        flows_api,
        "_missing_flow_agent_reference_messages",
        lambda *_args, **_kwargs: [],
    )

    created = await flows_api.create_flow(
        request=CreateFlowRequest(
            name="Topology parity",
            flow_definition=_multi_sidecar_flow(),
        ),
        user={"sub": "curator-1"},
        db=db,
    )
    monkeypatch.setattr(
        flows_api,
        "verify_flow_ownership",
        lambda *_args, **_kwargs: db.stored,
    )
    loaded = await flows_api.get_flow(
        flow_id=created.id,
        user={"sub": "curator-1"},
        db=db,
    )

    created_projection = project_executable_flow_graph(created.flow_definition)
    loaded_projection = project_executable_flow_graph(loaded.flow_definition)
    assert loaded_projection.to_dict() == created_projection.to_dict()
    assert [
        sidecar.binding_id for sidecar in loaded_projection.sidecars_for("extract")
    ] == ["symbol", "identifier"]
    assert loaded.flow_definition.model_dump() == created.flow_definition.model_dump()


def test_unavailable_step_fixture_has_consistent_save_load_runtime_and_batch_diagnostics(
    monkeypatch,
):
    flow = _unavailable_step_flow()
    definition = FlowDefinition.model_validate(flow)
    projection = project_executable_flow_graph(definition)
    assert projection.ordered_executable_node_ids == ("missing",)

    monkeypatch.setattr(
        flows_api,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows_api,
        "_flow_agent_policy_entry",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(HTTPException, match="unavailable_agent") as exc:
        flows_api._validated_flow_definition_payload(
            definition,
            db_user_id=17,
            enforce_agent_references=True,
        )
    assert exc.value.status_code == 422

    now = datetime.now(timezone.utc)
    loaded = flows_api._flow_to_response(
        cast(
            CurationFlow,
            SimpleNamespace(
                id=uuid4(),
                user_id=17,
                name="Unavailable step",
                description=None,
                flow_definition=definition.model_dump(),
                execution_count=0,
                last_executed_at=None,
                created_at=now,
                updated_at=now,
            ),
        )
    )
    assert loaded.has_critical_issues is True
    assert "unavailable_agent" in loaded.validation_warnings[0].message

    def _unresolvable_agent(*_args, **_kwargs):
        raise ValueError("agent not found")

    monkeypatch.setattr(executor, "get_agent_metadata", _unresolvable_agent)
    runtime_flow = cast(
        CurationFlow,
        SimpleNamespace(
            id="flow-1",
            name="Unavailable step",
            flow_definition=definition.model_dump(),
        ),
    )
    tools, created_names, unavailable_steps, _execution_state = (
        executor.get_all_agent_tools(runtime_flow, include_unavailable=True)
    )
    assert tools == []
    assert created_names == set()
    assert unavailable_steps == [
        {
            "step": 1,
            "agent_id": "unavailable_agent",
            "agent_name": "unavailable_agent",
            "reason": "agent could not be resolved from unified registry",
        }
    ]

    flow_tools.set_current_flow_context({"flow_name": "Unavailable", **flow})
    inspected = flow_tools._get_current_flow_handler()()
    assert [step["node_id"] for step in inspected["steps"]] == ["missing"]
    assert inspected["executable_graph"] == projection.to_dict()

    monkeypatch.setattr(batch_validation, "AGENT_REGISTRY", {})
    batch_result = batch_validation.validate_flow_for_batch(flow)
    assert batch_result.valid is False


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("branch", "branch"),
        ("join", "join"),
        ("cycle", "cycle"),
        ("disconnected", "disconnected"),
        ("ambiguous_entry", "ambiguous_entry"),
        ("ambiguous_terminal", "ambiguous_terminal"),
    ],
)
def test_invalid_topologies_are_rejected_by_schema_runtime_and_batch(kind, expected_code):
    flow = _invalid_flow(kind)
    projection = project_executable_flow_graph(flow, raise_on_invalid=False)
    assert expected_code in {issue.code for issue in projection.issues}

    with pytest.raises(ValidationError, match=expected_code):
        FlowDefinition.model_validate(flow)
    with pytest.raises(ExecutableFlowTopologyError, match=expected_code):
        executor._get_ordered_executable_nodes(
            cast(CurationFlow, SimpleNamespace(flow_definition=flow))
        )

    batch_result = batch_validation.validate_flow_for_batch(flow)
    assert batch_result.valid is False
    assert expected_code in batch_result.errors[0]


def test_duplicate_sidecar_binding_is_rejected_but_distinct_fanout_is_not_branching():
    flow = _multi_sidecar_flow()
    flow["edges"][2]["satisfies_binding_id"] = "symbol"

    projection = project_executable_flow_graph(flow, raise_on_invalid=False)
    assert "duplicate_validation_binding" in {issue.code for issue in projection.issues}
    assert "branch" not in {issue.code for issue in projection.issues}
    with pytest.raises(ValidationError, match="duplicate_validation_binding"):
        FlowDefinition.model_validate(flow)

    replacement_flow = _multi_sidecar_flow()
    extractor_data = replacement_flow["nodes"][1]["data"]
    extractor_data["validation_attachments"] = [
        {"attachment_id": "attachment-a", "validator_binding_id": "symbol"},
        {"attachment_id": "attachment-b", "validator_binding_id": "symbol"},
    ]
    for replacement_edge, attachment_id in zip(
        replacement_flow["edges"][1:3],
        ("attachment-a", "attachment-b"),
        strict=True,
    ):
        replacement_edge.pop("satisfies_binding_id")
        replacement_edge["replaces_attachment_id"] = attachment_id
    replacement_projection = project_executable_flow_graph(
        replacement_flow,
        raise_on_invalid=False,
    )
    assert "duplicate_validation_binding" in {
        issue.code for issue in replacement_projection.issues
    }


def test_sidecar_target_cannot_also_be_a_control_step():
    flow = _multi_sidecar_flow()
    flow["edges"].append(_edge("invalid_control", "validator_symbol", "output"))

    projection = project_executable_flow_graph(flow, raise_on_invalid=False)
    assert "sidecar_in_control_flow" in {issue.code for issue in projection.issues}
