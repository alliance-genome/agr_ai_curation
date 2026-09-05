"""Tests for custom-agent API endpoints."""

import asyncio
from datetime import UTC, datetime
import logging
from types import SimpleNamespace
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from src.lib import http_errors


@pytest.mark.parametrize("creating", [False, True])
@pytest.mark.parametrize("output", [
    {"output_contract": None},
    {"new_generic_profile": None},
    {"revise_generic_profile": None},
    {"output_contract": {"output_state": "none"}, "output_schema_key": None},
])
def test_output_transition_requests_reject_ambiguous_nulls(creating, output):
    from src.api.agent_studio_custom import CreateCustomAgentRequest, UpdateCustomAgentRequest
    request_type = CreateCustomAgentRequest if creating else UpdateCustomAgentRequest
    with pytest.raises(ValidationError):
        request_type(**({"name": "Draft"} if creating else {}), **output)


def test_output_transition_requests_accept_explicit_none_and_new_profile():
    from src.api.agent_studio_custom import CreateCustomAgentRequest, UpdateCustomAgentRequest
    cleared = UpdateCustomAgentRequest(output_contract={"output_state": "none"})
    assert cleared.output_contract.output_state == "none"
    created = CreateCustomAgentRequest(
        name="Draft", new_generic_profile={"name": "Record", "semantic_class": "example", "fields": []},
    )
    assert created.new_generic_profile.semantic_class == "example"


def test_profile_revision_edit_requires_one_explicit_complete_transition():
    from src.api.agent_studio_custom import UpdateCustomAgentRequest, CreateCustomAgentRequest
    edit = {
        "base": {"profile_id": str(uuid.uuid4()), "profile_revision_id": str(uuid.uuid4()), "revision": 1, "fingerprint": "sha256:" + "a" * 64},
        "contract": {"name": "Record", "semantic_class": "example", "fields": []},
    }
    assert UpdateCustomAgentRequest(revise_generic_profile=edit).revise_generic_profile.base.revision == 1
    for other in [{"output_contract": {"output_state": "none"}}, {"new_generic_profile": edit["contract"]}, {"output_schema_key": None}]:
        with pytest.raises(ValidationError):
            UpdateCustomAgentRequest(revise_generic_profile=edit, **other)
    with pytest.raises(ValidationError):
        CreateCustomAgentRequest(name="Clone", revise_generic_profile=edit)


def test_profile_revision_conflict_rolls_back_agent_save_and_returns_409(monkeypatch):
    import src.api.agent_studio_custom as api
    custom_agent = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda *_: SimpleNamespace(id=1))
    monkeypatch.setattr(api, "get_custom_agent_for_user", lambda *args, **kwargs: custom_agent)
    def conflict(**kwargs):
        raise api.ProfileConflictError("Profile changed since it was opened")
    monkeypatch.setattr(api, "update_custom_agent", conflict)
    db = _db_mock()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.update_custom_agent_endpoint(
            custom_agent_id=custom_agent.id, request=api.UpdateCustomAgentRequest(name="Edited"),
            user={"sub": "curator"}, db=db,
        ))
    assert caught.value.status_code == 409
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


class TestCustomAgentTestEndpoint:
    """Unit tests for POST /api/agent-studio/custom-agents/{id}/test."""

    def test_flatten_runner_event_merges_data_and_audit_fields(self):
        from src.api.agent_studio_custom import _flatten_runner_event

        event = {
            "type": "TEXT_MESSAGE_CONTENT",
            "data": {"delta": "hello", "trace_id": "trace-123"},
            "timestamp": "2026-02-11T00:00:00Z",
            "details": {"message": "ok"},
        }

        flattened = _flatten_runner_event(event, "session-123")

        assert flattened["type"] == "TEXT_MESSAGE_CONTENT"
        assert flattened["delta"] == "hello"
        assert flattened["trace_id"] == "trace-123"
        assert flattened["session_id"] == "session-123"
        assert flattened["timestamp"] == "2026-02-11T00:00:00Z"
        assert flattened["details"] == {"message": "ok"}

    def test_test_endpoint_requires_document_for_document_dependent_agent(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent_id = uuid.uuid4()

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: SimpleNamespace(id=custom_agent_id),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_runtime_info",
            lambda _aid, db=None, **_kwargs: SimpleNamespace(
                requires_document=True,
            ),
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.test_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    request=api_module.TestCustomAgentRequest(input="test query"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert exc_info.value.status_code == 400
        assert "requires a document_id" in str(exc_info.value.detail)

    def test_test_endpoint_streams_runner_events(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent_id = uuid.uuid4()
        run_kwargs = {}

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: SimpleNamespace(id=custom_agent_id),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_runtime_info",
            lambda _aid, db=None, **_kwargs: SimpleNamespace(
                requires_document=False,
            ),
        )
        agent_kwargs = {}
        construction_order = []
        monkeypatch.setattr(
            api_module,
            "clear_pending_configs",
            lambda: construction_order.append("clear"),
        )
        monkeypatch.setattr(
            api_module,
            "get_agent_by_id",
            lambda _aid, **kwargs: (
                construction_order.append("build"),
                agent_kwargs.update(kwargs),
                object(),
            )[-1],
        )
        monkeypatch.setattr(
            api_module,
            "get_groups_from_provider_groups",
            lambda provider_groups: ["RGD"] if provider_groups == ["provider-rgd"] else [],
        )

        async def _fake_run_agent_streamed(**kwargs):
            run_kwargs.update(kwargs)
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123",
                "execution_receipt": {"agent_key": "ca_pinned", "revision": 4}}}
            yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"delta": "hello"}}
            yield {
                "type": "RUN_FINISHED",
                "data": {"response": "hello", "trace_id": "trace-123"},
            }

        monkeypatch.setattr(api_module, "run_agent_streamed", _fake_run_agent_streamed)

        response = asyncio.run(
            api_module.test_custom_agent_endpoint(
                custom_agent_id=custom_agent_id,
                request=api_module.TestCustomAgentRequest(input="test query", group_id="WB"),
                user={"sub": "auth-sub", "cognito:groups": ["provider-rgd"]},
                db=SimpleNamespace(),
            )
        )

        assert isinstance(response, StreamingResponse)

        async def _consume_stream() -> str:
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, bytes):
                    chunks.append(chunk.decode("utf-8"))
                else:
                    chunks.append(chunk)
            return "".join(chunks)

        stream_text = asyncio.run(_consume_stream())
        assert '"type": "TEXT_MESSAGE_CONTENT"' in stream_text
        assert '"delta": "hello"' in stream_text
        assert '"type": "DONE"' in stream_text
        assert '"trace_id": "trace-123"' in stream_text
        assert '"execution_receipt": {"agent_key": "ca_pinned", "revision": 4}' in stream_text
        assert run_kwargs["active_groups"] == ["WB"]
        assert agent_kwargs["active_groups"] == ["WB"]
        assert agent_kwargs["authenticated_groups"] == ["RGD"]
        assert construction_order == ["clear", "build"]
        assert run_kwargs["context_messages"] == [{"role": "user", "content": "test query"}]

    def test_test_request_rejects_legacy_mod_id_alias(self):
        import src.api.agent_studio_custom as api_module

        with pytest.raises(ValidationError):
            api_module.TestCustomAgentRequest(input="test query", mod_id="WB")


def _custom_agent_payload(template_source: str = "gene") -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "agent_id": "ca_11111111-1111-1111-1111-111111111111",
        "user_id": 1,
        "template_source": template_source,
        "name": "My Agent",
        "description": "Desc",
        "custom_prompt": "Prompt",
        "group_prompt_overrides": {},
        "allowed_group_ids": ["RGD"],
        "inherited_allowed_group_ids": ["RGD", "WB"],
        "icon": "🔧",
        "include_group_rules": True,
        "model_id": "gpt-4o",
        "model_temperature": 0.1,
        "model_reasoning": None,
        "tool_ids": ["agr_curation_query"],
        "output_schema_key": None,
        "visibility": "private",
        "project_id": None,
        "is_active": True,
        "created_at": datetime(2026, 2, 23, tzinfo=UTC),
        "updated_at": datetime(2026, 2, 23, tzinfo=UTC),
    }


class TestCustomAgentCrudContract:
    """Unit tests for create/list contract shape and template-source filtering."""

    def test_create_endpoint_uses_template_source_only(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        observed_kwargs = {}

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )

        def _fake_create_custom_agent(**kwargs):
            observed_kwargs.update(kwargs)
            return SimpleNamespace()

        monkeypatch.setattr(api_module, "create_custom_agent", _fake_create_custom_agent)
        monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload("gene"))

        db = SimpleNamespace(
            commit=lambda: None,
            refresh=lambda _obj: None,
            rollback=lambda: None,
        )

        response = asyncio.run(
            api_module.create_custom_agent_endpoint(
                request=api_module.CreateCustomAgentRequest(
                    template_source="gene",
                    name="My Agent",
                    custom_prompt="Prompt",
                    model_id="gpt-4o",
                    allowed_group_ids=["RGD"],
                ),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

        assert observed_kwargs["template_source"] == "gene"
        assert observed_kwargs["allowed_group_ids"] == ["RGD"]
        assert response.allowed_group_ids == ["RGD"]
        assert response.inherited_allowed_group_ids == ["RGD", "WB"]
        assert "parent_agent_id" not in observed_kwargs
        assert response.template_source == "gene"
        assert "parent_agent_key" not in response.model_dump()
        assert {
            "parent_prompt_hash",
            "current_parent_prompt_hash",
            "parent_prompt_stale",
            "parent_exists",
        }.isdisjoint(response.model_dump())

    def test_create_request_rejects_unknown_legacy_fields(self):
        import src.api.agent_studio_custom as api_module

        with pytest.raises(ValidationError):
            api_module.CreateCustomAgentRequest(
                template_source="gene",
                name="My Agent",
                parent_agent_id="gene",  # legacy field should be rejected
            )

    def test_create_request_rejects_legacy_mod_alias_fields(self):
        import src.api.agent_studio_custom as api_module

        with pytest.raises(ValidationError):
            api_module.CreateCustomAgentRequest(
                template_source="gene",
                name="My Agent",
                mod_prompt_overrides={"WB": "Rules"},
                include_mod_rules=False,
            )

    def test_update_request_rejects_legacy_mod_alias_fields(self):
        import src.api.agent_studio_custom as api_module

        with pytest.raises(ValidationError):
            api_module.UpdateCustomAgentRequest(
                mod_prompt_overrides={"WB": "Rules"},
                include_mod_rules=True,
            )

    def test_create_endpoint_returns_400_for_unknown_model(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        validation_message = (
            "Agents using an envelope output schema must include a builder finalize "
            "tool before saving. Output schema 'gene_extraction' has no finalize_* "
            "tool in tool_ids; add the appropriate builder-finalization tool or clear "
            "the output schema."
        )
        monkeypatch.setattr(
            api_module,
            "create_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError(validation_message)),
        )

        db = SimpleNamespace(
            commit=lambda: None,
            refresh=lambda _obj: None,
            rollback=lambda: None,
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.create_custom_agent_endpoint(
                    request=api_module.CreateCustomAgentRequest(
                        name="My Agent",
                        custom_prompt="Prompt",
                        model_id="not-real",
                    ),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == validation_message
        assert "builder finalize tool" in str(exc_info.value.detail)
        assert "builder finalize tool" in caplog.text

    def test_list_endpoint_filters_by_template_source_only(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        observed = {}

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )

        def _fake_list_custom_agents_for_user(_db, user_id, template_source=None):
            observed["user_id"] = user_id
            observed["template_source"] = template_source
            return [SimpleNamespace(allowed_group_ids=[])]

        monkeypatch.setattr(api_module, "list_custom_agents_for_user", _fake_list_custom_agents_for_user)
        monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload("gene"))

        response = asyncio.run(
            api_module.list_custom_agents_endpoint(
                template_source="gene",
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert observed == {"user_id": 1, "template_source": "gene"}
        assert response.total == 1
        assert response.custom_agents[0].template_source == "gene"

    def test_list_endpoint_filters_group_restricted_agents(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_groups_from_provider_groups",
            lambda _groups: ["MGI"],
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_for_user",
            lambda *_args, **_kwargs: [
                SimpleNamespace(allowed_group_ids=[]),
                SimpleNamespace(allowed_group_ids=["RGD"]),
            ],
        )
        monkeypatch.setattr(
            api_module,
            "_as_response_payload",
            lambda agent: _custom_agent_payload(
                "open" if not agent.allowed_group_ids else "restricted"
            ),
        )

        response = asyncio.run(
            api_module.list_custom_agents_endpoint(
                user={"sub": "auth-sub", "cognito:groups": ["MGI"]},
                db=SimpleNamespace(),
            )
        )

        assert response.total == 1
        assert response.custom_agents[0].template_source == "open"
        assert "parent_agent_key" not in response.custom_agents[0].model_dump()


def _db_mock():
    return SimpleNamespace(
        commit=MagicMock(),
        refresh=MagicMock(),
        rollback=MagicMock(),
    )


class TestCustomAgentCrudErrorsAndBranches:
    def test_create_endpoint_returns_409_for_duplicate_name_value_error(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "create_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError("custom agent already exists")),
        )

        db = _db_mock()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.create_custom_agent_endpoint(
                    request=api_module.CreateCustomAgentRequest(name="My Agent"),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "A custom agent with this name already exists"
        assert "custom agent already exists" in caplog.text
        db.rollback.assert_called_once()

    def test_create_endpoint_returns_409_for_unique_integrity_error(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        duplicate_exc = IntegrityError(
            statement="insert",
            params={},
            orig=Exception("duplicate key value violates unique constraint"),
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "create_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(duplicate_exc),
        )

        db = _db_mock()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.create_custom_agent_endpoint(
                    request=api_module.CreateCustomAgentRequest(name="My Agent"),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )

        assert exc_info.value.status_code == 409
        db.rollback.assert_called_once()

    def test_create_endpoint_returns_500_for_non_unique_integrity_error(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        report_calls = []
        db_exc = IntegrityError(
            statement="insert",
            params={"instructions": "secret prompt text"},
            orig=Exception("db write failed secret prompt text"),
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "create_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(db_exc),
        )

        def _fake_report_runtime_exception(exc, **kwargs):
            report_calls.append((exc, kwargs))
            return True

        monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

        db = _db_mock()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.create_custom_agent_endpoint(
                    request=api_module.CreateCustomAgentRequest(name="My Agent"),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )

        assert exc_info.value.status_code == 500
        db.rollback.assert_called_once()
        assert len(report_calls) == 1
        assert isinstance(report_calls[0][0], api_module._CustomAgentDatabaseError)
        assert "Exception" in str(report_calls[0][0])
        assert "secret prompt text" not in str(report_calls[0][0])
        assert report_calls[0][1]["component"] == "api"
        assert report_calls[0][1]["operation"] == "sanitized_http_exception"
        assert report_calls[0][1]["context"]["logger_name"] == api_module.logger.name
        assert report_calls[0][1]["context"]["status_code"] == 500

    def test_update_endpoint_returns_500_for_non_unique_integrity_error(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        report_calls = []
        custom_agent = SimpleNamespace(id=uuid.uuid4())
        db_exc = IntegrityError(
            statement="update",
            params={"instructions": "secret prompt text"},
            orig=Exception("db write failed secret prompt text"),
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(api_module, "get_custom_agent_for_user", lambda *_args, **_kwargs: custom_agent)
        monkeypatch.setattr(
            api_module,
            "update_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(db_exc),
        )

        def _fake_report_runtime_exception(exc, **kwargs):
            report_calls.append((exc, kwargs))
            return True

        monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

        db = _db_mock()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.update_custom_agent_endpoint(
                    custom_agent_id=custom_agent.id,
                    request=api_module.UpdateCustomAgentRequest(name="Updated"),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Database error while updating custom agent"
        db.rollback.assert_called_once()
        assert len(report_calls) == 1
        assert isinstance(report_calls[0][0], api_module._CustomAgentDatabaseError)
        assert "Exception" in str(report_calls[0][0])
        assert "secret prompt text" not in str(report_calls[0][0])
        assert report_calls[0][1]["component"] == "api"
        assert report_calls[0][1]["operation"] == "sanitized_http_exception"
        assert report_calls[0][1]["context"]["logger_name"] == api_module.logger.name
        assert report_calls[0][1]["context"]["status_code"] == 500

    def test_list_endpoint_value_error_maps_to_400(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid template source")),
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                api_module.list_custom_agents_endpoint(
                    template_source="invalid",
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Custom agent query is invalid"
        assert "invalid template source" not in str(exc_info.value.detail)
        assert "invalid template source" in caplog.text

    def test_get_endpoint_maps_not_found_and_access_errors(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        from src.lib.agent_studio.custom_agent_service import CustomAgentAccessError, CustomAgentNotFoundError

        custom_agent_id = uuid.uuid4()
        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                CustomAgentNotFoundError(f"Custom agent '{custom_agent_id}' not found")
            ),
        )
        with pytest.raises(HTTPException) as not_found_exc:
            asyncio.run(
                api_module.get_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert not_found_exc.value.status_code == 404
        assert not_found_exc.value.detail == "Custom agent not found"
        assert str(custom_agent_id) not in str(not_found_exc.value.detail)
        assert str(custom_agent_id) in caplog.text

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CustomAgentAccessError("forbidden")),
        )
        with pytest.raises(HTTPException) as access_exc:
            asyncio.run(
                api_module.get_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert access_exc.value.status_code == 403
        assert access_exc.value.detail == "Access denied to custom agent"
        assert "forbidden" not in str(access_exc.value.detail)
        assert "forbidden" in caplog.text

    def test_update_endpoint_success_commits_refreshes_and_returns_payload(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent = SimpleNamespace(id=uuid.uuid4())
        observed_kwargs = {}
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: custom_agent,
        )
        monkeypatch.setattr(
            api_module,
            "update_custom_agent",
            lambda **kwargs: observed_kwargs.update(kwargs),
        )
        monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload("gene"))

        db = _db_mock()
        response = asyncio.run(
            api_module.update_custom_agent_endpoint(
                custom_agent_id=custom_agent.id,
                request=api_module.UpdateCustomAgentRequest(
                    name="Updated name", allowed_group_ids=["RGD"]
                ),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

        assert response.template_source == "gene"
        assert response.allowed_group_ids == ["RGD"]
        assert observed_kwargs["allowed_group_ids"] == ["RGD"]
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(custom_agent)

    def test_version_response_returns_allowed_group_ids(self):
        import src.api.agent_studio_custom as api_module

        version = SimpleNamespace(
            id=uuid.uuid4(),
            custom_agent_id=uuid.uuid4(),
            version=3,
            custom_prompt="Prompt",
            group_prompt_overrides={},
            allowed_group_ids=["RGD"],
            notes="Restricted snapshot",
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
        )

        response = api_module._as_version_payload(version)

        assert response.allowed_group_ids == ["RGD"]

    def test_version_response_requires_persisted_access_field(self):
        import src.api.agent_studio_custom as api_module

        version = SimpleNamespace(
            id=uuid.uuid4(),
            custom_agent_id=uuid.uuid4(),
            version=3,
            custom_prompt="Prompt",
            group_prompt_overrides={},
            notes="Incomplete snapshot",
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
        )

        with pytest.raises(AttributeError, match="allowed_group_ids"):
            api_module._as_version_payload(version)

    def test_update_endpoint_maps_value_and_integrity_errors(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        custom_agent = SimpleNamespace(id=uuid.uuid4())
        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(api_module, "get_custom_agent_for_user", lambda *_args, **_kwargs: custom_agent)

        monkeypatch.setattr(
            api_module,
            "update_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError("name already exists")),
        )
        db = _db_mock()
        with pytest.raises(HTTPException) as conflict_exc:
            asyncio.run(
                api_module.update_custom_agent_endpoint(
                    custom_agent_id=custom_agent.id,
                    request=api_module.UpdateCustomAgentRequest(name="Dup"),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )
        assert conflict_exc.value.status_code == 409
        assert conflict_exc.value.detail == "A custom agent with this name already exists"
        assert "name already exists" in caplog.text
        db.rollback.assert_called_once()

        validation_message = (
            "Agents using an envelope output schema must include a builder finalize "
            "tool before saving. Output schema 'gene_extraction' has no finalize_* "
            "tool in tool_ids; add the appropriate builder-finalization tool or clear "
            "the output schema."
        )
        monkeypatch.setattr(
            api_module,
            "update_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError(validation_message)),
        )
        db = _db_mock()
        with pytest.raises(HTTPException) as validation_exc:
            asyncio.run(
                api_module.update_custom_agent_endpoint(
                    custom_agent_id=custom_agent.id,
                    request=api_module.UpdateCustomAgentRequest(
                        output_schema_key="gene_extraction",
                        tool_ids=[],
                        allow_empty_tool_ids=True,
                    ),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )
        assert validation_exc.value.status_code == 400
        assert validation_exc.value.detail == validation_message
        assert "builder finalize tool" in str(validation_exc.value.detail)
        db.rollback.assert_called_once()

        db_unique = IntegrityError(
            statement="update",
            params={},
            orig=Exception("duplicate key value violates unique constraint"),
        )
        monkeypatch.setattr(
            api_module,
            "update_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(db_unique),
        )
        db = _db_mock()
        with pytest.raises(HTTPException) as integrity_exc:
            asyncio.run(
                api_module.update_custom_agent_endpoint(
                    custom_agent_id=custom_agent.id,
                    request=api_module.UpdateCustomAgentRequest(name="Dup"),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )
        assert integrity_exc.value.status_code == 409
        db.rollback.assert_called_once()

    def test_delete_and_versions_endpoints_map_access_errors(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        from src.lib.agent_studio.custom_agent_service import CustomAgentAccessError, CustomAgentNotFoundError

        custom_agent_id = uuid.uuid4()
        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CustomAgentAccessError("forbidden")),
        )
        db = _db_mock()
        with pytest.raises(HTTPException) as delete_exc:
            asyncio.run(
                api_module.delete_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )
        assert delete_exc.value.status_code == 403
        assert delete_exc.value.detail == "Access denied to custom agent"
        assert "forbidden" not in str(delete_exc.value.detail)
        assert "forbidden" in caplog.text
        db.rollback.assert_called_once()

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CustomAgentNotFoundError("missing")),
        )
        with pytest.raises(HTTPException) as versions_exc:
            asyncio.run(
                api_module.list_custom_agent_versions_endpoint(
                    custom_agent_id=custom_agent_id,
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert versions_exc.value.status_code == 404
        assert versions_exc.value.detail == "Custom agent not found"
        assert "missing" not in str(versions_exc.value.detail)
        assert "missing" in caplog.text

    def test_prompt_only_revert_route_is_removed(self):
        import src.api.agent_studio_custom as api_module

        assert not any("/revert/" in route.path for route in api_module.router.routes)
        payload = api_module.CustomAgentVersionResponse(
            id=str(uuid.uuid4()), custom_agent_id=str(uuid.uuid4()), version=1,
            custom_prompt="Historical prompt", created_at=datetime.now(UTC),
        )
        assert payload.executable is False

    def test_test_endpoint_runtime_and_stream_error_branches(self, monkeypatch, caplog):
        import src.api.agent_studio_custom as api_module

        custom_agent_id = uuid.uuid4()
        caplog.set_level(logging.WARNING, logger=api_module.logger.name)
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: SimpleNamespace(id=custom_agent_id),
        )

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_runtime_info",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(HTTPException) as missing_runtime_exc:
            asyncio.run(
                api_module.test_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    request=api_module.TestCustomAgentRequest(input="hello"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert missing_runtime_exc.value.status_code == 404

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_runtime_info",
            lambda *_args, **_kwargs: SimpleNamespace(
                requires_document=False,
            ),
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub=None),
        )
        with pytest.raises(HTTPException) as missing_user_exc:
            asyncio.run(
                api_module.test_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    request=api_module.TestCustomAgentRequest(input="hello"),
                    user={},
                    db=SimpleNamespace(),
                )
            )
        assert missing_user_exc.value.status_code == 401

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(api_module, "get_agent_by_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("init failed")))
        with pytest.raises(HTTPException) as init_exc:
            asyncio.run(
                api_module.test_custom_agent_endpoint(
                    custom_agent_id=custom_agent_id,
                    request=api_module.TestCustomAgentRequest(input="hello"),
                    user={"sub": "auth-sub"},
                    db=SimpleNamespace(),
                )
            )
        assert init_exc.value.status_code == 400
        assert init_exc.value.detail == "Failed to initialize custom agent"
        assert "init failed" not in str(init_exc.value.detail)
        assert "init failed" in caplog.text

        monkeypatch.setattr(api_module, "get_agent_by_id", lambda *_args, **_kwargs: object())

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-x"}}
            raise RuntimeError("stream exploded")

        monkeypatch.setattr(api_module, "run_agent_streamed", _fake_run_agent_streamed)
        response = asyncio.run(
            api_module.test_custom_agent_endpoint(
                custom_agent_id=custom_agent_id,
                request=api_module.TestCustomAgentRequest(input="hello"),
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )
        assert isinstance(response, StreamingResponse)

        async def _consume_stream() -> str:
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
            return "".join(chunks)

        stream_text = asyncio.run(_consume_stream())
        assert '"type": "RUN_ERROR"' in stream_text
        assert "Custom-agent test failed unexpectedly." in stream_text
        assert "stream exploded" not in stream_text
        assert "stream exploded" in caplog.text
