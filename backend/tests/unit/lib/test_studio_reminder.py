import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.lib import studio_reminder as subject


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('STUDIO_REMINDER_ENABLED', 'true')
    monkeypatch.setenv('JEV_OPENROUTER_API_KEY', 'not-a-real-key')
    response = MagicMock()
    response.json.return_value = {'answers': {'focused': {'noul': 0.8}}}
    client = AsyncMock()
    client.post.return_value = response
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(subject.httpx, 'AsyncClient', factory)
    monkeypatch.setattr(subject, 'report_runtime_exception', MagicMock())
    return client


@pytest.mark.asyncio
async def test_screening_is_bounded_and_uses_openrouter(client, monkeypatch):
    monkeypatch.setenv('STUDIO_REMINDER_MESSAGE_CHARS', '10')
    monkeypatch.setenv('STUDIO_REMINDER_CONTEXT_CHARS', '4')
    assert await subject.should_suggest_studio('change this prompt please', [
        {'role': 'user', 'text': 'oldest'},
        {'role': 'assistant', 'text': 'second'},
        {'role': 'user', 'text': 'newest'},
    ])
    args, kwargs = client.post.call_args
    assert args[0] == 'https://openrouter.ai/api/alpha/decisions'
    assert kwargs['json']['state'] == {'current_message': 'change thi', 'recent_context': [
        {'role': 'assistant', 'text': 'seco'}, {'role': 'user', 'text': 'newe'}]}
    assert kwargs['json']['model'] == 'typesafe/jev-1.13'
    assert 'not-a-real-key' not in json.dumps(kwargs['json'])


@pytest.mark.asyncio
@pytest.mark.parametrize('probability', [0.1, -1, 2, True, '0.9', None, float('nan')])
async def test_invalid_or_negative_decisions_do_not_show_reminder(client, probability):
    client.post.return_value.json.return_value = {'answers': {'focused': {'noul': probability}}}
    result = await subject.should_suggest_studio('hello', [])
    if probability == 0.1:
        assert result is False
        subject.report_runtime_exception.assert_not_called()
    else:
        assert result is None
        subject.report_runtime_exception.assert_called_once()


@pytest.mark.asyncio
async def test_disabled_or_missing_key_does_not_call_provider(client, monkeypatch):
    monkeypatch.setenv('STUDIO_REMINDER_ENABLED', 'false')
    assert not await subject.should_suggest_studio('change prompt', [])
    monkeypatch.setenv('STUDIO_REMINDER_ENABLED', 'true')
    monkeypatch.delenv('JEV_OPENROUTER_API_KEY')
    assert not await subject.should_suggest_studio('change prompt', [])
    client.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [httpx.ReadTimeout('timeout'), ValueError('invalid json'), KeyError('answers')])
async def test_provider_failure_is_optional_and_does_not_expose_payload(client, error, caplog):
    client.post.side_effect = error
    assert await subject.should_suggest_studio('private curator text', []) is None
    subject.report_runtime_exception.assert_called_once()
    reported = subject.report_runtime_exception.call_args.args[0]
    assert reported.__context__ is None
    assert reported.__cause__ is None
    assert str(reported) == 'Studio reminder screening failed'
    assert 'private curator text' not in str(subject.report_runtime_exception.call_args)
    assert 'not-a-real-key' not in str(subject.report_runtime_exception.call_args)
    assert 'private curator text' not in caplog.text
    assert 'not-a-real-key' not in caplog.text


@pytest.mark.asyncio
async def test_zero_context_and_configured_threshold(client, monkeypatch):
    monkeypatch.setenv('STUDIO_REMINDER_CONTEXT_MESSAGES', '0')
    monkeypatch.setenv('STUDIO_REMINDER_THRESHOLD', '0.9')
    assert not await subject.should_suggest_studio('change prompt', [{'role': 'user', 'text': 'old'}])
    assert client.post.call_args.kwargs['json']['state']['recent_context'] == []
