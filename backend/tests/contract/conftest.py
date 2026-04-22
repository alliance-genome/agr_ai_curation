"""Shared configuration for contract tests.

Contract tests validate API endpoint behavior against specifications.
These tests require proper authentication enforcement (no DEV_MODE bypass).
"""

import os

import pytest


CHAT_CONTRACT_AUTH_SUB = "api-key-test-user"
CHAT_CONTRACT_OTHER_AUTH_SUB = "contract-chat-other-user"


@pytest.fixture(scope="session", autouse=True)
def disable_dev_mode():
    """Disable DEV_MODE for all contract tests.

    Contract tests validate authentication requirements. DEV_MODE bypasses
    authentication, which would make all auth tests pass incorrectly.

    This fixture runs once before any contract test and sets DEV_MODE=false.
    """
    # Save original values
    original_dev_mode = os.environ.get("DEV_MODE")
    original_testing_api_key = os.environ.get("TESTING_API_KEY")

    # Disable DEV_MODE for contract tests
    os.environ["DEV_MODE"] = "false"
    # Provide deterministic API-key auth path for tests that send explicit auth headers.
    os.environ["TESTING_API_KEY"] = "contract-test-key"

    yield

    # Restore original values after all tests
    if original_dev_mode is not None:
        os.environ["DEV_MODE"] = original_dev_mode
    else:
        del os.environ["DEV_MODE"]

    if original_testing_api_key is not None:
        os.environ["TESTING_API_KEY"] = original_testing_api_key
    else:
        del os.environ["TESTING_API_KEY"]


@pytest.fixture
def contract_client(monkeypatch):
    """Create a FastAPI test client for durable chat contract coverage."""

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    import sys

    sys.path.insert(
        0,
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ),
    )
    from main import app

    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def chat_contract_auth_headers():
    """Return API-key auth headers for live contract tests."""
    return {"X-API-Key": "contract-test-key"}


@pytest.fixture
def chat_contract_db():
    """Provide a database session cleaned for durable chat contract tests."""
    from sqlalchemy import delete, select

    from src.lib.chat_state import document_state
    from src.models.sql.chat_message import ChatMessage
    from src.models.sql.chat_session import ChatSession
    from src.models.sql.database import Base, SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    def _cleanup(db):
        session_ids = db.scalars(
            select(ChatSession.session_id).where(
                ChatSession.user_auth_sub.in_(
                    (CHAT_CONTRACT_AUTH_SUB, CHAT_CONTRACT_OTHER_AUTH_SUB)
                )
            )
        ).all()
        if session_ids:
            db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
            db.execute(delete(ChatSession).where(ChatSession.session_id.in_(session_ids)))
            db.commit()
        document_state.clear_document(CHAT_CONTRACT_AUTH_SUB)
        document_state.clear_document(CHAT_CONTRACT_OTHER_AUTH_SUB)

    db = SessionLocal()
    try:
        Base.metadata.create_all(
            bind=db.get_bind(),
            tables=[
                User.__table__,
                PDFDocument.__table__,
                ChatSession.__table__,
                ChatMessage.__table__,
            ],
        )
        _cleanup(db)
        yield db
        _cleanup(db)
    finally:
        db.close()


@pytest.fixture
def seed_chat_contract_session(chat_contract_db):
    """Seed one durable chat session and optional transcript rows."""
    from sqlalchemy import text

    from src.lib.chat_history_repository import (
        ASSISTANT_CHAT_KIND,
        ChatHistoryRepository,
    )

    def _seed(
        *,
        session_id: str,
        user_auth_sub: str = CHAT_CONTRACT_AUTH_SUB,
        chat_kind: str = ASSISTANT_CHAT_KIND,
        title: str | None = None,
        generated_title: str | None = None,
        active_document_id=None,
        created_at=None,
        messages: list[dict] | None = None,
    ) -> str:
        repository = ChatHistoryRepository(chat_contract_db)
        repository.create_session(
            session_id=session_id,
            user_auth_sub=user_auth_sub,
            chat_kind=chat_kind,
            title=title,
            generated_title=generated_title,
            active_document_id=active_document_id,
            created_at=created_at,
        )
        for message in messages or []:
            repository.append_message(
                session_id=session_id,
                user_auth_sub=user_auth_sub,
                chat_kind=message.get("chat_kind", chat_kind),
                role=message["role"],
                content=message["content"],
                message_type=message.get("message_type", "text"),
                turn_id=message.get("turn_id"),
                payload_json=message.get("payload_json"),
                trace_id=message.get("trace_id"),
                created_at=message.get("created_at"),
            )
        chat_contract_db.commit()
        chat_contract_db.execute(
            text(
                """
                UPDATE chat_sessions
                SET
                    last_message_at = (
                        SELECT MAX(chat_messages.created_at)
                        FROM chat_messages
                        WHERE chat_messages.session_id = :session_id
                    ),
                    search_vector = to_tsvector(
                        'english',
                        concat_ws(
                            ' ',
                            COALESCE(title, ''),
                            COALESCE(generated_title, ''),
                            COALESCE(
                                (
                                    SELECT string_agg(chat_messages.content, ' ' ORDER BY chat_messages.created_at)
                                    FROM chat_messages
                                    WHERE chat_messages.session_id = :session_id
                                ),
                                ''
                            )
                        )
                    )
                WHERE session_id = :session_id
                """
            ),
            {"session_id": session_id},
        )
        chat_contract_db.commit()
        return session_id

    return _seed
