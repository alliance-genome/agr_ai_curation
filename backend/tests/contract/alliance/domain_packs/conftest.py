"""Marker wiring for Alliance domain-pack contract suites."""

from __future__ import annotations

from pathlib import Path

import pytest


LINKML_TEST_NAME_FRAGMENTS = (
    "linkml",
    "schema_ref",
    "schema_refs",
    "pinned_schema",
    "cache_script",
)


def pytest_collection_modifyitems(config, items):
    """Tag this subtree so gate scripts can select contract slices cleanly."""
    for item in items:
        item.add_marker(pytest.mark.contract)
        item.add_marker(pytest.mark.alliance_domain_pack)

        path = Path(str(item.fspath))
        if path.name == "test_live_db_lookup_contract.py":
            item.add_marker(pytest.mark.alliance_live_db)
            item.add_marker(pytest.mark.database_integration)

        item_name = item.name.lower()
        if any(fragment in item_name for fragment in LINKML_TEST_NAME_FRAGMENTS):
            item.add_marker(pytest.mark.alliance_linkml)
