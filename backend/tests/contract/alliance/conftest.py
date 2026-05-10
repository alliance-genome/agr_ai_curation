"""Alliance-specific contract test configuration."""

from __future__ import annotations


def pytest_configure(config):
    """Register Alliance-only markers outside the shared pytest config."""
    config.addinivalue_line(
        "markers",
        "alliance_domain_pack: Alliance domain-pack contract tests",
    )
    config.addinivalue_line(
        "markers",
        "alliance_linkml: Alliance LinkML grounding contract tests that require "
        "the pinned schema cache",
    )
    config.addinivalue_line(
        "markers",
        "alliance_live_db: Alliance live curation DB projection tests requiring "
        "explicit opt-in",
    )
