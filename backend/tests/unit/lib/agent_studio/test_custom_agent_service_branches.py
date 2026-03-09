"""Additional branch tests for custom agent service."""

import uuid
from types import SimpleNamespace

import pytest

from src.lib.agent_studio import custom_agent_service as service


class _FakeQuery:
    def __init__(self, first_value=None, all_value=None, scalar_value=None):
        self._first_value = first_value
        self._all_value = all_value if all_value is not None else []
        self._scalar_value = scalar_value
        self.filter_expressions = []
        self.order_by_expressions = []

    def filter(self, *args, **_kwargs):
        self.filter_expressions.extend(args)
        return self

    def order_by(self, *args, **_kwargs):
        self.order_by_expressions.extend(args)
        return self

    def first(self):
        return self._first_value

    def all(self):
        return self._all_value

    def scalar(self):
        return self._scalar_value


class _FakeDB:
    def __init__(self, queries):
        self._queries = list(queries)
        self.added = []
        self.closed = False
        self.used_queries = []

    def query(self, *_args, **_kwargs):
        if not self._queries:
            query = _FakeQuery()
            self.used_queries.append(query)
            return query
        query = self._queries.pop(0)
        self.used_queries.append(query)
        return query

    def add(self, obj):
        self.added.append(obj)

    def close(self):
        self.closed = True


def test_validate_requested_tool_ids_paths(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
                SimpleNamespace(tool_key="admin_only", allow_attach=False),
            ]
        ),
    )

    assert service._validate_requested_tool_ids(SimpleNamespace(), None) is None
    assert service._validate_requested_tool_ids(SimpleNamespace(), ["   "]) == []

    with pytest.raises(ValueError, match="Unknown tool_ids"):
        service._validate_requested_tool_ids(SimpleNamespace(), ["missing"])

    with pytest.raises(ValueError, match="not attachable"):
        service._validate_requested_tool_ids(SimpleNamespace(), ["admin_only"])

    assert service._validate_requested_tool_ids(SimpleNamespace(), [" search_document "]) == ["search_document"]


def test_validate_model_id_paths(monkeypatch):
    with pytest.raises(ValueError, match="model_id is required"):
        service._validate_model_id("")

    monkeypatch.setattr(service, "get_model", lambda _model_id: None)
    with pytest.raises(ValueError, match="Unknown model_id"):
        service._validate_model_id("gpt-x")

    monkeypatch.setattr(
        service,
        "get_model",
        lambda _model_id: SimpleNamespace(model_id=_model_id, curator_visible=False),
    )
    with pytest.raises(ValueError, match="not selectable in Agent Workshop"):
        service._validate_model_id("gpt-hidden")

    monkeypatch.setattr(
        service,
        "get_model",
        lambda _model_id: SimpleNamespace(model_id=_model_id, curator_visible=True),
    )
    assert service._validate_model_id(" gpt-5-mini ") == "gpt-5-mini"


def test_resolve_system_template_agent_paths():
    db = _FakeDB([_FakeQuery(first_value=SimpleNamespace(agent_key="gene"))])
    assert service._resolve_system_template_agent(db, "gene").agent_key == "gene"

    with pytest.raises(ValueError, match="template_source is required"):
        service._resolve_system_template_agent(_FakeDB([_FakeQuery(first_value=None)]), "")

    with pytest.raises(ValueError, match="No active system agent found"):
        service._resolve_system_template_agent(_FakeDB([_FakeQuery(first_value=None)]), "missing")


def test_has_active_custom_name_and_primary_project_lookup():
    assert (
        service._has_active_custom_name(
            _FakeDB([_FakeQuery(first_value=SimpleNamespace(id=1))]),
            user_id=7,
            name="My Agent",
        )
        is True
    )
    assert (
        service._has_active_custom_name(
            _FakeDB([_FakeQuery(first_value=None)]),
            user_id=7,
            name="My Agent",
        )
        is False
    )

    project_id = uuid.uuid4()
    assert (
        service._get_primary_project_id_for_user(
            _FakeDB([_FakeQuery(first_value=(project_id,))]),
            user_id=7,
        )
        == project_id
    )

    with pytest.raises(ValueError, match="User is not assigned to any project"):
        service._get_primary_project_id_for_user(_FakeDB([_FakeQuery(first_value=None)]), user_id=7)


def test_generate_clone_name_handles_collisions(monkeypatch):
    monkeypatch.setattr(service, "_has_active_custom_name", lambda _db, _uid, name: name in {"Gene (Copy)", "Gene (Copy 2)"})
    clone_name = service._generate_clone_name(SimpleNamespace(), user_id=7, source_name="Gene")
    assert clone_name == "Gene (Copy 3)"

    monkeypatch.setattr(service, "_has_active_custom_name", lambda _db, _uid, _name: False)
    assert service._generate_clone_name(SimpleNamespace(), user_id=7, source_name="") == "Custom Agent (Copy)"


def test_get_custom_agent_for_user_access_paths():
    custom_uuid = uuid.uuid4()
    owner_agent = SimpleNamespace(id=custom_uuid, user_id=7)

    db_found = _FakeDB([_FakeQuery(first_value=owner_agent)])
    assert service.get_custom_agent_for_user(db_found, custom_uuid, user_id=7) is owner_agent
    filter_sql = " ".join(str(expr) for expr in db_found.used_queries[0].filter_expressions)
    assert "id" in filter_sql
    assert "is_active" in filter_sql
    assert "visibility" in filter_sql
    assert "agent_key" in filter_sql

    with pytest.raises(service.CustomAgentNotFoundError):
        service.get_custom_agent_for_user(_FakeDB([_FakeQuery(first_value=None)]), custom_uuid, user_id=7)

    with pytest.raises(service.CustomAgentAccessError):
        service.get_custom_agent_for_user(
            _FakeDB([_FakeQuery(first_value=SimpleNamespace(id=custom_uuid, user_id=99))]),
            custom_uuid,
            user_id=7,
        )


def test_get_custom_agent_visible_to_user_private_and_project(monkeypatch):
    custom_uuid = uuid.uuid4()

    private_owner = SimpleNamespace(id=custom_uuid, user_id=7, visibility="private")
    assert (
        service.get_custom_agent_visible_to_user(
            _FakeDB([_FakeQuery(first_value=private_owner)]),
            custom_uuid,
            user_id=7,
        )
        is private_owner
    )

    with pytest.raises(service.CustomAgentAccessError):
        service.get_custom_agent_visible_to_user(
            _FakeDB([_FakeQuery(first_value=SimpleNamespace(id=custom_uuid, user_id=99, visibility="private"))]),
            custom_uuid,
            user_id=7,
        )

    project_agent = SimpleNamespace(
        id=custom_uuid,
        user_id=99,
        visibility="project",
        project_id=uuid.uuid4(),
    )
    db_project = _FakeDB([_FakeQuery(first_value=project_agent)])
    monkeypatch.setattr(service, "get_project_ids_for_user", lambda _db, _uid: {project_agent.project_id})
    assert (
        service.get_custom_agent_visible_to_user(
            db_project,
            custom_uuid,
            user_id=7,
        )
        is project_agent
    )
    filter_sql = " ".join(str(expr) for expr in db_project.used_queries[0].filter_expressions)
    assert "is_active" in filter_sql
    assert "visibility" in filter_sql
    assert "agent_key" in filter_sql

    monkeypatch.setattr(service, "get_project_ids_for_user", lambda _db, _uid: set())
    with pytest.raises(service.CustomAgentAccessError):
        service.get_custom_agent_visible_to_user(
            _FakeDB([_FakeQuery(first_value=project_agent)]),
            custom_uuid,
            user_id=7,
        )


def test_list_custom_agents_helpers(monkeypatch):
    mine = [SimpleNamespace(id=1)]
    visible = [SimpleNamespace(id=2), SimpleNamespace(id=3)]
    mine_db = _FakeDB([_FakeQuery(all_value=mine)])
    assert (
        service.list_custom_agents_for_user(mine_db, user_id=7)
        == mine
    )
    mine_filter_sql = " ".join(str(expr) for expr in mine_db.used_queries[0].filter_expressions)
    assert "user_id" in mine_filter_sql
    assert "is_active" in mine_filter_sql
    assert "visibility" in mine_filter_sql

    monkeypatch.setattr(service, "get_project_ids_for_user", lambda _db, _uid: set())
    visible_db = _FakeDB([_FakeQuery(all_value=visible)])
    assert (
        service.list_custom_agents_visible_to_user(visible_db, user_id=7)
        == visible
    )
    visible_filter_sql = " ".join(str(expr) for expr in visible_db.used_queries[0].filter_expressions)
    assert "is_active" in visible_filter_sql
    assert "visibility" in visible_filter_sql


def test_set_custom_agent_visibility_validation_and_private_path(monkeypatch):
    agent = SimpleNamespace(user_id=7, visibility="project", project_id=uuid.uuid4(), shared_at=object())

    with pytest.raises(service.CustomAgentAccessError):
        service.set_custom_agent_visibility(SimpleNamespace(), agent, user_id=99, visibility="private")

    with pytest.raises(ValueError, match="visibility must be"):
        service.set_custom_agent_visibility(SimpleNamespace(), agent, user_id=7, visibility="invalid")

    updated = service.set_custom_agent_visibility(SimpleNamespace(), agent, user_id=7, visibility="private")
    assert updated.visibility == "private"
    assert updated.project_id is None
    assert updated.shared_at is None

    project_id = uuid.uuid4()
    monkeypatch.setattr(service, "_get_primary_project_id_for_user", lambda _db, _uid: project_id)
    updated_project = service.set_custom_agent_visibility(SimpleNamespace(), agent, user_id=7, visibility="project")
    assert updated_project.visibility == "project"
    assert updated_project.project_id == project_id
    assert updated_project.shared_at is not None


def test_soft_delete_and_versions_listing():
    agent = SimpleNamespace(is_active=True)
    service.soft_delete_custom_agent(agent)
    assert agent.is_active is False

    versions = [SimpleNamespace(version=2), SimpleNamespace(version=1)]
    listed = service.list_custom_agent_versions(_FakeDB([_FakeQuery(all_value=versions)]), uuid.uuid4())
    assert listed == versions


def test_revert_custom_agent_to_version_paths(monkeypatch):
    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        custom_prompt="current prompt",
        group_prompt_overrides={" wb ": "keep"},
        version=4,
    )

    with pytest.raises(service.CustomAgentNotFoundError, match="Version 9 not found"):
        service.revert_custom_agent_to_version(
            _FakeDB([_FakeQuery(first_value=None)]),
            custom_agent=custom_agent,
            version=9,
        )

    target = SimpleNamespace(custom_prompt="old prompt", group_prompt_overrides={"mgi": "m rules"})
    db = _FakeDB([_FakeQuery(first_value=target)])
    monkeypatch.setattr(service, "_get_next_version", lambda _db, _id: 10)
    updated = service.revert_custom_agent_to_version(db, custom_agent=custom_agent, version=3, notes=None)
    assert updated.custom_prompt == "old prompt"
    assert updated.group_prompt_overrides == {"MGI": "m rules"}
    assert updated.version == 5
    assert len(db.added) == 1
    snapshot = db.added[0]
    assert snapshot.version == 10
    assert snapshot.custom_prompt == "current prompt"
    assert snapshot.mod_prompt_overrides == {"WB": "keep"}
    assert snapshot.notes == "Snapshot before revert to v3"


def test_custom_agent_runtime_info_and_to_dict(monkeypatch):
    custom_uuid = uuid.uuid4()
    runtime_agent = SimpleNamespace(
        id=custom_uuid,
        is_active=True,
        visibility="private",
        agent_key=service.make_custom_agent_id(custom_uuid),
        name="Runtime Agent",
        custom_prompt="prompt",
        group_prompt_overrides={" wb ": "rules"},
        include_group_rules=True,
        tool_ids=["search_document"],
        template_source="gene",
        user_id=7,
        description="desc",
        icon="tool",
        model_id="gpt-5-mini",
        model_temperature=0.2,
        model_reasoning="medium",
        output_schema_key=None,
        project_id=None,
        created_at="c",
        updated_at="u",
    )

    db = _FakeDB([_FakeQuery(first_value=runtime_agent)])
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    runtime = service.get_custom_agent_runtime_info(service.make_custom_agent_id(custom_uuid))
    assert runtime is not None
    assert runtime.custom_agent_uuid == custom_uuid
    assert runtime.requires_document is True
    assert db.closed is True

    assert service.get_custom_agent_runtime_info("not-custom-id") is None
    assert service.get_custom_agent_runtime_info(service.make_custom_agent_id(uuid.uuid4()), db=_FakeDB([_FakeQuery(first_value=None)])) is None

    as_dict = service.custom_agent_to_dict(runtime_agent)
    assert as_dict["agent_id"].startswith("ca_")
    assert as_dict["group_prompt_overrides"] == {"WB": "rules"}
    assert as_dict["parent_exists"] is True


def test_get_custom_agent_group_prompt_additional_paths(monkeypatch):
    assert service.get_custom_agent_group_prompt("gene", "", {"WB": "rules"}) is None

    def _get_prompt_optional(_agent_name, prompt_type, group_id=None, **_kwargs):
        _ = group_id
        if prompt_type == "group_rules":
            return None
        return SimpleNamespace(content="fallback mod rules")

    fake_cache_module = SimpleNamespace(get_prompt_optional=_get_prompt_optional)
    monkeypatch.setitem(__import__("sys").modules, "src.lib.prompts.cache", fake_cache_module)

    assert (
        service.get_custom_agent_mod_prompt(
            parent_agent_key="gene",
            mod_id="WB",
            mod_prompt_overrides=None,
        )
        == "fallback mod rules"
    )

    fake_cache_none = SimpleNamespace(get_prompt_optional=lambda *_args, **_kwargs: None)
    monkeypatch.setitem(__import__("sys").modules, "src.lib.prompts.cache", fake_cache_none)
    assert service.get_custom_agent_group_prompt("gene", "WB", None) is None
