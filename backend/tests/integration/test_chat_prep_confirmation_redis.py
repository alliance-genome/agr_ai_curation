"""Real Redis approval consumption across concurrent chat workers."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.lib.curation_workspace import chat_prep_confirmation as prep
from src.schemas.curation_workspace import CurationExtractionResultRecord, CurationExtractionSourceKind


@pytest.mark.asyncio
async def test_two_workers_cannot_consume_the_same_scope_confirmation(monkeypatch):
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    owner, session = "test-" + uuid4().hex, "test-" + uuid4().hex
    key = "chat:curation-prep:" + hashlib.sha256(json.dumps([owner, session]).encode()).hexdigest()
    record = CurationExtractionResultRecord(
        extraction_result_id="result", document_id="document", adapter_key="allele",
        agent_key="extractor", source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=session, user_id=owner, candidate_count=4,
        payload_json={"objects": ["fixture"]}, created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(prep, "get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(prep, "list_extraction_results", lambda **kwargs: [record])
    monkeypatch.setattr(prep, "summarize_curation_prep_scope", lambda selected:
                        SimpleNamespace(candidate_count=4, adapter_keys=["allele"]))
    monkeypatch.setattr(prep, "latest_assistant_trace_for_session", lambda **kwargs: "preview")
    run = AsyncMock(return_value=SimpleNamespace(review_row_count=4,
                    run_metadata=SimpleNamespace(warnings=[], processing_notes=[])))
    monkeypatch.setattr(prep, "run_curation_prep", run)
    args: dict[str, Any] = dict(candidate_scope="all_candidates_in_results", result_refs=["extraction-result:result"],
                expected_candidate_count=4, session_id=session, user_id=owner, document_id="document")
    try:
        preview = json.loads(await prep.prepare_from_chat(**args, action="preview", trace_id="preview",
                                                         authoritative_user_request="Prepare all four"))
        assert preview["status"] == "confirmation_required"
        assert await redis.ttl(key) > 0
        replies = await asyncio.gather(*[
            prep.prepare_from_chat(**args, action="confirm", trace_id=f"worker-{worker}",
                                   authoritative_user_request="Yes please") for worker in range(2)
        ])
        assert sorted(json.loads(reply)["status"] for reply in replies) == ["confirmation_required", "prepared"]
        assert run.await_count == 1
        assert await redis.get(key) is None
    finally:
        await redis.delete(key)
        await redis.aclose()
