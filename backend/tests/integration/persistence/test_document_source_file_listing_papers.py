"""Which papers list provider files, against real rows (P6): owner only, library papers only,
provider imports only (Q5), resolved by the stored reference CURIE."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from fastapi import HTTPException

from src.lib.document_sources import file_listing_service
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_listing import FileListing
from src.lib.document_sources.file_listing_service import FileListingUnavailable
from src.lib.document_sources.models import ProviderBearerKind
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import DocumentReferenceLink
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper

PROVIDER = "made_up_provider"


class _Provider:
    provider_id = PROVIDER


CONTEXT = DocumentSourceRequestContext(
    provider_groups=("group-a",), authorized_group_ids=("group-a",),
    bearer_kind=ProviderBearerKind.CURATOR, curator_token="curator-token",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


def _add_paper(owner_id, *, kind="library", provider=PROVIDER, reference_id="55", curie="MADEUP:55"):
    identifier = uuid4()
    mode = "text" if kind == "benchmark_frozen" else "local_pdf"
    with SessionLocal.begin() as session:
        session.add(new_paper(
            id=identifier, filename=f"{identifier}.pdf", user_id=owner_id,
            file_path=f"synthetic-listing-test/{identifier}", file_hash=identifier.hex * 2,
            file_size=1, page_count=None if mode == "text" else 1, display_mode=mode,
            document_kind=kind, source_provider=provider, source_provider_reference_id=reference_id,
            source_provider_reference_curie=curie, source_provider_source_file_id="10" if provider else None,
            source_provider_pdf_artifact_id="11" if provider else None,
        ))
    return identifier


@pytest.fixture
def owners():
    with SessionLocal.begin() as session:
        return (
            ensure_test_pdf_owner(session, auth_sub=f"listing-owner-{uuid4().hex}"),
            ensure_test_pdf_owner(session, auth_sub=f"listing-other-{uuid4().hex}"),
        )


@pytest.fixture
def captured(monkeypatch):
    calls = {}

    async def listed(provider, lookup, context, files_in_paper, *, route, provider_observations,
                     owner_user_id, expected_reference_id=None, caller_reports_download_outages=False):
        calls.update(
            lookup=lookup, files=set(files_in_paper), expected=expected_reference_id, route=route.value,
            observations=dict(provider_observations),
            caller_reports_download_outages=caller_reports_download_outages,
        )
        return FileListing(
            provider=PROVIDER, reference_id="55", reference_curie="MADEUP:55", main_status="ready",
            files=(), not_shown_count=0, duplicate_copies_count=0, check_version="v",
            existing_document_id=None,
        )

    monkeypatch.setattr(file_listing_service, "_list", listed)
    return calls


async def _list_for(document_id, owner_id):
    return await file_listing_service.list_files_for_paper(
        document_id, owner_user_id=owner_id, provider=_Provider(), context=CONTEXT,
    )


@pytest.mark.asyncio
async def test_an_imported_paper_is_listed_by_its_curie_with_its_file_ids(owners, captured):
    owner, _ = owners
    await _list_for(_add_paper(owner), owner)
    assert captured == {
        "lookup": "MADEUP:55", "files": {"10", "11"}, "expected": "55", "route": "paper", "observations": {},
        "caller_reports_download_outages": False,
    }


@pytest.mark.asyncio
async def test_another_curators_paper_is_refused(owners, captured):
    owner, other = owners
    with pytest.raises(HTTPException) as raised:
        await _list_for(_add_paper(owner), other)
    assert raised.value.status_code == 403 and captured == {}


@pytest.mark.asyncio
async def test_an_unknown_paper_and_a_frozen_benchmark_paper_are_not_found(owners, captured):
    owner, _ = owners
    for document_id in (uuid4(), _add_paper(owner, kind="benchmark_frozen")):
        with pytest.raises(HTTPException) as raised:
            await _list_for(document_id, owner)
        assert raised.value.status_code == 404
    assert captured == {}


@pytest.mark.asyncio
async def test_an_upload_linked_to_its_reference_has_no_provider_file_list(owners, captured):
    owner, _ = owners
    uploaded = _add_paper(owner, provider=None, reference_id=None, curie=None)
    with SessionLocal.begin() as session:
        session.add(DocumentReferenceLink(
            document_id=uploaded, provider=PROVIDER, reference_curie="MADEUP:55",
            linked_at=datetime.now(timezone.utc),
        ))
    with pytest.raises(FileListingUnavailable) as raised:
        await _list_for(uploaded, owner)
    assert raised.value.code == "not_imported_from_provider" and captured == {}
