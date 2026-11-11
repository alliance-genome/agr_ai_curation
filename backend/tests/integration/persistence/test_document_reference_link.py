"""A curator can link a document with no source reference (ALL-1482)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api import curation_workspace as api
from src.lib.curation_workspace import document_reference as service
from src.lib.curation_workspace.models import CurationActionLogEntry, CurationReviewSession
from src.lib.curation_workspace.session_queries import get_session_detail
from src.lib.packages.document_source_provider_models import DevelopmentReaderUnavailable
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceError,
    DocumentSourceReferenceNotFound,
    NormalizedSourceIdentifier,
    SourceReference,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import DocumentReferenceLink, PDFDocument
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationDocumentReferenceRequest,
    CurationDocumentReferenceSource,
    CurationDocumentReferenceStatus,
    CurationSessionStatus,
)
from tests.integration.persistence.test_validation_history import (
    migrated as migrated,
    scope as scope,
)

CURIE = "SOURCE:101"
TITLE = "A found paper"
CLAIMS = {"sub": "reference-linker", "email": "linker@example.invalid"}


class FakeProvider:
    provider_id = "fixture_source"

    def __init__(self, outcome):
        self.outcome = outcome
        self.tokens = []
        self.closed = False

    def normalize_identifier(self, identifier):
        if identifier.startswith("bad"):
            return NormalizedSourceIdentifier(
                original=identifier, normalized=None, error="Identifier is not recognized"
            )
        return NormalizedSourceIdentifier(original=identifier, normalized=identifier.upper())

    async def resolve_reference(self, identifier, *, request_bearer_token=None):
        self.tokens.append(request_bearer_token)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def aclose(self):
        self.closed = True


def _found() -> SourceReference:
    return SourceReference(provider="fixture_source", reference_curie=CURIE, title=TITLE)


@pytest.fixture
def wired(monkeypatch):
    state = SimpleNamespace(provider=FakeProvider(_found()), token="curator-token", reports=[])

    async def context(*, request, user_claims):
        return SimpleNamespace(curator_token=state.token)

    monkeypatch.setattr(service, "build_document_source_request_context", context)
    monkeypatch.setattr(
        service, "get_configured_document_source_provider", lambda: state.provider
    )
    monkeypatch.setattr(
        service,
        "report_runtime_exception",
        lambda exc, **kwargs: state.reports.append((exc, kwargs)),
    )
    monkeypatch.setattr(api, "external_document_source_import_enabled", lambda: True)

    async def provision(_principal):
        return None

    monkeypatch.setattr(api, "provision_user_async", provision)
    return state


def _session(document_id) -> str:
    session_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(
            CurationReviewSession(
                id=session_id,
                status=CurationSessionStatus.NEW,
                adapter_key="generic",
                document_id=document_id,
                created_by_id=CLAIMS["sub"],
                session_version=1,
                prepared_at=datetime.now(timezone.utc),
            )
        )
    return str(session_id)


def _document(scope):
    with SessionLocal() as db:
        document = db.get(PDFDocument, scope.document_id)
        link = document.reference_link
        return SimpleNamespace(
            source_provider=document.source_provider,
            source_provider_reference_curie=document.source_provider_reference_curie,
            linked_reference_curie=link.reference_curie if link else None,
            linked_reference_provider=link.provider if link else None,
            linked_reference_at=link.linked_at if link else None,
        )


def _set_imported(scope):
    with SessionLocal.begin() as db:
        document = db.get(PDFDocument, scope.document_id)
        document.source_provider = "fixture_source"
        document.source_provider_reference_curie = "SOURCE:IMPORTED"


async def _find(session_id, identifier="PMID:1"):
    with SessionLocal() as db:
        return await api.find_session_document_reference(
            session_id,
            CurationDocumentReferenceRequest(identifier=identifier),
            request=None,
            user=CLAIMS,
            db=db,
        )


async def _link(session_id, identifier="PMID:1"):
    with SessionLocal() as db:
        return await api.link_session_document_reference(
            session_id,
            CurationDocumentReferenceRequest(identifier=identifier),
            request=None,
            user=CLAIMS,
            db=db,
        )


async def _unlink(session_id):
    with SessionLocal() as db:
        return await api.unlink_session_document_reference(session_id, user=CLAIMS, db=db)


def _log_entries(session_id):
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(CurationActionLogEntry).where(
                    CurationActionLogEntry.session_id == session_id
                )
            )
        )


@pytest.mark.asyncio
async def test_find_returns_the_paper_without_saving(scope, migrated, wired):
    session_id = _session(scope.document_id)

    result = await _find(session_id)

    assert result.status is CurationDocumentReferenceStatus.FOUND
    assert (result.reference_curie, result.reference_title) == (CURIE, TITLE)
    assert wired.provider.tokens == ["curator-token"]
    assert wired.provider.closed is True
    assert _document(scope).linked_reference_curie is None


@pytest.mark.asyncio
async def test_a_result_with_no_curie_is_not_found_and_hides_its_title(scope, migrated, wired):
    wired.provider.outcome = SourceReference(provider="fixture_source", title="A PubMed-only title")
    session_id = _session(scope.document_id)

    for result in (await _find(session_id), await _link(session_id)):
        assert result.status is CurationDocumentReferenceStatus.NOT_FOUND
        assert result.reference_title is None
        assert "A PubMed-only title" not in result.message
    assert _document(scope).linked_reference_curie is None
    assert wired.reports == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identifier", "outcome", "token", "status", "reported"),
    [
        ("bad-id", None, "curator-token", CurationDocumentReferenceStatus.INVALID_IDENTIFIER, False),
        ("PMID:1", DocumentSourceReferenceNotFound("x"), "curator-token",
         CurationDocumentReferenceStatus.NOT_FOUND, False),
        ("PMID:1", DocumentSourceAccessDenied("x"), "curator-token",
         CurationDocumentReferenceStatus.ACCESS_DENIED, False),
        ("PMID:1", None, None, CurationDocumentReferenceStatus.TOKEN_UNAVAILABLE, False),
        ("PMID:1", DocumentSourceError("secret upstream detail"), "curator-token",
         CurationDocumentReferenceStatus.SOURCE_UNAVAILABLE, True),
    ],
    ids=["invalid", "not-found", "access-denied", "no-token", "source-down"],
)
async def test_each_failure_has_its_own_outcome_and_saves_nothing(
    scope, migrated, wired, identifier, outcome, token, status, reported
):
    if outcome is not None:
        wired.provider.outcome = outcome
    wired.token = token
    session_id = _session(scope.document_id)

    result = await _link(session_id, identifier)

    assert result.status is status
    assert result.message
    assert "secret upstream detail" not in result.message
    assert result.reference_curie is None
    assert bool(wired.reports) is reported
    assert _document(scope).linked_reference_curie is None
    assert _log_entries(session_id) == []


@pytest.mark.asyncio
async def test_link_saves_the_servers_answer_and_every_session_sees_it(scope, migrated, wired):
    earlier_session = _session(scope.document_id)
    later_session = _session(scope.document_id)

    result = await _link(later_session)

    assert result.status is CurationDocumentReferenceStatus.LINKED
    document = _document(scope)
    assert document.linked_reference_curie == CURIE
    assert document.linked_reference_provider == "fixture_source"
    assert document.linked_reference_at is not None
    assert document.source_provider is None
    assert document.source_provider_reference_curie is None

    with SessionLocal() as db:
        for session_id in (earlier_session, later_session):
            ref = get_session_detail(db, session_id, owner_sub=CLAIMS["sub"]).document
            assert ref.reference_curie == CURIE
            assert ref.reference_source is CurationDocumentReferenceSource.CURATOR_LINK

    entry, = _log_entries(later_session)
    assert entry.action_type is CurationActionType.DOCUMENT_REFERENCE_LINKED
    assert CLAIMS["sub"] in entry.actor.values()
    assert entry.action_metadata == {
        "reference_curie": CURIE,
        "reference_title": TITLE,
        "provider": "fixture_source",
    }


@pytest.mark.asyncio
async def test_a_link_saved_during_the_lookup_wins_and_the_second_is_refused(
    scope, migrated, wired, monkeypatch
):
    session_id = _session(scope.document_id)
    original_resolve = wired.provider.resolve_reference

    async def resolve_after_another_curator_links(identifier, *, request_bearer_token=None):
        with SessionLocal.begin() as other:
            other.add(
                DocumentReferenceLink(
                    document_id=scope.document_id,
                    provider="fixture_source",
                    reference_curie="SOURCE:OTHER",
                    linked_at=datetime.now(timezone.utc),
                )
            )
        return await original_resolve(identifier, request_bearer_token=request_bearer_token)

    monkeypatch.setattr(wired.provider, "resolve_reference", resolve_after_another_curator_links)

    with pytest.raises(HTTPException) as refused:
        await _link(session_id)

    assert refused.value.status_code == 409
    assert _document(scope).linked_reference_curie == "SOURCE:OTHER"
    assert _log_entries(session_id) == []


@pytest.mark.asyncio
async def test_an_unavailable_development_reader_is_reported_as_no_token(
    scope, migrated, wired, monkeypatch
):
    async def context(*, request, user_claims):
        raise DevelopmentReaderUnavailable("no reader")

    monkeypatch.setattr(service, "build_document_source_request_context", context)

    result = await _find(_session(scope.document_id))

    assert result.status is CurationDocumentReferenceStatus.TOKEN_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_provider_that_fails_to_close_does_not_lose_the_answer(
    scope, migrated, wired, monkeypatch
):
    async def failing_close():
        raise DocumentSourceError("close failed")

    monkeypatch.setattr(wired.provider, "aclose", failing_close)

    result = await _find(_session(scope.document_id))

    assert result.status is CurationDocumentReferenceStatus.FOUND


def test_the_request_cannot_carry_a_reference():
    with pytest.raises(ValueError):
        CurationDocumentReferenceRequest(identifier="PMID:1", reference_curie="SOURCE:EVIL")


@pytest.mark.asyncio
async def test_a_linked_document_must_be_unlinked_before_linking_again(scope, migrated, wired):
    session_id = _session(scope.document_id)
    await _link(session_id)

    with pytest.raises(HTTPException) as refused:
        await _link(session_id)
    assert refused.value.status_code == 409
    assert isinstance(refused.value.detail, str)

    result = await _unlink(session_id)

    assert result.status is CurationDocumentReferenceStatus.UNLINKED
    document = _document(scope)
    assert document.linked_reference_curie is None
    assert document.linked_reference_provider is None
    assert document.linked_reference_at is None
    with SessionLocal() as db:
        assert get_session_detail(db, session_id, owner_sub=CLAIMS["sub"]).document.reference_curie is None
    assert [entry.action_type for entry in _log_entries(session_id)].count(
        CurationActionType.DOCUMENT_REFERENCE_UNLINKED
    ) == 1

    assert (await _link(session_id)).status is CurationDocumentReferenceStatus.LINKED


@pytest.mark.asyncio
async def test_an_imported_reference_cannot_be_linked_over_or_unlinked(scope, migrated, wired):
    session_id = _session(scope.document_id)
    _set_imported(scope)

    with SessionLocal() as db:
        ref = get_session_detail(db, session_id, owner_sub=CLAIMS["sub"]).document
    assert ref.reference_curie == "SOURCE:IMPORTED"
    assert ref.reference_source is CurationDocumentReferenceSource.IMPORTED

    for action in (_find, _link, _unlink):
        with pytest.raises(HTTPException) as refused:
            await action(session_id)
        assert refused.value.status_code == 409
    assert _document(scope).linked_reference_curie is None
    assert wired.provider.tokens == []


@pytest.mark.asyncio
async def test_unlinking_a_document_with_no_link_is_refused(scope, migrated, wired):
    with pytest.raises(HTTPException) as refused:
        await _unlink(_session(scope.document_id))
    assert refused.value.status_code == 409


@pytest.mark.asyncio
async def test_a_missing_session_is_not_found(migrated, wired):
    for action in (_find, _link, _unlink):
        with pytest.raises(HTTPException) as refused:
            await action(str(uuid4()))
        assert refused.value.status_code == 404


@pytest.mark.asyncio
async def test_endpoints_refuse_when_document_source_import_is_off(
    scope, migrated, wired, monkeypatch
):
    monkeypatch.setattr(api, "external_document_source_import_enabled", lambda: False)
    session_id = _session(scope.document_id)

    for action in (_find, _link, _unlink):
        with pytest.raises(HTTPException) as refused:
            await action(session_id)
        assert refused.value.status_code == 503
    assert wired.provider.tokens == []


def test_linking_does_not_change_a_submission_retry_fingerprint(scope, migrated, wired):
    from src.lib.curation_workspace.session_submission_service import (
        _document_intent_payload,
    )

    with SessionLocal() as db:
        document = db.get(PDFDocument, scope.document_id)
        before = _document_intent_payload(document)
        document.reference_link = DocumentReferenceLink(
            provider="fixture_source", reference_curie=CURIE, linked_at=datetime.now(timezone.utc)
        )
        after = _document_intent_payload(document)
        db.rollback()

    assert before == after
    assert not {"filename", "reference_curie", "reference_source"} & set(before)


def test_deleting_a_document_removes_its_link(scope, migrated):
    from sqlalchemy import text

    with SessionLocal.begin() as db:
        db.add(
            DocumentReferenceLink(
                document_id=scope.document_id,
                provider="fixture_source",
                reference_curie=CURIE,
                linked_at=datetime.now(timezone.utc),
            )
        )
    with SessionLocal.begin() as db:
        db.delete(db.get(PDFDocument, scope.document_id))
    with SessionLocal() as db:
        remaining = db.execute(
            text("SELECT count(*) FROM pdf_document_reference_links WHERE document_id = :id"),
            {"id": scope.document_id},
        ).scalar_one()
    assert remaining == 0
