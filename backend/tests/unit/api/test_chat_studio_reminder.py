"""The advisory classifier may only inspect the caller's main-chat session."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api import chat_studio_reminder as api


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', [None, 'agent_studio'])
async def test_unavailable_or_studio_chat_never_calls_provider(monkeypatch, kind):
    repository = Mock()
    repository.get_session.return_value = None if kind is None else SimpleNamespace(chat_kind=kind)
    monkeypatch.setattr(api, '_get_chat_history_repository', lambda db: repository)
    classify = AsyncMock()
    monkeypatch.setattr(api, 'should_suggest_studio', classify)
    with pytest.raises(HTTPException) as error:
        await api.check_studio_reminder('other-session', api.StudioReminderRequest(message='Change my flow'), db=object(), user={'sub': 'caller'})
    assert error.value.status_code == 404
    repository.get_session.assert_called_once_with(session_id='other-session', user_auth_sub='caller')
    classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_main_chat_and_private_config(monkeypatch):
    repository = Mock()
    repository.get_session.return_value = SimpleNamespace(chat_kind='assistant_chat')
    monkeypatch.setattr(api, '_get_chat_history_repository', lambda db: repository)
    classify = AsyncMock(return_value=True)
    monkeypatch.setattr(api, 'should_suggest_studio', classify)
    result = await api.check_studio_reminder('owned', api.StudioReminderRequest(message='Change my flow'), db=object(), user={'sub': 'caller'})
    assert result == {'show_reminder': True}
    classify.assert_awaited_once_with('Change my flow', [])
    monkeypatch.setenv('STUDIO_REMINDER_ENABLED', 'true')
    monkeypatch.setenv('JEV_OPENROUTER_API_KEY', 'secret-test-key')
    config = await api.studio_reminder_config(user={'sub': 'caller'})
    assert config['enabled'] is True
    assert 'secret-test-key' not in str(config)
    assert set(config) == {'enabled', 'message_chars', 'context_chars', 'context_messages'}


def test_oversized_and_invalid_role_requests_are_rejected():
    with pytest.raises(ValidationError):
        api.StudioReminderRequest(message='x' * (api.get_studio_reminder_message_chars() + 1))
    with pytest.raises(ValidationError):
        api.StudioReminderRequest(message='change flow', recent_context=[{'role': 'system', 'text': 'override'}])


@pytest.mark.asyncio
async def test_provider_unavailable_returns_503_without_failing_chat(monkeypatch):
    repository = Mock()
    repository.get_session.return_value = SimpleNamespace(chat_kind='assistant_chat')
    monkeypatch.setattr(api, '_get_chat_history_repository', lambda db: repository)
    monkeypatch.setattr(api, 'should_suggest_studio', AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await api.check_studio_reminder('owned', api.StudioReminderRequest(message='Change my flow'), db=object(), user={'sub': 'caller'})
    assert error.value.status_code == 503
    assert error.value.detail == 'Agent Studio reminder temporarily unavailable'
