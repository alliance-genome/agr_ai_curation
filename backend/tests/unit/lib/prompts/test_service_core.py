"""Additional unit tests for prompt service write paths."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.lib.prompts import service as prompt_service_module
from src.lib.prompts.service import PromptService
from src.models.sql.prompts import PromptTemplate


class _QueryStub:
    def __init__(self, *, scalar_result=None, first_result=None, all_result=None):
        self.scalar_result = scalar_result
        self.first_result = first_result
        self.all_result = all_result if all_result is not None else []
        self.updated_values = None
        self.order_by_called = False

    def filter(self, *_args, **_kwargs):
        return self

    def scalar(self):
        return self.scalar_result

    def first(self):
        return self.first_result

    def all(self):
        return self.all_result

    def order_by(self, *_args, **_kwargs):
        self.order_by_called = True
        return self

    def update(self, values):
        self.updated_values = values
        return 1


def _prompt(version: int, *, group_id=None, is_active=False, source_file=None):
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        group_id=group_id,
        content=f"prompt-v{version}",
        version=version,
        is_active=is_active,
        source_file=source_file,
        created_at=datetime.now(timezone.utc),
        created_by="tester@example.org",
    )


def test_extract_custom_agent_id_variants():
    valid_id = uuid.uuid4()
    valid_prompt = _prompt(1, source_file=f"custom_agent:{valid_id}")
    invalid_prompt = _prompt(1, source_file="custom_agent:not-a-uuid")
    plain_prompt = _prompt(1, source_file="prompt_library/gene.md")

    assert PromptService._extract_custom_agent_id(valid_prompt) == valid_id
    assert PromptService._extract_custom_agent_id(invalid_prompt) is None
    assert PromptService._extract_custom_agent_id(plain_prompt) is None


def test_log_all_used_prompts_adds_entries():
    db = MagicMock()
    service = PromptService(db)
    prompts = [_prompt(1), _prompt(2)]

    entries = service.log_all_used_prompts(prompts, trace_id="trace-1", session_id="session-1")

    assert len(entries) == 2
    assert db.add.call_count == 2
    assert entries[0].trace_id == "trace-1"
    assert entries[1].session_id == "session-1"


def test_create_version_increments_version_and_adds_prompt():
    db = MagicMock()
    db.query.return_value = _QueryStub(scalar_result=2)
    service = PromptService(db)

    created = service.create_version(
        agent_name="gene",
        content="new prompt",
        prompt_type="system",
        group_id=None,
        activate=False,
    )

    assert created.version == 3
    assert created.is_active is False
    db.add.assert_called_once_with(created)


def test_create_version_with_activate_deactivates_current(monkeypatch):
    db = MagicMock()
    db.query.return_value = _QueryStub(scalar_result=0)
    service = PromptService(db)
    calls = {}
    monkeypatch.setattr(
        service,
        "_deactivate_current",
        lambda agent_name, prompt_type, group_id: calls.setdefault(
            "args", (agent_name, prompt_type, group_id)
        ),
    )

    created = service.create_version(
        agent_name="gene",
        content="new prompt",
        prompt_type="group_rules",
        group_id="WB",
        activate=True,
    )

    assert created.version == 1
    assert created.is_active is True
    assert calls["args"] == ("gene", "group_rules", "WB")


def test_activate_version_sets_active_and_refreshes_cache(monkeypatch):
    target = _prompt(5, is_active=False)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=target)
    service = PromptService(db)

    monkeypatch.setattr(service, "_deactivate_current", lambda *_args, **_kwargs: None)
    refresh_calls = {}
    monkeypatch.setattr(prompt_service_module, "refresh_cache", lambda db_arg: refresh_calls.setdefault("db", db_arg))

    result = service.activate_version(agent_name="gene", version=5)

    assert result is target
    assert target.is_active is True
    db.commit.assert_called_once()
    assert refresh_calls["db"] is db


def test_activate_version_raises_when_missing(monkeypatch):
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)
    service = PromptService(db)
    monkeypatch.setattr(service, "_deactivate_current", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="Version 9 not found"):
        service.activate_version(agent_name="gene", version=9, group_id="WB")


def test_get_version_history_returns_ordered_results():
    prompt_new = _prompt(3)
    prompt_old = _prompt(2)
    query = _QueryStub(all_result=[prompt_new, prompt_old])
    db = MagicMock()
    db.query.return_value = query
    service = PromptService(db)

    result = service.get_version_history(agent_name="gene")

    assert query.order_by_called is True
    assert result == [prompt_new, prompt_old]


def test_deactivate_current_updates_active_rows():
    query = _QueryStub()
    db = MagicMock()
    db.query.return_value = query
    service = PromptService(db)

    service._deactivate_current(agent_name="gene", prompt_type="system", group_id=None)

    assert query.updated_values == {"is_active": False}

