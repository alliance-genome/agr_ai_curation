"""Structural invariants for persisted chat route preferences."""

from sqlalchemy import CheckConstraint

from src.models.sql.chat_route_preference import ChatRoutePreference


def test_chat_route_preference_has_one_user_owned_mutually_exclusive_target():
    table = ChatRoutePreference.__table__

    assert table.primary_key.columns.keys() == ["user_id"]
    assert set(table.c.keys()) == {
        "user_id",
        "mode",
        "agent_id",
        "flow_id",
        "target_public_id",
        "target_display_name",
        "created_at",
        "updated_at",
    }
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_chat_route_preference_mode_target"
    )
    sql = str(constraint.sqltext)
    for mode in ("automatic", "agent", "flow"):
        assert f"mode = '{mode}'" in sql
    assert "agent_id IS NOT NULL AND flow_id IS NULL" in sql
    assert "agent_id IS NULL AND flow_id IS NOT NULL" in sql


def test_chat_route_preference_foreign_keys_use_canonical_targets():
    table = ChatRoutePreference.__table__

    assert next(iter(table.c.user_id.foreign_keys)).target_fullname == "users.user_id"
    assert next(iter(table.c.agent_id.foreign_keys)).target_fullname == "agents.id"
    assert next(iter(table.c.flow_id.foreign_keys)).target_fullname == "curation_flows.id"
