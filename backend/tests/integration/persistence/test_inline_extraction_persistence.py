"""Real-DB integration coverage for inline validated extraction persistence.

These tests exercise ``persist_inline_validated_extraction_result`` against the
isolated ``docker-compose.test.yml`` Postgres, so they prove behavior at the
actual database boundary that the unit tests (which use a fake session) cannot:

- the persistence seam creates a durable row independent of any chat-turn /
  ``RUN_FINISHED`` handling (design Part 6 integration scenario 1);
- idempotency: a second call with identical key material returns the existing
  row, ``created_new=False``, with no second row (design Part 2 / Follow-up 3b);
- the partial unique index ``uq_extraction_results_idempotency_key`` rejects a
  direct duplicate INSERT at the DB boundary (Follow-up 3b -- highest value);
- a non-fatal ``validator_error`` finding survives into the persisted payload
  (Follow-up 3, backend-layer assertion).

Run by path so this directory's isolated conftest does not apply parent autouse
mocks; these tests use real DB sessions only.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.lib.curation_workspace import extraction_results as extraction_results_module
from src.lib.curation_workspace.extraction_results import (
    persist_inline_validated_extraction_result,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import CurationExtractionSourceKind
from tests.pdf_document_test_support import ensure_test_pdf_owner


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    """Ensure this branch's idempotency-key migration is applied before tests."""

    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def document_id(db_session):
    """Create a real ``pdf_documents`` row to satisfy the extraction-results FK."""

    doc_id = uuid4()
    hex_value = doc_id.hex
    owner_id = ensure_test_pdf_owner(
        db_session,
        auth_sub="test_pdf_owner_inline_extraction",
    )
    db_session.add(
        PDFDocument(
            id=doc_id,
            user_id=owner_id,
            filename=f"test_inline_persistence_{hex_value}.pdf",
            title="Inline persistence fixture",
            file_path=f"{doc_id}/inline.pdf",
            file_hash=f"{hex_value}{hex_value}",
            file_size=2048,
            page_count=3,
            upload_timestamp=datetime.now(timezone.utc),
            last_accessed=datetime.now(timezone.utc),
            status="processed",
        )
    )
    db_session.commit()
    try:
        yield str(doc_id)
    finally:
        db_session.rollback()
        db_session.execute(
            delete(ExtractionResultModel).where(
                ExtractionResultModel.document_id == doc_id
            )
        )
        db_session.execute(delete(PDFDocument).where(PDFDocument.id == doc_id))
        db_session.commit()


def _canonical_gene_envelope(*, object_count: int = 1) -> dict:
    """Build a strict canonical domain envelope the inline path accepts.

    ``domain_pack_id="gene"`` resolves to a real pack whose
    ``gene_mention_evidence`` object carries a ``supervisor_manifest`` policy, so
    the same row is later renderable by the manifest/inspect_results path.
    """

    extracted_objects = []
    for index in range(1, object_count + 1):
        extracted_objects.append(
            {
                "object_type": "gene_mention_evidence",
                "object_role": "curatable_unit",
                "pending_ref_id": f"gene-mention-{index}",
                "payload": {
                    "mention": f"gene-{index}",
                    "gene_symbol": f"sym-{index}",
                    "primary_external_id": f"FB:FBgn{index:07d}",
                    "taxon": "NCBITaxon:7227",
                },
                "evidence_record_ids": [f"evidence-{index}"],
            }
        )
    return {
        "envelope_id": f"envelope-{uuid4()}",
        "domain_pack_id": "gene",
        "domain_pack_version": "0.1.0",
        "status": "extracted",
        "extracted_objects": extracted_objects,
        "validation_findings": [],
        "history": [],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": f"evidence-{index}",
                    "entity": f"gene-{index}",
                    "verified_quote": f"gene-{index} was experimentally analyzed.",
                    "page": index,
                    "section": "Results",
                    "chunk_id": f"chunk-{index}",
                }
                for index in range(1, object_count + 1)
            ]
        },
    }


_BUILDER_FINALIZATION = {
    "builder_run_id": "trace-inline-1",
    "builder_invocation_id": "builder-invocation-inline-1",
    "candidate_ids": ["candidate-1"],
    "source_candidate_ids": ["source-candidate-1"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("route_mode", ["agent", "flow", None])
async def test_direct_chat_real_builder_persists_before_completion(monkeypatch, document_id, db_session, route_mode):
    """Actual package stage/finalize -> runner -> committed SQL -> chat ref, no provider."""
    import json
    from types import SimpleNamespace
    from agents import Agent
    from agents.tool_context import ToolContext
    from src.api import chat_common
    from src.lib.agent_studio import catalog_service
    from src.lib.openai_agents import runner, extraction_builder_workspace as builder
    from src.lib.openai_agents.tools import evidence_workspace
    from src.lib.domain_packs import validator_dispatch

    for name in ("write_stream_event", "write_extraction_trace_event", "set_live_event_list"):
        monkeypatch.setattr(runner, name, lambda *a, **k: None)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **k: None)
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "_build_agents_run_config", lambda **k: SimpleNamespace())
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    for name in ("commit_pending_prompts", "_log_used_prompts_to_db", "provider_context_preflight",
                 "start_extraction_trace_run", "clear_extraction_trace_run"):
        monkeypatch.setattr(runner, name, lambda *a, **k: None)

    async def with_resources(**kwargs):
        async for event in runner._run_agent_with_owned_resources(
            owned_openai_resources=SimpleNamespace(client=None, provider=None), **kwargs
        ):
            yield event

    monkeypatch.setattr(runner, "_run_agent_with_tracing", with_resources)
    tool_context = catalog_service.ToolExecutionContext(database_url="unused")
    agent = Agent(name="Synthetic direct gene", model="gpt-5.6-sol", tools=[
        catalog_service._resolve_package_tool(name, tool_context)
        for name in ("stage_gene_mention_evidence", "finalize_gene_extraction")
    ])
    agent.agent_key = "gene_extractor"
    agent.curation_metadata = {"launchable": True, "adapter_key": "gene"}
    captured = {}

    class Result:
        final_output = "Finalized one synthetic gene; not a canonical envelope."

        def __init__(self, active):
            self.agent = active

        async def stream_events(self):
            evidence_workspace._workspace_records().append({
                "evidence_record_id": "evidence-1", "entity": "synthetic gene",
                "verified_quote": "The synthetic gene was measured.",
                "chunk_id": "synthetic-chunk", "page": 1, "section": "Results",
            })
            arguments = [
                {"pending_ref_id": "pending:gene:1", "mention": "synthetic gene",
                 "evidence_record_ids": ["evidence-1"], "confidence": "high",
                 "identity_resolution_notes": ["Synthetic unresolved identity"]},
                {"candidate_ids": ["gene-candidate-1"]},
            ]
            for tool, args in zip(self.agent.tools, arguments):
                output = await tool.on_invoke_tool(ToolContext(
                    context=None, tool_name=tool.name, tool_call_id="synthetic-call",
                    tool_arguments=json.dumps(args)), json.dumps(args))
                assert "An error occurred" not in str(output)
            workspace = builder.get_active_extraction_builder_workspace()
            captured["finalization"] = workspace.finalization
            captured["trace_id"] = workspace.run_id
            if False:
                yield None

    def validate(envelope, domain_pack, **kwargs):
        # Keep real package normalization, replacing only paid validator execution.
        assert envelope.domain_pack_id == "gene"
        captured["validated"] = envelope
        return SimpleNamespace(envelope=envelope, matched_bindings=(), validator_results=(), appended_findings=())

    monkeypatch.setattr(runner.Runner, "run_streamed", lambda active, **k: Result(active))
    monkeypatch.setattr(validator_dispatch, "dispatch_active_validator_bindings", validate)
    session_id, turn_id = (str(uuid4()) for _ in range(2))
    events = []
    async for event in runner.run_agent_streamed(
        agent=agent, context_messages=[{"role": "user", "content": "Extract the synthetic paper"}],
        user_id="test_pdf_owner_inline_extraction", document_id=document_id,
        document_name="Synthetic paper", session_id=session_id, turn_id=turn_id,
        chat_route_mode=route_mode, chat_route_target_id="gene_extractor",
        inline_chat_persistence=True,
        doc_context=SimpleNamespace(hierarchy={}, abstract="", section_count=lambda: 0),
    ):
        if event["type"] in {"INTERNAL_EXTRACTION_RESULT", "STRUCTURED_RESULT", "RUN_FINISHED"}:
            # Independent session sees the commit before success/transcript handling.
            with SessionLocal() as reader:
                records = reader.scalars(select(ExtractionResultModel).where(
                    ExtractionResultModel.document_id == document_id)).all()
                if route_mode != "agent":
                    assert not records
                    events.append(event)
                    continue
                assert len(records) == 1
                record = records[0]
                assert record.payload_json == captured["validated"].model_dump(mode="json")
                assert record.origin_session_id == session_id
                assert record.trace_id == captured["trace_id"]
                assert record.user_id == "test_pdf_owner_inline_extraction"
                assert record.agent_key == "gene_extractor"
                assert record.source_kind == CurationExtractionSourceKind.CHAT
                metadata = record.extraction_metadata
                assert metadata["chat_turn_id"] == turn_id
                assert metadata["execution_context"]["document"]["document_id"] == document_id
                assert metadata["execution_context"]["executed_query"] == "Extract the synthetic paper"
        events.append(event)
    assert events[-1]["type"] == "RUN_FINISHED"
    internal = [event for event in events if event["type"] == "INTERNAL_EXTRACTION_RESULT"]
    if route_mode != "agent":
        assert not internal
        return
    assert len(internal) == 1
    persisted_ref = chat_common._build_persisted_extraction_result_ref_from_tool_event(
        internal[0], tool_agent_map={})
    assert persisted_ref is not None
    assert chat_common._build_extraction_candidate_from_tool_event(
        internal[0], tool_agent_map={}, conversation_summary=None) is None
    # The actual persistence primitive's stable identity is reusable, not another row.
    again = persist_inline_validated_extraction_result(
        payload_json=captured["validated"].model_dump(mode="json"), document_id=document_id, agent_key="gene_extractor",
        adapter_key="gene", tool_name="gene_extractor", source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=session_id, trace_id=captured["trace_id"], user_id="test_pdf_owner_inline_extraction",
        builder_finalization=captured["finalization"],
    )
    assert not again.created_new
    assert str(persisted_ref.extraction_result_id) == again.extraction_result_id
    # This is the session/user/document-filtered repository consumed by preparation.
    visible = extraction_results_module.list_extraction_results(
        document_id=document_id, origin_session_id=session_id, user_id="test_pdf_owner_inline_extraction")
    assert len(visible) == 1
    assert not extraction_results_module.list_extraction_results(
        document_id=document_id, origin_session_id=session_id, user_id="synthetic-foreign-owner")
    from src.lib.curation_workspace.curation_prep_invocation import build_chat_curation_prep_preview

    with SessionLocal() as reader:
        preview = build_chat_curation_prep_preview(
            session_id=session_id, user_id="test_pdf_owner_inline_extraction", db=reader)
    assert preview.ready
    assert preview.extraction_result_count == 1
    assert preview.preparable_candidate_count == 1


def _persist(
    db_session,
    document_id,
    *,
    payload,
    origin_session_id,
    trace_id="trace-inline-1",
    builder_finalization=None,
):
    return persist_inline_validated_extraction_result(
        payload_json=payload,
        document_id=document_id,
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=origin_session_id,
        trace_id=trace_id,
        user_id="user-inline-1",
        builder_finalization=builder_finalization or dict(_BUILDER_FINALIZATION),
        db=db_session,
    )


def test_inline_persistence_creates_durable_row_at_db_boundary(db_session, document_id):
    """Scenario 1: the persistence seam writes a real, queryable row.

    The row exists from ``persist_inline_validated_extraction_result`` alone --
    no RUN_FINISHED / chat-turn handling is involved -- proving inline
    persistence is independent of outer-turn completion.
    """

    session_id = f"inline-session-{uuid4()}"
    payload = _canonical_gene_envelope(object_count=2)

    result = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()
    db_session.expire_all()

    assert result.created_new is True
    assert result.result_ref == f"extraction-result:{result.extraction_result_id}"

    rows = db_session.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == result.extraction_result_id
    assert row.source_kind is CurationExtractionSourceKind.CHAT
    assert row.candidate_count == 2
    assert row.idempotency_key == result.idempotency_key
    assert row.payload_hash == result.payload_hash
    assert row.extraction_metadata["persistence_phase"] == "inline_validated_extraction"


def test_inline_persistence_is_idempotent_on_duplicate_call(db_session, document_id):
    """Design Part 2: a duplicate call returns the existing row, not a new one."""

    session_id = f"inline-idem-session-{uuid4()}"
    payload = _canonical_gene_envelope()

    first = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()

    # Re-persist with identical key material (same payload/builder/session/trace).
    second = _persist(
        db_session,
        document_id,
        payload=copy.deepcopy(payload),
        origin_session_id=session_id,
    )
    db_session.commit()
    db_session.expire_all()

    assert first.created_new is True
    assert second.created_new is False
    assert second.extraction_result_id == first.extraction_result_id
    assert second.idempotency_key == first.idempotency_key

    rows = db_session.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    assert len(rows) == 1


def test_partial_unique_index_rejects_direct_duplicate_insert(db_session, document_id):
    """Follow-up 3b (highest value): the DB-level partial unique index holds.

    After one inline-persisted row, a direct second INSERT carrying the same
    ``idempotency_key`` must be rejected by ``uq_extraction_results_idempotency_key``
    -- proving duplicate prevention lives at the database boundary, not just in
    the in-process pre-check.
    """

    session_id = f"inline-uq-session-{uuid4()}"
    payload = _canonical_gene_envelope()

    result = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()

    # Build a fresh row that reuses the SAME idempotency_key. Everything else can
    # differ; only the unique key matters for the constraint.
    from uuid import UUID

    duplicate = ExtractionResultModel(
        document_id=UUID(document_id),
        adapter_key="gene",
        agent_key="gene",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=f"{session_id}-other",
        trace_id="trace-inline-other",
        user_id="user-inline-other",
        candidate_count=1,
        payload_json={"envelope_id": "x", "domain_pack_id": "gene", "extracted_objects": []},
        idempotency_key=result.idempotency_key,
        payload_hash="some-other-hash",
        extraction_metadata={},
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.expire_all()
    rows = db_session.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.idempotency_key == result.idempotency_key
        )
    ).all()
    assert len(rows) == 1
    assert str(rows[0].id) == result.extraction_result_id


def test_idempotency_conflict_savepoint_preserves_unrelated_outer_work(
    db_session,
    document_id,
    monkeypatch,
):
    payload = _canonical_gene_envelope()
    session_id = f"inline-savepoint-session-{uuid4()}"
    winner = _persist(
        db_session,
        document_id,
        payload=payload,
        origin_session_id=session_id,
    )
    db_session.commit()

    document = db_session.get(PDFDocument, document_id)
    document.title = "Unrelated outer transaction work"

    original_lookup = extraction_results_module._load_extraction_result_by_idempotency_key
    lookup_calls = 0

    def _simulate_race(session, idempotency_key):
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return original_lookup(session, idempotency_key)

    monkeypatch.setattr(
        extraction_results_module,
        "_load_extraction_result_by_idempotency_key",
        _simulate_race,
    )

    race_loser = _persist(
        db_session,
        document_id,
        payload=payload,
        origin_session_id=session_id,
    )
    db_session.commit()
    db_session.expire_all()

    assert race_loser.created_new is False
    assert race_loser.extraction_result_id == winner.extraction_result_id
    assert db_session.get(PDFDocument, document_id).title == (
        "Unrelated outer transaction work"
    )


def test_inline_persistence_retains_non_fatal_validator_finding(db_session, document_id):
    """Follow-up 3: a non-fatal validator_error finding survives into the row.

    This asserts the backend persistence layer (not just the frontend severity
    helper) keeps a non-fatal ``validator_error`` finding in the durable payload.
    """

    session_id = f"inline-finding-session-{uuid4()}"
    payload = _canonical_gene_envelope()
    payload["validation_findings"] = [
        {
            "severity": "warning",
            "status": "open",
            "code": "domain_pack.validator_error",
            "message": "Gene validator could not be run for the target.",
            "object_ref": {
                "pending_ref_id": "gene-mention-1",
                "object_type": "gene_mention_evidence",
            },
            "details": {"fatal": False},
        }
    ]

    result = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()
    db_session.expire_all()

    assert result.created_new is True
    row = db_session.scalar(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    )
    assert row is not None
    findings = row.payload_json["validation_findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "domain_pack.validator_error"
    assert finding["severity"] == "warning"
    assert finding["details"]["fatal"] is False


def test_inline_persistence_rejects_legacy_row_source(db_session, document_id):
    """Design forward-only rule: the strict inline path refuses legacy envelopes."""

    legacy_payload = {
        "adapter_key": "gene",
        "items": [{"label": "notch"}],
        "raw_mentions": [],
        "exclusions": [],
        "ambiguities": [],
        "run_summary": {"candidate_count": 1, "kept_count": 1},
    }
    with pytest.raises(ValueError, match="strict canonical domain envelope"):
        _persist(
            db_session,
            document_id,
            payload=legacy_payload,
            origin_session_id=f"inline-legacy-session-{uuid4()}",
        )
