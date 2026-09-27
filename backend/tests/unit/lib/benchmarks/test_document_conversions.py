"""Unit checks for benchmark document conversion records and repository guards."""

from unittest.mock import MagicMock

import pytest

from src.lib.benchmarks.document_conversions import DocumentConversionRepository
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
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
