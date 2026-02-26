"""Lightweight unit tests for files API helper and guardrail behavior."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api import files
from src.schemas.file_output import FileOutputCreate


class _DummyQuery:
    def __init__(self, first_result=None, all_results=None, count_result=0):
        self._first_result = first_result
        self._all_results = all_results if all_results is not None else []
        self._count_result = count_result
        self.filter_calls = 0

    def filter(self, *_args, **_kwargs):
        self.filter_calls += 1
        return self

    def first(self):
        return self._first_result

    def count(self):
        return self._count_result

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._all_results


class _DummyDB:
    def __init__(self, query: _DummyQuery):
        self._query = query
        self.commit_calls = 0
        self.added = None

    def query(self, _model):
        return self._query

    def add(self, obj):
        self.added = obj

    def commit(self):
        self.commit_calls += 1

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        if getattr(obj, "download_count", None) is None:
            obj.download_count = 0


def _sample_create_payload(file_path: str) -> FileOutputCreate:
    return FileOutputCreate(
        filename="test.csv",
        file_path=file_path,
        file_type="csv",
        file_size=10,
        file_hash="a" * 64,
        curator_id="curator-a",
        session_id="session-1",
        trace_id="d3b0a19f2c2df7b2b31dfb7cded3acbd",
    )


def test_get_curator_id_fallback_order():
    assert files._get_curator_id({"sub": "sub-1", "uid": "uid-1"}) == "sub-1"
    assert files._get_curator_id({"uid": "uid-1"}) == "uid-1"
    assert files._get_curator_id({}) == "unknown"


def test_build_download_url():
    file_id = uuid4()
    assert files._build_download_url(file_id) == f"/api/files/{file_id}/download"


def test_verify_session_ownership_rejects_other_curator():
    query = _DummyQuery(first_result=SimpleNamespace(curator_id="curator-b"))
    db = _DummyDB(query)
    with pytest.raises(HTTPException) as exc:
        files._verify_session_ownership(db, "session-1", "curator-a")
    assert exc.value.status_code == 403
    assert query.filter_calls >= 2


def test_record_file_rejects_excessive_size():
    db = _DummyDB(_DummyQuery(first_result=None))
    payload = _sample_create_payload("/tmp/test.csv")
    payload.file_size = files.MAX_FILE_SIZE + 1

    with pytest.raises(HTTPException) as exc:
        files.record_file(payload, db, {"sub": "curator-a"})

    assert exc.value.status_code == 400
    assert "exceeds maximum" in exc.value.detail


def test_record_file_rejects_duplicate_path():
    db = _DummyDB(_DummyQuery(first_result=SimpleNamespace(id=uuid4())))
    payload = _sample_create_payload("/tmp/test.csv")

    with pytest.raises(HTTPException) as exc:
        files.record_file(payload, db, {"sub": "curator-a"})

    assert exc.value.status_code == 409


def test_record_file_rejects_path_outside_storage(monkeypatch, tmp_path):
    db = _DummyDB(_DummyQuery(first_result=None))
    payload = _sample_create_payload("/etc/passwd")

    storage = SimpleNamespace(base_path=tmp_path / "allowed")
    monkeypatch.setattr(files, "FileOutputStorageService", lambda: storage)

    with pytest.raises(HTTPException) as exc:
        files.record_file(payload, db, {"sub": "curator-a"})

    assert exc.value.status_code == 400


def test_record_file_success_uses_authenticated_curator(monkeypatch, tmp_path):
    base_path = tmp_path / "allowed"
    base_path.mkdir(parents=True)
    real_file = base_path / "good.csv"
    real_file.write_text("a,b\n1,2\n", encoding="utf-8")

    query = _DummyQuery(first_result=None)
    db = _DummyDB(query)
    storage = SimpleNamespace(base_path=base_path)
    monkeypatch.setattr(files, "FileOutputStorageService", lambda: storage)

    payload = _sample_create_payload(str(real_file))
    payload.curator_id = "should-be-ignored"
    result = files.record_file(payload, db, {"sub": "curator-auth"})

    assert db.commit_calls == 1
    assert db.added.curator_id == "curator-auth"
    assert result.curator_id == "curator-auth"
    assert result.file_type == "csv"


def test_get_file_metadata_guards():
    db_not_found = _DummyDB(_DummyQuery(first_result=None))
    with pytest.raises(HTTPException) as exc_not_found:
        files.get_file_metadata(uuid4(), db_not_found, {"sub": "curator-a"})
    assert exc_not_found.value.status_code == 404

    file_obj = SimpleNamespace(
        id=uuid4(),
        curator_id="curator-b",
        filename="x.csv",
        file_type="csv",
        file_size=1,
        session_id="s1",
        trace_id="d3b0a19f2c2df7b2b31dfb7cded3acbd",
        download_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db_forbidden = _DummyDB(_DummyQuery(first_result=file_obj))
    with pytest.raises(HTTPException) as exc_forbidden:
        files.get_file_metadata(file_obj.id, db_forbidden, {"sub": "curator-a"})
    assert exc_forbidden.value.status_code == 403


def test_download_file_guards_for_not_found_and_forbidden():
    missing_db = _DummyDB(_DummyQuery(first_result=None))
    with pytest.raises(HTTPException) as exc_missing:
        files.download_file(uuid4(), missing_db, {"sub": "curator-a"})
    assert exc_missing.value.status_code == 404

    file_obj = SimpleNamespace(
        id=uuid4(),
        curator_id="curator-b",
        filename="x.csv",
        file_path="/tmp/x.csv",
        file_type="csv",
        download_count=0,
        last_download_at=None,
    )
    forbidden_db = _DummyDB(_DummyQuery(first_result=file_obj))
    with pytest.raises(HTTPException) as exc_forbidden:
        files.download_file(file_obj.id, forbidden_db, {"sub": "curator-a"})
    assert exc_forbidden.value.status_code == 403


def test_download_file_sanitizes_filename_and_updates_metrics(monkeypatch, tmp_path):
    base_path = tmp_path / "storage"
    base_path.mkdir(parents=True)
    file_path = base_path / "danger.csv"
    file_path.write_text("a,b\n1,2\n", encoding="utf-8")

    file_obj = SimpleNamespace(
        id=uuid4(),
        curator_id="curator-a",
        filename='bad"name\r\n.csv',
        file_path=str(file_path),
        file_type="csv",
        download_count=0,
        last_download_at=None,
    )

    db = _DummyDB(_DummyQuery(first_result=file_obj))
    monkeypatch.setattr(files, "FileOutputStorageService", lambda: SimpleNamespace(base_path=base_path))

    response = files.download_file(file_obj.id, db, {"sub": "curator-a"})

    assert db.commit_calls == 1
    assert file_obj.download_count == 1
    assert "\n" not in response.headers["content-disposition"]
    assert '"' in response.headers["content-disposition"]


def test_download_file_rejects_path_outside_storage(monkeypatch, tmp_path):
    base_path = tmp_path / "storage"
    base_path.mkdir(parents=True)
    outside_file = tmp_path / "outside.csv"
    outside_file.write_text("a,b\n1,2\n", encoding="utf-8")

    file_obj = SimpleNamespace(
        id=uuid4(),
        curator_id="curator-a",
        filename="outside.csv",
        file_path=str(outside_file),
        file_type="csv",
        download_count=0,
        last_download_at=None,
    )
    db = _DummyDB(_DummyQuery(first_result=file_obj))
    monkeypatch.setattr(files, "FileOutputStorageService", lambda: SimpleNamespace(base_path=base_path))

    with pytest.raises(HTTPException) as exc:
        files.download_file(file_obj.id, db, {"sub": "curator-a"})
    assert exc.value.status_code == 404


def test_download_file_rejects_missing_disk_file(monkeypatch, tmp_path):
    base_path = tmp_path / "storage"
    base_path.mkdir(parents=True)
    missing_file = base_path / "missing.csv"

    file_obj = SimpleNamespace(
        id=uuid4(),
        curator_id="curator-a",
        filename="missing.csv",
        file_path=str(missing_file),
        file_type="csv",
        download_count=0,
        last_download_at=None,
    )
    db = _DummyDB(_DummyQuery(first_result=file_obj))
    monkeypatch.setattr(files, "FileOutputStorageService", lambda: SimpleNamespace(base_path=base_path))

    with pytest.raises(HTTPException) as exc:
        files.download_file(file_obj.id, db, {"sub": "curator-a"})
    assert exc.value.status_code == 404


def test_list_session_files_honors_type_filter(monkeypatch):
    first_file = SimpleNamespace(
        id=uuid4(),
        filename="a.csv",
        file_type="csv",
        file_size=10,
        curator_id="curator-a",
        session_id="session-1",
        trace_id="d3b0a19f2c2df7b2b31dfb7cded3acbd",
        download_count=0,
        created_at=datetime.now(timezone.utc),
    )
    query = _DummyQuery(first_result=None, all_results=[first_file], count_result=1)
    db = _DummyDB(query)

    # Ownership check should pass (no cross-user file found).
    result = files.list_session_files(
        session_id="session-1",
        db=db,
        user={"sub": "curator-a"},
        page=1,
        page_size=20,
        file_type="csv",
    )

    assert result.total_count == 1
    assert len(result.items) == 1
    assert result.items[0].file_type == "csv"
    # session + curator + file_type filters should all be applied on the query chain
    assert query.filter_calls >= 3


def test_list_session_files_propagates_ownership_denial(monkeypatch):
    query = _DummyQuery(first_result=None, all_results=[], count_result=0)
    db = _DummyDB(query)

    def _deny(*_args, **_kwargs):
        raise HTTPException(status_code=403, detail="not authorized")

    monkeypatch.setattr(files, "_verify_session_ownership", _deny)

    with pytest.raises(HTTPException) as exc:
        files.list_session_files(
            session_id="session-1",
            db=db,
            user={"sub": "curator-a"},
            page=1,
            page_size=20,
            file_type=None,
        )

    assert exc.value.status_code == 403
