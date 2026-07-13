"""Unit tests for flow API ownership and soft-delete behavior."""

import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.schemas.flows import FlowDefinition

flows = importlib.import_module("src.api.flows")


class _DummyQuery:
    def __init__(self, flow):
        self._flow = flow

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        if self._flow is not None and getattr(self._flow, "is_active", True) is False:
            return None
        return self._flow


class _DummyDB:
    def __init__(self, flow=None):
        self._flow = flow
        self.commit_called = False

    def query(self, _model):
        return _DummyQuery(self._flow)

    def commit(self):
        self.commit_called = True


def test_flows_crud_enforces_ownership_and_soft_delete(monkeypatch):
    flow_id = uuid4()
    owned_flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="WB Expression Flow",
        is_active=True,
    )

    db = _DummyDB(flow=owned_flow)

    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda _db, _auth_user: SimpleNamespace(id=7),
    )
    resolved = flows.verify_flow_ownership(db, flow_id, {"sub": "auth-sub"})
    assert resolved is owned_flow

    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda _db, _auth_user: SimpleNamespace(id=99),
    )
    with pytest.raises(HTTPException) as exc:
        flows.verify_flow_ownership(db, flow_id, {"sub": "auth-sub"})
    assert exc.value.status_code == 403

    delete_db = _DummyDB()
    monkeypatch.setattr(
        flows,
        "verify_flow_ownership",
        lambda _db, _flow_id, _user: owned_flow,
    )

    result = asyncio.run(
        flows.delete_flow(
            flow_id=flow_id,
            user={"sub": "auth-sub"},
            db=delete_db,
        )
    )

    assert owned_flow.is_active is False
    assert delete_db.commit_called is True
    assert result.success is True
    assert "deleted" in result.message.lower()


def test_verify_flow_ownership_returns_404_for_missing_or_deleted_flow(monkeypatch):
    flow_id = uuid4()
    db = _DummyDB(flow=None)

    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda _db, _auth_user: SimpleNamespace(id=7),
    )

    with pytest.raises(HTTPException) as exc:
        flows.verify_flow_ownership(db, flow_id, {"sub": "auth-sub"})

    assert exc.value.status_code == 404

    soft_deleted_flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Deleted Flow",
        is_active=False,
    )
    soft_deleted_db = _DummyDB(flow=soft_deleted_flow)
    with pytest.raises(HTTPException) as soft_deleted_exc:
        flows.verify_flow_ownership(soft_deleted_db, flow_id, {"sub": "auth-sub"})
    assert soft_deleted_exc.value.status_code == 404


def _minimal_flow_definition_payload() -> dict:
    return {
        "version": "1.1",
        "nodes": [
            {
                "id": "task_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Extract genes",
                    "output_key": "task_input",
                },
            },
            {
                "id": "extract_1",
                "type": "agent",
                "position": {"x": 100, "y": 100},
                "data": {
                    "agent_id": "fixture_agent_without_pack",
                    "agent_display_name": "Fixture Agent",
                    "output_key": "extract_output",
                },
            },
        ],
        "edges": [{"id": "e1", "source": "task_1", "target": "extract_1"}],
        "entry_node_id": "task_1",
    }


def test_flow_definition_payload_defaults_saved_edges_to_control_flow():
    payload = flows._validated_flow_definition_payload(
        FlowDefinition.model_validate(_minimal_flow_definition_payload())
    )

    assert payload["edges"][0]["role"] == "control_flow"


def test_flow_response_defaults_legacy_saved_edge_roles(monkeypatch):
    flow_id = uuid4()
    now = datetime.now(timezone.utc)
    stored_flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Legacy saved flow",
        description=None,
        flow_definition=_minimal_flow_definition_payload(),
        execution_count=0,
        last_executed_at=None,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda *_args, **_kwargs: {
            "name": "Fixture Agent",
            "category": "Extraction",
            "supervisor": {"enabled": True},
        },
    )

    response = flows._flow_to_response(stored_flow)

    assert response.flow_definition.edges[0].role == "control_flow"
    assert response.validation_warnings == []
    assert response.has_critical_issues is False


def test_flow_response_rejects_legacy_formatter_control_step(
    monkeypatch,
):
    payload = _minimal_flow_definition_payload()
    payload["nodes"][1]["data"].update(
        {
            "agent_id": "csv_formatter",
            "agent_display_name": "CSV Formatter",
            "output_key": "csv_output",
        }
    )
    now = datetime.now(timezone.utc)
    stored_flow = SimpleNamespace(
        id=uuid4(),
        user_id=7,
        name="Legacy formatter flow",
        description=None,
        flow_definition=payload,
        execution_count=0,
        last_executed_at=None,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda *_args, **_kwargs: {
            "name": "CSV Formatter",
            "category": "Output",
            "subcategory": "File Formatter",
            "supervisor": {"enabled": True},
        },
    )

    with pytest.raises(ValidationError, match="formatter_in_control_flow"):
        flows._flow_to_response(stored_flow)


def _output_attachment_flow_payload() -> dict:
    payload = _minimal_flow_definition_payload()
    payload["version"] = "1.1"
    payload["nodes"][1]["data"].update(
        {
            "agent_id": "allele_extractor",
            "agent_display_name": "Allele Extraction",
        }
    )
    payload["nodes"].append(
        {
            "id": "output_1",
            "type": "output",
            "position": {"x": 300, "y": 100},
            "data": {
                "agent_id": "csv_formatter",
                "agent_display_name": "CSV Formatter",
                "output_key": "csv_output",
            },
        }
    )
    payload["edges"].append(
        {
            "id": "output_edge_1",
            "source": "extract_1",
            "target": "output_1",
            "role": "output_attachment",
        }
    )
    return payload


def test_flow_definition_payload_enforces_output_attachment_agent_roles(monkeypatch):
    payload = _output_attachment_flow_payload()

    def _entry(agent_id, **_kwargs):
        return {
            "name": agent_id,
            "category": {
                "allele_extractor": "Extraction",
                "csv_formatter": "Output",
            }.get(agent_id, "Input"),
            "supervisor": {"enabled": True},
        }

    monkeypatch.setattr(flows, "_flow_agent_policy_entry", _entry)
    saved = flows._validated_flow_definition_payload(
        FlowDefinition.model_validate(payload),
        db_user_id=7,
        enforce_agent_references=True,
        enforce_agent_step_policy=True,
    )
    assert saved["edges"][-1]["role"] == "output_attachment"

    payload["edges"][-1]["source"] = "task_1"
    with pytest.raises(HTTPException, match="not an extraction agent or a typed validation agent"):
        flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(payload),
            db_user_id=7,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        )


@pytest.mark.parametrize(
    ("source_category", "output_schema_key", "is_active", "allowed"),
    [
        ("Validation", "AlleleResultEnvelope", True, True),
        ("Validation", "", True, False),
        ("Validation", "AlleleResultEnvelope", False, False),
        ("Custom", "AlleleResultEnvelope", True, False),
        ("General", "AlleleResultEnvelope", True, False),
    ],
)
def test_flow_definition_payload_requires_typed_validation_formatter_sources(
    monkeypatch,
    source_category,
    output_schema_key,
    is_active,
    allowed,
):
    payload = _output_attachment_flow_payload()

    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda agent_id, **_kwargs: {
            "name": agent_id,
            "category": (
                "Output" if agent_id == "csv_formatter" else source_category
            ),
            "subcategory": "Formatter" if agent_id == "csv_formatter" else "",
            "output_schema_key": (
                None if agent_id == "csv_formatter" else output_schema_key
            ),
            "is_active": True if agent_id == "csv_formatter" else is_active,
            "supervisor": {"enabled": agent_id == "csv_formatter"},
        },
    )

    definition = FlowDefinition.model_validate(payload)
    if allowed:
        saved = flows._validated_flow_definition_payload(
            definition,
            db_user_id=7,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        )
        assert saved["edges"][-1]["role"] == "output_attachment"
    else:
        with pytest.raises(HTTPException, match="typed validation agent"):
            flows._validated_flow_definition_payload(
                definition,
                db_user_id=7,
                enforce_agent_references=True,
                enforce_agent_step_policy=True,
            )


def test_flow_agent_policy_entry_rejects_unresolvable_validation_schema(monkeypatch):
    monkeypatch.setattr(
        flows,
        "get_agent_metadata",
        lambda _agent_id, **_kwargs: {
            "display_name": "Broken Validator",
            "category": "Validation",
            "output_schema_key": "MissingValidatorResult",
            "is_active": True,
        },
    )
    monkeypatch.setattr(flows, "resolve_output_schema", lambda _schema_key: None)

    entry = flows._flow_agent_policy_entry("broken_validator", db_user_id=7)

    assert entry is not None
    assert entry["produces_flow_artifacts"] is False
    assert flows.agent_can_source_output_attachment(entry) is False


def test_flow_definition_payload_validates_each_multi_source_attachment(monkeypatch):
    payload = _output_attachment_flow_payload()
    payload["nodes"].insert(
        -1,
        {
            "id": "extract_2",
            "type": "agent",
            "position": {"x": 200, "y": 100},
            "data": {
                "agent_id": "gene_extractor",
                "agent_display_name": "Gene Extractor",
                "output_key": "gene_output",
            },
        },
    )
    payload["edges"].insert(
        -1,
        {
            "id": "control_extract_2",
            "source": "extract_1",
            "target": "extract_2",
            "role": "control_flow",
        },
    )
    payload["edges"].append(
        {
            "id": "output_edge_2",
            "source": "extract_2",
            "target": "output_1",
            "role": "output_attachment",
        }
    )

    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda agent_id, **_kwargs: {
            "name": agent_id,
            "category": (
                "Output"
                if agent_id == "csv_formatter"
                else "Extraction"
                if agent_id in {"allele_extractor", "gene_extractor"}
                else "Input"
            ),
            "supervisor": {"enabled": True},
        },
    )

    saved = flows._validated_flow_definition_payload(
        FlowDefinition.model_validate(payload),
        db_user_id=7,
        enforce_agent_references=True,
        enforce_agent_step_policy=True,
    )

    assert [
        edge["source"]
        for edge in saved["edges"]
        if edge["role"] == "output_attachment"
    ] == ["extract_1", "extract_2"]

    payload["nodes"][-2]["data"]["agent_id"] = "gene_validator"
    with pytest.raises(
        HTTPException,
        match="Gene Extractor.*not an extraction agent or a typed validation agent",
    ):
        flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(payload),
            db_user_id=7,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        )


def test_flow_definition_payload_rejects_non_formatter_output_target(monkeypatch):
    payload = _output_attachment_flow_payload()
    payload["nodes"][-1]["data"].update(
        {
            "agent_id": "gene_validator",
            "agent_display_name": "Gene Validator",
        }
    )

    def _entry(agent_id, **_kwargs):
        return {
            "name": agent_id,
            "category": {
                "allele_extractor": "Extraction",
                "gene_validator": "Validation",
            }.get(agent_id, "Input"),
            "supervisor": {"enabled": True},
        }

    monkeypatch.setattr(flows, "_flow_agent_policy_entry", _entry)
    with pytest.raises(HTTPException, match="is not an output formatter"):
        flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(payload),
            db_user_id=7,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        )


def test_flow_definition_payload_rejects_custom_output_without_runtime_formatter(
    monkeypatch,
):
    payload = _output_attachment_flow_payload()
    payload["nodes"][-1]["data"].update(
        {
            "agent_id": "custom_output",
            "agent_display_name": "Custom Output",
        }
    )

    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda agent_id, **_kwargs: {
            "name": agent_id,
            "category": "Extraction" if agent_id == "allele_extractor" else "Output",
            "supervisor": {"enabled": True},
        },
    )

    with pytest.raises(HTTPException, match="is not an output formatter"):
        flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(payload),
            db_user_id=7,
            enforce_agent_references=True,
            enforce_agent_step_policy=True,
        )


def test_flow_definition_payload_rejects_missing_agent_reference(monkeypatch):
    monkeypatch.setattr(
        flows,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as exc:
        flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(_minimal_flow_definition_payload()),
            db_user_id=7,
            enforce_agent_references=True,
        )

    assert exc.value.status_code == 422
    assert "references unavailable agent" in str(exc.value.detail)
    assert "fixture_agent_without_pack" in str(exc.value.detail)


def test_flow_response_reports_missing_agent_reference_on_load(monkeypatch):
    flow_id = uuid4()
    now = datetime.now(timezone.utc)
    stored_flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Broken saved flow",
        description=None,
        flow_definition=_minimal_flow_definition_payload(),
        execution_count=0,
        last_executed_at=None,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        flows,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda *_args, **_kwargs: None,
    )

    response = flows._flow_to_response(stored_flow)

    assert response.id == flow_id
    assert response.has_critical_issues is True
    assert response.validation_warnings[0].type == "CRITICAL"
    assert "references unavailable agent" in response.validation_warnings[0].message
    assert "fixture_agent_without_pack" in response.validation_warnings[0].message


def test_flow_definition_payload_rejects_attachment_only_validator_control_flow(
    monkeypatch,
):
    payload = _minimal_flow_definition_payload()
    payload["nodes"][1]["data"].update(
        {
            "agent_id": "allele_validation",
            "agent_display_name": "Allele Validation",
            "output_key": "allele_validation_output",
        }
    )
    monkeypatch.setattr(
        flows,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows,
        "AGENT_REGISTRY",
        {
            "allele_validation": {
                "name": "Allele Validation",
                "category": "Validation",
                "supervisor": {"enabled": False},
            }
        },
    )

    with pytest.raises(HTTPException) as exc:
        flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(payload),
            enforce_agent_step_policy=True,
        )

    assert exc.value.status_code == 422
    assert "attachment-only validator" in str(exc.value.detail)
    assert "e1" in str(exc.value.detail)


def test_flow_definition_payload_allows_attachment_only_validator_sidecar(
    monkeypatch,
):
    payload = _minimal_flow_definition_payload()
    payload["nodes"].append(
        {
            "id": "validator_1",
            "type": "agent",
            "position": {"x": 200, "y": 100},
            "data": {
                "agent_id": "allele_validation",
                "agent_display_name": "Allele Validation",
                "output_key": "allele_validation_output",
            },
        }
    )
    payload["edges"].append(
        {
            "id": "v1",
            "source": "extract_1",
            "target": "validator_1",
            "role": "validation_attachment",
            "satisfies_binding_id": "allele_mention_reference_validation",
        }
    )
    monkeypatch.setattr(
        flows,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows,
        "AGENT_REGISTRY",
        {
            "allele_validation": {
                "name": "Allele Validation",
                "category": "Validation",
                "supervisor": {"enabled": False},
            }
        },
    )

    result = flows._validated_flow_definition_payload(
        FlowDefinition.model_validate(payload),
        enforce_agent_step_policy=True,
    )

    assert result["edges"][1]["role"] == "validation_attachment"


def test_flow_definition_payload_allows_supervisor_enabled_validator_step(
    monkeypatch,
):
    payload = _minimal_flow_definition_payload()
    payload["nodes"][1]["data"].update(
        {
            "agent_id": "ontology_term_validation",
            "agent_display_name": "Ontology Term Validation",
            "output_key": "ontology_term_validation_output",
        }
    )
    monkeypatch.setattr(
        flows,
        "apply_flow_validation_attachment_defaults",
        lambda flow_definition: flow_definition,
    )
    monkeypatch.setattr(
        flows,
        "AGENT_REGISTRY",
        {
            "ontology_term_validation": {
                "name": "Ontology Term Validation",
                "category": "Validation",
                "supervisor": {"enabled": True},
            }
        },
    )

    result = flows._validated_flow_definition_payload(
        FlowDefinition.model_validate(payload),
        enforce_agent_step_policy=True,
    )

    assert result["nodes"][1]["data"]["agent_id"] == "ontology_term_validation"
