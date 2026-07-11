"""Consumer-parity coverage for canonical executable flow topology."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.lib.agent_studio import flow_tools
from src.lib.batch import validation as batch_validation
from src.lib.flows import executor
from src.lib.executable_flow_graph import (
    ExecutableFlowTopologyError,
    project_executable_flow_graph,
)
from src.schemas.flows import FlowDefinition


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
        SimpleNamespace(flow_definition=saved)
    )
    assert [node["id"] for node in runtime_nodes] == ["extract", "output"]
    runtime_flow = SimpleNamespace(flow_definition=saved)
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
    assert batch_validation.get_entry_nodes(saved) == {"task"}
    assert batch_validation.get_exit_nodes(saved) == {"output"}
    assert batch_validation.validate_flow_for_batch(saved).valid is True


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
        executor._get_ordered_executable_nodes(SimpleNamespace(flow_definition=flow))

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
