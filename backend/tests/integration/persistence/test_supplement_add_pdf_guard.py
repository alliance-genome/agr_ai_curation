"""Until P7c2, the add route never stores a PDF supplement as text (KANBAN-1976 / ALL-1460).

Through the real route: the endpoint starts the job, the queued background task runs it
against the real database and search index, and the job's ``metadata.supplements`` says
what happened. The listing entry is deliberately tickable: the guard is the add's own,
on the provider's file format, not the listing's flag.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the P7c1 module and used by name.

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
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
from src.lib.document_sources.file_reasons import FileReason
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


def _pdf_entry(original_id: int) -> ListedFile:
    """A converted PDF supplement as ABC lists it: a ``SOURCE_PDF`` original of format PDF."""

    original = _artifact(
        str(original_id), SourceArtifactRole.SOURCE_PDF, SourceArtifactFormat.PDF,
        file_class="supplement", file_extension="pdf",
    )
    converted = _artifact(
        str(original_id + 1), SourceArtifactRole.CONVERTED_TEXT, SourceArtifactFormat.MARKDOWN,
        file_class="converted_merged_supplement", file_extension="md",
    )
    return ListedFile(
        provider_file_id=original.artifact_id, converted_text_id=converted.artifact_id,
        display_name=original.display_name, file_role="supplement", status=ListedFileStatus.READY,
        check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED), in_this_paper=False,
        md5=original.md5sum, converted_md5=converted.md5sum, artifact_format="pdf",
        category=SupplementFileCategory.CONVERTIBLE, single_title_required=False,
        tickable=True, not_tickable_reason=None,  # the guard must not depend on the listing's flag
        last_provider_observation=None, source_artifact=original, converted_artifact=converted,
    )


class _ClosingProvider(_Provider):
    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_the_add_route_leaves_a_pdf_supplement_out_and_adds_the_rest(owner_id, index, monkeypatch):
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
        lambda: _ClosingProvider({"21": WORKBOOK, "71": b"# Made_up_S70\n\nMade-up text of a PDF.\n"}),
    )
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: index)

    payload = AddFilesRequest(files=[
        {"provider_file_id": "70", "md5": "md5-70", "converted_md5": "md5-71"},
        {"provider_file_id": "20", "md5": "md5-20", "converted_md5": "md5-21"},
    ])
    request = Request({"type": "http", "method": "POST", "path": f"/weaviate/documents/{paper}/files", "headers": []})
    background = BackgroundTasks()
    job = await documents_api.add_files_to_paper_endpoint(
        payload, background, request, document_id=str(paper), user={"sub": "owner-subject"},
    )
    await background()

    finished = pdf_job_service.get_job(job_id=job.job_id, user_id=owner_id)
    entries = {item["provider_file_id"]: item for item in finished.metadata["supplements"]}
    assert entries["70"]["state"] == "left_out"
    assert entries["70"]["reason_code"] == FileReason.PDF_SUPPLEMENT_NOT_SUPPORTED_YET.value
    assert entries["70"]["file_id"] is None
    assert entries["20"]["state"] == "added"
    # Only the main file and the Excel supplement are rows of the paper; no PDF text was stored.
    assert [row.source_provider_source_file_id for row in _rows(paper) if row.role == "supplement"] == ["20"]
    supplement = next(row for row in _rows(paper) if row.role == "supplement")
    assert _indexes(index, str(paper)) == [0, 1, 2, *range(supplement.chunk_index_start, supplement.chunk_index_end + 1)]
