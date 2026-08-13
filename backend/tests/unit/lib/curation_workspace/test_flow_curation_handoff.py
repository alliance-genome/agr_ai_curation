from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

import src.lib.curation_workspace.bootstrap_service as bootstrap_service
from src.lib.curation_workspace.bootstrap_service import run_flow_curation_handoff
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


def _record(adapter_key: str, suffix: str) -> CurationExtractionResultRecord:
    return CurationExtractionResultRecord(
        extraction_result_id=f"extract-{suffix}",
        document_id="doc-1",
        adapter_key=adapter_key,
        agent_key=f"{adapter_key}_extractor",
        source_kind=CurationExtractionSourceKind.FLOW,
        origin_session_id="sess-1",
        trace_id="trace-1",
        flow_run_id="run-1",
        user_id="runner-sub",
        candidate_count=1,
        conversation_summary="summary",
        payload_json={"curatable_objects": []},
        created_at=datetime.now(timezone.utc),
        metadata={},
    )


async def test_run_flow_curation_handoff_creates_one_session_per_distinct_adapter(
    monkeypatch,
):
    db = Mock()
    extraction_results = [
        _record("gene", "gene-1"),
        _record("gene_expression", "gene-expression-1"),
        _record("gene", "gene-2"),
    ]
    prep_calls = []
    events = []
    sentry: dict[str, Any] = {"span_data": {}, "statuses": []}
    db.execute.return_value.all.return_value = []

    class FakeSpan:
        def set_data(self, key, value):
            sentry["span_data"][key] = value

        def set_status(self, status):
            sentry["statuses"].append(status)

    @contextmanager
    def _fake_sentry_span(**kwargs):
        sentry["span_kwargs"] = kwargs
        yield FakeSpan()

    async def _fake_run_curation_prep(
        adapter_records,
        *,
        scope_confirmation,
        persistence_context=None,
        db=None,
    ):
        events.append(f"prep:{scope_confirmation.adapter_keys[0]}")
        prep_calls.append(
            {
                "records": list(adapter_records),
                "scope": scope_confirmation,
                "context": persistence_context,
            }
        )
        assert scope_confirmation.confirmed is True
        assert len(scope_confirmation.adapter_keys) == 1
        assert persistence_context is not None
        assert persistence_context.source_kind == CurationExtractionSourceKind.FLOW
        assert persistence_context.document_id == "doc-1"
        assert persistence_context.user_id == "runner-sub"
        assert persistence_context.flow_run_id == "run-1"
        assert persistence_context.origin_session_id == "sess-1"
        assert persistence_context.conversation_summary == "summary"
        assert persistence_context.workflow == "curation_handoff"
        return SimpleNamespace(envelope_refs=[])

    bootstrap_calls = []

    async def _fake_bootstrap_document_session(
        document_id,
        request,
        *,
        current_user_id,
        db,
        manage_transaction,
    ):
        events.append(f"bootstrap:{request.adapter_key}")
        bootstrap_calls.append((document_id, request, current_user_id, manage_transaction))
        return SimpleNamespace(
            session=SimpleNamespace(session_id=f"session-{request.adapter_key}"),
            created=True,
        )

    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _fake_run_curation_prep)
    monkeypatch.setattr(bootstrap_service, "gen_ai_invoke_agent_span", _fake_sentry_span)
    monkeypatch.setattr(
        bootstrap_service,
        "bootstrap_document_session",
        _fake_bootstrap_document_session,
    )

    result = await run_flow_curation_handoff(
        extraction_results=extraction_results,
        document_id="doc-1",
        runner_user_id="runner-sub",
        flow_run_id="run-1",
        origin_session_id="sess-1",
        conversation_summary="summary",
        db=db,
    )

    assert [call["scope"].adapter_keys for call in prep_calls] == [
        ["gene"],
        ["gene_expression"],
    ]
    assert [[record.adapter_key for record in call["records"]] for call in prep_calls] == [
        ["gene", "gene"],
        ["gene_expression"],
    ]
    assert [call[1].adapter_key for call in bootstrap_calls] == ["gene", "gene_expression"]
    assert all(call[0] == "doc-1" for call in bootstrap_calls)
    assert all(call[1].curator_id == "runner-sub" for call in bootstrap_calls)
    assert all(call[1].flow_run_id == "run-1" for call in bootstrap_calls)
    assert all(call[2] == "runner-sub" for call in bootstrap_calls)
    assert all(call[3] is False for call in bootstrap_calls)
    assert events == [
        "prep:gene",
        "prep:gene_expression",
        "bootstrap:gene",
        "bootstrap:gene_expression",
    ]
    assert sentry["span_kwargs"]["agent_key"] == "curation_handoff"
    assert sentry["span_kwargs"]["provider_name"] == "ai_curation"
    assert sentry["span_kwargs"]["response_streaming"] is False
    assert sentry["span_kwargs"]["workflow"] == "curation_handoff"
    assert sentry["span_kwargs"]["conversation_id"] == "sess-1"
    assert sentry["statuses"] == ["ok"]
    assert sentry["span_data"]["ai_curation.curation_handoff.status"] == "success"
    assert sentry["span_data"]["ai_curation.curation_handoff.review_session_count"] == 2
    assert sentry["span_data"]["ai_curation.agent.output"]["adapter_keys"] == [
        "gene",
        "gene_expression",
    ]
    assert result.review_session_ids == ["session-gene", "session-gene_expression"]
    assert result.adapter_keys == ["gene", "gene_expression"]
    db.commit.assert_called_once()


async def test_run_flow_curation_handoff_rolls_back_on_failure(monkeypatch):
    db = Mock()
    db.in_transaction.return_value = True
    db.execute.return_value.all.return_value = []

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("prep failed")

    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _boom)

    with pytest.raises(RuntimeError, match="prep failed"):
        await run_flow_curation_handoff(
            extraction_results=[_record("gene", "gene-1")],
            document_id="doc-1",
            runner_user_id="runner-sub",
            flow_run_id="run-1",
            origin_session_id="sess-1",
            conversation_summary="summary",
            db=db,
        )

    db.rollback.assert_called_once()


async def test_run_flow_curation_handoff_reuses_exact_canonical_source_sessions(monkeypatch):
    db = Mock()
    session_id = uuid4()
    record = _record("gene", "gene-1")
    db.execute.return_value.all.return_value = [
        (session_id, "gene", record.extraction_result_id)
    ]

    async def _unexpected(*_args, **_kwargs):
        pytest.fail("an exact committed handoff must be reused without new prep state")

    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _unexpected)
    monkeypatch.setattr(bootstrap_service, "bootstrap_document_session", _unexpected)

    result = await run_flow_curation_handoff(
        extraction_results=[record],
        document_id="doc-1",
        runner_user_id="runner-sub",
        flow_run_id="run-1",
        origin_session_id="sess-1",
        conversation_summary="summary",
        db=db,
    )

    assert result.review_session_ids == [str(session_id)]
    assert result.adapter_keys == ["gene"]
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


async def test_run_flow_curation_handoff_rolls_back_on_review_session_failure(monkeypatch):
    db = Mock()
    db.in_transaction.return_value = True
    db.execute.return_value.all.return_value = []

    async def _prep(*_args, **_kwargs):
        return SimpleNamespace(envelope_refs=[])

    async def _review_failure(*_args, **_kwargs):
        raise RuntimeError("review session failed")

    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _prep)
    monkeypatch.setattr(bootstrap_service, "bootstrap_document_session", _review_failure)

    with pytest.raises(RuntimeError, match="review session failed"):
        await run_flow_curation_handoff(
            extraction_results=[_record("gene", "gene-1")],
            document_id="doc-1",
            runner_user_id="runner-sub",
            flow_run_id="run-1",
            origin_session_id="sess-1",
            conversation_summary="summary",
            db=db,
        )

    db.commit.assert_not_called()
    db.rollback.assert_called_once()


async def test_run_flow_curation_handoff_rolls_back_on_final_commit_failure(monkeypatch):
    db = Mock()
    db.in_transaction.return_value = True
    db.execute.return_value.all.return_value = []
    db.commit.side_effect = RuntimeError("final commit failed")

    async def _prep(*_args, **_kwargs):
        return SimpleNamespace(envelope_refs=[])

    async def _bootstrap(*_args, **_kwargs):
        return SimpleNamespace(
            session=SimpleNamespace(session_id="session-gene"), created=True
        )

    monkeypatch.setattr(bootstrap_service, "run_curation_prep", _prep)
    monkeypatch.setattr(bootstrap_service, "bootstrap_document_session", _bootstrap)

    with pytest.raises(RuntimeError, match="final commit failed"):
        await run_flow_curation_handoff(
            extraction_results=[_record("gene", "gene-1")],
            document_id="doc-1",
            runner_user_id="runner-sub",
            flow_run_id="run-1",
            origin_session_id="sess-1",
            conversation_summary="summary",
            db=db,
        )

    db.commit.assert_called_once()
    db.rollback.assert_called_once()


async def test_run_flow_curation_handoff_records_sentry_error_metadata(monkeypatch):
    db = Mock()
    db.in_transaction.return_value = True
    sentry: dict[str, Any] = {"span_data": {}, "statuses": []}
    record = _record("gene", "gene-1").model_copy(update={"adapter_key": None})

    class FakeSpan:
        def set_data(self, key, value):
            sentry["span_data"][key] = value

        def set_status(self, status):
            sentry["statuses"].append(status)

    @contextmanager
    def _fake_sentry_span(**kwargs):
        sentry["span_kwargs"] = kwargs
        yield FakeSpan()

    monkeypatch.setattr(bootstrap_service, "gen_ai_invoke_agent_span", _fake_sentry_span)

    with pytest.raises(ValueError, match="at least one adapter key"):
        await run_flow_curation_handoff(
            extraction_results=[record],
            document_id="doc-1",
            runner_user_id="runner-sub",
            flow_run_id="run-1",
            origin_session_id="sess-1",
            conversation_summary="summary",
            db=db,
        )

    assert sentry["span_kwargs"]["workflow"] == "curation_handoff"
    assert sentry["statuses"] == ["invalid_argument"]
    assert sentry["span_data"]["ai_curation.curation_handoff.status"] == "error"
    assert sentry["span_data"]["ai_curation.error.detail"] == {
        "phase": "curation_handoff",
        "error_type": "ValueError",
        "message": (
            "Curation handoff requires extraction results for at least one adapter key."
        ),
    }
    db.rollback.assert_called_once()


async def test_run_flow_curation_handoff_requires_adapter_scope():
    db = Mock()
    db.in_transaction.return_value = True
    record = _record("gene", "gene-1").model_copy(update={"adapter_key": None})

    with pytest.raises(ValueError, match="at least one adapter key"):
        await run_flow_curation_handoff(
            extraction_results=[record],
            document_id="doc-1",
            runner_user_id="runner-sub",
            flow_run_id="run-1",
            origin_session_id="sess-1",
            conversation_summary="summary",
            db=db,
        )

    db.rollback.assert_called_once()
