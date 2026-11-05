"""Topic-tag proposals survive real persistence and reach one review row (ALL-1461)."""

from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest

from src.lib.curation_workspace.curation_prep_service import ensure_domain_envelope_materialization
from src.lib.curation_workspace.execution_provenance import capture_source_document
from src.lib.curation_workspace.extraction_results import persist_extraction_result
from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.domain_packs.materialization import materialize_persisted_envelope_review_rows
from src.lib.openai_agents.extraction_builder_workspace import ExtractionBuilderWorkspace
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionSourceKind,
)
from src.schemas.execution_provenance import ExtractionExecutionContext
from tests.pdf_document_test_support import ensure_test_pdf_owner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_PYTHON_SRC = BACKEND_ROOT.parent / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.tools import topic_entity_tag_builder_tools as tools  # noqa: E402

DOCUMENT_CURIE = "AGRKB:101000000000001"
EVIDENCE = [
    {
        "evidence_record_id": "evidence-1",
        "entity": "expression",
        "verified_quote": "unc-54 expression was observed in body wall muscle.",
        "page": 4,
        "section": "Results",
        "chunk_id": "chunk-1",
    }
]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def db_session():
    with SessionLocal() as db:
        yield db
        db.rollback()


def _proposal_payload(monkeypatch, **stage_args):
    workspace = ExtractionBuilderWorkspace(
        run_id=f"topic-tag-{uuid4()}", agent_id="gene_expression_topic_tag"
    )
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: EVIDENCE)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_event: None)
    staged = tools._stage_topic_entity_tag_impl(**stage_args)
    assert staged.status == "ok", staged.message
    finalized = tools._finalize_topic_entity_tag_extraction_impl([staged.data["candidate_id"]])
    assert finalized.status == "ok", finalized.message
    return workspace.finalization.payload


def _persisted_rows(db, monkeypatch, payload, *, reference_curie):
    subject = f"topic-tag-{uuid4()}"
    document = PDFDocument(
        id=uuid4(),
        user_id=ensure_test_pdf_owner(db, auth_sub=subject),
        filename="topic-tag.pdf",
        file_path=f"topic-tag/{uuid4()}.pdf",
        file_hash=uuid4().hex * 2,
        file_size=100,
        page_count=1,
        status="completed",
        source_provider="abc_literature" if reference_curie else None,
        source_provider_reference_curie=reference_curie,
    )
    db.add(document)
    db.flush()
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: nullcontext(db))
    context = ExtractionExecutionContext(
        captured_at=datetime.now(timezone.utc),
        source_kind="flow",
        flow_id="fixture-flow",
        step_id="topic-tag-step",
        agent_key="gene_expression_topic_tag",
        executed_query="Assess this paper.",
        document=capture_source_document(str(document.id), subject),
    )
    record = persist_extraction_result(
        CurationExtractionPersistenceRequest(
            document_id=str(document.id),
            agent_key="gene_expression_topic_tag",
            adapter_key="topic_entity_tag",
            source_kind=CurationExtractionSourceKind.FLOW,
            flow_run_id=str(uuid4()),
            user_id=subject,
            candidate_count=len(payload["curatable_objects"]),
            payload_json=payload,
            metadata={"execution_context": context.model_dump(mode="json")},
        ),
        db=db,
    ).extraction_result
    ref = ensure_domain_envelope_materialization(record, persist=True, db=db)
    assert ref.review_row_count == 1
    assert db.get(DomainEnvelopeModel, ref.envelope_id) is not None
    return materialize_persisted_envelope_review_rows(db, ref.envelope_id).rows


def _fields(row):
    return {field["field_path"]: field["value"] for field in row.metadata["workspace_fields"]}


def test_negated_proposal_without_evidence_persists_to_one_review_row(db_session, monkeypatch):
    payload = _proposal_payload(monkeypatch, negated=True, rationale="No expression data.")

    rows = _persisted_rows(db_session, monkeypatch, payload, reference_curie=DOCUMENT_CURIE)

    assert len(rows) == 1
    fields = _fields(rows[0])
    assert fields["negated"] is True
    assert fields["topic"] == "ATP:0000041"
    assert fields["reference_curie"] == DOCUMENT_CURIE


def test_positive_proposal_for_an_upload_persists_with_evidence_and_blank_reference(
    db_session, monkeypatch
):
    payload = _proposal_payload(
        monkeypatch,
        negated=False,
        rationale="Reports expression in wild-type animals.",
        evidence_record_ids=["evidence-1"],
    )

    rows = _persisted_rows(db_session, monkeypatch, payload, reference_curie=None)

    assert len(rows) == 1
    fields = _fields(rows[0])
    assert fields["negated"] is False
    assert fields["reference_curie"] in (None, "")
    assert rows[0].metadata["evidence_record_ids"] == ["evidence-1"]
