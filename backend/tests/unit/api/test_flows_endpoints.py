"""Unit tests for flow CRUD endpoint handlers."""

import importlib
import inspect
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.lib import http_errors
from src.schemas.flows import (
    CreateFlowRequest,
    FlowDefinition,
    FlowValidationAttachmentSelection,
    UpdateFlowRequest,
)

flows = importlib.import_module("src.api.flows")


@pytest.fixture(autouse=True)
def _available_flow_agent_policy(monkeypatch):
    """Keep CRUD tests independent of the runtime agent catalog and database."""

    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        lambda agent_id, **_kwargs: {
            "name": agent_id,
            "category": "Extraction",
            "subcategory": "",
            "output_schema_key": None,
            "is_active": True,
            "visible": True,
            "visibility": None,
            "produces_flow_artifacts": True,
            "supervisor": {},
            "curation": {"domain_pack_id": "fixture.validation"},
        },
    )


def _flow_definition():
    return {
        "version": "1.1",
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "id": "task_input_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Task Input",
                    "task_instructions": "Extract curated observations from this paper.",
                    "output_key": "task_input_text",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 1, "y": 1},
                "data": {
                    "agent_id": "gene_expression",
                    "agent_display_name": "Gene Expression",
                    "output_key": "gene_expression_output",
                },
            },
        ],
        "edges": [{"id": "e1", "source": "task_input_1", "target": "agent_1"}],
    }


def test_create_flow_request_rejects_v1_0_definition():
    definition = _flow_definition()
    definition["version"] = "1.0"

    with pytest.raises(ValueError, match="1.1"):
        CreateFlowRequest(
            name="Legacy flow",
            description=None,
            flow_definition=definition,
        )


def _flow(name="Flow A"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        user_id=17,
        name=name,
        description="desc",
        flow_definition=_flow_definition(),
        execution_count=0,
        last_executed_at=None,
        created_at=now,
        updated_at=now,
        is_active=True,
    )


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


@pytest.mark.asyncio
async def test_list_flows_returns_paginated_response(monkeypatch):
    flow_a = _flow(name="A")
    flow_b = _flow(name="B")

    db = SimpleNamespace(
        scalar=lambda _query: 2,
        scalars=lambda _query: _ScalarsResult([flow_a, flow_b]),
    )
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    response = await flows.list_flows(page=1, page_size=20, user={"sub": "u1"}, db=db)
    assert response.total == 2
    assert [item.name for item in response.flows] == ["A", "B"]
    assert response.flows[0].step_count == 2


def test_list_flows_uses_shared_default_page_size():
    page_size_default = inspect.signature(flows.list_flows).parameters["page_size"].default

    assert page_size_default.default == flows.DEFAULT_FLOW_LIST_PAGE_SIZE
    assert flows.DEFAULT_FLOW_LIST_PAGE_SIZE == 50


@pytest.mark.asyncio
async def test_get_flow_uses_verify_ownership(monkeypatch):
    owned = _flow(name="Owned")
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: owned)

    response = await flows.get_flow(flow_id=owned.id, user={"sub": "u1"}, db=object())
    assert response.id == owned.id
    assert response.name == "Owned"


@pytest.mark.asyncio
async def test_get_flow_hydrates_metadata_validation_attachments_on_read(monkeypatch):
    owned = _flow(name="Owned")
    calls = []

    def _hydrate(flow_definition):
        calls.append(flow_definition.nodes[1].data.agent_id)
        hydrated = flow_definition.model_copy(deep=True)
        hydrated.nodes[1].data.validation_attachments = [
            FlowValidationAttachmentSelection(
                attachment_id="fixture.validation:binding:shape:pack",
                domain_pack_id="fixture.validation",
                validator_id="shape",
                validator_binding_id="shape",
                state="active",
                scope="pack",
                required=True,
                export_blocking=True,
                default_enabled=True,
                enabled=True,
            )
        ]
        return hydrated

    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: owned)
    monkeypatch.setattr(flows, "apply_flow_validation_attachment_defaults", _hydrate)

    response = await flows.get_flow(flow_id=owned.id, user={"sub": "u1"}, db=object())

    assert calls == ["gene_expression"]
    attachments = response.flow_definition.nodes[1].data.validation_attachments
    assert attachments[0].attachment_id == "fixture.validation:binding:shape:pack"


@pytest.mark.asyncio
async def test_get_flow_reports_missing_agent_reference_on_read(monkeypatch):
    owned = _flow(name="Owned")
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: owned)
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

    response = await flows.get_flow(flow_id=owned.id, user={"sub": "u1"}, db=object())

    assert response.id == owned.id
    assert response.has_critical_issues is True
    assert response.validation_warnings[0].type == "CRITICAL"
    assert "references unavailable agent" in response.validation_warnings[0].message
    assert "gene_expression" in response.validation_warnings[0].message


@pytest.mark.parametrize("metadata_state", ["unavailable", "missing_domain_pack"])
def test_flow_response_preserves_unresolvable_custom_agent_attachments_with_warning(
    monkeypatch,
    metadata_state,
):
    template_definition = _flow_definition()
    template_definition["nodes"][1]["data"].update(
        {
            "agent_id": "gene_extractor",
            "agent_display_name": "Gene Extractor",
        }
    )
    template_definition["nodes"].append(
        {
            "id": "validator_1",
            "type": "agent",
            "position": {"x": 2, "y": 1},
            "data": {
                "agent_id": "custom_validator",
                "agent_display_name": "Custom Validator",
                "output_key": "custom_validator_output",
            },
        }
    )
    template_definition["edges"].append(
        {
            "id": "validation_edge_1",
            "source": "agent_1",
            "target": "validator_1",
            "role": "validation_attachment",
            "satisfies_binding_id": "custom.supplemental",
        }
    )
    template_flow = flows.apply_flow_validation_attachment_defaults(
        FlowDefinition.model_validate(template_definition)
    )
    inherited_attachments = [
        attachment.model_dump()
        for attachment in template_flow.nodes[1].data.validation_attachments
    ]

    custom_agent_id = "ca_00000000-0000-4000-8000-000000000002"
    owned = _flow(name="Historical custom flow")
    owned.flow_definition = template_flow.model_dump()
    owned.flow_definition["nodes"][1]["data"].update(
        {
            "agent_id": custom_agent_id,
            "agent_display_name": "Unavailable Custom Extraction Agent",
        }
    )
    inherited_groups = owned.flow_definition["nodes"][1]["data"]["validation_groups"]
    inherited_edges = owned.flow_definition["edges"]

    def _custom_metadata(agent_id, **_kwargs):
        if metadata_state == "unavailable":
            raise ValueError("unavailable")
        return {
            "agent_id": agent_id,
            "display_name": "Custom Extraction Agent",
            "category": "Extraction",
            "curation": None,
        }

    def _policy_entry(agent_id, **_kwargs):
        if agent_id != custom_agent_id:
            return {}
        if metadata_state == "unavailable":
            return None
        return {"curation": None}

    monkeypatch.setattr(
        flows,
        "get_active_visible_agent_metadata",
        _custom_metadata,
    )
    monkeypatch.setattr(
        flows,
        "_flow_agent_policy_entry",
        _policy_entry,
    )

    response = flows._flow_to_response(owned)

    assert response.has_critical_issues is True
    assert custom_agent_id in response.validation_warnings[0].message
    if metadata_state == "missing_domain_pack":
        assert "no longer declares validation attachments" in (
            response.validation_warnings[0].message
        )
    attachments = response.flow_definition.nodes[1].data.validation_attachments
    assert [attachment.model_dump() for attachment in attachments] == inherited_attachments
    groups = response.flow_definition.nodes[1].data.validation_groups
    assert [group.model_dump() for group in groups] == inherited_groups
    assert [edge.model_dump() for edge in response.flow_definition.edges] == inherited_edges


@pytest.mark.asyncio
async def test_create_flow_success(monkeypatch):
    class _DB:
        def __init__(self):
            self.added = None
            self.committed = False
            self.refreshed = False

        def add(self, obj):
            self.added = obj

        def commit(self):
            self.committed = True

        def refresh(self, _obj):
            now = datetime.now(timezone.utc)
            _obj.id = uuid4()
            _obj.execution_count = 0
            _obj.created_at = now
            _obj.updated_at = now
            self.refreshed = True

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    request = CreateFlowRequest(name="Created", description="new", flow_definition=_flow_definition())
    response = await flows.create_flow(request=request, user={"sub": "u1"}, db=db)

    assert db.committed is True
    assert db.refreshed is True
    assert response.name == "Created"
    assert response.user_id == 17


@pytest.mark.asyncio
async def test_validate_flow_draft_is_side_effect_free_and_preserves_phase(monkeypatch):
    class _Query:
        def filter(self, *_args):
            return self

        def one_or_none(self):
            return SimpleNamespace(id=17)

    class _DB:
        def query(self, *_args):
            return _Query()

        def add(self, _obj):
            raise AssertionError("validation must not write")

        def commit(self):
            raise AssertionError("validation must not commit")

    captured = {}

    def _validate(candidate, **kwargs):
        captured["candidate"] = candidate
        captured.update(kwargs)
        return SimpleNamespace(
            valid=True,
            findings=(),
            to_dict=lambda: {
                "artifact_kind": "flow",
                "phase": kwargs["phase"],
                "valid": True,
                "findings": [],
            },
        )

    monkeypatch.setattr(flows, "validate_flow_authoring_draft", _validate)
    request = flows.FlowDraftValidationRequest(
        flow_definition=_flow_definition(),
        phase="post_apply",
        expected_draft_fingerprint=f"sha256:{'a' * 64}",
        current_draft_fingerprint=f"sha256:{'a' * 64}",
    )

    response = await flows.validate_flow_draft(
        request=request,
        user={"sub": "u1", "cognito:groups": []},
        db=_DB(),
    )

    assert response["valid"] is True
    assert response["phase"] == "post_apply"
    assert captured["context"].expected_draft_fingerprint == f"sha256:{'a' * 64}"


@pytest.mark.asyncio
async def test_create_flow_hydrates_metadata_validation_attachments(monkeypatch):
    class _DB:
        def __init__(self):
            self.added = None

        def add(self, obj):
            self.added = obj

        def commit(self):
            return None

        def refresh(self, _obj):
            now = datetime.now(timezone.utc)
            _obj.id = uuid4()
            _obj.execution_count = 0
            _obj.created_at = now
            _obj.updated_at = now

    flow_definition = _flow_definition()
    flow_definition["nodes"][1]["data"]["agent_id"] = "disease_extractor"
    flow_definition["nodes"][1]["data"]["agent_display_name"] = "Disease Extractor"

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    await flows.create_flow(
        request=CreateFlowRequest(
            name="Created",
            description="new",
            flow_definition=flow_definition,
        ),
        user={"sub": "u1"},
        db=db,
    )

    assert db.added is not None
    attachments = db.added.flow_definition["nodes"][1]["data"]["validation_attachments"]
    states = {attachment["state"] for attachment in attachments}

    assert any(
        attachment["state"] == "active" and attachment["enabled"]
        for attachment in attachments
    )
    assert states.issuperset({"active", "under_development"})


@pytest.mark.asyncio
async def test_create_flow_accepts_inherited_custom_agent_validation_attachments(
    monkeypatch,
):
    class _DB:
        def __init__(self):
            self.added = None

        def add(self, obj):
            self.added = obj

        def commit(self):
            return None

        def refresh(self, obj):
            now = datetime.now(timezone.utc)
            obj.id = uuid4()
            obj.execution_count = 0
            obj.created_at = now
            obj.updated_at = now

    template_definition = _flow_definition()
    template_definition["nodes"][1]["data"].update(
        {
            "agent_id": "gene_extractor",
            "agent_display_name": "Gene Extractor",
        }
    )
    template_flow = flows.apply_flow_validation_attachment_defaults(
        FlowDefinition.model_validate(template_definition)
    )
    inherited_attachments = [
        attachment.model_dump()
        for attachment in template_flow.nodes[1].data.validation_attachments
    ]

    custom_agent_id = "ca_00000000-0000-4000-8000-000000000001"
    custom_definition = _flow_definition()
    custom_definition["nodes"][1]["data"].update(
        {
            "agent_id": custom_agent_id,
            "agent_display_name": "Custom Extraction Agent",
            "validation_attachments": inherited_attachments,
        }
    )

    metadata_calls = []

    def _custom_agent_metadata(agent_id, **kwargs):
        metadata_calls.append(kwargs)
        return {
            "agent_id": agent_id,
            "curation": {
                "adapter_key": "gene",
                "domain_pack_id": "gene",
                "launchable": True,
            },
        }

    monkeypatch.setattr(
        flows,
        "get_active_visible_agent_metadata",
        _custom_agent_metadata,
    )
    monkeypatch.setattr(
        flows,
        "get_groups_from_provider_groups",
        lambda _groups: ["group-17"],
    )
    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda *_args, **_kwargs: SimpleNamespace(id=17),
    )

    db = _DB()
    response = await flows.create_flow(
        request=CreateFlowRequest(
            name="Custom extraction flow",
            description="Regression for inherited validation attachments",
            flow_definition=custom_definition,
        ),
        user={"sub": "u1", "cognito:groups": ["provider-group-17"]},
        db=db,
    )

    assert db.added is not None
    persisted = db.added.flow_definition["nodes"][1]["data"]
    assert persisted["agent_id"] == custom_agent_id
    assert persisted["validation_attachments"] == inherited_attachments
    assert response.flow_definition.nodes[1].data.validation_attachments
    assert metadata_calls
    assert all(
        call["authenticated_groups"] == ["group-17"] for call in metadata_calls
    )


@pytest.mark.asyncio
async def test_create_flow_maps_unique_integrity_error_to_409(monkeypatch):
    class _DB:
        def add(self, _obj):
            return None

        def commit(self):
            raise IntegrityError(
                statement="insert into curation_flows",
                params={},
                orig=Exception("duplicate key value violates constraint uq_user_flow_name_active"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    with pytest.raises(HTTPException) as exc:
        await flows.create_flow(
            request=CreateFlowRequest(name="Dup", description=None, flow_definition=_flow_definition()),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_flow_maps_other_integrity_error_to_500(monkeypatch, caplog):
    report_calls = []
    secret_text = "SECRET_FLOW_DEFINITION_SHOULD_NOT_APPEAR"

    class _DB:
        def add(self, _obj):
            return None

        def commit(self):
            raise IntegrityError(
                statement="insert into curation_flows",
                params={"flow_definition": secret_text},
                orig=Exception(f"some other integrity error {secret_text}"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    def _fake_report_runtime_exception(exc, **kwargs):
        report_calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)
    caplog.set_level(logging.ERROR, logger=flows.logger.name)

    with pytest.raises(HTTPException) as exc:
        await flows.create_flow(
            request=CreateFlowRequest(name="Err", description=None, flow_definition=_flow_definition()),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "Database error while creating flow"
    assert db.rolled_back is True
    assert len(report_calls) == 1
    assert isinstance(report_calls[0][0], flows._FlowDatabaseError)
    assert "Exception" in str(report_calls[0][0])
    assert secret_text not in str(report_calls[0][0])
    assert report_calls[0][0].__traceback__ is not None
    assert report_calls[0][0].__context__ is None
    assert report_calls[0][0].__cause__ is None
    assert report_calls[0][1]["component"] == "api"
    assert report_calls[0][1]["operation"] == "sanitized_http_exception"
    assert report_calls[0][1]["context"]["logger_name"] == flows.logger.name
    assert report_calls[0][1]["context"]["status_code"] == 500
    assert secret_text not in caplog.text


@pytest.mark.asyncio
async def test_update_flow_commits_and_flags_json(monkeypatch):
    flow_obj = _flow(name="Before")
    captured = {"flagged": False}

    class _DB:
        def __init__(self):
            self.committed = False
            self.refreshed = False

        def commit(self):
            self.committed = True

        def refresh(self, _obj):
            self.refreshed = True

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)
    monkeypatch.setattr(
        flows,
        "flag_modified",
        lambda _obj, field: captured.__setitem__("flagged", field == "flow_definition"),
    )

    request = UpdateFlowRequest(
        name="After",
        description="",
        flow_definition=_flow_definition(),
    )
    response = await flows.update_flow(flow_id=flow_obj.id, request=request, user={"sub": "u1"}, db=db)

    assert db.committed is True
    assert db.refreshed is True
    assert captured["flagged"] is True
    assert response.name == "After"
    assert response.description is None


@pytest.mark.asyncio
async def test_update_flow_without_changes_skips_commit(monkeypatch):
    flow_obj = _flow(name="No Change")

    class _DB:
        def __init__(self):
            self.committed = False

        def commit(self):
            self.committed = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    response = await flows.update_flow(
        flow_id=flow_obj.id,
        request=UpdateFlowRequest(),
        user={"sub": "u1"},
        db=db,
    )
    assert db.committed is False
    assert response.name == "No Change"


@pytest.mark.asyncio
async def test_update_flow_maps_unique_integrity_error_to_409(monkeypatch):
    flow_obj = _flow(name="Before")

    class _DB:
        def commit(self):
            raise IntegrityError(
                statement="update curation_flows",
                params={},
                orig=Exception("duplicate key value violates constraint uq_user_flow_name_active"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    with pytest.raises(HTTPException) as exc:
        await flows.update_flow(
            flow_id=flow_obj.id,
            request=UpdateFlowRequest(name="Duplicate"),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_flow_maps_other_integrity_error_to_500(monkeypatch, caplog):
    report_calls = []
    secret_text = "SECRET_FLOW_UPDATE_SHOULD_NOT_APPEAR"
    flow_obj = _flow(name="Before")

    class _DB:
        def commit(self):
            raise IntegrityError(
                statement="update curation_flows",
                params={"flow_definition": secret_text},
                orig=Exception(f"some other integrity error {secret_text}"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    def _fake_report_runtime_exception(exc, **kwargs):
        report_calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)
    caplog.set_level(logging.ERROR, logger=flows.logger.name)

    with pytest.raises(HTTPException) as exc:
        await flows.update_flow(
            flow_id=flow_obj.id,
            request=UpdateFlowRequest(name="After", description=None),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "Database error while updating flow"
    assert db.rolled_back is True
    assert len(report_calls) == 1
    assert isinstance(report_calls[0][0], flows._FlowDatabaseError)
    assert "Exception" in str(report_calls[0][0])
    assert secret_text not in str(report_calls[0][0])
    assert report_calls[0][0].__traceback__ is not None
    assert report_calls[0][0].__context__ is None
    assert report_calls[0][0].__cause__ is None
    assert report_calls[0][1]["component"] == "api"
    assert report_calls[0][1]["operation"] == "sanitized_http_exception"
    assert report_calls[0][1]["context"]["logger_name"] == flows.logger.name
    assert report_calls[0][1]["context"]["status_code"] == 500
    assert secret_text not in caplog.text


@pytest.mark.asyncio
async def test_delete_flow_marks_inactive(monkeypatch):
    flow_obj = _flow(name="Delete Me")

    class _DB:
        def __init__(self):
            self.committed = False

        def commit(self):
            self.committed = True

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    response = await flows.delete_flow(flow_id=flow_obj.id, user={"sub": "u1"}, db=db)
    assert flow_obj.is_active is False
    assert db.committed is True
    assert response.success is True
