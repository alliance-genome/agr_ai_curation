"""The add route adds a converted PDF supplement as its PDF (KANBAN-1976 / ALL-1460, P7c2).

Through the real route: the endpoint starts the job, the queued background task runs it
against the real database and search index, and the job's ``metadata.supplements`` says
what happened. It fails if #1127's interim refusal (``pdf_supplement_not_supported_yet``)
survived: the PDF would be left out.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the P7c1 module and used by name.

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from pypdf import PdfWriter
from starlette.requests import Request

from src.api import documents as documents_api
from src.lib.document_sources import file_listing_service
from src.lib.document_sources.file_listing import (
    FileListing,
    ListedFile,
    ListedFileCheck,
    ListedFileCheckStatus,
    ListedFileStatus,
)
from src.lib.document_sources.models import SourceArtifactFormat, SourceArtifactRole, SupplementFileCategory
from src.lib.pdf_jobs import service as pdf_job_service
from src.schemas.document_files import AddFilesRequest
from tests.integration.persistence.test_supplement_add_search_index import (  # noqa: F401
    CONTEXT,
    WORKBOOK,
    _artifact,
    _entry,
    _indexes,
    _paper,
    _Provider,
    _rows,
    index,
    migrated_database,
    owner_id,
)


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


PDF = _pdf(2)
PDF_TEXT = b"## Made_up_S70\n\nMade-up text of a PDF.\n"


def _pdf_entry(original_id: int) -> ListedFile:
    """A converted PDF supplement as ABC lists it: a ``SOURCE_PDF`` original of format PDF."""

    original = _artifact(
        str(original_id), SourceArtifactRole.SOURCE_PDF, SourceArtifactFormat.PDF,
        md5sum=hashlib.md5(PDF).hexdigest(), file_class="supplement", file_extension="pdf",
    )
    converted = _artifact(
        str(original_id + 1), SourceArtifactRole.CONVERTED_TEXT, SourceArtifactFormat.MARKDOWN,
        md5sum=hashlib.md5(PDF_TEXT).hexdigest(), file_class="converted_merged_supplement", file_extension="md",
    )
    return ListedFile(
        provider_file_id=original.artifact_id, converted_text_id=converted.artifact_id,
        display_name=original.display_name, file_role="supplement", status=ListedFileStatus.READY,
        check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED), in_this_paper=False,
        md5=original.md5sum, converted_md5=converted.md5sum, artifact_format="pdf",
        category=SupplementFileCategory.CONVERTIBLE, single_title_required=False,
        tickable=True, not_tickable_reason=None,
        last_provider_observation=None, source_artifact=original, converted_artifact=converted,
    )


class _ClosingProvider(_Provider):
    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_the_add_route_adds_a_pdf_supplement_as_its_pdf_with_the_rest(owner_id, index, monkeypatch, tmp_path):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)

    async def listing(document_id, *, owner_user_id, provider, context):
        return FileListing(
            provider="made_up_provider", reference_id="101", reference_curie="MADE:101", main_status="ready",
            files=(_entry(20, frozenset()), _pdf_entry(70)), not_shown_count=0,
            duplicate_copies_count=0, check_version="made-up", existing_document_id=None,
        )

    async def provision(_principal):
        return SimpleNamespace(id=owner_id, auth_sub="owner-subject")

    async def context(**_):
        return CONTEXT

    monkeypatch.setattr(file_listing_service, "list_files_for_paper", listing)
    monkeypatch.setattr(documents_api, "external_document_source_import_enabled", lambda: True)
    monkeypatch.setattr(documents_api, "provision_user_async", provision)
    monkeypatch.setattr(documents_api, "principal_from_claims", lambda claims: claims)
    monkeypatch.setattr(documents_api, "_document_source_context_or_503", context)
    monkeypatch.setattr(
        "src.lib.document_sources.registry.get_configured_document_source_provider",
        lambda: _ClosingProvider({"21": WORKBOOK, "70": PDF, "71": PDF_TEXT}),
    )
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: index)

    payload = AddFilesRequest(files=[
        {"provider_file_id": "70", "md5": hashlib.md5(PDF).hexdigest(), "converted_md5": hashlib.md5(PDF_TEXT).hexdigest()},
        {"provider_file_id": "20", "md5": "md5-20", "converted_md5": hashlib.md5(WORKBOOK).hexdigest()},
    ])
    request = Request({"type": "http", "method": "POST", "path": f"/weaviate/documents/{paper}/files", "headers": []})
    background = BackgroundTasks()
    job = await documents_api.add_files_to_paper_endpoint(
        payload, background, request, document_id=str(paper), user={"sub": "owner-subject"},
    )
    await background()

    finished = pdf_job_service.get_job(job_id=job.job_id, user_id=owner_id)
    entries = {item["provider_file_id"]: item for item in finished.metadata["supplements"]}
    assert entries["70"]["state"] == "added", entries["70"]
    assert entries["20"]["state"] == "added"
    supplements = {row.source_provider_source_file_id: row for row in _rows(paper) if row.role == "supplement"}
    pdf = supplements["70"]
    assert (pdf.display_mode, pdf.page_count, pdf.status) == ("local_pdf", 2, "ready")
    assert (Path(tmp_path) / pdf.file_path).read_bytes() == PDF
    assert supplements["20"].display_mode == "text"
