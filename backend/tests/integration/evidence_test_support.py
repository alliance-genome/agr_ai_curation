"""Shared helpers for evidence pipeline integration tests."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import Security
from fastapi.testclient import TestClient

from conftest import MOCK_USERS
from tests.fixtures.evidence.harness import (
    DEFAULT_FIXTURE_NAME,
    build_expected_sse_records,
    build_extraction_payload,
    load_evidence_fixture,
)


def _hash(document_id: UUID) -> str:
    hex_value = document_id.hex
    return f"{hex_value}{hex_value}"


@pytest.fixture
def evidence_fixture(request) -> dict[str, object]:
    fixture_name = getattr(request, "param", DEFAULT_FIXTURE_NAME)
    return load_evidence_fixture(str(fixture_name))


@pytest.fixture
def client(test_db, get_auth_mock, monkeypatch):
    """Create an isolated app client with auth and DB overrides."""
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY", "test-key"))
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")

    get_auth_mock.set_user("curator1")

    modules_to_clear = [
        name
        for name in list(sys.modules.keys())
        if name == "main" or name.startswith("src.")
    ]
    for module_name in modules_to_clear:
        del sys.modules[module_name]

    with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:
        mock_get_auth_dep.return_value = Security(get_auth_mock.get_user)

        from main import app
        from src.models.sql.chat_message import ChatMessage
        from src.models.sql.chat_session import ChatSession
        from src.models.sql.database import Base
        from src.models.sql.database import get_db
        from src.models.sql.pdf_document import PDFDocument
        from src.models.sql.user import User

        def override_get_db():
            yield test_db

        Base.metadata.create_all(
            bind=test_db.get_bind(),
            tables=[
                User.__table__,
                PDFDocument.__table__,
                ChatSession.__table__,
                ChatMessage.__table__,
            ],
        )
        test_db.query(ChatMessage).delete(synchronize_session=False)
        test_db.query(ChatSession).delete(synchronize_session=False)
        test_db.commit()

        app.dependency_overrides[get_db] = override_get_db
        try:
            test_client = TestClient(app)
            test_client.current_user_auth_sub = MOCK_USERS["curator1"]["sub"]
            yield test_client
        finally:
            test_db.query(ChatMessage).delete(synchronize_session=False)
            test_db.query(ChatSession).delete(synchronize_session=False)
            test_db.commit()
            app.dependency_overrides.clear()


@pytest.fixture
def evidence_integration_context(client: TestClient, evidence_fixture, test_db):
    """Seed a document and curation workspace tables for evidence integration tests."""
    from src.lib.curation_workspace.models import (
        CurationActionLogEntry as SessionActionLogModel,
        CurationCandidate,
        CurationDraft,
        CurationEvidenceRecord,
        CurationExtractionResultRecord,
        CurationReviewSession,
        CurationSubmissionRecord,
        CurationValidationSnapshot,
    )
    from src.models.sql.database import Base
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            User.__table__,
            PDFDocument.__table__,
            CurationReviewSession.__table__,
            CurationExtractionResultRecord.__table__,
            CurationCandidate.__table__,
            CurationEvidenceRecord.__table__,
            CurationDraft.__table__,
            CurationValidationSnapshot.__table__,
            CurationSubmissionRecord.__table__,
            SessionActionLogModel.__table__,
        ],
    )

    current_user_auth_sub = client.current_user_auth_sub
    document_id = uuid4()
    file_hash = _hash(document_id)
    paper = evidence_fixture["paper"]

    test_db.add(
        User(
            auth_sub=current_user_auth_sub,
            email="curator1@alliancegenome.org",
            display_name="Curator One",
            is_active=True,
        )
    )
    test_db.add(
        PDFDocument(
            id=document_id,
            filename=str(paper["filename"]),
            title=str(paper["title"]),
            file_path=f"{document_id}/{paper['filename']}",
            file_hash=file_hash,
            file_size=4096,
            page_count=6,
            upload_timestamp=datetime.now(timezone.utc),
            last_accessed=datetime.now(timezone.utc),
            status="processed",
        )
    )
    test_db.commit()

    yield {
        "document_id": str(document_id),
        "current_user_auth_sub": current_user_auth_sub,
        "paper": paper,
    }

    session_ids = [
        row[0]
        for row in (
            test_db.query(CurationReviewSession.id)
            .filter(CurationReviewSession.document_id == document_id)
            .all()
        )
    ]
    candidate_ids = (
        [
            row[0]
            for row in (
                test_db.query(CurationCandidate.id)
                .filter(CurationCandidate.session_id.in_(session_ids))
                .all()
            )
        ]
        if session_ids
        else []
    )

    if session_ids:
        test_db.query(SessionActionLogModel).filter(
            SessionActionLogModel.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationSubmissionRecord).filter(
            CurationSubmissionRecord.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationValidationSnapshot).filter(
            CurationValidationSnapshot.session_id.in_(session_ids)
        ).delete(synchronize_session=False)

    if candidate_ids:
        test_db.query(CurationEvidenceRecord).filter(
            CurationEvidenceRecord.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationDraft).filter(
            CurationDraft.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationCandidate).filter(
            CurationCandidate.id.in_(candidate_ids)
        ).delete(synchronize_session=False)

    if session_ids:
        test_db.query(CurationReviewSession).filter(
            CurationReviewSession.id.in_(session_ids)
        ).delete(synchronize_session=False)

    test_db.query(CurationExtractionResultRecord).filter(
        CurationExtractionResultRecord.document_id == document_id
    ).delete(synchronize_session=False)
    test_db.query(PDFDocument).filter(PDFDocument.id == document_id).delete(synchronize_session=False)
    test_db.query(User).filter(User.auth_sub == current_user_auth_sub).delete(synchronize_session=False)
    test_db.commit()


def collect_sse_events(response) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _patch_chat_impl(monkeypatch, modules, name: str, value) -> None:
    patched = False
    for module in modules:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    if not patched:
        raise AttributeError(name)


def configure_chat_stream_mocks(
    monkeypatch,
    *,
    document_id: str,
    filename: str,
    tool_agent_map: dict[str, str],
    run_agent_streamed,
):
    from src.api import chat_common, chat_stream

    chat_modules = (chat_common, chat_stream)

    _patch_chat_impl(monkeypatch, chat_modules, "set_current_session_id", lambda _session_id: None)
    _patch_chat_impl(monkeypatch, chat_modules, "set_current_user_id", lambda _user_id: None)
    _patch_chat_impl(
        monkeypatch,
        chat_modules,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: {"id": document_id, "filename": filename}),
    )
    _patch_chat_impl(monkeypatch, chat_modules, "get_groups_from_cognito", lambda _groups: [])
    _patch_chat_impl(
        monkeypatch,
        chat_modules,
        "_build_context_messages_from_durable_messages",
        lambda *_args, **_kwargs: [{"role": "user", "content": _kwargs.get("user_message", "")}] if _kwargs.get("user_message") is not None else [],
    )
    _patch_chat_impl(monkeypatch, chat_modules, "get_supervisor_tool_agent_map", lambda: dict(tool_agent_map))

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return None

    async def _clear_cancel_signal(_session_id: str):
        return None

    async def _check_cancel_signal(_session_id: str) -> bool:
        return False

    _patch_chat_impl(monkeypatch, chat_modules, "register_active_stream", _register_active_stream)
    _patch_chat_impl(monkeypatch, chat_modules, "unregister_active_stream", _unregister_active_stream)
    _patch_chat_impl(monkeypatch, chat_modules, "clear_cancel_signal", _clear_cancel_signal)
    _patch_chat_impl(monkeypatch, chat_modules, "check_cancel_signal", _check_cancel_signal)
    _patch_chat_impl(monkeypatch, chat_modules, "run_agent_streamed", run_agent_streamed)


def make_fixture_runner(evidence_fixture: dict[str, object]):
    extraction = evidence_fixture["extraction"]
    expected_sse_records = build_expected_sse_records(evidence_fixture)
    extraction_payload = build_extraction_payload(evidence_fixture)

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-evidence-fixture"}}
        for evidence_record in expected_sse_records:
            tool_output = {
                "status": "verified",
                "verified_quote": evidence_record["verified_quote"],
                "page": evidence_record["page"],
                "section": evidence_record["section"],
            }
            subsection = evidence_record.get("subsection")
            if subsection:
                tool_output["subsection"] = subsection
            figure_reference = evidence_record.get("figure_reference")
            if figure_reference:
                tool_output["figure_reference"] = figure_reference

            yield {
                "type": "TOOL_COMPLETE",
                "details": {"toolName": "record_evidence"},
                "internal": {
                    "tool_input": {
                        "entity": evidence_record["entity"],
                        "chunk_id": evidence_record["chunk_id"],
                        "claimed_quote": evidence_record["verified_quote"],
                    },
                    "tool_output": json.dumps(tool_output),
                },
            }

        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": extraction["tool_name"]},
            "internal": {
                "tool_output": json.dumps(extraction_payload),
            },
        }
        yield {"type": "RUN_FINISHED", "data": {"response": "fixture complete"}}

    return _run_agent_streamed
