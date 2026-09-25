"""Exact totals and environment controls for migration inventory."""

from decimal import Decimal, localcontext

import pytest

from src.lib.cost_ledger.decimal_math import add_exact
from src.lib.openai_agents.config import get_cost_migration_audit_page_size


def test_audit_does_not_round_away_small_provider_charges():
    with localcontext() as context:
        context.prec = 3
        assert add_exact(Decimal("1000000000000000000000"), Decimal("0.000000000000000000123")) == Decimal("1000000000000000000000.000000000000000000123")
        assert add_exact(Decimal("999.99"), Decimal("0.01")) == Decimal("1000.00")
        assert add_exact(Decimal("0"), Decimal("0")) == Decimal("0")


@pytest.mark.parametrize("setting, expected", [(None, 200), ("3", 3), ("0", 1), ("invalid", 200)])
def test_audit_page_size_is_environment_configurable(monkeypatch, setting, expected):
    if setting is None:
        monkeypatch.delenv("COST_MIGRATION_AUDIT_PAGE_SIZE", raising=False)
    else:
        monkeypatch.setenv("COST_MIGRATION_AUDIT_PAGE_SIZE", setting)
    assert get_cost_migration_audit_page_size() == expected
