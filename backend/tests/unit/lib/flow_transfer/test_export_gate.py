"""The export gate's order and reasons, with the resolver and flow response stubbed."""

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api import flows as flows_api
from src.lib.flow_transfer import export
from src.lib.flow_transfer.bundle import InvalidBundle
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


@pytest.fixture
def reports(monkeypatch):
    calls = []
    monkeypatch.setattr(export, "report_runtime_exception",
                        lambda exc, **kwargs: calls.append((exc, kwargs)) or True)
    return calls


def _fake_detail():
    """A stand-in for content an exception message could carry, built at runtime."""
    return "-".join(("fake", "flow", "content", uuid4().hex))


def _assert_one_sanitized_report(reports, caplog, check, leaked):
    [(error, kwargs)] = reports
    assert isinstance(error, RuntimeError) and error.__cause__ is None and error.__context__ is None
    assert str(error) == f"Flow export bundle is inconsistent ({check})"
    assert (kwargs["component"], kwargs["operation"]) == ("flow_export", "flow_export_inconsistent")
    assert kwargs["context"]["check"] == check
    assert kwargs["fingerprint"] == ["flow_export", "flow_export_inconsistent", check]
    assert leaked not in repr(error) and leaked not in str(kwargs)
    [record] = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert record.getMessage() == "flow_export_inconsistent" and record.check == check
    assert record.sentry_skip_event is True  # the facade's event is the only one
    assert leaked not in str(record.__dict__) and leaked not in caplog.text


def _events(caplog, level):
    return [record.getMessage() for record in caplog.records if record.levelno == level]


def _runnable(monkeypatch, nodes=(), entries=None):
    monkeypatch.setattr(export, "resolve_flow_execution_revisions", lambda *a, **k: SimpleNamespace(
        findings=(), definition=SimpleNamespace(nodes=list(nodes)), entries_by_node=entries or {}))
    monkeypatch.setattr(flows_api, "_flow_to_response",
                        lambda *a, **k: SimpleNamespace(has_critical_issues=False))


def test_an_unreadable_definition_is_a_curator_refusal_not_a_defect(caplog, reports):
    caplog.set_level(logging.INFO, logger=export.__name__)
    assert _evaluate(_flow({"nodes": []})).reason == "cannot_run"
    assert _events(caplog, logging.INFO) == ["flow_export_refused"]
    assert _events(caplog, logging.ERROR) == [] and reports == []


def test_an_integrity_failure_in_the_closure_is_reported_as_a_defect(monkeypatch, caplog, reports):
    receipt = {"agent_id": str(uuid4()), "agent_key": "ca_finder", "agent_revision_id": str(uuid4()),
               "revision": 1, "fingerprint": "sha256:" + "0" * 64,
               "output_contract": {"output_state": "none"}}
    _runnable(monkeypatch, nodes=[SimpleNamespace(id="node_0")],
              entries={"node_0": {"execution_receipt": receipt}})
    leaked = _fake_detail()

    def mismatch(*args, **kwargs):
        raise ValueError(f"Executable revision fingerprint mismatch {leaked}")

    monkeypatch.setattr(export, "get_execution_revision", mismatch)
    caplog.set_level(logging.INFO, logger=export.__name__)
    evaluated = _evaluate(_flow())
    # The curator still sees the fixed cross-repo reason; Sentry gets the defect.
    assert evaluated.reason == "cannot_run" and evaluated.bundle is None
    _assert_one_sanitized_report(reports, caplog, "ValueError", leaked)
    assert _events(caplog, logging.INFO) == []


def test_a_bundle_the_resolver_would_refuse_is_reported_as_a_defect(monkeypatch, caplog, reports):
    _runnable(monkeypatch)
    leaked = _fake_detail()

    def refuse(bundle_json):
        raise InvalidBundle("receipt")

    monkeypatch.setattr(export, "check_bundle", refuse)
    caplog.set_level(logging.INFO, logger=export.__name__)
    flow = _flow()
    flow.name = leaked  # flow names never reach logs or Sentry
    assert _evaluate(flow).reason == "cannot_run"
    _assert_one_sanitized_report(reports, caplog, "receipt", leaked)


def test_reporting_uses_the_real_facade_without_promoting_the_log(monkeypatch, caplog):
    """The unpatched facade receives the chain-free wrapper (Sentry SDK stubbed)."""
    captured = []
    fake_sdk = SimpleNamespace(
        new_scope=lambda: _Scope(), capture_exception=lambda exc: captured.append(exc) or "event")
    monkeypatch.setattr("src.lib.observability.runtime.importlib.import_module",
                        lambda name: fake_sdk)
    _runnable(monkeypatch)
    leaked = _fake_detail()

    def refuse(bundle_json):
        raise ValueError(leaked)

    monkeypatch.setattr(export, "check_bundle", refuse)
    caplog.set_level(logging.INFO, logger=export.__name__)
    assert _evaluate(_flow()).reason == "cannot_run"
    [error] = captured
    assert str(error) == "Flow export bundle is inconsistent (ValueError)"
    assert error.__cause__ is None and error.__context__ is None and leaked not in caplog.text


class _Scope:
    fingerprint = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_level(self, level):
        pass

    def set_tag(self, key, value):
        pass

    def set_context(self, key, value):
        pass
