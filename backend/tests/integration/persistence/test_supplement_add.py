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


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_start", "before_insert"])
@pytest.mark.parametrize("concurrent_status", ["ready", "removed"])
async def test_prebuilt_add_matches_duplicate_copy_against_live_and_removed_rows(
    paper, monkeypatch, when, concurrent_status,
):
    """ALL-1751 review: when a listing collapses duplicate copies (40, 50) onto representative 40,
    a concurrent ready row for copy 50 rejects representative 40 as already_in_paper (never adding
    both copies), and a retained removed row for copy 50 raises FileRemovalUnfinished."""
    from src.lib.document_sources.file_reasons import FileReason
    from src.lib.document_sources.models import SourceReference

    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", supplement_add.current_file_check_limits)

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
    copy_40_entry = _bound_listing(40).files[0]
    assert copy_40_entry.converted_artifact is not None
    # Copy 50 has the exact same original MD5 as representative 40 so listing collapses them.
    copy_50_original = SourceArtifact(
        provider="abc_literature", artifact_id="50",
        role=SourceArtifactRole.SOURCE_SUPPLEMENT, artifact_format=SourceArtifactFormat.UNKNOWN,
        status=SourceArtifactStatus.AVAILABLE, reference_id="101", reference_curie="AGRKB:101",
        display_name="Made_up_S50", md5sum=copy_40_entry.source_artifact.md5sum,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
        metadata={"file_class": "supplement", "file_extension": "xlsx"},
    )
    copy_50_converted = SourceArtifact(
        provider="abc_literature", artifact_id="51",
        role=SourceArtifactRole.CONVERTED_TEXT, artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE, reference_id="101", reference_curie="AGRKB:101",
        parent_artifact_id="50", display_name="Made_up_S50_xlsx",
        md5sum=copy_40_entry.converted_artifact.md5sum,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("group-a",)),
        metadata={"file_class": "converted_merged_supplement", "file_extension": "md"},
    )
    reference = SourceReference(provider="abc_literature", reference_id="101", reference_curie="AGRKB:101")
    provider = _ListingProvider({**WORKBOOKS, "51": WORKBOOK_2})

    reused_listing = await file_listing_service.list_files_for_paper_from_artifacts(
        document_id, owner_user_id=owner_id, provider=provider, context=CONTEXT,
        reference=reference,
        artifacts=(
            main_pdf, main_text,
            copy_40_entry.source_artifact, copy_40_entry.converted_artifact,
            copy_50_original, copy_50_converted,
        ),
    )
    by_id = {item.provider_file_id: item for item in reused_listing.files}
    assert by_id["40"].copy_provider_file_ids == ("40", "50")
    assert "50" not in by_id

    def _insert_copy_50() -> None:
        concurrent_id = uuid4()
        with SessionLocal.begin() as db:
            db.add(PDFDocumentFile(
                id=concurrent_id, document_id=document_id, role="supplement",
                display_name="Made_up_S50.xlsx", status=concurrent_status, tab_order=1,
                display_mode="text", file_path=f"{subject}/source_markdown/{concurrent_id}.md",
                file_hash=concurrent_id.hex * 2, file_size=10,
                source_provider_source_file_id="50",
                source_markdown_path=f"{subject}/source_markdown/{concurrent_id}.md",
                chunk_index_start=30 if concurrent_status == "ready" else None,
                chunk_index_end=31 if concurrent_status == "ready" else None,
                chunk_count=2 if concurrent_status == "ready" else None,
                text_bytes=10 if concurrent_status == "ready" else None,
                added_at=datetime.now(timezone.utc),
            ))
            if concurrent_status == "ready":
                db.get(PDFDocument, document_id).next_chunk_index = 32

    if when == "before_start":
        _insert_copy_50()

    raced = False

    async def _checkpoint(stage: str) -> None:
        nonlocal raced
        if when == "before_insert" and stage == "saving_files" and not raced:
            raced = True
            _insert_copy_50()

    if concurrent_status == "removed":
        with pytest.raises(supplement_add.FileRemovalUnfinished):
            await supplement_add.add_files_to_paper_with_listing(
                document_id, owner_id, CONTEXT, [_chosen(40)],
                listing=reused_listing, job_id=uuid4(), provider=provider,
                weaviate_client=object(), stage_checkpoint=_checkpoint,
            )
    else:
        result = await supplement_add.add_files_to_paper_with_listing(
            document_id, owner_id, CONTEXT, [_chosen(40)],
            listing=reused_listing, job_id=uuid4(), provider=provider,
            weaviate_client=object(), stage_checkpoint=_checkpoint,
        )
        (outcome,) = result.files
        assert outcome.provider_file_id == "40"
        assert outcome.outcome == "left_out"
        assert outcome.reason_code is FileReason.ALREADY_IN_PAPER

    assert [(row.source_provider_source_file_id, row.status) for row in _files(document_id)] == [
        ("10", "ready"), ("50", concurrent_status),
    ]
    assert not list(storage.rglob("*.md")) and not list(storage.rglob("*.json"))
    assert index.stored == []


@pytest.mark.asyncio
async def test_manual_add_refuses_duplicate_copy_with_retained_removed_row_even_when_representative_fails_checksum(
    paper, monkeypatch,
):
    """ALL-1751 review: in manual add_files_to_paper, when duplicate copy 50 has a retained
    removed row, choosing representative 40 (even with a mismatched checksum) plus valid file 20
    raises FileRemovalUnfinished upfront so file 20 is NOT committed."""
    from dataclasses import replace

    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)

    removed_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocumentFile(
            id=removed_id, document_id=document_id, role="supplement",
            display_name="Made_up_S50.xlsx", status="removed", tab_order=1,
            display_mode="text", file_path=f"{subject}/source_markdown/{removed_id}.md",
            file_hash=removed_id.hex * 2, file_size=10,
            source_provider_source_file_id="50",
            source_markdown_path=f"{subject}/source_markdown/{removed_id}.md",
            added_at=datetime.now(timezone.utc),
        ))

    listing = _bound_listing(20, 40)
    listed_20, listed_40 = listing.files
    listing_with_copy = replace(
        listing,
        files=(listed_20, replace(listed_40, copy_provider_file_ids=("40", "50"))),
    )

    async def _list_with_copy(*_args, **_kwargs):
        return listing_with_copy

    monkeypatch.setattr(file_listing_service, "list_files_for_paper", _list_with_copy)

    stale_40 = ChosenFile("40", md5="md5-40", converted_md5="0" * 32)
    with pytest.raises(supplement_add.FileRemovalUnfinished):
        await supplement_add.add_files_to_paper(
            document_id, owner_id, CONTEXT, [stale_40, _chosen(20)],
            job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
            weaviate_client=object(), stage_checkpoint=AsyncMock(),
        )

    assert [(row.source_provider_source_file_id, row.status) for row in _files(document_id)] == [
        ("10", "ready"), ("50", "removed"),
    ]
    assert not list(storage.rglob("*.md")) and not list(storage.rglob("*.json"))
    assert index.stored == []


@pytest.mark.asyncio
async def test_prebuilt_add_enforces_paper_text_limit_under_insert_lock(paper, monkeypatch):
    """ALL-1751 review: a concurrent ready file inserted before _insert_rows locks the paper
    consumes part of DOCUMENT_PAPER_MAX_TEXT_BYTES so the locked check rejects the over-budget
    file with paper_text_limit and persists zero rows, files, or chunks for it."""
    from src.lib.document_sources.file_reasons import FileReason

    # Paper main file has text_bytes=1000. Each prepared workbook is small (<200 bytes).
    # Allow 1600 bytes initially so both 20 and 40 pass _check_and_write (1000 + ~100 + ~100 <= 1600),
    # then race a concurrent ready row consuming 500 bytes (1000 + 500 = 1500) so only 20 fits!
    monkeypatch.setenv("DOCUMENT_PAPER_MAX_TEXT_BYTES", "1600")
    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    raced = False

    async def _race_ready_bytes_before_insert(stage: str) -> None:
        nonlocal raced
        if stage == "saving_files" and not raced:
            raced = True
            concurrent_id = uuid4()
            with SessionLocal.begin() as db:
                db.add(PDFDocumentFile(
                    id=concurrent_id, document_id=document_id, role="supplement",
                    display_name="Made_up_S99.xlsx", status="ready", tab_order=1,
                    display_mode="text", file_path=f"{subject}/source_markdown/{concurrent_id}.md",
                    file_hash=concurrent_id.hex * 2, file_size=500,
                    source_provider_source_file_id="99",
                    source_markdown_path=f"{subject}/source_markdown/{concurrent_id}.md",
                    chunk_index_start=30, chunk_index_end=31, chunk_count=2, text_bytes=500,
                    added_at=datetime.now(timezone.utc),
                ))
                db.get(PDFDocument, document_id).next_chunk_index = 32

    result = await supplement_add.add_files_to_paper_with_listing(
        document_id, owner_id, CONTEXT, [_chosen(20), _chosen(40)],
        listing=_bound_listing(20, 40), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
        weaviate_client=object(), stage_checkpoint=_race_ready_bytes_before_insert,
    )

    outcomes = {item.provider_file_id: item for item in result.files}
    assert outcomes["20"].outcome == "added" and outcomes["20"].file_id is not None
    assert outcomes["40"].outcome == "left_out"
    assert outcomes["40"].reason_code is FileReason.PAPER_TEXT_LIMIT
    assert outcomes["40"].limit_value == 1600
    assert outcomes["40"].measured_value is not None and outcomes["40"].measured_value > 1600
    assert [(row.source_provider_source_file_id, row.status, row.tab_order) for row in _files(document_id)] == [
        ("10", "ready", 0), ("99", "ready", 1), ("20", "ready", 2),
    ]
    assert [path.name for path in storage.rglob("*.md")] == [f"{outcomes['20'].file_id}.md"]
    assert [path.name for path in storage.rglob("*.json")] == [f"{outcomes['20'].file_id}.json"]
    assert len(index.stored) == 1


@pytest.mark.asyncio
async def test_overlapping_pending_writers_cannot_exceed_paper_text_limit(paper, monkeypatch):
    """ALL-1751 review: writer A reserves its measured text_bytes on its newly inserted
    pending row under _insert_rows lock, so an overlapping writer B running before writer A
    commits mark_files_ready cannot spend the same remaining DOCUMENT_PAPER_MAX_TEXT_BYTES."""
    import asyncio

    from src.lib.document_sources.file_reasons import FileReason

    # Main file uses 1000 bytes; each workbook prepares to ~55-70 bytes (so one fits in 100 bytes
    # of headroom while both together exceed 100 bytes). Setting limit=1100 lets writer A's 20 fit
    # while writer B's 40 exceeds the limit once writer A reserves its pending row's text_bytes.
    monkeypatch.setenv("DOCUMENT_PAPER_MAX_TEXT_BYTES", "1100")
    document_id, owner_id, _subject, storage = paper
    index = _Index().install(monkeypatch)

    writer_a_inserted = asyncio.Event()
    writer_b_finished = asyncio.Event()
    writer_b_prepared = asyncio.Event()

    async def _writer_a_checkpoint(stage: str) -> None:
        if stage == "saving_files":
            # Let writer B read _paper_state and prepare file 40 BEFORE writer A inserts its pending row,
            # so both writers pass _check_and_write and race into _insert_rows.
            await asyncio.wait_for(writer_b_prepared.wait(), timeout=10)
        elif stage == "vector_storage":
            # Writer A has inserted its pending row and released the paper lock; pause before commit.
            writer_a_inserted.set()
            await asyncio.wait_for(writer_b_finished.wait(), timeout=10)

    async def _writer_b_checkpoint(stage: str) -> None:
        if stage == "saving_files":
            writer_b_prepared.set()
            await asyncio.wait_for(writer_a_inserted.wait(), timeout=10)

    async def _run_writer_b():
        try:
            return await supplement_add.add_files_to_paper_with_listing(
                document_id, owner_id, CONTEXT, [_chosen(40)],
                listing=_bound_listing(40), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
                weaviate_client=object(), stage_checkpoint=_writer_b_checkpoint,
            )
        finally:
            writer_b_finished.set()

    result_a, result_b = await asyncio.gather(
        supplement_add.add_files_to_paper_with_listing(
            document_id, owner_id, CONTEXT, [_chosen(20)],
            listing=_bound_listing(20), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
            weaviate_client=object(), stage_checkpoint=_writer_a_checkpoint,
        ),
        _run_writer_b(),
    )

    (outcome_a,) = result_a.files
    (outcome_b,) = result_b.files
    assert outcome_a.outcome == "added" and outcome_a.file_id is not None
    assert outcome_b.outcome == "left_out"
    assert outcome_b.reason_code is FileReason.PAPER_TEXT_LIMIT
    assert outcome_b.limit_value == 1100
    assert outcome_b.measured_value is not None and outcome_b.measured_value > 1100
    assert [(row.source_provider_source_file_id, row.status) for row in _files(document_id)] == [
        ("10", "ready"), ("20", "ready"),
    ]
    assert [path.name for path in storage.rglob("*.md")] == [f"{outcome_a.file_id}.md"]
    assert [path.name for path in storage.rglob("*.json")] == [f"{outcome_a.file_id}.json"]
    assert len(index.stored) == 1


class _ClosableProvider(_Provider):
    def __init__(self, payloads):
        super().__init__(payloads)
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def _fresh_job(job_id: str | UUID) -> PdfProcessingJob:
    with SessionLocal() as db:
        return db.get(PdfProcessingJob, UUID(str(job_id)))


@pytest.mark.asyncio
async def test_run_supplement_add_job_commits_ready_files_and_completed_job_atomically(paper, monkeypatch):
    from unittest.mock import Mock

    document_id, owner_id, subject, storage = paper
    _Index().install(monkeypatch)
    job = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20), _chosen(40)])
    provider = _ClosableProvider({"21": WORKBOOK, "41": WORKBOOK_2})
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    second_mark_completed = Mock(side_effect=AssertionError("mark_completed must not issue a second DB transaction"))
    monkeypatch.setattr(pdf_job_service, "mark_completed", second_mark_completed)

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=UUID(job.job_id),
        document_id=document_id,
        user_id=subject,
        owner_user_id=owner_id,
        context=CONTEXT,
        files=(_chosen(20), _chosen(40)),
    ))

    second_mark_completed.assert_not_called()
    assert provider.closed == 1
    fresh_job = _fresh_job(job.job_id)
    assert fresh_job.status == PdfJobStatus.COMPLETED.value
    assert fresh_job.current_stage == "completed"
    assert fresh_job.progress_percentage == 100
    assert fresh_job.message == "Added 2 of 2 files."
    assert fresh_job.completed_at is not None
    assert [entry["state"] for entry in fresh_job.metadata_json["supplements"]] == ["added", "added"]

    main, first, second = _files(document_id)
    assert main.role == "main" and main.status == "ready"
    assert first.status == second.status == "ready"
    assert first.source_import_status == second.source_import_status == "completed"
    assert (storage / first.source_markdown_path).is_file() and (storage / second.processed_json_path).is_file()
    assert [entry["file_id"] for entry in fresh_job.metadata_json["supplements"]] == [str(first.id), str(second.id)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["commit_error", "cancel_requested_before_ready", "already_failed", "already_cancelled"],
)
async def test_run_supplement_add_job_rolls_back_job_and_files_on_ready_failure_cancel_or_terminal_job(
    paper, monkeypatch, case,
):
    from unittest.mock import Mock

    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from src.lib.document_files import service as file_service

    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    job = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20)])
    provider = _ClosableProvider({"21": WORKBOOK})
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    real_mark_files_ready = file_service.mark_files_ready
    fired = False

    def raced_mark_files_ready(sizes, *, on_ready_in_session=None):
        nonlocal fired
        if case == "cancel_requested_before_ready":
            pdf_job_service.request_cancel(job_id=job.job_id, user_id=owner_id)
            return real_mark_files_ready(sizes, on_ready_in_session=on_ready_in_session)
        if case == "already_failed":
            pdf_job_service.mark_failed(job_id=job.job_id, message="stale failure", stage="failed")
            return real_mark_files_ready(sizes, on_ready_in_session=on_ready_in_session)
        if case == "already_cancelled":
            pdf_job_service.mark_cancelled(job_id=job.job_id, reason="already cancelled")
            return real_mark_files_ready(sizes, on_ready_in_session=on_ready_in_session)

        def fail_on_commit(_session):
            nonlocal fired
            if not fired:
                fired = True
                raise OperationalError(
                    "COMMIT",
                    {"filename": "Secret_Commit_Rollback.pdf"},
                    RuntimeError("simulated commit failure"),
                )

        event.listen(Session, "before_commit", fail_on_commit)
        try:
            return real_mark_files_ready(sizes, on_ready_in_session=on_ready_in_session)
        finally:
            event.remove(Session, "before_commit", fail_on_commit)

    monkeypatch.setattr(file_service, "mark_files_ready", raced_mark_files_ready)

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=UUID(job.job_id),
        document_id=document_id,
        user_id=subject,
        owner_user_id=owner_id,
        context=CONTEXT,
        files=(_chosen(20),),
    ))

    assert provider.closed == 1
    (main,) = _files(document_id)
    assert main.role == "main" and main.status == "ready"
    assert len(index.deleted) == 1 and index.deleted[0][0] >= 30
    assert not list(storage.rglob("*.md")) and not list(storage.rglob("*.json"))
    assert _paper_row(document_id).status == "completed"

    fresh_job = _fresh_job(job.job_id)
    if case == "commit_error":
        assert fresh_job.status == PdfJobStatus.FAILED.value
        assert fresh_job.metadata_json["error_code"] == "add_failed"
        report.assert_called_once()
        assert report.call_args.kwargs["operation"] == "supplement_add_failed"
        assert "Secret_Commit_Rollback.pdf" not in str(report.call_args)
    elif case == "cancel_requested_before_ready":
        assert fresh_job.status == PdfJobStatus.CANCELLED.value
        assert fresh_job.cancel_requested is True
        report.assert_not_called()
    elif case == "already_failed":
        assert fresh_job.status == PdfJobStatus.FAILED.value
        assert fresh_job.error_message == "stale failure"
        report.assert_called_once()
        assert report.call_args.kwargs["context"] == {"exception_type": "DocumentFileError"}
    else:
        assert fresh_job.status == PdfJobStatus.CANCELLED.value
        report.assert_not_called()


@pytest.mark.asyncio
async def test_run_supplement_add_job_deadline_during_blocked_ready_commit_preserves_ready_files_and_completed_job(
    paper, monkeypatch,
):
    import asyncio
    import threading
    from unittest.mock import Mock

    from src.lib.document_files import service as file_service

    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)
    job = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20)])
    provider = _ClosableProvider({"21": WORKBOOK})
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 60.0)
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    captured_timeouts: list[asyncio.Timeout] = []
    real_timeout = asyncio.timeout

    def spy_timeout(delay: float | None) -> asyncio.Timeout:
        timeout_cm = real_timeout(delay)
        captured_timeouts.append(timeout_cm)
        return timeout_cm

    monkeypatch.setattr(asyncio, "timeout", spy_timeout)

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    real_mark_files_ready = file_service.mark_files_ready

    def blocked_mark_files_ready(sizes, *, on_ready_in_session=None):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        return real_mark_files_ready(sizes, on_ready_in_session=on_ready_in_session)

    monkeypatch.setattr(file_service, "mark_files_ready", blocked_mark_files_ready)

    task = asyncio.create_task(supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=UUID(job.job_id),
        document_id=document_id,
        user_id=subject,
        owner_user_id=owner_id,
        context=CONTEXT,
        files=(_chosen(20),),
    )))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert len(captured_timeouts) == 1
        # Fire the real job deadline deterministically while mark_files_ready is blocked in its worker thread.
        captured_timeouts[0].reschedule(loop.time())
        deadline_wait = loop.time() + 5.0
        while not captured_timeouts[0].expired():
            assert loop.time() < deadline_wait, "Job deadline did not expire while mark_files_ready was blocked"
            await asyncio.sleep(0.01)
        # Let the deadline handler issue task.cancel() and enter _drain_add_task before releasing the commit thread.
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        release.set()
    await task

    assert provider.closed == 1
    assert index.deleted == []
    main, added = _files(document_id)
    assert main.role == "main" and added.status == "ready" and added.source_import_status == "completed"
    assert (storage / added.source_markdown_path).is_file()
    fresh_job = _fresh_job(job.job_id)
    assert fresh_job.status == PdfJobStatus.COMPLETED.value
    assert fresh_job.current_stage == "completed"
    assert fresh_job.metadata_json["supplements"][0]["state"] == "added"
    report.assert_not_called()


@pytest.mark.asyncio
async def test_after_add_serializes_concurrent_piece_count_publishes_with_real_paper_operation_lock(
    paper, monkeypatch,
):
    import asyncio

    document_id, owner_id, subject, _ = paper
    first_publishing = asyncio.Event()
    release_first = asyncio.Event()
    published: list[int] = []

    async def publish_count(_doc_id, piece_count, _client, _subject, *, caller_reports_failure=False):
        assert caller_reports_failure is True
        if piece_count == 30:
            first_publishing.set()
            await release_first.wait()
        published.append(piece_count)

    monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", publish_count)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_: None)

    first = asyncio.create_task(supplement_add._after_add(document_id, subject, object()))
    await asyncio.wait_for(first_publishing.wait(), timeout=10)

    # Commit a new ready supplement in Postgres (raising ready piece total from 30 -> 35)
    # while refresh #1 still holds the real Postgres paper_operation_lock.
    extra_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocumentFile(
            id=extra_id,
            document_id=document_id,
            role="supplement",
            display_name="Made_up_S99.xlsx",
            status="ready",
            tab_order=1,
            display_mode="text",
            file_path=f"{subject}/source_markdown/{extra_id}.md",
            file_hash=extra_id.hex * 2,
            file_size=10,
            source_provider_source_file_id="99",
            source_markdown_path=f"{subject}/source_markdown/{extra_id}.md",
            chunk_index_start=30,
            chunk_index_end=34,
            chunk_count=5,
            text_bytes=50,
            added_at=datetime.now(timezone.utc),
        ))
    second = asyncio.create_task(supplement_add._after_add(document_id, subject, object()))
    await asyncio.sleep(0.12)
    assert published == []
    release_first.set()
    await asyncio.gather(first, second)

    assert published == [30, 35]


@pytest.mark.asyncio
async def test_run_supplement_add_job_foreign_document_leaves_other_papers_job_untouched(paper, monkeypatch):
    from unittest.mock import Mock

    document_id, owner_id, subject, storage = paper
    other_document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(new_paper(
            id=other_document_id, user_id=owner_id, filename=f"{PREFIX}{other_document_id}.pdf", status="completed",
            processing_completed_at=COMPLETED_AT, file_path=f"{subject}/{other_document_id}/made-up.pdf",
            file_hash=other_document_id.hex * 2, file_size=100, page_count=2, next_chunk_index=30,
            chunk_index_start=0, chunk_index_end=29, chunk_count=30, text_bytes=1_000,
            source_provider="abc_literature", source_provider_reference_id="102",
            source_provider_reference_curie="AGRKB:102", source_provider_source_file_id="10",
        ))
    other_job = supplement_add.start_supplement_add(other_document_id, owner_id, [_chosen(20)])
    before_job = _fresh_job(other_job.job_id)

    index = _Index().install(monkeypatch)
    provider = _ClosableProvider({"21": WORKBOOK})
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=UUID(other_job.job_id),
        document_id=document_id,
        user_id=subject,
        owner_user_id=owner_id,
        context=CONTEXT,
        files=(_chosen(20),),
    ))

    # Target paper's uncommitted files are rolled back cleanly.
    assert provider.closed == 1
    (main,) = _files(document_id)
    assert main.role == "main" and main.status == "ready"
    assert len(index.deleted) == 1 and index.deleted[0][0] >= 30
    assert not list(storage.rglob("*.md")) and not list(storage.rglob("*.json"))
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "supplement_add_failed"
    assert report.call_args.kwargs["context"] == {"exception_type": "DocumentFileError"}

    # The foreign paper's job was neither progressed nor failed, and document-scoped cancel also leaves it untouched.
    assert pdf_job_service.mark_cancelled(job_id=other_job.job_id, document_id=document_id) is None
    after_job = _fresh_job(other_job.job_id)
    assert after_job.document_id == other_document_id
    assert after_job.status == before_job.status
    assert after_job.current_stage == before_job.current_stage
    assert after_job.error_message is None
    assert after_job.completed_at is None
    assert after_job.cancel_requested is False
    assert after_job.metadata_json == before_job.metadata_json


@pytest.mark.asyncio
async def test_after_add_reuses_paper_lock_connection_under_one_connection_pool(paper, monkeypatch):
    from unittest.mock import Mock

    from sqlalchemy import create_engine

    from src.lib.document_files import paper_lock
    from src.models.sql import database

    document_id, _owner_id, subject, _ = paper
    small_engine = create_engine(database.engine.url, pool_size=1, max_overflow=0, pool_timeout=1)
    published: list[int] = []

    async def publish_count(_doc_id, piece_count, _client, _subject, *, caller_reports_failure=False):
        assert caller_reports_failure is True
        published.append(piece_count)

    report = Mock(return_value=True)
    monkeypatch.setattr(database, "engine", small_engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        Mock(side_effect=AssertionError("_after_add must reuse the lock connection instead of opening SessionLocal")),
    )
    monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", publish_count)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_: None)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    try:
        await supplement_add._after_add(document_id, subject, object())
        assert published == [30]
        report.assert_not_called()
        # The single pooled connection was cleanly unlocked and returned to the pool.
        async with paper_lock.paper_operation_lock(document_id) as held_conn:
            assert not held_conn.in_transaction()
    finally:
        small_engine.dispose()


@pytest.mark.asyncio
async def test_zero_write_supplement_add_job_never_fails_concurrent_writers_pending_row(paper, monkeypatch):
    """All-skipped run_supplement_add_job must not reconcile another concurrent writer's pending row."""
    import asyncio
    from unittest.mock import Mock

    from src.lib.document_sources.file_reasons import FileReason

    monkeypatch.setenv("DOCUMENT_PAPER_MAX_TEXT_BYTES", "1100")
    document_id, owner_id, subject, storage = paper
    index = _Index().install(monkeypatch)

    writer_a_inserted = asyncio.Event()
    writer_b_finished = asyncio.Event()
    writer_b_prepared = asyncio.Event()

    async def _writer_a_checkpoint(stage: str) -> None:
        if stage == "saving_files":
            await asyncio.wait_for(writer_b_prepared.wait(), timeout=10)
        elif stage == "vector_storage":
            writer_a_inserted.set()
            await asyncio.wait_for(writer_b_finished.wait(), timeout=10)

    # Create writer B's standalone supplement_add job (the paper's latest job row) and wire a
    # fresh listing + closable provider so writer B runs through the real run_supplement_add_job.
    job_b = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(40)])
    provider_b = _ClosableProvider(WORKBOOKS)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider_b)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", Mock(return_value=True))

    real_checkpoint_factory = supplement_add._cancel_checkpoint

    def _coordinating_checkpoint(job_id, *, document_id=None):
        inner = real_checkpoint_factory(job_id, document_id=document_id)

        async def _checkpoint(stage: str) -> None:
            if stage == "saving_files":
                writer_b_prepared.set()
                await asyncio.wait_for(writer_a_inserted.wait(), timeout=10)
            await inner(stage)

        return _checkpoint

    monkeypatch.setattr(supplement_add, "_cancel_checkpoint", _coordinating_checkpoint)

    pending_observed_after_writer_b: list[tuple[str | None, str]] = []

    async def _run_writer_b() -> None:
        try:
            await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
                job_id=UUID(job_b.job_id),
                document_id=document_id,
                user_id=subject,
                owner_user_id=owner_id,
                context=CONTEXT,
                files=(_chosen(40),),
            ))
            # While writer A is still paused pre-commit, verify writer A's row is STILL pending.
            pending_observed_after_writer_b.extend(
                (row.source_provider_source_file_id, row.status) for row in _files(document_id)
            )
        finally:
            writer_b_finished.set()

    result_a, _ = await asyncio.gather(
        supplement_add.add_files_to_paper_with_listing(
            document_id, owner_id, CONTEXT, [_chosen(20)],
            listing=_bound_listing(20), job_id=uuid4(), provider=_AbcBoundProvider(WORKBOOKS),
            weaviate_client=object(), stage_checkpoint=_writer_a_checkpoint,
        ),
        _run_writer_b(),
    )

    assert pending_observed_after_writer_b == [("10", "ready"), ("20", "pending")]
    fresh_job_b = _fresh_job(job_b.job_id)
    assert fresh_job_b.status == PdfJobStatus.FAILED.value
    assert fresh_job_b.message == supplement_add._MESSAGE_NONE_ADDED
    assert fresh_job_b.metadata_json["supplements"][0]["state"] == "left_out"
    assert fresh_job_b.metadata_json["supplements"][0]["reason_code"] == FileReason.PAPER_TEXT_LIMIT.value

    (outcome_a,) = result_a.files
    assert outcome_a.outcome == "added" and outcome_a.file_id is not None
    assert [(row.source_provider_source_file_id, row.status) for row in _files(document_id)] == [
        ("10", "ready"), ("20", "ready"),
    ]
    assert (storage / f"{subject}/source_markdown/{outcome_a.file_id}.md").is_file()
    assert len(index.stored) == 1


@pytest.mark.asyncio
async def test_zero_write_all_skipped_job_respects_cancel_requested_under_row_lock(paper, monkeypatch):
    """If request_cancel commits while _insert_rows skips all candidates, the job ends cancelled."""
    from unittest.mock import Mock

    from src.lib.document_sources.file_reasons import FileReason

    document_id, owner_id, subject, _storage = paper
    _Index().install(monkeypatch)
    job = supplement_add.start_supplement_add(document_id, owner_id, [_chosen(20)])
    provider = _ClosableProvider(WORKBOOKS)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    def _raced_all_skipped_insert(*_args, **_kwargs):
        # Simulate curator clicking cancel while _insert_rows runs and skips all candidates.
        pdf_job_service.request_cancel(job_id=job.job_id, user_id=owner_id)
        return {"20": (FileReason.PAPER_TEXT_LIMIT, 1150, 1100)}

    monkeypatch.setattr(supplement_add, "_insert_rows", _raced_all_skipped_insert)

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=UUID(job.job_id),
        document_id=document_id,
        user_id=subject,
        owner_user_id=owner_id,
        context=CONTEXT,
        files=(_chosen(20),),
    ))

    fresh_job = _fresh_job(job.job_id)
    assert fresh_job.status == PdfJobStatus.CANCELLED.value
    assert fresh_job.current_stage == "cancelled"
    assert fresh_job.cancel_requested is True
    assert fresh_job.error_message is None
    assert provider.closed == 1
    report.assert_not_called()
