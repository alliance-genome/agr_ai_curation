"""Adding converted supplements to an existing paper writes real rows (P7c1).

Postgres is real. The persistence image has no Alliance package, so the paper's
file listing is built from core ``SourceArtifact`` records in the shape the
listing returns (the ABC shapes are covered by the unit tests), and the provider
only serves downloads. The search index is a fake that records piece ranges. All
values are made up.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select

from src.lib.document_sources import file_listing_service, supplement_add
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.models import ProviderBearerKind
from src.lib.document_sources.supplement_add import ChosenFile
from src.lib.pdf_jobs import service as pdf_job_service
from src.lib.pdf_jobs.service import ActiveJobExistsError
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper
from src.lib.document_sources.file_assessment import FileCheckLimits
from src.lib.document_sources.file_listing import (
    FileListing,
    ListedFile,
    ListedFileCheck,
    ListedFileCheckStatus,
    ListedFileStatus,
)
from src.lib.document_sources.models import (
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SupplementFileCategory,
)

PREFIX = "p7c1_add_test_"
COMPLETED_AT = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CONTEXT = DocumentSourceRequestContext(
    provider_groups=("group-a",), authorized_group_ids=("group-a",), bearer_kind=ProviderBearerKind.CURATOR,
    curator_token="curator-token",
)
WORKBOOK = b"# Made_up_S20\n\n## Sheet1\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n"
WORKBOOK_2 = b"# Made_up_S40\n\n## Sheet1\n\n| gene | count |\n| --- | --- |\n| g1 | 3 |\n"
WORKBOOKS = {"21": WORKBOOK, "41": WORKBOOK_2}


@pytest.fixture
def paper(monkeypatch, tmp_path):
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    subject = f"p7c1-owner-{uuid4()}"
    document_id = uuid4()
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=subject)
        db.add(new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
            processing_completed_at=COMPLETED_AT, file_path=f"{subject}/{document_id}/made-up.pdf",
            file_hash=document_id.hex * 2, file_size=100, page_count=2, next_chunk_index=30,
            chunk_index_start=0, chunk_index_end=29, chunk_count=30, text_bytes=1_000,
            source_provider="abc_literature", source_provider_reference_id="101",
            source_provider_reference_curie="AGRKB:101", source_provider_source_file_id="10",
        ))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    stored: dict = {}
    monkeypatch.setattr(
        file_listing_service, "_stores",
        lambda _provider: (lambda *key: stored.get(key), lambda *args: stored.__setitem__(args[:3], args[3])),
    )
    limits = FileCheckLimits(
        max_bytes=10_000, few_characters_below=10, row_token_limit=200, count_tokens=len,
        token_encoding="made-up-encoding",
    )
    monkeypatch.setattr(supplement_add, "current_file_check_limits", lambda: limits)
    yield document_id, owner_id, subject, tmp_path
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


class _Index:
    """The search index: stored and deleted piece ranges, per paper."""

    def __init__(self, fail_on: int | None = None):
        self.stored: list[tuple[int, int]] = []
        self.deleted: list[tuple[int, int]] = []
        self.fail_on = fail_on

    def install(self, monkeypatch):
        async def store(chunks, document_id, client, user_id, *, chunk_range):
            if self.fail_on is not None and len(self.stored) == self.fail_on:
                raise RuntimeError("made-up storage failure")
            self.stored.append(chunk_range)

        def delete_range(document_id, user_id, *, start, end, caller_reports_failure=False):
            self.deleted.append((start, end))
            return end - start + 1

        monkeypatch.setattr("src.lib.pipeline.store.store_added_file_pieces", store)
        monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", delete_range)
        monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", AsyncMock())
        monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_: None)
        return self


class _Provider:
    provider_id = "made_up_provider"

    def __init__(self, payloads):
        self.payloads = payloads

    async def download_artifact(self, artifact_id, *, request_bearer_token, max_bytes):
        return self.payloads[artifact_id]


def _artifact(artifact_id, role, artifact_format, *, md5sum, **metadata) -> SourceArtifact:
    return SourceArtifact(
        provider="made_up_provider", artifact_id=artifact_id, role=role, artifact_format=artifact_format,
        status=SourceArtifactStatus.AVAILABLE, display_name=f"Made_up_S{artifact_id}", md5sum=md5sum,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
        metadata=metadata,
    )


def _supplement_entry(original_id: int, in_paper: frozenset[str]) -> ListedFile:
    original = _artifact(
        str(original_id), SourceArtifactRole.SOURCE_SUPPLEMENT, SourceArtifactFormat.UNKNOWN,
        md5sum=f"md5-{original_id}", file_class="supplement", file_extension="xlsx",
    )
    converted = _artifact(
        str(original_id + 1), SourceArtifactRole.CONVERTED_TEXT, SourceArtifactFormat.MARKDOWN,
        md5sum=hashlib.md5(WORKBOOKS[str(original_id + 1)]).hexdigest(),
        file_class="converted_merged_supplement", file_extension="md",
    )
    return ListedFile(
        provider_file_id=original.artifact_id, converted_text_id=converted.artifact_id,
        display_name=original.display_name, file_role="supplement", status=ListedFileStatus.READY,
        check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED), in_this_paper=original.artifact_id in in_paper,
        md5=original.md5sum, converted_md5=converted.md5sum, artifact_format="unknown",
        category=SupplementFileCategory.CONVERTIBLE, single_title_required=True, tickable=True,
        not_tickable_reason=None, last_provider_observation=None, source_artifact=original,
        converted_artifact=converted,
    )


def _provider(payloads):
    return _Provider(payloads)


@pytest.fixture(autouse=True)
def listing(monkeypatch):
    async def list_files_for_paper(document_id, *, owner_user_id, provider, context):
        with SessionLocal() as db:
            in_paper = frozenset(
                row.source_provider_source_file_id
                for row in db.get(PDFDocument, document_id).files
                if row.source_provider_source_file_id and row.status != "failed"
            )
        return FileListing(
            provider="made_up_provider", reference_id="101", reference_curie="MADE:101", main_status="ready",
            files=tuple(_supplement_entry(item, in_paper) for item in (20, 40)), not_shown_count=0,
            duplicate_copies_count=0, check_version="made-up", existing_document_id=None,
        )

    monkeypatch.setattr(file_listing_service, "list_files_for_paper", list_files_for_paper)


def _chosen(original_id: int) -> ChosenFile:
    return ChosenFile(str(original_id), md5=f"md5-{original_id}",
                      converted_md5=hashlib.md5(WORKBOOKS[str(original_id + 1)]).hexdigest())


async def _add(document_id, owner_id, files, payloads, job_id=None):
    return await supplement_add.add_files_to_paper(
        document_id, owner_id, CONTEXT, files, job_id=job_id or uuid4(), provider=_provider(payloads),
        weaviate_client=object(), stage_checkpoint=AsyncMock(),
    )


def _files(document_id):
    with SessionLocal() as db:
        return db.execute(
            select(PDFDocumentFile).where(PDFDocumentFile.document_id == document_id).order_by(PDFDocumentFile.tab_order)
        ).scalars().all()


def _paper_row(document_id):
    with SessionLocal() as db:
        return db.get(PDFDocument, document_id)


@pytest.mark.asyncio
async def test_an_add_writes_ready_rows_after_the_main_file_and_never_touches_the_paper(paper, monkeypatch):
    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)

    result = await _add(document_id, owner_id, [_chosen(20), _chosen(40)], {"21": WORKBOOK, "41": WORKBOOK_2})

    assert [item.outcome for item in result.files] == ["added", "added"]
    main, first, second = _files(document_id)
    assert (main.role, main.chunk_index_start, main.chunk_index_end, main.status) == ("main", 0, 29, "ready")
    for row, tab_order in ((first, 1), (second, 2)):
        assert (row.role, row.status, row.tab_order, row.display_mode, row.page_count) == (
            "supplement", "ready", tab_order, "text", None,
        )
        assert row.source_import_status == "completed" and row.text_bytes > 0
        assert (storage / row.source_markdown_path).is_file() and (storage / row.processed_json_path).is_file()
    assert first.chunk_index_start == 30 and second.chunk_index_start == first.chunk_index_end + 1
    assert index.stored == [(first.chunk_index_start, first.chunk_index_end),
                            (second.chunk_index_start, second.chunk_index_end)]
    stored_paper = _paper_row(document_id)
    assert stored_paper.status == "completed" and stored_paper.processing_completed_at == COMPLETED_AT
    assert stored_paper.next_chunk_index == second.chunk_index_end + 1


@pytest.mark.asyncio
async def test_a_write_failure_leaves_the_paper_and_its_earlier_files_unchanged(paper, monkeypatch):
    document_id, owner_id, _, storage = paper
    index = _Index(fail_on=1).install(monkeypatch)

    with pytest.raises(RuntimeError, match="made-up storage failure"):
        await _add(document_id, owner_id, [_chosen(20), _chosen(40)], {"21": WORKBOOK, "41": WORKBOOK_2})

    (main,) = _files(document_id)
    assert (main.chunk_index_start, main.chunk_index_end) == (0, 29)
    assert all(start >= 30 for start, _ in index.deleted) and len(index.deleted) == 2
    assert _paper_row(document_id).status == "completed"
    assert not list(storage.rglob("*.md")) and not list(storage.rglob("*.json"))


@pytest.mark.asyncio
async def test_the_same_supplement_may_be_added_to_two_papers(paper, monkeypatch):
    document_id, owner_id, subject, _ = paper
    _Index().install(monkeypatch)
    other_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(new_paper(
            id=other_id, user_id=owner_id, filename=f"{PREFIX}{other_id}.pdf", status="completed",
            file_path=f"{subject}/{other_id}/made-up.pdf", file_hash=other_id.hex * 2, file_size=100,
            page_count=2, next_chunk_index=5, chunk_index_start=0, chunk_index_end=4, chunk_count=5,
            text_bytes=500, source_provider="abc_literature", source_provider_reference_id="101",
            source_provider_reference_curie="AGRKB:101", source_provider_source_file_id="10",
        ))

    for target in (document_id, other_id):
        result = await _add(target, owner_id, [_chosen(20)], {"21": WORKBOOK})
        assert result.files[0].outcome == "added"
    assert _files(document_id)[1].file_hash == _files(other_id)[1].file_hash


def test_two_adds_on_one_paper_give_one_job(paper):
    document_id, owner_id, _, _ = paper
    first = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20)])
    with pytest.raises(ActiveJobExistsError):
        supplement_add.start_supplement_add(document_id, owner_id, [_chosen(40)])
    assert first.status == PdfJobStatus.PENDING.value


def test_too_many_files_for_the_paper_is_refused_before_any_job(paper, monkeypatch):
    document_id, owner_id, _, _ = paper
    monkeypatch.setenv("DOCUMENT_MAX_FILES_PER_PAPER", "2")
    with pytest.raises(supplement_add.SupplementAddRequestError) as raised:
        supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20), _chosen(40)])
    assert (raised.value.code, raised.value.limit_value) == ("paper_file_limit", 2)
    with SessionLocal() as db:
        assert db.scalar(select(PdfProcessingJob.id).where(PdfProcessingJob.document_id == document_id)) is None


@pytest.mark.asyncio
async def test_a_stale_add_fails_its_rows_not_the_paper_and_the_next_add_clears_them(paper, monkeypatch):
    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    job = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20)])
    # The backend died mid-add: a pending row holds a reserved range and some pieces.
    dead_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocumentFile(
            id=dead_id, document_id=document_id, role="supplement", display_name="Made_up_S20.xlsx",
            status="pending", tab_order=1, display_mode="text", file_path=f"{subject}/source_markdown/{dead_id}.md",
            file_hash=dead_id.hex * 2, file_size=10, source_provider_source_file_id="20",
            source_markdown_path=f"{subject}/source_markdown/{dead_id}.md",
            chunk_index_start=30, chunk_index_end=31, chunk_count=2, added_at=datetime.now(timezone.utc),
        ))
        db.get(PDFDocument, document_id).next_chunk_index = 32
        stale = db.get(PdfProcessingJob, UUID(job.job_id))
        stale.status = PdfJobStatus.RUNNING.value
        stale.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)

    with SessionLocal.begin() as db:
        stale = db.get(PdfProcessingJob, UUID(job.job_id))
        pdf_job_service._reconcile_stale_job(
            db, stale, stale_after_seconds=7200, now=datetime.now(timezone.utc),
        )

    rows = {row.id: row for row in _files(document_id)}
    assert rows[dead_id].status == "failed"
    stored_paper = _paper_row(document_id)
    assert stored_paper.status == "completed" and stored_paper.processing_completed_at == COMPLETED_AT

    result = await _add(document_id, owner_id, [_chosen(20)], {"21": WORKBOOK})
    assert result.files[0].outcome == "added"
    assert index.deleted == [(30, 31)]
    remaining = _files(document_id)
    assert dead_id not in {row.id for row in remaining}
    assert remaining[-1].chunk_index_start == 32  # piece numbers are never reused


def test_an_add_racing_the_stale_sweep_never_fails_the_paper(paper):
    """An old failed job and a new add: recovery of the old job leaves the finished paper alone."""

    document_id, owner_id, _, _ = paper
    old = pdf_job_service.create_job(document_id=document_id, user_id=owner_id)
    pdf_job_service.mark_failed(job_id=old.job_id, message="made-up failure", stage="failed")
    supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20)])
    with SessionLocal.begin() as db:
        old_job = db.get(PdfProcessingJob, UUID(old.job_id))
        pdf_job_service._reconcile_terminal_document(db, old_job)
    assert _paper_row(document_id).status == "completed"


def _bound_listing(*original_ids: int) -> FileListing:
    """A prebuilt FileListing bound to the real Postgres paper's provider and reference."""
    entries = []
    for original_id in original_ids:
        original = SourceArtifact(
            provider="abc_literature", artifact_id=str(original_id),
            role=SourceArtifactRole.SOURCE_SUPPLEMENT, artifact_format=SourceArtifactFormat.UNKNOWN,
            status=SourceArtifactStatus.AVAILABLE, reference_id="101", reference_curie="AGRKB:101",
            display_name=f"Made_up_S{original_id}", md5sum=f"md5-{original_id}",
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
            metadata={"file_class": "supplement", "file_extension": "xlsx"},
        )
        converted = SourceArtifact(
            provider="abc_literature", artifact_id=str(original_id + 1),
            role=SourceArtifactRole.CONVERTED_TEXT, artifact_format=SourceArtifactFormat.MARKDOWN,
            status=SourceArtifactStatus.AVAILABLE, reference_id="101", reference_curie="AGRKB:101",
            parent_artifact_id=str(original_id), display_name=f"Made_up_S{original_id}_xlsx",
            md5sum=hashlib.md5(WORKBOOKS[str(original_id + 1)]).hexdigest(),
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
            metadata={"file_class": "converted_merged_supplement", "file_extension": "md"},
        )
        entries.append(ListedFile(
            provider_file_id=original.artifact_id, converted_text_id=converted.artifact_id,
            display_name=original.display_name, file_role="supplement", status=ListedFileStatus.READY,
            check=ListedFileCheck(ListedFileCheckStatus.PASSED), in_this_paper=False,
            md5=original.md5sum, converted_md5=converted.md5sum, artifact_format="unknown",
            category=SupplementFileCategory.CONVERTIBLE, single_title_required=True, tickable=True,
            not_tickable_reason=None, last_provider_observation=None, source_artifact=original,
            converted_artifact=converted,
        ))
    return FileListing(
        provider="abc_literature", reference_id="101", reference_curie="AGRKB:101", main_status="ready",
        files=tuple(entries), not_shown_count=0, duplicate_copies_count=0, check_version="made-up",
        existing_document_id=None,
    )


class _AbcBoundProvider(_Provider):
    provider_id = "abc_literature"


@pytest.mark.asyncio
async def test_prebuilt_add_handles_concurrent_duplicate_insert_without_failing_the_batch(paper, monkeypatch):
    """ALL-1751 review: a concurrent active row inserted before _insert_rows yields
    already_in_paper for that file while the rest of the batch still writes cleanly."""
    from src.lib.document_sources.file_reasons import FileReason

    document_id, owner_id, subject, _ = paper
    _Index().install(monkeypatch)
    raced = False

    async def _race_before_insert(stage: str) -> None:
        nonlocal raced
        if stage == "saving_files" and not raced:
            raced = True
            concurrent_id = uuid4()
            with SessionLocal.begin() as db:
                db.add(PDFDocumentFile(
                    id=concurrent_id, document_id=document_id, role="supplement",
                    display_name="Made_up_S20.xlsx", status="ready", tab_order=1,
                    display_mode="text", file_path=f"{subject}/source_markdown/{concurrent_id}.md",
                    file_hash=concurrent_id.hex * 2, file_size=10,
                    source_provider_source_file_id="20",
                    source_markdown_path=f"{subject}/source_markdown/{concurrent_id}.md",
                    chunk_index_start=30, chunk_index_end=31, chunk_count=2, text_bytes=10,
                    added_at=datetime.now(timezone.utc),
                ))
                db.get(PDFDocument, document_id).next_chunk_index = 32

    result = await supplement_add.add_files_to_paper_with_listing(
        document_id, owner_id, CONTEXT, [_chosen(20), _chosen(40)],
        listing=_bound_listing(20, 40), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
        weaviate_client=object(), stage_checkpoint=_race_before_insert,
    )

    outcomes = {item.provider_file_id: item for item in result.files}
    assert outcomes["20"].outcome == "left_out" and outcomes["20"].reason_code is FileReason.ALREADY_IN_PAPER
    assert outcomes["40"].outcome == "added"
    rows = _files(document_id)
    assert [(row.source_provider_source_file_id, row.status, row.tab_order) for row in rows] == [
        ("10", "ready", 0), ("20", "ready", 1), ("40", "ready", 2),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_start", "before_insert"])
async def test_prebuilt_add_refuses_retained_removed_row_with_named_error(paper, monkeypatch, when):
    """ALL-1751 review: a retained removed row raises FileRemovalUnfinished instead of an
    unhandled Postgres unique-constraint error, whether present upfront or raced before insert."""
    document_id, owner_id, subject, storage = paper
    _Index().install(monkeypatch)

    def _insert_removed_row() -> None:
        removed_id = uuid4()
        with SessionLocal.begin() as db:
            db.add(PDFDocumentFile(
                id=removed_id, document_id=document_id, role="supplement",
                display_name="Made_up_S20.xlsx", status="removed", tab_order=1,
                display_mode="text", file_path=f"{subject}/source_markdown/{removed_id}.md",
                file_hash=removed_id.hex * 2, file_size=10,
                source_provider_source_file_id="20",
                source_markdown_path=f"{subject}/source_markdown/{removed_id}.md",
                added_at=datetime.now(timezone.utc),
            ))

    if when == "before_start":
        _insert_removed_row()

    raced = False

    async def _checkpoint(stage: str) -> None:
        nonlocal raced
        if when == "before_insert" and stage == "saving_files" and not raced:
            raced = True
            _insert_removed_row()

    with pytest.raises(supplement_add.FileRemovalUnfinished) as raised:
        await supplement_add.add_files_to_paper_with_listing(
            document_id, owner_id, CONTEXT, [_chosen(20), _chosen(40)],
            listing=_bound_listing(20, 40), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
            weaviate_client=object(), stage_checkpoint=_checkpoint,
        )

    assert raised.value.code == "file_removal_unfinished"
    assert [(row.source_provider_source_file_id, row.status) for row in _files(document_id)] == [
        ("10", "ready"), ("20", "removed"),
    ]
    assert not list(storage.rglob("*.md")) and not list(storage.rglob("*.json"))


@pytest.mark.asyncio
async def test_failed_leftover_is_excluded_from_listing_membership_and_replaced_on_add(paper, monkeypatch):
    """ALL-1751 review: a failed supplement leftover row is excluded from in_this_paper by
    _paper_lookup so reusing the listing cleans up the leftover and adds the file cleanly
    instead of falsely rejecting it as already_in_paper."""
    from src.lib.document_sources.models import SourceReference

    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", supplement_add.current_file_check_limits)

    failed_id = uuid4()
    leftover_rel = f"{subject}/source_markdown/{failed_id}.md"
    leftover_path = storage / leftover_rel
    leftover_path.parent.mkdir(parents=True, exist_ok=True)
    leftover_path.write_text("# stale failed attempt\n", encoding="utf-8")
    with SessionLocal.begin() as db:
        db.add(PDFDocumentFile(
            id=failed_id, document_id=document_id, role="supplement",
            display_name="Made_up_S20.xlsx", status="failed", tab_order=1,
            display_mode="text", file_path=leftover_rel,
            file_hash=failed_id.hex * 2, file_size=22,
            source_provider_source_file_id="20",
            source_markdown_path=leftover_rel,
            added_at=datetime.now(timezone.utc),
        ))

    class _ListingProvider(_AbcBoundProvider):
        def is_reference_source_artifact(self, artifact: SourceArtifact) -> bool:
            return artifact.artifact_id == "10"

        def is_main_text_artifact(self, artifact: SourceArtifact) -> bool:
            return artifact.artifact_id == "11"

        def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
            return (0,)

        def supplement_file_category(self, artifact: SourceArtifact) -> SupplementFileCategory | None:
            return SupplementFileCategory.CONVERTIBLE if artifact.role is SourceArtifactRole.SOURCE_SUPPLEMENT else None

        def converted_text_has_single_title(self, original: SourceArtifact) -> bool:
            return True

        def is_listed_converted_supplement_text(self, artifact: SourceArtifact) -> bool:
            return artifact.role is SourceArtifactRole.CONVERTED_TEXT and artifact.parent_artifact_id != "10"

        def supplement_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
            return (0,)

    main_pdf = SourceArtifact(
        provider="abc_literature", artifact_id="10",
        role=SourceArtifactRole.SOURCE_PDF, artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE, reference_id="101", reference_curie="AGRKB:101",
        display_name="Made_up_main", md5sum="md5-10",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
    )
    main_text = SourceArtifact(
        provider="abc_literature", artifact_id="11",
        role=SourceArtifactRole.CONVERTED_TEXT, artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE, reference_id="101", reference_curie="AGRKB:101",
        parent_artifact_id="10", display_name="Made_up_main_md", md5sum="md5-11",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
    )
    listed_20 = _bound_listing(20).files[0]
    assert listed_20.converted_artifact is not None
    reference = SourceReference(provider="abc_literature", reference_id="101", reference_curie="AGRKB:101")
    provider = _ListingProvider(WORKBOOKS)

    reused_listing = await file_listing_service.list_files_for_paper_from_artifacts(
        document_id, owner_user_id=owner_id, provider=provider, context=CONTEXT,
        reference=reference,
        artifacts=(main_pdf, main_text, listed_20.source_artifact, listed_20.converted_artifact),
    )
    by_id = {item.provider_file_id: item for item in reused_listing.files}
    assert by_id["10"].in_this_paper is True
    assert by_id["20"].in_this_paper is False
    assert by_id["20"].tickable is True

    result = await supplement_add.add_files_to_paper_with_listing(
        document_id, owner_id, CONTEXT, [_chosen(20)],
        listing=reused_listing, job_id=uuid4(), provider=provider,
        weaviate_client=object(), stage_checkpoint=AsyncMock(),
    )

    (outcome,) = result.files
    assert outcome.provider_file_id == "20"
    assert outcome.outcome == "added"
    assert outcome.file_id is not None and outcome.file_id != failed_id
    assert not leftover_path.exists()
    assert [(row.source_provider_source_file_id, row.status) for row in _files(document_id)] == [
        ("10", "ready"), ("20", "ready"),
    ]
    assert len(index.stored) == 1


@pytest.mark.asyncio
async def test_prebuilt_add_enforces_paper_file_limit_under_insert_lock(paper, monkeypatch):
    """ALL-1751 review: a concurrent distinct file insert reaching DOCUMENT_MAX_FILES_PER_PAPER
    before _insert_rows locks the paper prevents over-limit files from being inserted, returning
    paper_file_limit with zero persisted rows, disk files, or search chunks for the over-limit file."""
    from src.lib.document_sources.file_reasons import FileReason

    monkeypatch.setenv("DOCUMENT_MAX_FILES_PER_PAPER", "3")
    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    raced = False

    async def _race_distinct_file_before_insert(stage: str) -> None:
        nonlocal raced
        if stage == "saving_files" and not raced:
            raced = True
            concurrent_id = uuid4()
            with SessionLocal.begin() as db:
                db.add(PDFDocumentFile(
                    id=concurrent_id, document_id=document_id, role="supplement",
                    display_name="Made_up_S99.xlsx", status="ready", tab_order=1,
                    display_mode="text", file_path=f"{subject}/source_markdown/{concurrent_id}.md",
                    file_hash=concurrent_id.hex * 2, file_size=10,
                    source_provider_source_file_id="99",
                    source_markdown_path=f"{subject}/source_markdown/{concurrent_id}.md",
                    chunk_index_start=30, chunk_index_end=31, chunk_count=2, text_bytes=10,
                    added_at=datetime.now(timezone.utc),
                ))
                db.get(PDFDocument, document_id).next_chunk_index = 32

    result = await supplement_add.add_files_to_paper_with_listing(
        document_id, owner_id, CONTEXT, [_chosen(20), _chosen(40)],
        listing=_bound_listing(20, 40), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
        weaviate_client=object(), stage_checkpoint=_race_distinct_file_before_insert,
    )

    outcomes = {item.provider_file_id: item for item in result.files}
    assert outcomes["20"].outcome == "added" and outcomes["20"].file_id is not None
    assert outcomes["40"].outcome == "left_out"
    assert outcomes["40"].reason_code is FileReason.PAPER_FILE_LIMIT
    assert outcomes["40"].measured_value == 4
    assert outcomes["40"].limit_value == 3
    assert [(row.source_provider_source_file_id, row.status, row.tab_order) for row in _files(document_id)] == [
        ("10", "ready", 0), ("99", "ready", 1), ("20", "ready", 2),
    ]
    assert [path.name for path in storage.rglob("*.md")] == [f"{outcomes['20'].file_id}.md"]
    assert [path.name for path in storage.rglob("*.json")] == [f"{outcomes['20'].file_id}.json"]
    assert len(index.stored) == 1
