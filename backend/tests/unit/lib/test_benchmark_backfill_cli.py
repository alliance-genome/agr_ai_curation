"""Offline migration must not write without explicit apply and audit receipt."""

from unittest.mock import MagicMock, Mock
import json

import pytest

from src.lib.cost_ledger import benchmark_backfill as runner
from src.lib.openai_agents.config import get_cost_migration_lock_timeout_ms


@pytest.mark.parametrize("apply", [False, True])
def test_cli_dry_run_default_and_explicit_apply(monkeypatch, capsys, apply):
    args = ["backfill", "--deployment-id", "fixture", "--source-namespace", "fixture"]
    if apply:
        args += ["--apply", "--expected-planned-facts-sha256", "a" * 64]
    monkeypatch.setattr("sys.argv", args)
    session = MagicMock()
    monkeypatch.setattr(runner, "SessionLocal", MagicMock(return_value=session))
    audit = Mock(return_value={"dry_run": True})
    backfill = Mock(return_value={"committed": False})
    monkeypatch.setattr(runner, "audit_benchmark_costs", audit)
    monkeypatch.setattr(runner, "backfill_benchmark_costs", backfill)
    runner.main()
    report = json.loads(capsys.readouterr().out)
    assert audit.call_count == int(not apply) and backfill.call_count == int(apply)
    assert session.__enter__.return_value.commit.call_count == int(apply)
    assert report == ({"committed": True} if apply else {"dry_run": True})


def test_apply_requires_reviewed_fingerprint_before_opening_database(monkeypatch):
    monkeypatch.setattr("sys.argv", ["backfill", "--deployment-id", "fixture", "--source-namespace", "fixture", "--apply"])
    session = Mock()
    monkeypatch.setattr(runner, "SessionLocal", session)
    with pytest.raises(SystemExit):
        runner.main()
    session.assert_not_called()


def test_cli_failure_does_not_print_sql_or_credentials(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["backfill", "--deployment-id", "fixture", "--source-namespace", "fixture"])
    monkeypatch.setattr(runner, "SessionLocal", Mock(side_effect=RuntimeError("private SQL credentials")))
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "private" not in captured.err and "credentials" not in captured.err
    assert json.loads(captured.err)["code"] == "RuntimeError"


def test_lock_timeout_is_configurable(monkeypatch):
    monkeypatch.delenv("COST_MIGRATION_LOCK_TIMEOUT_MS", raising=False)
    assert get_cost_migration_lock_timeout_ms() == 30000
    monkeypatch.setenv("COST_MIGRATION_LOCK_TIMEOUT_MS", "25")
    assert get_cost_migration_lock_timeout_ms() == 25
