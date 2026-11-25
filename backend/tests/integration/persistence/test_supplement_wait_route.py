"""An import with a chosen supplement, through the real route, to the file added.

KANBAN-1976 / ALL-1460, P7b, with Chris's decision of 23 Nov 2026 (card 31): the
supplement wait only watches ABC's read-only listing and never repeats the
conversion request.

Real: the import route and its services, Postgres, the Alliance ABC adapter and
its HTTP client, the file listing and the add. ABC is an ``httpx.MockTransport``
serving ABC's recorded response shapes (``show_all`` rows, conversion answers,
downloads). Faked at the boundaries this lane cannot reach: the search index
(vector storage and its document record) and the main paper's Markdown
ingestion, which stands in by writing the rows the real ingestion writes (main
file ready, paper completed). All values are made up.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import BackgroundTasks
from sqlalchemy import delete, select
from starlette.requests import Request

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_PACKAGE_SRC = BACKEND_ROOT.parent / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PACKAGE_SRC))

from agr_ai_curation_alliance.document_sources.abc_literature import (  # noqa: E402
    ABCLiteratureDocumentSourceProvider,
)
from agr_ai_curation_alliance.literature.client import (  # noqa: E402
    ABCLiteratureClient,
    ABCLiteratureClientConfig,
)

from src.api import documents  # noqa: E402
from src.lib.document_files.service import (  # noqa: E402
    main_file_id,
    mark_file_ready,
    reserve_file_chunk_range,
)
from src.lib.document_sources import supplement_add, supplement_wait  # noqa: E402
from src.lib.document_sources.file_assessment import FileCheckLimits  # noqa: E402
from src.lib.document_sources.identifier_import import IdentifierImportService  # noqa: E402
from src.lib.document_sources.ingestion import _sync_sql_document_status  # noqa: E402
from src.lib.pdf_jobs import service as job_service  # noqa: E402
from src.lib.pdf_jobs import upload_execution_service as execution  # noqa: E402
from src.lib.pdf_jobs.upload_execution_service import UploadExecutionService  # noqa: E402
from src.models.api_schemas import (  # noqa: E402
    DocumentSourceIdentifierImportRequest,
    DocumentSourceSupplementChoice,
)
from src.models.sql.database import SessionLocal  # noqa: E402
from src.models.sql.pdf_document import PDFDocument  # noqa: E402
from src.models.sql.pdf_processing_job import PdfProcessingJob  # noqa: E402
from tests.pdf_document_test_support import ensure_test_pdf_owner  # noqa: E402

PREFIX = "p7b_route_test_"
REFERENCE = "AGRKB:101"
MAIN_PDF = b"%PDF-1.4\n% made-up main paper\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
MAIN_MARKDOWN = b"# A made-up paper\n\n## Results\n\nMade-up results text for a made-up assay.\n"
WORKBOOK = (
    b"# Made-up workbook\n\n## Sheet1\n\nMade-up strains and scores for a made-up assay.\n\n"
    b"| strain | score |\n| --- | --- |\n| s1 | 1 |\n| s2 | 2 |\n"
)


# --- ABC's recorded shapes (``show_all`` rows, ``_status_payload``) -----------------------


def _mods():
    return [{"referencefile_mod_id": 7000, "mod_abbreviation": "WB"}]


def _row(referencefile_id, *, file_class, extension, display_name, source=None, pdf_type=None):
    return {
        "referencefile_id": referencefile_id, "reference_id": 101, "display_name": display_name,
        "file_class": file_class, "file_publication_status": "final", "file_extension": extension,
        "pdf_type": pdf_type, "md5sum": f"md5-{referencefile_id}", "is_annotation": False,
        "referencefile_mods": _mods(), "source": source,
    }


def _source(row):
    return {key: row[key] for key in ("referencefile_id", "display_name", "file_class", "file_extension", "md5sum")}


def _entry(referencefile_id, file_class, status, converted_id=None):
    converted = None
    if converted_id is not None:
        kind = "main" if file_class == "main" else "supplement"
        converted = {"display_name": f"Made_up_{referencefile_id}_merged",
                     "file_class": f"converted_merged_{kind}", "referencefile_id": converted_id}
    return {"source": {"display_name": f"Made_up_{referencefile_id}", "file_class": file_class,
                       "referencefile_id": referencefile_id},
            "converted": converted, "figures": [], "status": status, "error": None}


def _answer(progress):
    return {
        "reference_curie": REFERENCE, "status": "running", "job_id": "made-up-job", "previous_job_id": None,
        "error_message": None, "started_at": "2026-11-23T10:00:00", "completed_at": None,
        "converted_classes": ["converted_merged_main"], "per_file_progress": progress, "per_mod_status": [],
    }


class _Abc:
    """ABC over HTTP: one paper whose files ABC converts while the import runs.

    After the import's request, ABC stores the main text before the main wait's
    second listing read, unless ``fails_main``: then it never does, and any later
    conversion request answers ``failed``."""

    def __init__(
        self,
        main_name: str,
        *,
        fails_main: bool = False,
        never_converts: bool = False,
        supplement_extension: str = "xlsx",
        supplement_pdf: bytes | None = None,
    ):
        self.fails_main = fails_main
        self.never_converts = never_converts
        self.supplement_extension = supplement_extension
        self.supplement_pdf = supplement_pdf
        self.main_reads = 0
        self.main = _row(10, file_class="main", extension="pdf", display_name=main_name, pdf_type="pdf")
        self.supplement = _row(
            30, file_class="supplement", extension=supplement_extension, display_name="Made_up_S30",
        )
        if supplement_pdf is not None:
            self.supplement["md5sum"] = hashlib.md5(supplement_pdf).hexdigest()
        self.rows = [self.main, self.supplement]
        self.conversion_requests: list[str] = []  # "main" or "stage", by when it was sent
        self.stage_reads = 0
        self.stage_requests: list[str] = []
        self.main_ready = False
        self.unknown: list[str] = []

    def abc_stores_the_main_text(self):
        self.rows.append(_row(11, file_class="converted_merged_main", extension="md",
                              display_name=f"{self.main['display_name']}_merged", source=_source(self.main)))

    def abc_stores_the_supplement_text(self):
        row = _row(31, file_class="converted_merged_supplement", extension="md",
                   display_name=f"Made_up_S30_{self.supplement_extension}", source=_source(self.supplement))
        row["md5sum"] = hashlib.md5(WORKBOOK).hexdigest()
        self.rows.append(row)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if self.main_ready:
            self.stage_requests.append(path)
        if path == f"/reference/{REFERENCE}":
            return httpx.Response(200, json={
                "reference_id": 101, "curie": REFERENCE, "title": "A made-up paper",
                "cross_references": [{"curie": "PMID:41000001", "curie_prefix": "PMID", "is_obsolete": False}],
                "copyright_license_name": None, "copyright_license_open_access": None,
            })
        if path == f"/reference/referencefile/show_all/{REFERENCE}":
            if self.conversion_requests and not self.main_ready:
                self.main_reads += 1
                if self.main_reads == 2 and not (self.fails_main or self.never_converts):
                    self.abc_stores_the_main_text()
            if self.main_ready:
                self.stage_reads += 1
                if self.stage_reads == 3:  # ABC stores the converted supplement between two reads
                    self.abc_stores_the_supplement_text()
            return httpx.Response(200, json=list(self.rows))
        if path == f"/reference/referencefile/conversion_request/{REFERENCE}":
            self.conversion_requests.append("stage" if self.main_ready else "main")
            if len(self.conversion_requests) == 1:
                return httpx.Response(202, json=_answer([_entry(10, "main", "pending"), _entry(30, "supplement", "pending")]))
            if self.never_converts:
                return httpx.Response(202, json=_answer([_entry(10, "main", "pending"), _entry(30, "supplement", "pending")]))
            if self.fails_main:
                failed = {**_answer([{**_entry(10, "main", "failed"), "error": "Made-up PDF could not be parsed"}]),
                          "status": "failed", "error_message": "Made-up PDF could not be parsed",
                          "completed_at": "2026-11-23T10:20:00"}
                return httpx.Response(200, json=failed)
            return httpx.Response(202, json=_answer(
                [_entry(10, "main", "success", converted_id=11), _entry(30, "supplement", "pending")]
            ))
        if path.startswith("/reference/referencefile/download_file/"):
            payloads = {"10": MAIN_PDF, "11": MAIN_MARKDOWN, "31": WORKBOOK}
            if self.supplement_pdf is not None:
                payloads["30"] = self.supplement_pdf
            body = payloads[path.rsplit("/", 1)[1]]
            # A streamed body, as ABC's download route sends it (the client reads it raw).
            return httpx.Response(200, stream=httpx.ByteStream(body), headers={"content-length": str(len(body))})
        self.unknown.append(f"{request.method} {path}")
        return httpx.Response(404, json={"detail": "Not Found"})

    def provider(self):
        client = ABCLiteratureClient(
            ABCLiteratureClientConfig(base_url="https://abc.example.org"),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )
        return ABCLiteratureDocumentSourceProvider(client)


# --- Fixtures --------------------------------------------------------------------------------


@pytest.fixture
def owner(monkeypatch, tmp_path):
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    subject = f"p7b-route-owner-{uuid4()}"
    from src.models.sql.document_file_assessment import DocumentFileAssessment

    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=subject)
        db.execute(
            delete(DocumentFileAssessment).where(
                DocumentFileAssessment.provider == "abc_literature",
                DocumentFileAssessment.provider_file_id == "31",
            )
        )
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    for name, value in {
        "DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS": "5",
        "DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS": "5",
    }.items():
        monkeypatch.setenv(name, value)
    yield owner_id, subject
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))
        db.execute(
            delete(DocumentFileAssessment).where(
                DocumentFileAssessment.provider == "abc_literature",
                DocumentFileAssessment.provider_file_id == "31",
            )
        )


def _search_index(monkeypatch):
    """The search index, which this lane does not run: its writes succeed and are not read."""

    async def store(chunks, document_id, client, user_id, *, chunk_range):
        return None

    monkeypatch.setattr("src.lib.pipeline.store.store_added_file_pieces", store)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", lambda *_a, **_k: 0)
    monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", AsyncMock())
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_: None)
    monkeypatch.setattr(execution, "get_connection", lambda: None)
    monkeypatch.setattr(execution, "update_document_status_detailed", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(
        supplement_add, "current_file_check_limits",
        lambda: FileCheckLimits(max_bytes=10_000, few_characters_below=10, row_token_limit=200,
                                count_tokens=len, token_encoding="made-up-encoding"),
    )


async def _main_ingestion(request, *, weaviate_client):
    """Stands in for the main paper's Markdown ingestion (hierarchy model and vector storage):
    writes the rows the real ingestion leaves: the main file's piece range (three made-up
    pieces), the file ready and the paper completed."""

    file_id = await asyncio.to_thread(main_file_id, request.document_id)
    await asyncio.to_thread(reserve_file_chunk_range, request.document_id, file_id, 3)
    await asyncio.to_thread(mark_file_ready, file_id, text_bytes=len(request.markdown.encode()))
    await _sync_sql_document_status(
        request.document_id, user_id=request.user_id, owner_user_id=request.document_owner_user_id,
        status="completed",
    )
    return SimpleNamespace(validation_warnings=[])


async def _import_with_a_chosen_supplement(abc: _Abc, subject: str, monkeypatch, tmp_path) -> UUID:
    """POST the import through the real route and run its background work to the end."""

    _search_index(monkeypatch)
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)
    monkeypatch.setattr(
        "src.lib.document_sources.access.get_groups_for_provider_groups", lambda groups: ["WB"],
    )

    async def _ingest(request, *, weaviate_client):
        result = await _main_ingestion(request, weaviate_client=weaviate_client)
        abc.main_ready = True  # every later ABC request comes from the supplement stage
        return result

    executions = UploadExecutionService(
        pipeline_tracker=AsyncMock(), document_source_provider_factory=abc.provider,
        provider_markdown_ingestion_fn=_ingest,
    )
    monkeypatch.setattr(documents, "identifier_import_service", IdentifierImportService(
        upload_execution_service=executions,
        provider_factory=abc.provider,
        storage_path_provider=lambda: tmp_path,
        create_document_fn=AsyncMock(return_value={"success": True}),
    ))

    background_tasks = BackgroundTasks()
    request = Request({
        "type": "http", "method": "POST", "path": "/documents/import/source-identifiers",
        "headers": [(b"cookie", b"auth_token=made-up-token")], "query_string": b"",
    })
    user = {"sub": subject, "groups": ["made-up-staff"], "exp": int(time.time()) + 86400}

    answer = await documents.import_documents_by_source_identifiers(
        DocumentSourceIdentifierImportRequest(
            identifiers=REFERENCE,
            supplements=[DocumentSourceSupplementChoice(
                identifier=REFERENCE,
                provider_file_id="30",
                md5=abc.supplement["md5sum"],
                converted_md5=None,
            )],
        ),
        background_tasks, request, user,
    )
    assert answer["results"][0]["status"] == "imported", answer
    document_id = UUID(answer["results"][0]["document_id"])

    await asyncio.wait_for(background_tasks(), timeout=60)
    await asyncio.wait_for(asyncio.gather(*list(supplement_wait._stage_tasks)), timeout=60)
    assert abc.unknown == []
    return document_id


def _main_job(document_id: UUID) -> PdfProcessingJob:
    with SessionLocal() as db:
        jobs = db.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.document_id == document_id)
        ).scalars().all()
    return next(job for job in jobs if not (job.metadata_json or {}).get("job_kind"))


def _sample_pdf(pages: int = 2) -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_an_imported_supplement_joins_once_abc_lists_it_with_no_conversion_request_from_the_wait(
    owner, monkeypatch, tmp_path,
):
    owner_id, subject = owner
    abc = _Abc(main_name=f"{PREFIX}{uuid4().hex}")
    document_id = await _import_with_a_chosen_supplement(abc, subject, monkeypatch, tmp_path)

    # KANBAN-2191 / ALL-1725: the import's one on-demand request; the main wait then
    # only read the listing until the main text appeared; the supplement wait never asked.
    assert abc.conversion_requests == ["main"]
    # Two reads by the wait (the second listed the main text), one by the main ingestion's
    # figure lookup.
    assert abc.main_reads == 3
    main_job = _main_job(document_id)
    assert main_job.status == "completed"
    assert "supplements" not in (main_job.metadata_json or {})
    assert set(main_job.metadata_json["provider_conversion_requests"]) == {"on_demand_sent_at"}
    # ALL-1751: after the main paper was ready:
    # - 1 GET /reference (the stage's first full listing only; reused by polls and add)
    # - 3 show_all reads (the stage's first listing + 2 cheap poll reads; none on full
    #   listing or add)
    # - 2 download_file reads (1 check download on the reused listing + 1 add download)
    # Total: 6 requests (down from 10 before ALL-1751).
    assert abc.stage_reads == 3
    assert abc.stage_requests == [
        f"/reference/{REFERENCE}",
        f"/reference/referencefile/show_all/{REFERENCE}",
        f"/reference/referencefile/show_all/{REFERENCE}",
        f"/reference/referencefile/show_all/{REFERENCE}",
        "/reference/referencefile/download_file/31",
        "/reference/referencefile/download_file/31",
    ]
    with SessionLocal() as db:
        paper = db.get(PDFDocument, document_id)
        assert paper.status == "completed"
        added = [row for row in paper.files if row.source_provider_source_file_id == "30"]
        assert [(row.role, row.status) for row in added] == [("supplement", "ready")]
        jobs = db.execute(
            select(PdfProcessingJob).where(PdfProcessingJob.document_id == document_id)
        ).scalars().all()
    stage_job = next(job for job in jobs if (job.metadata_json or {}).get("job_kind") == "supplement_wait")
    assert stage_job.status == "completed"
    assert [(entry["provider_file_id"], entry["state"]) for entry in stage_job.metadata_json["supplements"]] == [
        ("30", "added"),
    ]
    assert stage_job.metadata_json["supplements"][0]["display_name"] == "Made_up_S30.xlsx"
    latest = job_service.get_latest_paper_job_for_document(document_id=document_id, user_id=owner_id)
    assert latest.status == "completed"


@pytest.mark.asyncio
async def test_an_imported_pdf_supplement_reuses_the_stage_listing_and_downloads_text_and_pdf(
    owner, monkeypatch, tmp_path,
):
    """ALL-1751 + #1126: a PDF supplement sends 1 GET /reference, 3 show_all reads, 1 check
    download, 1 add text download, and 1 add original PDF download (7 requests total)."""
    _owner_id, subject = owner
    pdf_bytes = _sample_pdf(2)
    abc = _Abc(
        main_name=f"{PREFIX}{uuid4().hex}",
        supplement_extension="pdf",
        supplement_pdf=pdf_bytes,
    )
    document_id = await _import_with_a_chosen_supplement(abc, subject, monkeypatch, tmp_path)

    assert abc.conversion_requests == ["main"]
    assert abc.stage_reads == 3
    assert abc.stage_requests == [
        f"/reference/{REFERENCE}",
        f"/reference/referencefile/show_all/{REFERENCE}",
        f"/reference/referencefile/show_all/{REFERENCE}",
        f"/reference/referencefile/show_all/{REFERENCE}",
        "/reference/referencefile/download_file/31",
        "/reference/referencefile/download_file/31",
        "/reference/referencefile/download_file/30",
    ]
    with SessionLocal() as db:
        paper = db.get(PDFDocument, document_id)
        added = [row for row in paper.files if row.source_provider_source_file_id == "30"]
        assert [(row.role, row.status, row.display_mode, row.page_count) for row in added] == [
            ("supplement", "ready", "local_pdf", 2),
        ]
        assert (tmp_path / added[0].file_path).is_file()



@pytest.mark.asyncio
async def test_a_paper_abc_fails_is_reported_at_the_end_with_abcs_reason_after_one_more_request(
    owner, monkeypatch, tmp_path,
):
    """A 12-second main wait read every 5 s; the one late request goes out once another
    read would not fit (12 - (5 + 1) s), inside ABC's failure hold."""

    owner_id, subject = owner
    monkeypatch.setenv("DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", "1")
    abc = _Abc(main_name=f"{PREFIX}{uuid4().hex}", fails_main=True)
    document_id = await _import_with_a_chosen_supplement(abc, subject, monkeypatch, tmp_path)

    assert abc.conversion_requests == ["main", "main"]  # the import's, and the one at the end
    main_job = _main_job(document_id)
    assert (main_job.status, main_job.error_message) == ("failed", "Made-up PDF could not be parsed")
    assert set(main_job.metadata_json["provider_conversion_requests"]) == {"on_demand_sent_at", "end_sent_at"}
    with SessionLocal() as db:
        assert db.get(PDFDocument, document_id).status == "failed"



@pytest.mark.asyncio
async def test_a_paper_abc_never_converts_gets_exactly_one_request_at_the_end(owner, monkeypatch, tmp_path):
    """The end request is sent once, and the wait then times out at the deadline of the
    import's own request (a 12-second deadline here)."""

    owner_id, subject = owner
    monkeypatch.setenv("DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", "1")
    abc = _Abc(main_name=f"{PREFIX}{uuid4().hex}", never_converts=True)
    document_id = await _import_with_a_chosen_supplement(abc, subject, monkeypatch, tmp_path)

    assert abc.conversion_requests == ["main", "main"]  # the import's, and the one at the end
    main_job = _main_job(document_id)
    assert (main_job.status, main_job.error_message) == (
        "failed",
        "ABC had not converted this paper 12 seconds after the import started, "
        "so the import stopped. The time includes any wait behind other imports.",
    )
    assert set(main_job.metadata_json["provider_conversion_requests"]) == {"on_demand_sent_at", "end_sent_at"}
    assert [
        (entry["provider_file_id"], entry["display_name"], entry["state"], entry["reason_code"])
        for entry in main_job.metadata_json["supplements"]
    ] == [("30", "Made_up_S30", "left_out", "supplement_stage_failed")]


@pytest.mark.asyncio
async def test_cancelling_an_import_while_queued_closes_chosen_supplements_as_import_cancelled(
    owner, monkeypatch, tmp_path,
):
    owner_id, subject = owner
    abc = _Abc(main_name=f"{PREFIX}{uuid4().hex}")
    _search_index(monkeypatch)
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)
    monkeypatch.setattr(
        "src.lib.document_sources.access.get_groups_for_provider_groups", lambda groups: ["WB"],
    )
    executions = UploadExecutionService(
        pipeline_tracker=AsyncMock(),
        document_source_provider_factory=abc.provider,
        provider_markdown_ingestion_fn=_main_ingestion,
    )
    monkeypatch.setattr(
        documents,
        "identifier_import_service",
        IdentifierImportService(
            upload_execution_service=executions,
            provider_factory=abc.provider,
            storage_path_provider=lambda: tmp_path,
            create_document_fn=AsyncMock(return_value={"success": True}),
        ),
    )

    background_tasks = BackgroundTasks()
    request = Request({
        "type": "http", "method": "POST", "path": "/documents/import/source-identifiers",
        "headers": [(b"cookie", b"auth_token=made-up-token")], "query_string": b"",
    })
    user = {"sub": subject, "groups": ["made-up-staff"], "exp": int(time.time()) + 86400}

    answer = await documents.import_documents_by_source_identifiers(
        DocumentSourceIdentifierImportRequest(
            identifiers=REFERENCE,
            supplements=[DocumentSourceSupplementChoice(
                identifier=REFERENCE, provider_file_id="30", md5="md5-30", converted_md5=None,
            )],
        ),
        background_tasks, request, user,
    )
    document_id = UUID(answer["results"][0]["document_id"])
    job_id = answer["results"][0]["job_id"]

    # Before the background task starts, the accepted job carries queued entries with real display_name.
    accepted = _main_job(document_id)
    assert [
        (entry["provider_file_id"], entry["display_name"], entry["state"], entry["reason_code"])
        for entry in accepted.metadata_json["supplements"]
    ] == [("30", "Made_up_S30", "queued", None)]

    # Cancel while queued, then run the queued background task.
    job_service.request_cancel(job_id=job_id, user_id=owner_id)
    await asyncio.wait_for(background_tasks(), timeout=60)

    cancelled = _main_job(document_id)
    assert cancelled.status == "cancelled"
    assert [
        (entry["provider_file_id"], entry["display_name"], entry["state"], entry["reason_code"])
        for entry in cancelled.metadata_json["supplements"]
    ] == [("30", "Made_up_S30", "left_out", "import_cancelled")]
    assert cancelled.metadata_json["supplements"][0]["observed_at"]
