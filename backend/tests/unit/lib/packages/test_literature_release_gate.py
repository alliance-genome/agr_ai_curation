"""The release lookup gate must fail closed, never silently skip missing config."""
import importlib.util
from pathlib import Path

import pytest

from . import find_repo_root


spec = importlib.util.spec_from_file_location(
    "literature_release_gate",
    find_repo_root(Path(__file__)) / "scripts/testing/literature_reference_smoke.py",
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize("missing", ["ELASTICSEARCH_HOST", "ELASTICSEARCH_SCHEME", "ELASTICSEARCH_PORT", "ELASTICSEARCH_INDEX"])
def test_each_missing_setting_fails_before_lookup(monkeypatch, missing):
    for key in ("ELASTICSEARCH_HOST", "ELASTICSEARCH_SCHEME", "ELASTICSEARCH_PORT", "ELASTICSEARCH_INDEX"):
        monkeypatch.setenv(key, "configured")
    monkeypatch.setenv(missing, " ")
    monkeypatch.setattr("sys.argv", ["smoke", "--identifier", "TEST:reference"])
    monkeypatch.setattr(gate, "lookup", lambda _: pytest.fail("must not call tool with incomplete config"))
    assert gate.main() == 1


@pytest.mark.parametrize("passed", [True, False])
def test_lookup_verdict_controls_exit_status(monkeypatch, passed):
    for key in ("ELASTICSEARCH_HOST", "ELASTICSEARCH_SCHEME", "ELASTICSEARCH_PORT", "ELASTICSEARCH_INDEX"):
        monkeypatch.setenv(key, "configured")
    monkeypatch.setattr("sys.argv", ["smoke", "--identifier", "TEST:reference"])
    async def lookup(identifier):
        assert identifier == "TEST:reference"
        return {"passed": passed}
    monkeypatch.setattr(gate, "lookup", lookup)
    assert gate.main() == (0 if passed else 1)
