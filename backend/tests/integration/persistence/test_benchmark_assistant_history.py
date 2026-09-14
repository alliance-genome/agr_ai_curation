"""Exercise durable Chat turns with real indexes on temporary PostgreSQL tables."""

import os
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from src.lib.benchmarks.assistant_history import (
    AssistantTurnConflict,
    complete_assistant_turn,
    create_assistant_session,
    prepare_assistant_turn,
)
from src.lib.chat_history_repository import ChatHistoryRepository, ChatHistorySessionNotFoundError
from src.lib.benchmarks import assistant_turns
from src.lib.agent_studio.openai_runtime import AgentStudioRunState
from src.models.sql.chat_message import ChatMessage
from src.models.sql.chat_session import ChatSession


@pytest.mark.skipif(
    not os.getenv("BENCHMARK_ASSISTANT_TEST_DATABASE_URL"),
    reason="requires explicitly selected isolated PostgreSQL",
)
def test_real_turn_replay_owner_and_changed_request_preserve_first_completion(monkeypatch):
    engine = sa.create_engine(os.environ["BENCHMARK_ASSISTANT_TEST_DATABASE_URL"])
    try:
        with engine.connect() as connection, connection.begin():
            for model in (ChatSession, ChatMessage):
                # Use actual production columns/constraints/indexes, omitting only
                # unrelated document/session FKs for these isolated temp tables.
                ddl = str(CreateTable(model.__table__, include_foreign_key_constraints=[])
                          .compile(dialect=connection.dialect))
                connection.exec_driver_sql(ddl.replace("CREATE TABLE", "CREATE TEMP TABLE", 1))
                for index in model.__table__.indexes:
                    connection.execute(CreateIndex(index))
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                repo = ChatHistoryRepository(db)
                session = create_assistant_session(repo, subject="curator")
                args = dict(subject="curator", session_id=session.session_id, turn_id="turn-1",
                            message="Check my draft", context={"revision": 1})
                first = prepare_assistant_turn(repo, **args)
                assert first.replay is None
                db.commit()
                usage = {"assistant_usage": {"input_tokens": 42, "cost_usd": None}}
                completed = complete_assistant_turn(
                    repo, subject="curator", session_id=session.session_id, turn_id="turn-1",
                    message="Please review this proposed correction.", payload=usage, trace_id="trace",
                )
                db.commit()
                replay = prepare_assistant_turn(repo, **args)
                assert replay.replay.message_id == completed.message_id
                assert replay.replay.payload_json == usage
                with pytest.raises(AssistantTurnConflict):
                    prepare_assistant_turn(repo, **(args | {"message": "Changed request"}))
                with pytest.raises(ChatHistorySessionNotFoundError):
                    prepare_assistant_turn(repo, **(args | {"subject": "foreign"}))
                repeated = complete_assistant_turn(
                    repo, subject="curator", session_id=session.session_id, turn_id="turn-1",
                    message="Must not replace first answer", payload={}, trace_id=None,
                )
                assert repeated.message_id == completed.message_id
                assert repeated.payload_json == usage
                assert db.scalar(sa.select(sa.func.count()).select_from(ChatMessage)) == 2
                @contextmanager
                def existing_session():
                    yield db

                monkeypatch.setattr(assistant_turns, "SessionLocal", existing_session)
                for status, content in (("cancelled", ""), ("failed", ""),
                                        ("completed", ""), ("cancelled", " \n\t")):
                    turn_id = status + str(len(content))
                    empty_args = args | {"turn_id": turn_id}
                    prepare_assistant_turn(repo, **empty_args)
                    db.commit()
                    assistant_turns._persist(
                        owner="curator", session_id=session.session_id, turn_id=turn_id,
                        state=AgentStudioRunState(trace_id="empty-turn", assistant_text_parts=[content]),
                        status=status, elapsed=1,
                    )
                    stored = prepare_assistant_turn(repo, **empty_args).replay
                    assert stored is not None and stored.content.strip()
                    assert stored.payload_json["status"] == status
                    assert stored.payload_json["assistant_usage"]["input_tokens"] is None
    finally:
        engine.dispose()
