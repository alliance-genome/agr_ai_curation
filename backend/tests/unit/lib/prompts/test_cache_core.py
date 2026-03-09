"""Additional unit tests for prompt cache core behavior."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.lib.prompts import cache as prompt_cache
from src.models.sql.prompts import PromptTemplate


def _prompt(
    agent_name: str,
    prompt_type: str,
    version: int,
    *,
    group_id=None,
    is_active=False,
    content="prompt",
):
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name=agent_name,
        prompt_type=prompt_type,
        group_id=group_id,
        content=content,
        version=version,
        is_active=is_active,
        created_at=datetime.now(timezone.utc),
        created_by="tester@example.org",
    )


@pytest.fixture(autouse=True)
def _reset_cache_state(monkeypatch):
    monkeypatch.setattr(prompt_cache, "_active_cache", {})
    monkeypatch.setattr(prompt_cache, "_version_cache", {})
    monkeypatch.setattr(prompt_cache, "_initialized", False)
    monkeypatch.setattr(prompt_cache, "_loaded_at", None)


def test_initialize_populates_active_and_version_caches():
    prompts = [
        _prompt("gene", "system", 1, is_active=False, content="v1"),
        _prompt("gene", "system", 2, is_active=True, content="v2"),
        _prompt("gene", "group_rules", 1, group_id="WB", is_active=True, content="wb"),
    ]
    db = MagicMock()
    db.query.return_value.all.return_value = prompts

    prompt_cache.initialize(db)

    assert prompt_cache.is_initialized() is True
    assert prompt_cache.get_prompt("gene").content == "v2"
    assert prompt_cache.get_prompt("gene", prompt_type="group_rules", group_id="WB").content == "wb"
    assert prompt_cache.get_prompt_by_version("gene", version=1).content == "v1"

    info = prompt_cache.get_cache_info()
    assert info["initialized"] is True
    assert info["active_prompts"] == 2
    assert info["total_versions"] == 3
    assert info["loaded_at"] is not None

    active = prompt_cache.get_all_active_prompts()
    assert sorted(active.keys()) == ["gene:group_rules:WB", "gene:system:base"]


def test_get_prompt_reads_group_rules_by_group_id(monkeypatch):
    prompt = _prompt("gene", "group_rules", 1, group_id="WB", is_active=True, content="wb")
    monkeypatch.setattr(prompt_cache, "_initialized", True)
    monkeypatch.setattr(prompt_cache, "_active_cache", {"gene:group_rules:WB": prompt})

    assert prompt_cache.get_prompt("gene", prompt_type="group_rules", group_id="WB").content == "wb"


def test_refresh_reinitializes_cache(monkeypatch):
    calls = {}

    def _fake_initialize(db):
        calls["db"] = db

    monkeypatch.setattr(prompt_cache, "initialize", _fake_initialize)

    db = object()
    prompt_cache.refresh(db)
    assert calls["db"] is db


def test_cache_accessors_raise_when_not_initialized():
    with pytest.raises(RuntimeError):
        prompt_cache.get_prompt("gene")
    with pytest.raises(RuntimeError):
        prompt_cache.get_prompt_optional("gene")
    with pytest.raises(RuntimeError):
        prompt_cache.get_prompt_by_version("gene", 1)
    with pytest.raises(RuntimeError):
        prompt_cache.get_all_active_prompts()


def test_get_prompt_missing_key_raises_not_found(monkeypatch):
    monkeypatch.setattr(prompt_cache, "_initialized", True)
    monkeypatch.setattr(prompt_cache, "_active_cache", {})

    with pytest.raises(prompt_cache.PromptNotFoundError):
        prompt_cache.get_prompt("missing")


def test_get_prompt_by_version_missing_raises_not_found(monkeypatch):
    monkeypatch.setattr(prompt_cache, "_initialized", True)
    monkeypatch.setattr(prompt_cache, "_version_cache", {})

    with pytest.raises(prompt_cache.PromptNotFoundError):
        prompt_cache.get_prompt_by_version("gene", version=99)


def test_get_prompt_optional_returns_none_for_missing_prompt(monkeypatch):
    monkeypatch.setattr(prompt_cache, "_initialized", True)
    monkeypatch.setattr(prompt_cache, "_active_cache", {})

    assert prompt_cache.get_prompt_optional("missing") is None
