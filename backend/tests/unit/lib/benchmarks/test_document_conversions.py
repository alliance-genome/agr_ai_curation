"""Unit checks for benchmark document conversion records, guards, and the conversion service."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.lib.benchmarks.document_conversions import (
    ABC_NOT_FOUND_MESSAGE,
    ConversionStateError,
    DocumentConversionRepository,
    DocumentConversionService,
    conversion_identity,
    identity_version,
    run_conversion,
)
from src.lib.benchmarks.document_inputs import decode_frozen_document
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceReferenceNotFound,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceReference,
)
from src.lib.exceptions import PDFParsingError
from src.models.sql.benchmark import BenchmarkDocumentConversion


DIGEST = "sha256:" + "a" * 64


def _curator() -> BenchmarkCuratorContext:
    return BenchmarkCuratorContext(
        subject="synthetic-curator", auth_provider="oidc", db_user_id=7,
        active_groups=("group-alpha",),
    )


def _constraint_names() -> set[str]:
    return {
        constraint.name
        for constraint in BenchmarkDocumentConversion.__table__.constraints
        if constraint.name
    }


def test_conversion_table_declares_owner_key_uniqueness_and_input_shape():
    table = BenchmarkDocumentConversion.__table__
    assert table.name == "benchmark_document_conversions"
    assert {
        "uq_benchmark_document_conversions_owner_key",
        "ck_benchmark_document_conversions_input_kind",
        "ck_benchmark_document_conversions_input_fields",
        "ck_benchmark_document_conversions_status",
        "ck_benchmark_document_conversions_status_fields",
    } <= _constraint_names()
    foreign_targets = {fk.target_fullname for fk in table.foreign_keys}
    assert foreign_targets == {"users.user_id", "benchmark_input_snapshots.id"}
    assert table.c.source_digest.nullable
    assert table.c.abc_reference.nullable
    assert table.c.snapshot_id.nullable
    assert not table.c.owner_subject.nullable
    assert not table.c.idempotency_key.nullable


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"input_kind": "url"}, "input kind"),
        ({"source_digest": None}, "PDF conversions"),
        ({"source_blob_reference": None}, "PDF conversions"),
        ({"abc_reference": "AGRKB:101000000000001"}, "PDF conversions"),
        ({"source_digest": "sha256:short"}, "digest"),
        ({"idempotency_key": ""}, "idempotency key"),
        ({"owner_subject": ""}, "owner"),
    ],
)
def test_create_or_get_rejects_invalid_pdf_input_before_touching_the_database(overrides, message):
    db = MagicMock()
    arguments = {
        "owner_subject": "service:portal",
        "service_principal": "portal-client",
        "curator": _curator(),
        "input_kind": "pdf",
        "source_digest": DIGEST,
        "source_blob_reference": "blob/" + "a" * 64,
        "abc_reference": None,
        "idempotency_key": "key-1",
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        DocumentConversionRepository().create_or_get(db, **arguments)

    db.execute.assert_not_called()
    db.scalar.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"abc_reference": None},
        {"abc_reference": "PMID:12345"},
        {"source_digest": DIGEST},
        {"source_blob_reference": "blob/x"},
    ],
)
def test_create_or_get_rejects_invalid_abc_input_before_touching_the_database(overrides):
    db = MagicMock()
    arguments = {
        "owner_subject": "service:portal",
        "service_principal": "portal-client",
        "curator": _curator(),
        "input_kind": "abc_reference",
        "source_digest": None,
        "source_blob_reference": None,
        "abc_reference": "AGRKB:101000000000001",
        "idempotency_key": "key-1",
        **overrides,
    }

    with pytest.raises(ValueError):
        DocumentConversionRepository().create_or_get(db, **arguments)

    db.execute.assert_not_called()
    db.scalar.assert_not_called()


@pytest.mark.parametrize(
    ("code", "message"),
    [("", "Conversion failed."), ("Not A Code", "Conversion failed."), ("parse_failed", ""),
     ("parse_failed", "x" * 513)],
)
def test_mark_failed_rejects_unsanitized_error_fields_before_touching_the_database(code, message):
    db = MagicMock()

    with pytest.raises(ValueError):
        DocumentConversionRepository().mark_failed(db, "any-id", code=code, message=message)

    db.scalar.assert_not_called()


# --- Conversion service -----------------------------------------------------

PDF_BYTES = b"%PDF-1.4\n%synthetic conversion fixture\n"
PDF_DIGEST = "sha256:" + hashlib.sha256(PDF_BYTES).hexdigest()
PDF_BLOB = "sha256/aa/" + PDF_DIGEST.removeprefix("sha256:")
ABC_REFERENCE = "AGRKB:101000000000001"
MAIN_TEXT = "# Synthetic paper\n\n## Results\n\nThe synthetic gene is expressed in the wing.\n"
ELEMENTS = [
    {"index": 0, "type": "Title", "text": "Results", "metadata": {"page_number": 1}},
    {"index": 1, "type": "NarrativeText", "text": "Synthetic body.", "metadata": {"page_number": 1}},
]
RECEIPT = {
    "schema": "pdfx-merged-page-provenance",
    "contract_version": "merged-page-provenance-v1",
    "record_sha256": "c" * 64,
    "expected_page_count": 1,
    "range_count": 1,
    "summary": {},
}
_SERVICE_MODULE = "src.lib.benchmarks.document_conversions"


def _conversion_row(**overrides):
    values = {
        "id": uuid4(),
        "owner_subject": "service:portal",
        "service_principal": "portal-client",
        "curator_subject": "synthetic-curator",
        "curator_db_user_id": 7,
        "input_kind": "pdf",
        "source_digest": PDF_DIGEST,
        "source_blob_reference": PDF_BLOB,
        "abc_reference": None,
        "status": "queued",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _abc_row(**overrides):
    return _conversion_row(
        input_kind="abc_reference", source_digest=None, source_blob_reference=None,
        abc_reference=ABC_REFERENCE, **overrides,
    )


class _FakeConversionRepository:
    """Records lifecycle calls and enforces the repository's field contract."""

    def __init__(self, row):
        self.row = row
        self.failed: list[tuple[str, str]] = []
        self.succeeded: list[dict[str, Any]] = []

    def mark_running(self, db, conversion_id):
        assert conversion_id == self.row.id
        if self.row.status != "queued":
            raise ConversionStateError("Only a queued conversion can start")
        self.row.status = "running"
        return self.row

    def mark_succeeded(self, db, conversion_id, *, snapshot_id, identity):
        assert conversion_id == self.row.id and self.row.status == "running"
        self.row.status = "succeeded"
        self.succeeded.append({"snapshot_id": snapshot_id, "identity": identity})
        return self.row

    def mark_failed(self, db, conversion_id, *, code, message):
        assert conversion_id == self.row.id
        assert re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", code)
        assert message and message == message.strip() and len(message) <= 512
        self.row.status = "failed"
        self.failed.append((code, message))
        return self.row


class _FakeStore:
    def __init__(self, blobs=None):
        self.blobs = dict(blobs or {})

    def put(self, *, digest, content):
        reference = "sha256/" + digest.removeprefix("sha256:")
        self.blobs[reference] = content
        return reference

    def read(self, *, blob_reference, max_bytes):
        content = self.blobs[blob_reference]
        assert len(content) <= max_bytes
        return content


@dataclass
class _SnapshotRecorder:
    frozen: list[dict[str, Any]] = field(default_factory=list)

    def factory(self, db, store):
        recorder = self

        class _Snapshots:
            def freeze_input(self, source, *, owner_subject, service_principal):
                recorder.frozen.append({
                    "source": source,
                    "owner_subject": owner_subject,
                    "service_principal": service_principal,
                })
                return SimpleNamespace(id=uuid4(), owner_subject=owner_subject)

        return _Snapshots()


@contextmanager
def _session():
    yield MagicMock()


def _service(row, *, store=None, snapshots=None, max_input_bytes=1_000_000):
    repository = _FakeConversionRepository(row)
    recorder = snapshots or _SnapshotRecorder()
    service = DocumentConversionService(
        session_factory=_session,
        repository=repository,
        snapshot_store_factory=lambda: store or _FakeStore({PDF_BLOB: PDF_BYTES}),
        snapshot_repository_factory=recorder.factory,
        max_input_bytes=lambda: max_input_bytes,
    )
    return service, repository, recorder


class _FakeParser:
    instances: list["_FakeParser"] = []

    def __init__(self, *, result=None, error=None):
        self.methods = "grobid,marker"
        self.merge_enabled = True
        self.download_variant = "merged"
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self._error = error

    async def parse_pdf_document(self, file_path, document_id, user_id, *, save_artifacts=True):
        self.calls.append({
            "file_path": file_path,
            "bytes": file_path.read_bytes(),
            "suffix": file_path.suffix,
            "document_id": document_id,
            "user_id": user_id,
            "save_artifacts": save_artifacts,
        })
        if self._error is not None:
            raise self._error
        return self._result or {
            "elements": ELEMENTS, "pdfx_json_path": None, "processed_json_path": None,
            "page_provenance": RECEIPT,
        }


def _parser_patch(**parser_kwargs):
    created: list[_FakeParser] = []

    def _factory():
        parser = _FakeParser(**parser_kwargs)
        created.append(parser)
        return parser

    return patch(f"{_SERVICE_MODULE}.PDFXParser", side_effect=_factory), created


def _artifact(artifact_id, *, role, fmt, status=SourceArtifactStatus.AVAILABLE, parent=None,
              scope=SourceAccessScope.GLOBAL, groups=(), metadata=None):
    return SourceArtifact(
        provider="abc_literature",
        artifact_id=artifact_id,
        role=role,
        artifact_format=fmt,
        status=status,
        reference_curie=ABC_REFERENCE,
        display_name=f"{artifact_id}.dat",
        parent_artifact_id=parent,
        access_policy=SourceAccessPolicy(scope=scope, group_ids=tuple(groups)),
        metadata=metadata or {},
    )


def _source_pdf(**kwargs):
    return _artifact(
        "pdf-1", role=SourceArtifactRole.SOURCE_PDF, fmt=SourceArtifactFormat.PDF,
        metadata={"file_class": "main", "file_publication_status": "final", "pdf_type": "pdf"},
        **kwargs,
    )


def _main_text(parent="pdf-1"):
    return _artifact(
        "md-1", role=SourceArtifactRole.CONVERTED_TEXT, fmt=SourceArtifactFormat.MARKDOWN,
        parent=parent, metadata={"file_class": "converted_merged_main"},
    )


class _FakeABCProvider:
    """Provider contract double with ABC-like main-text and main-PDF rules."""

    provider_id = "abc_literature"

    def __init__(self, artifacts=(), downloads=None, resolve_error=None, download_error=None):
        self.artifacts = list(artifacts)
        self.downloads = dict(downloads or {})
        self.resolve_error = resolve_error
        self.download_error = download_error
        self.tokens: list[Any] = []
        self.downloaded: list[str] = []
        self.conversion_requests = 0
        self.closed = False

    async def resolve_reference(self, identifier, *, request_bearer_token=None):
        self.tokens.append(request_bearer_token)
        if self.resolve_error is not None:
            raise self.resolve_error
        return SourceReference(provider=self.provider_id, reference_curie=identifier)

    async def list_artifacts(self, reference, *, request_bearer_token=None):
        self.tokens.append(request_bearer_token)
        return list(self.artifacts)

    async def download_artifact(self, artifact_id, *, request_bearer_token=None):
        self.tokens.append(request_bearer_token)
        self.downloaded.append(artifact_id)
        if self.download_error is not None:
            raise self.download_error
        return self.downloads[artifact_id]

    async def request_conversion(self, *_args, **_kwargs):
        self.conversion_requests += 1
        raise AssertionError("benchmark conversions must not request provider conversion")

    def is_main_text_artifact(self, artifact):
        return artifact.metadata.get("file_class") == "converted_merged_main"

    def main_text_artifact_sort_key(self, artifact):
        return (0,)

    def reference_source_artifact_sort_key(self, artifact, authorized_group_ids):
        return (0,)

    def provider_metadata_artifacts_for_source(self, source_artifact, artifacts):
        return ()

    async def aclose(self):
        self.closed = True


def _provider_patch(provider):
    return patch(f"{_SERVICE_MODULE}.get_configured_document_source_provider", return_value=provider)


def test_identity_version_is_sha256_of_canonical_json():
    identity = conversion_identity(
        input_kind="pdf", parser="pdfx", methods=["grobid", "marker"], merge=True,
        content_format="merged_markdown", page_provenance_receipt=RECEIPT,
    )
    reordered = dict(reversed(list(identity.items())))

    assert identity == {
        "input_kind": "pdf", "parser": "pdfx", "methods": ["grobid", "marker"],
        "merge": True, "content_format": "merged_markdown", "page_provenance_receipt": RECEIPT,
    }
    expected = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()
    assert identity_version(identity) == expected == identity_version(reordered)
    assert re.fullmatch(r"[0-9a-f]{64}", identity_version(identity))


@pytest.mark.asyncio
async def test_pdf_conversion_freezes_json_owned_by_the_calling_service():
    row = _conversion_row()
    service, repository, recorder = _service(row)
    parser_patch, parsers = _parser_patch()

    with parser_patch:
        await service.run(row.id, authorized_group_ids=())

    assert repository.failed == []
    [parser] = parsers
    [call] = parser.calls
    assert call["bytes"] == PDF_BYTES and call["suffix"] == ".pdf"
    assert call["document_id"] == str(row.id)
    assert call["user_id"] == "7"
    assert call["save_artifacts"] is False
    assert not call["file_path"].exists()

    [frozen] = recorder.frozen
    assert frozen["owner_subject"] == "service:portal"
    assert frozen["service_principal"] == "portal-client"
    source = frozen["source"]
    assert source.resolver == "document_conversion"
    assert source.metadata.content_type == "application/json"
    content = source.content.encode("utf-8")
    assert source.metadata.content_bytes == len(content)
    assert source.digest == "sha256:" + hashlib.sha256(content).hexdigest()
    assert decode_frozen_document(content, content_type="application/json") == ELEMENTS
    assert json.loads(source.reference) == {
        "schema": "document_conversion/v1",
        "input_kind": "pdf",
        "source_digest": PDF_DIGEST,
        "curator_subject": "synthetic-curator",
    }
    assert source.provenance.resolver == source.resolver
    assert source.provenance.reference == source.reference
    assert source.provenance.digest == source.digest

    [succeeded] = repository.succeeded
    identity = succeeded["identity"]
    assert identity == {
        "input_kind": "pdf", "parser": "pdfx", "methods": ["grobid", "marker"], "merge": True,
        "content_format": "merged_markdown", "page_provenance_receipt": RECEIPT,
    }
    assert source.version == source.provenance.version == identity_version(identity)
    assert row.status == "succeeded"


@pytest.mark.asyncio
async def test_each_conversion_uses_a_new_parser_instance():
    parser_patch, parsers = _parser_patch()
    with parser_patch:
        for _ in range(2):
            row = _conversion_row()
            service, repository, _recorder = _service(row)
            await service.run(row.id, authorized_group_ids=())
            assert repository.failed == []
    assert len(parsers) == 2 and parsers[0] is not parsers[1]


@pytest.mark.asyncio
async def test_converted_elements_over_the_input_limit_fail_as_oversize_without_a_snapshot():
    row = _conversion_row()
    limit = len(json.dumps(ELEMENTS, ensure_ascii=False).encode("utf-8")) - 1
    service, repository, recorder = _service(row, max_input_bytes=limit)
    parser_patch, _parsers = _parser_patch()

    with parser_patch:
        await service.run(row.id, authorized_group_ids=())

    assert recorder.frozen == []
    assert repository.succeeded == []
    [(code, message)] = repository.failed
    assert code == "oversize_payload"
    assert "size limit" in message


@pytest.mark.asyncio
async def test_parser_failure_is_recorded_as_a_sanitized_terminal_failure():
    row = _conversion_row()
    service, repository, recorder = _service(row)
    secret = "provider said http://internal.example/token=abc123 failed"
    parser_patch, parsers = _parser_patch(error=PDFParsingError(secret))

    with parser_patch:
        await service.run(row.id, authorized_group_ids=())

    assert recorder.frozen == []
    [(code, message)] = repository.failed
    assert code == "extraction_failed"
    assert "internal.example" not in message and "abc123" not in message
    assert len(parsers) == 1 and len(parsers[0].calls) == 1


@pytest.mark.asyncio
async def test_unexpected_failure_is_reported_sanitized_and_never_raised():
    row = _conversion_row()
    service, repository, _recorder = _service(row)
    parser_patch, _parsers = _parser_patch(error=RuntimeError("SELECT secret FROM users"))

    with parser_patch, patch(f"{_SERVICE_MODULE}.report_runtime_exception") as report:
        await service.run(row.id, authorized_group_ids=())

    [(code, message)] = repository.failed
    assert code == "conversion_failed"
    assert "secret" not in message
    [reported] = [call.args[0] for call in report.call_args_list]
    assert "secret" not in str(reported)
    assert "RuntimeError" in str(reported)


@pytest.mark.asyncio
async def test_stored_pdf_that_does_not_match_the_recorded_digest_fails():
    row = _conversion_row()
    store = _FakeStore({PDF_BLOB: PDF_BYTES + b"tampered"})
    service, repository, recorder = _service(row, store=store)
    parser_patch, parsers = _parser_patch()

    with parser_patch:
        await service.run(row.id, authorized_group_ids=())

    assert parsers == [] and recorder.frozen == []
    [(code, _message)] = repository.failed
    assert code == "source_unavailable"


@pytest.mark.asyncio
async def test_conversion_that_is_not_queued_is_left_untouched():
    row = _conversion_row(status="failed")
    service, repository, recorder = _service(row)
    parser_patch, parsers = _parser_patch()

    with parser_patch:
        await service.run(row.id, authorized_group_ids=())

    assert parsers == [] and recorder.frozen == [] and repository.failed == []
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_abc_main_text_is_used_before_the_pdf_with_ai_curation_access():
    row = _abc_row()
    provider = _FakeABCProvider(
        artifacts=[_source_pdf(), _main_text()],
        downloads={"md-1": MAIN_TEXT.encode("utf-8"), "pdf-1": PDF_BYTES},
    )
    service, repository, recorder = _service(row)
    parser_patch, parsers = _parser_patch()

    with parser_patch, _provider_patch(provider):
        await service.run(row.id, authorized_group_ids=("group-alpha",))

    assert repository.failed == []
    assert parsers == []
    assert provider.downloaded == ["md-1"]
    assert set(provider.tokens) == {None}
    assert provider.conversion_requests == 0
    assert provider.closed
    [frozen] = recorder.frozen
    assert frozen["owner_subject"] == "service:portal"
    elements = json.loads(frozen["source"].content)
    assert any("synthetic gene" in element["text"] for element in elements)
    assert json.loads(frozen["source"].reference) == {
        "schema": "document_conversion/v1",
        "input_kind": "abc_reference",
        "abc_reference": ABC_REFERENCE,
        "curator_subject": "synthetic-curator",
    }
    [succeeded] = repository.succeeded
    identity = succeeded["identity"]
    assert identity["parser"] == "abc_main_text"
    assert identity["input_kind"] == "abc_reference"
    assert identity["abc_artifact"] == {
        "id": "md-1",
        "checksum": "sha256:" + hashlib.sha256(MAIN_TEXT.encode("utf-8")).hexdigest(),
    }
    assert frozen["source"].version == identity_version(identity)


@pytest.mark.asyncio
async def test_abc_without_main_text_parses_the_selected_main_pdf():
    row = _abc_row()
    provider = _FakeABCProvider(artifacts=[_source_pdf()], downloads={"pdf-1": PDF_BYTES})
    service, repository, recorder = _service(row)
    parser_patch, parsers = _parser_patch()

    with parser_patch, _provider_patch(provider):
        await service.run(row.id, authorized_group_ids=("group-alpha",))

    assert repository.failed == []
    assert provider.downloaded == ["pdf-1"]
    assert set(provider.tokens) == {None}
    [parser] = parsers
    [call] = parser.calls
    assert call["bytes"] == PDF_BYTES and call["save_artifacts"] is False
    [frozen] = recorder.frozen
    assert json.loads(frozen["source"].content) == ELEMENTS
    [succeeded] = repository.succeeded
    identity = succeeded["identity"]
    assert identity["parser"] == "pdfx"
    assert identity["page_provenance_receipt"] == RECEIPT
    assert identity["abc_artifact"] == {"id": "pdf-1", "checksum": PDF_DIGEST}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        _FakeABCProvider(resolve_error=DocumentSourceReferenceNotFound("no such paper")),
        _FakeABCProvider(artifacts=[]),
        _FakeABCProvider(artifacts=[_main_text(parent=None)]),
    ],
    ids=["unknown-reference", "no-artifacts", "text-without-source-pdf"],
)
async def test_abc_paper_without_usable_text_or_pdf_fails_not_found(provider):
    row = _abc_row()
    service, repository, recorder = _service(row)
    parser_patch, parsers = _parser_patch()

    with parser_patch, _provider_patch(provider):
        await service.run(row.id, authorized_group_ids=("group-alpha",))

    assert parsers == [] and recorder.frozen == []
    assert repository.failed == [("not_found", ABC_NOT_FOUND_MESSAGE)]
    assert ABC_NOT_FOUND_MESSAGE == (
        "The Alliance literature database has no usable text or PDF for this paper."
    )


@pytest.mark.asyncio
async def test_abc_pdf_restricted_to_other_groups_fails_access_denied():
    row = _abc_row()
    provider = _FakeABCProvider(
        artifacts=[_source_pdf(scope=SourceAccessScope.RESTRICTED, groups=("group-beta",))],
        downloads={"pdf-1": PDF_BYTES},
    )
    service, repository, recorder = _service(row)
    parser_patch, parsers = _parser_patch()

    with parser_patch, _provider_patch(provider):
        await service.run(row.id, authorized_group_ids=("group-alpha",))

    assert provider.downloaded == [] and parsers == [] and recorder.frozen == []
    [(code, _message)] = repository.failed
    assert code == "access_denied"


@pytest.mark.asyncio
async def test_abc_download_denied_fails_access_denied():
    row = _abc_row()
    provider = _FakeABCProvider(
        artifacts=[_source_pdf()], download_error=DocumentSourceAccessDenied("denied"),
    )
    service, repository, _recorder = _service(row)
    parser_patch, _parsers = _parser_patch()

    with parser_patch, _provider_patch(provider):
        await service.run(row.id, authorized_group_ids=())

    [(code, _message)] = repository.failed
    assert code == "access_denied"


@pytest.mark.asyncio
async def test_failure_to_record_a_failure_is_reported_and_not_raised():
    row = _conversion_row()
    service, repository, _recorder = _service(row)
    repository.mark_failed = MagicMock(side_effect=RuntimeError("database password=hunter2"))
    parser_patch, _parsers = _parser_patch(error=PDFParsingError("boom"))

    with parser_patch, patch(f"{_SERVICE_MODULE}.report_runtime_exception") as report:
        await service.run(row.id, authorized_group_ids=())

    assert repository.mark_failed.called
    assert report.called
    assert all("hunter2" not in str(call.args[0]) for call in report.call_args_list)


@pytest.mark.asyncio
async def test_run_conversion_never_raises_to_its_caller():
    with patch(f"{_SERVICE_MODULE}.DocumentConversionService") as service_cls, \
            patch(f"{_SERVICE_MODULE}.report_runtime_exception") as report:
        service_cls.return_value.run = AsyncMock(side_effect=RuntimeError("token=abc123"))
        await run_conversion(uuid4(), authorized_group_ids=())

    assert report.called
    assert "abc123" not in str(report.call_args.args[0])


@pytest.mark.asyncio
async def test_conversion_finished_elsewhere_while_running_is_not_overwritten():
    row = _conversion_row()
    service, repository, _recorder = _service(row)
    repository.mark_succeeded = MagicMock(
        side_effect=ConversionStateError("Only a running conversion can succeed"),
    )
    parser_patch, _parsers = _parser_patch()

    with parser_patch, patch(f"{_SERVICE_MODULE}.report_runtime_exception") as report:
        await service.run(row.id, authorized_group_ids=())

    assert repository.mark_succeeded.called
    assert repository.failed == []
    assert not report.called
