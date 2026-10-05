"""The export gate's order and reasons, with the resolver and flow response stubbed."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api import flows as flows_api
from src.lib.flow_transfer import export
from src.lib.flow_transfer.export import ExportCurator, evaluate_flow

from .support import CURATOR_ISS, ISSUER, task_node

CURATOR = ExportCurator(subject="sub-1", issuer=CURATOR_ISS, user_id=1, groups=[])


def _flow(definition=None):
    return SimpleNamespace(id=uuid4(), user_id=1, name="Flow", description=None,
                           flow_definition=definition or {"version": "1.1", "nodes": [task_node()],
                                                          "edges": [], "entry_node_id": "task"})


def _evaluate(flow):
    return evaluate_flow(None, flow, CURATOR, issuer=ISSUER, app_version="0.10.1",
                         exported_at="2026-10-06T10:00:00Z")


@pytest.mark.parametrize("code,reason", [
    ("unavailable_model", "model_unavailable"),
    ("extraction_identity_lookup_tools", "lookup_tools"),
    ("missing_execution_revision", "step_not_saved"),
    ("unavailable_execution_revision", "step_unavailable"),
    ("invalid_selected_export", "fields_need_choosing"),
])
def test_the_first_blocking_finding_gives_the_reason(monkeypatch, code, reason):
    finding = SimpleNamespace(code=code, severity="error", node_id="node_0")
    monkeypatch.setattr(export, "resolve_flow_execution_revisions", lambda *a, **k: SimpleNamespace(
        findings=(SimpleNamespace(code="x", severity="warning"), finding),
        definition=None, entries_by_node={}))
    evaluated = _evaluate(_flow())
    assert evaluated.reason == reason and evaluated.bundle is None


def test_remaining_critical_issues_cannot_run(monkeypatch):
    monkeypatch.setattr(export, "resolve_flow_execution_revisions", lambda *a, **k: SimpleNamespace(
        findings=(), definition=SimpleNamespace(nodes=[]), entries_by_node={}))
    monkeypatch.setattr(flows_api, "_flow_to_response",
                        lambda *a, **k: SimpleNamespace(has_critical_issues=True))
    assert _evaluate(_flow()).reason == "cannot_run"


def test_an_unreadable_definition_cannot_run_and_does_not_raise():
    evaluated = _evaluate(_flow({"nodes": []}))
    assert evaluated.reason == "cannot_run" and evaluated.version.startswith("sha256:")
