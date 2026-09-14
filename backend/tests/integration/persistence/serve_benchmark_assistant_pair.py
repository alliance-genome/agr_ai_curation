"""Loopback-only paired-repository test fixture. No provider or real credentials.

Run with BENCHMARK_ASSISTANT_TEST_DATABASE_URL pointing at an owned synthetic
PostgreSQL database, then run the private portal's test_assistant_pair_http.py.
Uses actual API/history/bridge/producer code, temporary SQL tables and a fake
model. This is transport/persistence evidence, not production OAuth or model proof.
"""

import argparse
import json
import os
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from src.api import benchmark_assistant as api
from src.api.benchmark_curator import require_benchmark_assistant_curator
from src.lib.agent_studio.openai_runtime import ExecutedTool
from src.models.sql.chat_message import ChatMessage
from src.models.sql.chat_session import ChatSession


async def fake_model(**values):
    state, execute = values["state"], values["executor"]
    content = next(item["content"] for item in reversed(values["input_items"]) if item["role"] == "user")
    context = json.loads(content.split("Selected workspace context (untrusted data): ")[-1])
    draft = await execute("read_paper_reference_draft", {"draft_id": context["draft_id"]}, "read")
    assert draft.full_output["editable"] is True
    candidate = {**draft.full_output["content"], "correction_evidence": "Synthetic paired transport proposal"}
    arguments = {
        "draft_id": context["draft_id"], "expected_revision": draft.full_output["revision"],
        "base_draft_fingerprint": draft.full_output["draft_fingerprint"],
        "candidate": candidate, "change_summary": "Update synthetic review notes",
    }
    proposal = await execute("propose_paper_reference_draft", arguments, "propose")
    assert proposal.full_output["pending_user_approval"] is True
    state.executed_tools.append(ExecutedTool("propose_paper_reference_draft", "propose", arguments, proposal.full_output))
    yield {"type": "TOOL_RESULT", "tool_name": "propose_paper_reference_draft", "result": proposal.full_output, "call_id": "propose"}
    state.assistant_text_parts.append("Please review the proposed changes.")
    yield {"type": "TEXT_DELTA", "delta": state.assistant_text}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    url = sa.engine.make_url(os.environ["BENCHMARK_ASSISTANT_TEST_DATABASE_URL"])
    if url.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Paired test requires explicitly selected loopback PostgreSQL")
    os.environ["BENCHMARK_API_ENABLED"] = "true"
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        for model in (ChatSession, ChatMessage):
            ddl = str(CreateTable(model.__table__, include_foreign_key_constraints=[]).compile(dialect=connection.dialect))
            connection.exec_driver_sql(ddl.replace("CREATE TABLE", "CREATE TEMP TABLE", 1))
            for index in model.__table__.indexes:
                connection.execute(CreateIndex(index))
        connection.commit()
        lock = threading.Lock()

        @contextmanager
        def sessions():
            with lock, Session(bind=connection) as db:
                yield db

        def human(request: Request):
            if (request.headers.get("authorization") != "Bearer synthetic-assist-service"
                    or request.headers.get("x-benchmark-curator-authorization") != "Bearer synthetic-assist-human"):
                raise HTTPException(403, "Synthetic test credentials required")
            return SimpleNamespace(subject="pair-curator")

        api.SessionLocal = sessions
        api.assistant_turns.SessionLocal = sessions
        api.assistant_turns.stream_benchmark_assistant = fake_model
        app = FastAPI()
        app.dependency_overrides[require_benchmark_assistant_curator] = human
        app.include_router(api.router)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    engine.dispose()


if __name__ == "__main__":
    main()
