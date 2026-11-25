"""Stored file check results: keyed by provider file, MD5 and check version (P6)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from src.lib.document_sources.file_assessment import FileAssessment
from src.lib.document_sources.file_assessment_store import store_assessment, stored_assessment
from src.lib.document_sources.file_reasons import FileReason, FileReasonGroupError
from src.models.sql.database import SessionLocal
from src.models.sql.document_file_assessment import DocumentFileAssessment

PROVIDER = "made_up_provider"


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.fixture
def file_id():
    identifier = f"assessment-test-{uuid4().hex[:12]}"
    yield identifier
    with SessionLocal.begin() as db:
        db.execute(delete(DocumentFileAssessment).where(DocumentFileAssessment.provider_file_id == identifier))


def _key(file_id, **overrides):
    key = dict(provider=PROVIDER, provider_file_id=file_id, md5="a" * 32, check_version="1-abc")
    key.update(overrides)
    return key


def test_a_stored_result_is_reused_only_for_the_same_file_md5_and_version(file_id):
    refused = FileAssessment(refused=True, reason=FileReason.TABLE_ROW_TOO_WIDE, measured_value=900, limit_value=800)
    with SessionLocal.begin() as db:
        store_assessment(db, **_key(file_id), assessment=refused)
    with SessionLocal() as db:
        assert stored_assessment(db, **_key(file_id)) == refused
        assert stored_assessment(db, **_key(file_id, md5="b" * 32)) is None
        assert stored_assessment(db, **_key(file_id, check_version="1-def")) is None


def test_a_second_writer_of_the_same_key_changes_nothing(file_id):
    note = FileAssessment(refused=False, few_characters_count=12)
    with SessionLocal.begin() as db:
        store_assessment(db, **_key(file_id), assessment=note)
    with SessionLocal.begin() as db:
        store_assessment(db, **_key(file_id), assessment=note)
    with SessionLocal() as db:
        assert db.query(DocumentFileAssessment).filter_by(provider_file_id=file_id).count() == 1
        assert stored_assessment(db, **_key(file_id)) == note


def test_a_refusal_needs_a_reason_and_a_pass_has_none(file_id):
    with pytest.raises(IntegrityError):
        with SessionLocal.begin() as db:
            db.add(DocumentFileAssessment(**_key(file_id), result="refused", reason_code=None))
    with pytest.raises(IntegrityError):
        with SessionLocal.begin() as db:
            db.add(DocumentFileAssessment(**_key(file_id), result="ok", reason_code="too_large"))


def test_a_stored_code_that_is_not_a_check_reason_is_a_named_error(file_id):
    refused = FileAssessment(refused=True, reason=FileReason.TITLE_COUNT, measured_value=2, limit_value=1)
    with SessionLocal.begin() as db:
        store_assessment(db, **_key(file_id), assessment=refused)
        db.query(DocumentFileAssessment).filter_by(provider_file_id=file_id).update(
            {"reason_code": FileReason.PROVIDER_NOT_CONVERTED.value}
        )
    with SessionLocal() as db, pytest.raises(FileReasonGroupError):
        stored_assessment(db, **_key(file_id))


def test_stored_assessment_normalizes_md5_case_and_whitespace(file_id):
    passed = FileAssessment(refused=False)
    with SessionLocal.begin() as db:
        store_assessment(db, **_key(file_id, md5=f" {'A' * 32} "), assessment=passed)
    with SessionLocal() as db:
        assert stored_assessment(db, **_key(file_id, md5="a" * 32)) == passed
        assert stored_assessment(db, **_key(file_id, md5="A" * 32)) == passed
        assert stored_assessment(db, **_key(file_id, md5="   ")) is None
        row = db.query(DocumentFileAssessment).filter_by(provider_file_id=file_id).one()
        assert row.md5 == "a" * 32
