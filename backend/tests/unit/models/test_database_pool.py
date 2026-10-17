"""Shared application pool configuration and dependency ownership."""
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from src.lib.openai_agents.config import get_app_db_max_overflow, get_app_db_pool_size
from src.models.sql import database


@pytest.mark.parametrize("getter,key,default,override", [
    (get_app_db_pool_size, "APP_DB_POOL_SIZE", 5, "3"),
    (get_app_db_max_overflow, "APP_DB_MAX_OVERFLOW", 10, "0"),
])
def test_capacity_settings(monkeypatch, getter, key, default, override):
    monkeypatch.delenv(key, raising=False)
    assert getter() == default
    monkeypatch.setenv(key, override)
    assert getter() == int(override)


@pytest.mark.parametrize("getter,key,invalid", [
    (get_app_db_pool_size, "APP_DB_POOL_SIZE", value)
    for value in ("0", "-1", "", "abc", "1.5")
] + [
    (get_app_db_max_overflow, "APP_DB_MAX_OVERFLOW", value)
    for value in ("-1", "", "abc", "1.5")
])
def test_invalid_capacity_fails(monkeypatch, getter, key, invalid):
    monkeypatch.setenv(key, invalid)
    with pytest.raises(ValueError):
        getter()


@pytest.mark.parametrize("provider", [database.get_db, database.get_feedback_db])
@pytest.mark.parametrize("failed", [False, True])
def test_dependency_always_closes_its_session(monkeypatch, provider, failed):
    session = Mock()
    factory = Mock(return_value=session)
    monkeypatch.setattr(database, "SessionLocal", factory)
    dependency = provider()
    assert next(dependency) is session
    if failed:
        with pytest.raises(RuntimeError):
            dependency.throw(RuntimeError("request failed"))
    else:
        dependency.close()
    factory.assert_called_once_with()
    session.close.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("size,overflow", [(None, None), ("2", "0"), ("3", "4")])
def test_fresh_import_constructs_one_lazy_pool(size, overflow):
    env = os.environ.copy()
    for key, value in (("APP_DB_POOL_SIZE", size), ("APP_DB_MAX_OVERFLOW", overflow)):
        env.pop(key, None)
        if value is not None:
            env[key] = value
    env.pop("OPENAI_API_KEY", None)
    script = """
from unittest.mock import patch
import sys
import sqlalchemy
with patch('sqlalchemy.create_engine', wraps=sqlalchemy.create_engine) as create:
    from src.models.sql import database as db
assert create.call_count == 1
assert create.call_args.kwargs['pool_size'] == SIZE
assert create.call_args.kwargs['max_overflow'] == OVERFLOW
assert create.call_args.kwargs['pool_pre_ping'] is True
assert db.engine.pool.checkedout() == 0
assert db.engine.pool.checkedin() == 0
from src.models import sql
from src.lib.feedback.models import FeedbackReport
assert sql.Base is db.Base and FeedbackReport.metadata is db.Base.metadata
assert sql.engine is db.engine and sql.SessionLocal is db.SessionLocal
assert db.SessionLocal.kw['autocommit'] is False
assert db.SessionLocal.kw['autoflush'] is False
assert db.SessionLocal.kw['expire_on_commit'] is False
a, b = db.get_db(), db.get_feedback_db()
first, second = next(a), next(b)
assert first is not second
assert first.bind is second.bind is db.engine
assert db.engine.pool.checkedout() == 0
a.close()
b.close()
assert 'src.lib.openai_agents.runner' not in sys.modules
""".replace("SIZE", str(int(size) if size else 5)).replace("OVERFLOW", str(int(overflow) if overflow else 10))
    subprocess.run([sys.executable, "-c", script], env=env, check=True, timeout=30)
