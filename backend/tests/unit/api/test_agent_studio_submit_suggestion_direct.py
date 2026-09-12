"""Tests for AI-assisted direct suggestion submission endpoints."""

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException


def test_submit_suggestion_direct_requires_openai_key(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.submit_suggestion_direct(
                request=api_module.DirectSubmissionRequest(),
                background_tasks=BackgroundTasks(),
                db=SimpleNamespace(),
                user={"email": "curator@example.org", "sub": "auth-sub-1"},
            )
        )

    assert exc_info.value.status_code == 500
    assert "OpenAI API key not configured" in str(exc_info.value.detail)


def test_submit_suggestion_direct_rejects_invalid_system_agent(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "get_prompt_catalog",
        lambda: SimpleNamespace(get_agent=lambda _agent_id: None),
    )
    monkeypatch.setattr(api_module, "get_agent_by_key", lambda *_args, **_kwargs: None)

    request = api_module.DirectSubmissionRequest(
        context=api_module.ChatContext(selected_agent_id="missing_agent")
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.submit_suggestion_direct(
                request=request,
                background_tasks=BackgroundTasks(),
                db=SimpleNamespace(),
                user={"email": "curator@example.org", "sub": "auth-sub-1"},
            )
        )

    assert exc_info.value.status_code == 400
    assert "Invalid agent_id: missing_agent" in str(exc_info.value.detail)


def test_submit_suggestion_direct_enqueues_background_job(monkeypatch):
    import src.api.agent_studio as api_module

    captured = {}

    class _FakeBackgroundTasks:
        def add_task(self, func, **kwargs):
            captured["func"] = func
            captured["kwargs"] = kwargs

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "get_prompt_catalog",
        lambda: SimpleNamespace(get_agent=lambda _agent_id: SimpleNamespace(agent_id=_agent_id)),
    )
    monkeypatch.setattr(
        api_module,
        "get_agent_by_key",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(api_module, "_build_opus_system_prompt", lambda _context: "system prompt")

    request = api_module.DirectSubmissionRequest(
        context=api_module.ChatContext(selected_agent_id="gene"),
        messages=[api_module.ChatMessage(role="user", content="Please help")],
    )

    response = asyncio.run(
        api_module.submit_suggestion_direct(
            request=request,
            background_tasks=_FakeBackgroundTasks(),
            db=SimpleNamespace(),
            user={"email": "curator@example.org", "sub": "auth-sub-1"},
        )
    )

    assert response.success is True
    assert response.message == "Submission sent"
    assert getattr(captured["func"], "__observability_original_task__") == api_module._process_suggestion_background
    assert getattr(captured["func"], "__observability_task_name__") == "agent_studio.process_suggestion"
    assert getattr(captured["func"], "__observability_tags__") == {
        "component": "agent_studio"
    }
    assert captured["kwargs"]["user_email"] == "curator@example.org"
    assert captured["kwargs"]["user_auth_sub"] == "auth-sub-1"
    assert "api_key" not in captured["kwargs"]
    assert captured["kwargs"]["context"] == request.context
    assert captured["kwargs"]["messages"][0]["content"] == "Please help"
    assert captured["kwargs"]["messages"][-1]["content"].startswith(
        "The user has requested you submit feedback to the development team"
    )


def test_process_suggestion_background_notifies_when_no_tool_use(monkeypatch):
    import src.api.agent_studio as api_module

    notified = {}
    reported = []
    async def _fake_forced_tool(**_kwargs):
        return None

    monkeypatch.setattr(api_module, "run_forced_agent_studio_tool", _fake_forced_tool)
    monkeypatch.setattr(
        api_module,
        "_send_error_notification_sns",
        lambda user_email, error_message, context=None: notified.update(
            {"user_email": user_email, "error_message": error_message, "context": context}
        ),
    )
    monkeypatch.setattr(
        api_module,
        "report_background_task_exception",
        lambda exc, *, task_name, tags=None, context=None: reported.append(
            {
                "exc": str(exc),
                "task_name": task_name,
                "tags": dict(tags or {}),
                "context": dict(context or {}),
            }
        ),
    )

    asyncio.run(
        api_module._process_suggestion_background(
            messages=[{"role": "user", "content": "hello"}],
            system_prompt="system",
            context=None,
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
        )
    )

    assert notified["user_email"] == "curator@example.org"
    assert "did not submit" in notified["error_message"]
    assert reported[0]["task_name"] == "agent_studio.process_suggestion"
    assert reported[0]["tags"] == {
        "component": "agent_studio",
        "failure_stage": "openai_agents_sdk",
    }


def test_process_suggestion_background_uses_openai_agents_sdk_contract(monkeypatch):
    import src.api.agent_studio as api_module

    captured = {}
    async def _fake_forced_tool(**kwargs):
        captured["request"] = kwargs
        result = await kwargs["executor"](
            "submit_prompt_suggestion",
            {"summary": "Focused request contract"},
            "call-1",
        )
        return SimpleNamespace(output=result.full_output)

    async def _fake_handle_tool_call(**kwargs):
        captured["tool_call"] = kwargs
        return {"success": True, "suggestion_id": "suggestion-1"}

    monkeypatch.setattr(api_module, "run_forced_agent_studio_tool", _fake_forced_tool)
    monkeypatch.setattr(api_module, "_handle_tool_call", _fake_handle_tool_call)

    asyncio.run(
        api_module._process_suggestion_background(
            messages=[{"role": "user", "content": "hello"}],
            system_prompt="system",
            context=None,
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
        )
    )

    request = captured["request"]
    assert request["instructions"] == "system"
    assert request["input_items"] == [{"role": "user", "content": "hello"}]
    assert request["tool_definition"] == api_module.SUGGESTION_TOOL
    assert request["max_turns"] == api_module.get_agent_studio_suggestion_max_turns()
    assert request["max_output_tokens"] == api_module.get_agent_studio_suggestion_max_output_tokens()
    assert request["user_id"] == "auth-sub-1"
    assert captured["tool_call"]["tool_input"] == {
        "summary": "Focused request contract"
    }


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        pytest.param("refusal", "refusal", id="refusal"),
        pytest.param("incomplete", "incomplete", id="incomplete"),
        pytest.param(
            "context_overflow",
            "context_overflow",
            id="context-overflow",
        ),
    ],
)
def test_process_suggestion_background_does_not_report_typed_outcomes_as_crashes(
    monkeypatch,
    error,
    expected_outcome,
):
    import src.api.agent_studio as api_module

    provider_error = {
        "refusal": api_module.ModelRefusalError("sensitive refusal"),
        "incomplete": api_module.ModelBehaviorError(
            "Responses stream ended with terminal event `response.incomplete`."
        ),
        "context_overflow": api_module.openai.BadRequestError(
            "Sensitive request exceeded the context length token limit",
            response=httpx.Response(
                400,
                request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            ),
            body={},
        ),
    }[error]
    notifications = []
    reports = []

    async def _raise_typed_outcome(**_kwargs):
        raise provider_error

    monkeypatch.setattr(
        api_module,
        "run_forced_agent_studio_tool",
        _raise_typed_outcome,
    )
    monkeypatch.setattr(
        api_module,
        "report_background_task_exception",
        lambda *args, **kwargs: reports.append((args, kwargs)),
    )
    monkeypatch.setattr(
        api_module,
        "_send_error_notification_sns",
        lambda *args: notifications.append(args),
    )

    asyncio.run(
        api_module._process_suggestion_background(
            messages=[{"role": "user", "content": "hello"}],
            system_prompt="system",
            context=None,
            user_email="curator@example.org",
            user_auth_sub="auth-sub-1",
        )
    )

    assert reports == []
    assert len(notifications) == 1
    assert expected_outcome.replace("_", " ") in notifications[0][1]
    assert "sensitive" not in notifications[0][1].lower()


def test_submit_suggestion_direct_sanitizes_unexpected_errors(monkeypatch, caplog):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(api_module, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    caplog.set_level(logging.ERROR, logger=api_module.logger.name)

    response = asyncio.run(
        api_module.submit_suggestion_direct(
            request=api_module.DirectSubmissionRequest(),
            background_tasks=BackgroundTasks(),
            db=SimpleNamespace(),
            user={"email": "curator@example.org", "sub": "auth-sub-1"},
        )
    )

    assert response.success is False
    assert response.message == "An error occurred"
    assert response.error == "Failed to submit suggestion"
    assert "database unavailable" not in str(response.error)
    assert "database unavailable" in caplog.text
