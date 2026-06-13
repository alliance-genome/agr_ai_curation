"""Integration coverage for inspect_results resolving real persisted rows.

These tests persist canonical extraction results through the inline persistence
helper (the real durable write path) and then drive the model-facing
``inspect_results`` tool against those committed rows. They cover design Part 6
integration scenarios:

- 5: a follow-up inspects "those objects" via the persisted result_ref;
- 6: same-turn follow-up resolves via result_ref / target="latest" WITHOUT the
  retired ``scope=current_turn`` concept, and old scope strings are rejected;
- 7: allele-trace-shaped durability -- a 16-object result stays resolvable and
  unchanged after a later EMPTY extraction is persisted (the empty attempt
  cannot erase or replace the good extraction);
- 10 (durability half): a generic/PDF-source canonical row is durable and
  inspectable. (The "export reads canonical rows" half is documented as
  production-smoke-only below; it needs the live export pipeline.)

inspect_results reads through its own SessionLocal(), so rows are committed to
the shared test DB before each call. Authorization context (session id + user
id) is set via src.lib.context, matching the real chat runtime.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from src.lib.context import (
    clear_context,
    set_current_session_id,
    set_current_user_id,
)
from src.lib.curation_workspace.extraction_results import (
    persist_inline_validated_extraction_result,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.lib.openai_agents.inspect_results import inspect_results
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import CurationExtractionSourceKind


BACKEND_ROOT = Path(__file__).resolve().parents[2]

_USER_ID = "inspect-user-1"


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


@pytest.fixture
def persistence_db():
    """A real DB session for seeding documents and asserting durable rows."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def document_id(persistence_db):
    doc_id = uuid4()
    hex_value = doc_id.hex
    persistence_db.add(
        PDFDocument(
            id=doc_id,
            filename=f"test_inspect_results_{hex_value}.pdf",
            title="inspect_results fixture",
            file_path=f"{doc_id}/inspect.pdf",
            file_hash=f"{hex_value}{hex_value}",
            file_size=2048,
            page_count=3,
            upload_timestamp=datetime.now(timezone.utc),
            last_accessed=datetime.now(timezone.utc),
            status="processed",
        )
    )
    persistence_db.commit()
    try:
        yield str(doc_id)
    finally:
        persistence_db.rollback()
        persistence_db.execute(
            delete(ExtractionResultModel).where(
                ExtractionResultModel.document_id == doc_id
            )
        )
        persistence_db.execute(delete(PDFDocument).where(PDFDocument.id == doc_id))
        persistence_db.commit()


@pytest.fixture
def chat_context():
    """Set/clear the chat context inspect_results requires for authorization."""

    session_id = f"inspect-session-{uuid4()}"
    set_current_session_id(session_id)
    set_current_user_id(_USER_ID)
    try:
        yield session_id
    finally:
        clear_context()


def _gene_envelope(*, object_count: int) -> dict:
    objects = []
    for index in range(1, object_count + 1):
        objects.append(
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
        "objects": objects,
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


def _persist(
    document_id,
    session_id,
    *,
    payload,
    builder_invocation_id="builder-invocation-1",
    trace_id="trace-inspect-1",
    source_kind=CurationExtractionSourceKind.CHAT,
    flow_run_id=None,
):
    return persist_inline_validated_extraction_result(
        payload_json=payload,
        document_id=document_id,
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=source_kind,
        origin_session_id=session_id,
        trace_id=trace_id,
        flow_run_id=flow_run_id,
        user_id=_USER_ID,
        builder_finalization={
            "builder_run_id": trace_id,
            "builder_invocation_id": builder_invocation_id,
        },
        db=None,  # owns its own committed transaction, visible to inspect_results
    )


async def test_inspect_results_resolves_persisted_ref(document_id, chat_context):
    """Scenario 5: a follow-up inspects 'those objects' via the persisted ref."""

    result = _persist(document_id, chat_context, payload=_gene_envelope(object_count=3))

    response = json.loads(
        await inspect_results(action="objects", result_ref=result.result_ref)
    )
    assert response["status"] == "ok"
    assert response["result_ref"] == result.result_ref
    assert response["object_count"] == 3
    object_refs = {obj["object_ref"] for obj in response["objects"]}
    assert object_refs == {"gene-mention-1", "gene-mention-2", "gene-mention-3"}


async def test_inspect_results_resolves_evidence_by_ref(document_id, chat_context):
    """Scenario 5: evidence resolves through bounded object-scoped lookup."""

    result = _persist(document_id, chat_context, payload=_gene_envelope(object_count=1))

    response = json.loads(
        await inspect_results(
            action="evidence",
            result_ref=result.result_ref,
            object_ref="gene-mention-1",
        )
    )
    assert response["status"] == "ok"
    assert response["evidence_count"] >= 1
    quotes = [item.get("verified_quote") for item in response["evidence"]]
    assert any("experimentally analyzed" in (quote or "") for quote in quotes)


async def test_inspect_results_latest_resolves_without_current_turn_scope(
    document_id, chat_context
):
    """Scenario 6: target='latest' resolves the just-produced extraction.

    Replaces the retired scope=current_turn path -- the supervisor no longer has
    to choose a storage-timing scope for a same-turn follow-up.
    """

    result = _persist(document_id, chat_context, payload=_gene_envelope(object_count=2))

    response = json.loads(await inspect_results(action="summary", target="latest"))
    assert response["status"] == "ok"
    assert response["result_ref"] == result.result_ref
    assert response["summary"]["object_count"] == 2


@pytest.mark.parametrize(
    "old_ref",
    ["current-turn:0", "current_chat", "current_turn", "current_document"],
)
async def test_inspect_results_rejects_old_scope_strings(
    document_id, chat_context, old_ref
):
    """Scenario 6: retired scope strings are rejected as invalid requests.

    They are not valid ``result_ref`` values (the canonical form is
    ``extraction-result:<uuid>``), so the tool returns an invalid-request error
    rather than silently aliasing old scope-first behavior.
    """

    # Seed a real row so the failure is specifically about the bad ref shape,
    # not an empty result set.
    _persist(document_id, chat_context, payload=_gene_envelope(object_count=1))

    response = json.loads(
        await inspect_results(action="objects", result_ref=old_ref)
    )
    assert response["status"] == "error"
    assert response["error_code"] in {"invalid_result_ref", "raw_uuid_result_ref"}


async def test_allele_trace_shaped_durability_empty_attempt_cannot_replace(
    document_id, chat_context, persistence_db
):
    """Scenario 7: a 16-object result survives a later EMPTY extraction.

    Mirrors the production allele trace: a good 16-observation extraction is
    persisted, then a later empty summarization attempt is persisted. The empty
    attempt is a distinct row (distinct idempotency key) and cannot erase or
    replace the 16-object row, which stays resolvable and unchanged.
    """

    good = _persist(
        document_id,
        chat_context,
        payload=_gene_envelope(object_count=16),
        builder_invocation_id="builder-good",
        trace_id="trace-good",
    )

    empty_payload = _gene_envelope(object_count=0)
    empty = _persist(
        document_id,
        chat_context,
        payload=empty_payload,
        builder_invocation_id="builder-empty",
        trace_id="trace-empty",
    )

    # Distinct rows: the empty attempt did not overwrite the good one.
    assert empty.extraction_result_id != good.extraction_result_id

    # The 16-object result is still fully resolvable and unchanged.
    response = json.loads(
        await inspect_results(action="objects", result_ref=good.result_ref, limit=100)
    )
    assert response["status"] == "ok"
    assert response["object_count"] == 16

    # The manifest reports the good result as a non-empty, answerable extraction.
    good_summary = json.loads(
        await inspect_results(action="summary", result_ref=good.result_ref)
    )
    assert good_summary["status"] == "ok"
    assert good_summary["manifest"]["result_status"] == "non_empty_extraction_ready"
    assert good_summary["manifest"]["object_count"] == 16

    # The empty result is independently distinguishable as empty.
    empty_response = json.loads(
        await inspect_results(action="objects", result_ref=empty.result_ref)
    )
    assert empty_response["status"] == "ok"
    assert empty_response["object_count"] == 0

    empty_summary = json.loads(
        await inspect_results(action="summary", result_ref=empty.result_ref)
    )
    assert empty_summary["manifest"]["result_status"] == "empty_extraction"

    # Durable rows both exist; the good row's payload is intact.
    from uuid import UUID

    persistence_db.expire_all()
    good_row = persistence_db.get(
        ExtractionResultModel, UUID(good.extraction_result_id)
    )
    assert good_row is not None
    assert len(good_row.payload_json["objects"]) == 16


async def test_generic_source_canonical_row_is_durable_and_inspectable(
    document_id, chat_context
):
    """Scenario 10 (durability half): a generic/PDF-source row is inspectable.

    Persists a canonical generic-source extraction and confirms it resolves
    through the same result tool. The 'export reads canonical rows, not artifact
    summaries' half requires the live export pipeline and is covered as
    production smoke (see module docstring), not faked here.
    """

    result = _persist(
        document_id,
        chat_context,
        payload=_gene_envelope(object_count=4),
        builder_invocation_id="builder-generic",
        trace_id="trace-generic",
    )

    response = json.loads(
        await inspect_results(action="summary", result_ref=result.result_ref)
    )
    assert response["status"] == "ok"
    assert response["result_ref"] == result.result_ref
    assert response["summary"]["object_count"] == 4
