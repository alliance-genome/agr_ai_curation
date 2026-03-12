"""Additional runtime tests for prompt loader branches."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.config import prompt_loader


class _QueryStub:
    def __init__(self, first_result=None):
        self.first_result = first_result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.first_result


@pytest.fixture(autouse=True)
def _reset_loader_state():
    prompt_loader.reset_cache()
    yield
    prompt_loader.reset_cache()


def test_get_default_agents_path_prefers_env(monkeypatch):
    monkeypatch.setenv("AGENTS_CONFIG_PATH", "/tmp/custom-agents")
    result = prompt_loader._get_default_agents_path()
    assert result == Path("/tmp/custom-agents")


def test_upsert_prompt_noop_when_content_unchanged():
    existing = SimpleNamespace(content="same content", version=3, is_active=True)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=existing)

    created, version = prompt_loader._upsert_prompt(
        db=db,
        agent_name="gene",
        prompt_type="system",
        content="same content",
    )

    assert created is False
    assert version == 3
    db.add.assert_not_called()


def test_upsert_prompt_creates_new_version_when_content_changes():
    existing = SimpleNamespace(content="old", version=4, is_active=True)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=existing)

    created, version = prompt_loader._upsert_prompt(
        db=db,
        agent_name="gene",
        prompt_type="system",
        content="new",
        group_id=None,
        source_file="config/agents/gene/prompt.yaml",
        description="new prompt",
    )

    assert created is True
    assert version == 5
    assert existing.is_active is False
    db.add.assert_called_once()


def test_upsert_prompt_creates_version_one_when_missing():
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)

    created, version = prompt_loader._upsert_prompt(
        db=db,
        agent_name="gene",
        prompt_type="group_rules",
        content="rules",
        group_id="WB",
    )

    assert created is True
    assert version == 1
    db.add.assert_called_once()


def test_load_group_rules_infers_group_and_skips_examples(tmp_path):
    from src.lib.config.agent_sources import resolve_agent_config_sources

    agent_folder = tmp_path / "gene"
    rules_dir = agent_folder / "group_rules"
    rules_dir.mkdir(parents=True)

    (rules_dir / "_template.yaml").write_text("group_id: XX\ncontent: ignore\n")
    (rules_dir / "example.yaml").write_text("group_id: XX\ncontent: ignore\n")
    (rules_dir / "wb.yaml").write_text("content: WB specific rules\n")
    (rules_dir / "fb.yaml").write_text("group_id: FB\ncontent: FB specific rules\n")

    db = MagicMock()
    calls = []

    def _capture_upsert(**kwargs):
        calls.append(kwargs)
        return (True, 1)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)
        source = resolve_agent_config_sources(tmp_path)[0]
        count = prompt_loader._load_group_rules(source, "gene", db)
    finally:
        monkeypatch.undo()

    assert count == 2
    assert {c["group_id"] for c in calls} == {"WB", "FB"}
    assert all(c["prompt_type"] == "group_rules" for c in calls)


def test_acquire_advisory_lock_when_immediately_available():
    db = MagicMock()
    db.execute.return_value.scalar.return_value = True

    lock_acquired, is_loader = prompt_loader._acquire_advisory_lock(db)

    assert (lock_acquired, is_loader) == (True, True)


def test_acquire_advisory_lock_waits_when_other_worker_has_lock():
    first = MagicMock()
    first.scalar.return_value = False
    second = MagicMock()
    db = MagicMock()
    db.execute.side_effect = [first, second]

    lock_acquired, is_loader = prompt_loader._acquire_advisory_lock(db)

    assert (lock_acquired, is_loader) == (True, False)
    assert db.execute.call_count == 2


def test_acquire_advisory_lock_falls_back_on_error():
    db = MagicMock()
    db.execute.side_effect = RuntimeError("no advisory locks")

    lock_acquired, is_loader = prompt_loader._acquire_advisory_lock(db)
    assert (lock_acquired, is_loader) == (True, True)


def test_release_advisory_lock_swallows_errors():
    db = MagicMock()
    db.execute.side_effect = RuntimeError("unlock failed")
    prompt_loader._release_advisory_lock(db)
    assert db.execute.call_count == 1


def test_load_prompts_skips_when_already_initialized(tmp_path):
    prompt_loader._initialized = True
    db = MagicMock()

    result = prompt_loader.load_prompts(agents_path=tmp_path, db=db, force_reload=False)
    assert result["skipped"] is True
    assert db.execute.call_count == 0


def test_load_prompts_skips_when_another_worker_loaded(tmp_path, monkeypatch):
    agents_path = tmp_path / "agents"
    agents_path.mkdir()
    db = MagicMock()
    calls = {"released": 0}

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, False))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: calls.__setitem__("released", calls["released"] + 1))

    result = prompt_loader.load_prompts(agents_path=agents_path, db=db)
    assert result["skipped"] is True
    assert calls["released"] == 1
    assert prompt_loader.is_initialized() is True


def test_load_prompts_loader_path_counts_and_commits(tmp_path, monkeypatch):
    agents_path = tmp_path / "agents"
    (agents_path / "gene").mkdir(parents=True)
    (agents_path / "gene" / "agent.yaml").write_text("agent_id: gene\n")
    (agents_path / "_ignored").mkdir(parents=True)
    (agents_path / "not_a_dir.txt").write_text("x")
    db = MagicMock()
    calls = {"released": 0}

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: calls.__setitem__("released", calls["released"] + 1))
    monkeypatch.setattr(prompt_loader, "_load_base_prompt", lambda source, _db: source.folder_name if source.folder_name == "gene" else None)
    monkeypatch.setattr(prompt_loader, "_load_group_rules", lambda _source, _agent_name, _db: 2)

    result = prompt_loader.load_prompts(agents_path=agents_path, db=db)

    assert result == {"base_prompts": 1, "group_rules": 2}
    assert db.commit.called
    assert calls["released"] == 1
    assert prompt_loader.is_initialized() is True


def test_load_prompts_force_reload_even_if_initialized(tmp_path, monkeypatch):
    agents_path = tmp_path / "agents"
    (agents_path / "gene").mkdir(parents=True)
    (agents_path / "gene" / "agent.yaml").write_text("agent_id: gene\n")
    prompt_loader._initialized = True
    db = MagicMock()

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)
    monkeypatch.setattr(prompt_loader, "_load_base_prompt", lambda _source, _db: "gene")
    monkeypatch.setattr(prompt_loader, "_load_group_rules", lambda *_args, **_kwargs: 0)

    result = prompt_loader.load_prompts(agents_path=agents_path, db=db, force_reload=True)
    assert result == {"base_prompts": 1, "group_rules": 0}
