from types import SimpleNamespace

import src.lib.agent_studio.tool_search_authorization as authorization


def _policy(name: str, *, visible=True, executable=True, groups=None):
    return SimpleNamespace(
        tool_key=name,
        curator_visible=visible,
        allow_execute=executable,
        config={"allowed_group_ids": list(groups or [])},
    )


def test_compiler_filters_policy_before_declaration_and_is_deterministic(monkeypatch):
    monkeypatch.setattr("src.lib.config.groups_loader.get_valid_group_ids", lambda: ["TEAM_C"])
    policies = [
        _policy("blocked", executable=False),
        _policy("group_only", groups=["TEAM_C"]),
    ]
    policy_service = SimpleNamespace(
        list_all=lambda _db: [_policy("blocked", executable=True)],
        refresh=lambda _db: policies,
    )
    monkeypatch.setattr(
        authorization,
        "get_tool_policy_cache",
        lambda: policy_service,
    )
    definitions = [
        {"name": "open", "description": "", "input_schema": {"type": "object"}},
        {"name": "blocked", "description": "", "input_schema": {"type": "object"}},
        {"name": "group_only", "description": "", "input_schema": {"type": "object"}},
    ]
    result = authorization.compile_authorized_tool_universe(
        db=object(), definitions=definitions, user_id=7, active_group_ids=[]
    )
    assert [item["name"] for item in result.definitions] == ["open"]
    assert result.filtered_count == 2
    assert "blocked" not in result.authorized_names
    repeated = authorization.compile_authorized_tool_universe(
        db=object(), definitions=list(reversed(definitions)), user_id=7, active_group_ids=[]
    )
    assert repeated.fingerprint == result.fingerprint


class _Query:
    def __init__(self, policy):
        self.policy = policy

    def filter(self, *_args):
        return self

    def first(self):
        return self.policy


class _Db:
    def __init__(self, policy):
        self.policy = policy

    def query(self, *_args):
        return _Query(self.policy)


def test_invocation_rechecks_changed_policy():
    assert not authorization.is_tool_authorized_at_invocation(
        db=_Db(_policy("tool", executable=False)),
        tool_name="tool",
        declared_names=frozenset({"tool"}),
        active_group_ids=[],
    )
