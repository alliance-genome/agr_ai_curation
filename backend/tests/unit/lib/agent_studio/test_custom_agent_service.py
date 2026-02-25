"""Tests for custom-agent service helpers."""

import uuid
from types import SimpleNamespace

import pytest

from src.lib.agent_studio.custom_agent_service import (
    CUSTOM_AGENT_PREFIX,
    clone_visible_agent_for_user,
    create_custom_agent,
    get_custom_agent_mod_prompt,
    set_custom_agent_visibility,
    make_custom_agent_id,
    normalize_mod_prompt_overrides,
    parse_custom_agent_id,
)


def test_make_and_parse_custom_agent_id_round_trip():
    custom_uuid = uuid.uuid4()
    agent_id = make_custom_agent_id(custom_uuid)
    assert agent_id.startswith(CUSTOM_AGENT_PREFIX)
    assert parse_custom_agent_id(agent_id) == custom_uuid


def test_parse_custom_agent_id_rejects_invalid_values():
    assert parse_custom_agent_id("gene") is None
    assert parse_custom_agent_id("ca_not-a-uuid") is None
    assert parse_custom_agent_id("") is None


def test_normalize_mod_prompt_overrides_cleans_keys_and_empty_values():
    normalized = normalize_mod_prompt_overrides({
        " wb ": "WormBase custom rules",
        "FB": "",
        "": "ignored",
        "mgi": "Mouse rules",
    })

    assert normalized == {
        "WB": "WormBase custom rules",
        "MGI": "Mouse rules",
    }


def test_get_custom_agent_mod_prompt_prefers_override():
    override_content = get_custom_agent_mod_prompt(
        parent_agent_key="gene",
        mod_id="WB",
        mod_prompt_overrides={"WB": "custom wb rules"},
    )
    assert override_content == "custom wb rules"


def test_get_custom_agent_mod_prompt_falls_back_to_cached_rules(monkeypatch):
    fake_cache_module = SimpleNamespace(
        get_prompt_optional=lambda agent_name, prompt_type, mod_id: (
            type("Prompt", (), {"content": "cached wb rules"})()
            if agent_name == "gene" and prompt_type == "group_rules" and mod_id == "WB"
            else None
        )
    )

    monkeypatch.setitem(__import__("sys").modules, "src.lib.prompts.cache", fake_cache_module)

    content = get_custom_agent_mod_prompt(
        parent_agent_key="gene",
        mod_id="WB",
        mod_prompt_overrides={},
    )
    assert content == "cached wb rules"


def test_create_custom_agent_creates_unified_custom_agent(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id: SimpleNamespace(
            agent_key="gene",
            instructions="base system prompt",
            model_id="gpt-4o",
            model_temperature=0.1,
            model_reasoning="medium",
            tool_ids=["agr_curation_query"],
            output_schema_key=None,
            category="Validation",
        ),
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: SimpleNamespace(model_id=_model_id))

    custom = create_custom_agent(
        db=FakeDB(),
        user_id=7,
        template_source="gene",
        name="My Agent",
    )

    assert custom.parent_agent_key == "gene"
    assert custom.user_id == 7
    assert custom.agent_key.startswith("ca_")


def test_create_custom_agent_requires_model_for_scratch_mode():
    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    with pytest.raises(ValueError, match="model_id is required when template_source is not provided"):
        create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Scratch Agent",
            template_source=None,
            model_id=None,
        )


def test_create_custom_agent_rejects_non_attachable_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="agr_curation_query", allow_attach=True),
                SimpleNamespace(tool_key="admin_only_tool", allow_attach=False),
            ],
        ),
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: SimpleNamespace(model_id=_model_id))

    with pytest.raises(ValueError, match="not attachable"):
        create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Tool Guardrail Agent",
            template_source=None,
            model_id="gpt-4o",
            tool_ids=["admin_only_tool"],
        )


def test_update_custom_agent_rejects_unknown_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        mod_prompt_overrides={},
        include_mod_rules=True,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[],
        output_schema_key=None,
    )

    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="agr_curation_query", allow_attach=True),
            ],
        ),
    )

    with pytest.raises(ValueError, match="Unknown tool_ids"):
        service.update_custom_agent(
            db=FakeDB(),
            custom_agent=custom_agent,
            tool_ids=["missing_tool"],
        )


def test_update_custom_agent_rejects_clearing_existing_tool_ids_without_override():
    import src.lib.agent_studio.custom_agent_service as service

    class FakeDB:
        pass

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        mod_prompt_overrides={},
        include_mod_rules=True,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=["agr_curation_query"],
        output_schema_key=None,
    )

    with pytest.raises(ValueError, match="Refusing to clear all tool_ids"):
        service.update_custom_agent(
            db=FakeDB(),
            custom_agent=custom_agent,
            tool_ids=[],
        )


def test_create_custom_agent_rejects_unknown_model_id(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(service, "get_model", lambda _model_id: None)

    with pytest.raises(ValueError, match="Unknown model_id"):
        create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Bad Model Agent",
            template_source=None,
            model_id="unknown-model",
        )


def test_update_custom_agent_rejects_unknown_model_id(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        mod_prompt_overrides={},
        include_mod_rules=True,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[],
        output_schema_key=None,
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: None)

    with pytest.raises(ValueError, match="Unknown model_id"):
        service.update_custom_agent(
            db=FakeDB(),
            custom_agent=custom_agent,
            model_id="not-real",
        )


def test_create_custom_agent_rejects_non_curator_visible_model(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "get_model",
        lambda _model_id: SimpleNamespace(model_id=_model_id, curator_visible=False),
    )

    with pytest.raises(ValueError, match="Model is not selectable in Agent Workshop"):
        create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Hidden Model Agent",
            template_source=None,
            model_id="gpt-4o",
        )


def test_set_custom_agent_visibility_sets_project_membership(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    project_id = uuid.uuid4()
    monkeypatch.setattr(service, "_get_primary_project_id_for_user", lambda _db, _uid: project_id)

    custom_agent = SimpleNamespace(
        user_id=7,
        visibility="private",
        project_id=None,
        shared_at=None,
    )

    updated = set_custom_agent_visibility(
        db=SimpleNamespace(),
        custom_agent=custom_agent,
        user_id=7,
        visibility="project",
    )

    assert updated.visibility == "project"
    assert updated.project_id == project_id
    assert updated.shared_at is not None


def test_clone_visible_agent_for_user_clones_from_visible_source(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    source = SimpleNamespace(
        agent_key="ca_source",
        visibility="project",
        name="Shared Agent",
        template_source="gene",
        instructions="prompt",
        mod_prompt_overrides={"WB": "rules"},
        description="desc",
        icon="🔧",
        group_rules_enabled=True,
        model_id="gpt-4o",
        tool_ids=["agr_curation_query"],
        output_schema_key=None,
        category="Validation",
        model_temperature=0.1,
        model_reasoning="medium",
    )
    observed = {}

    monkeypatch.setattr(service, "get_agent_by_key", lambda _db, _key, user_id=None: source)
    monkeypatch.setattr(service, "_generate_clone_name", lambda _db, _uid, _name: "Shared Agent (Copy)")
    monkeypatch.setattr(service, "_has_active_custom_name", lambda _db, _uid, _name: False)

    def _fake_create_custom_agent(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(service, "create_custom_agent", _fake_create_custom_agent)

    clone_visible_agent_for_user(
        db=SimpleNamespace(),
        user_id=7,
        source_agent_key="ca_source",
        name=None,
    )

    assert observed["user_id"] == 7
    assert observed["name"] == "Shared Agent (Copy)"
    assert observed["template_source"] == "gene"
    assert observed["custom_prompt"] == "prompt"
