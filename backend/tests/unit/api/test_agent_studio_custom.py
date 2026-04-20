"""Tests for custom-agent API endpoints."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


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
        assert flattened["sessionId"] == "session-123"
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
            lambda _aid, db=None: SimpleNamespace(
                parent_exists=True,
                requires_document=True,
                parent_agent_key="pdf_extraction",
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
            lambda _aid, db=None: SimpleNamespace(
                parent_exists=True,
                requires_document=False,
                parent_agent_key="gene",
            ),
        )
        monkeypatch.setattr(api_module, "get_agent_by_id", lambda _aid, **_kwargs: object())

        async def _fake_run_agent_streamed(**kwargs):
            run_kwargs.update(kwargs)
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-123"}}
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
                user={"sub": "auth-sub"},
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
        assert run_kwargs["active_groups"] == ["WB"]
        assert run_kwargs["context_messages"] == [{"role": "user", "content": "test query"}]

    def test_test_request_accepts_legacy_mod_id_alias(self):
        import src.api.agent_studio_custom as api_module

        request = api_module.TestCustomAgentRequest(input="test query", mod_id="WB")

        assert request.group_id == "WB"


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
        "icon": "🔧",
        "include_group_rules": True,
        "model_id": "gpt-4o",
        "model_temperature": 0.1,
        "model_reasoning": None,
        "tool_ids": ["agr_curation_query"],
        "output_schema_key": None,
        "visibility": "private",
        "project_id": None,
        "parent_prompt_hash": None,
        "current_parent_prompt_hash": None,
        "parent_prompt_stale": False,
        "parent_exists": True,
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
                ),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

        assert observed_kwargs["template_source"] == "gene"
        assert "parent_agent_id" not in observed_kwargs
        assert response.template_source == "gene"
        assert "parent_agent_key" not in response.model_dump()

    def test_create_request_rejects_unknown_legacy_fields(self):
        import src.api.agent_studio_custom as api_module

        with pytest.raises(ValidationError):
            api_module.CreateCustomAgentRequest(
                template_source="gene",
                name="My Agent",
                parent_agent_id="gene",  # legacy field should be rejected
            )

    def test_create_request_accepts_legacy_mod_alias_fields(self):
        import src.api.agent_studio_custom as api_module

        request = api_module.CreateCustomAgentRequest(
            template_source="gene",
            name="My Agent",
            mod_prompt_overrides={"WB": "Rules"},
            include_mod_rules=False,
        )

        assert request.group_prompt_overrides == {"WB": "Rules"}
        assert request.include_group_rules is False

    def test_update_request_accepts_legacy_mod_alias_fields(self):
        import src.api.agent_studio_custom as api_module

        request = api_module.UpdateCustomAgentRequest(
            mod_prompt_overrides={"WB": "Rules"},
            include_mod_rules=True,
        )

        assert request.group_prompt_overrides == {"WB": "Rules"}
        assert request.include_group_rules is True

    def test_create_endpoint_returns_400_for_unknown_model(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(
            api_module,
            "create_custom_agent",
            lambda **_kwargs: (_ for _ in ()).throw(ValueError("Unknown model_id: not-real")),
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
        assert "Unknown model_id" in str(exc_info.value.detail)

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
            return [SimpleNamespace()]

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
        assert "parent_agent_key" not in response.custom_agents[0].model_dump()

    def test_test_endpoint_does_not_block_when_parent_missing(self, monkeypatch):
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
            lambda _aid, db=None: SimpleNamespace(
                parent_exists=False,
                requires_document=False,
                parent_agent_key="missing_template",
            ),
        )
        monkeypatch.setattr(api_module, "get_agent_by_id", lambda _aid, **_kwargs: object())

        async def _fake_run_agent_streamed(**_kwargs):
            yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-parentless"}}
            yield {
                "type": "RUN_FINISHED",
                "data": {"response": "ok", "trace_id": "trace-parentless"},
            }

        monkeypatch.setattr(api_module, "run_agent_streamed", _fake_run_agent_streamed)

        response = asyncio.run(
            api_module.test_custom_agent_endpoint(
                custom_agent_id=custom_agent_id,
                request=api_module.TestCustomAgentRequest(input="test query"),
                user={"sub": "auth-sub"},
                db=SimpleNamespace(),
            )
        )

        assert isinstance(response, StreamingResponse)


def _db_mock():
    return SimpleNamespace(
        commit=MagicMock(),
        refresh=MagicMock(),
        rollback=MagicMock(),
    )


class TestCustomAgentCrudErrorsAndBranches:
    def test_create_endpoint_returns_409_for_duplicate_name_value_error(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

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

        db_exc = IntegrityError(statement="insert", params={}, orig=Exception("db write failed"))
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

    def test_list_endpoint_value_error_maps_to_400(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

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

    def test_get_endpoint_maps_not_found_and_access_errors(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        from src.lib.agent_studio.custom_agent_service import CustomAgentAccessError, CustomAgentNotFoundError

        custom_agent_id = uuid.uuid4()
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CustomAgentNotFoundError("not found")),
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

    def test_update_endpoint_success_commits_refreshes_and_returns_payload(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(api_module, "get_custom_agent_for_user", lambda *_args, **_kwargs: custom_agent)
        monkeypatch.setattr(api_module, "update_custom_agent", lambda **_kwargs: None)
        monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload("gene"))

        db = _db_mock()
        response = asyncio.run(
            api_module.update_custom_agent_endpoint(
                custom_agent_id=custom_agent.id,
                request=api_module.UpdateCustomAgentRequest(name="Updated name"),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

        assert response.template_source == "gene"
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(custom_agent)

    def test_update_endpoint_maps_value_and_integrity_errors(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        custom_agent = SimpleNamespace(id=uuid.uuid4())
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

    def test_delete_and_versions_endpoints_map_access_errors(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        from src.lib.agent_studio.custom_agent_service import CustomAgentAccessError, CustomAgentNotFoundError

        custom_agent_id = uuid.uuid4()
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

    def test_revert_endpoint_success_and_404(self, monkeypatch):
        import src.api.agent_studio_custom as api_module

        from src.lib.agent_studio.custom_agent_service import CustomAgentNotFoundError

        custom_agent = SimpleNamespace(id=uuid.uuid4())
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
        )
        monkeypatch.setattr(api_module, "get_custom_agent_for_user", lambda *_args, **_kwargs: custom_agent)
        monkeypatch.setattr(api_module, "revert_custom_agent_to_version", lambda **_kwargs: None)
        monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload("gene"))

        db = _db_mock()
        response = asyncio.run(
            api_module.revert_custom_agent_endpoint(
                custom_agent_id=custom_agent.id,
                version=2,
                request=api_module.RevertCustomAgentRequest(notes="rollback"),
                user={"sub": "auth-sub"},
                db=db,
            )
        )
        assert response.template_source == "gene"
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(custom_agent)

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CustomAgentNotFoundError("missing")),
        )
        db = _db_mock()
        with pytest.raises(HTTPException) as revert_exc:
            asyncio.run(
                api_module.revert_custom_agent_endpoint(
                    custom_agent_id=custom_agent.id,
                    version=99,
                    request=api_module.RevertCustomAgentRequest(),
                    user={"sub": "auth-sub"},
                    db=db,
                )
            )
        assert revert_exc.value.status_code == 404
        db.rollback.assert_called_once()

    def test_test_endpoint_runtime_and_stream_error_branches(self, monkeypatch):
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
                parent_exists=True,
                requires_document=False,
                parent_agent_key="gene",
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
        assert "stream exploded" in stream_text
