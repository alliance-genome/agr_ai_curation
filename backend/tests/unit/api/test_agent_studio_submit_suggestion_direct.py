"""Tests for AI-assisted direct suggestion submission endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException


def test_submit_suggestion_direct_requires_anthropic_key(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.submit_suggestion_direct(
                request=api_module.DirectSubmissionRequest(),
                background_tasks=BackgroundTasks(),
                db=SimpleNamespace(),
                user={"email": "curator@example.org"},
            )
        )

    assert exc_info.value.status_code == 500
    assert "Anthropic API key not configured" in str(exc_info.value.detail)


def test_submit_suggestion_direct_rejects_invalid_system_agent(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
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

    request = api_module.DirectSubmissionRequest(
        context=api_module.ChatContext(selected_agent_id="missing_agent")
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.submit_suggestion_direct(
                request=request,
                background_tasks=BackgroundTasks(),
                db=SimpleNamespace(),
                user={"email": "curator@example.org"},
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

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
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
            user={"email": "curator@example.org"},
        )
    )

    assert response.success is True
    assert response.message == "Submission sent"
    assert captured["func"] == api_module._process_suggestion_background
    assert captured["kwargs"]["user_email"] == "curator@example.org"
    assert captured["kwargs"]["api_key"] == "test-key"
    assert captured["kwargs"]["messages"][0]["content"] == "Please help"
    assert captured["kwargs"]["messages"][-1]["content"].startswith(
        "The user has requested you submit feedback to the development team"
    )


def test_process_suggestion_background_notifies_when_no_tool_use(monkeypatch):
    import src.api.agent_studio as api_module

    notified = {}

    class _FakeMessagesClient:
        @staticmethod
        def create(**_kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="No tool call happened")],
            )

    class _FakeAnthropicClient:
        def __init__(self, **_kwargs):
            self.messages = _FakeMessagesClient()

    monkeypatch.setattr(api_module.anthropic, "Anthropic", _FakeAnthropicClient)
    monkeypatch.setattr(
        api_module,
        "_send_error_notification_sns",
        lambda user_email, error_message, context=None: notified.update(
            {"user_email": user_email, "error_message": error_message, "context": context}
        ),
    )

    asyncio.run(
        api_module._process_suggestion_background(
            messages=[{"role": "user", "content": "hello"}],
            system_prompt="system",
            context=None,
            user_email="curator@example.org",
            api_key="test-key",
        )
    )

    assert notified["user_email"] == "curator@example.org"
    assert "Opus did not call submit_prompt_suggestion" in notified["error_message"]
