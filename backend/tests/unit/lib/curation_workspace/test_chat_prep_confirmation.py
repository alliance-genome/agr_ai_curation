"""Preparation never broadens a subset or trusts model-authored confirmation."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError

from src.lib.curation_workspace import chat_prep_confirmation as prep
from src.schemas.curation_workspace import CurationExtractionResultRecord, CurationExtractionSourceKind


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, *, ex):
        assert ex > 0
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def eval(self, script, count, key, expected):
        assert count == 1
        if self.values.get(key) != expected:
            return 0
        return await self.delete(key)


@pytest.mark.parametrize("value, expected", [(None, 1800), ("90", 90), ("0", 1), ("invalid", 1800)])
def test_confirmation_expiry_is_configurable_with_safe_default(monkeypatch, value, expected):
    monkeypatch.delenv("CHAT_CURATION_CONFIRMATION_TTL_SECONDS", raising=False)
    if value is not None:
        monkeypatch.setenv("CHAT_CURATION_CONFIRMATION_TTL_SECONDS", value)
    assert prep.get_chat_curation_confirmation_ttl_seconds() == expected


@pytest.fixture
def context(monkeypatch):
    redis = MemoryRedis()
    records = [CurationExtractionResultRecord(
        extraction_result_id="r1", document_id="doc", adapter_key="allele",
        agent_key="extractor", source_kind=CurationExtractionSourceKind.CHAT, origin_session_id="session",
        user_id="user", candidate_count=14, payload_json={"objects": ["saved"]},
        created_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )]
    captured = {}

    def load(**kwargs):
        captured["scope"] = kwargs
        return records

    monkeypatch.setattr(prep, "get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(prep, "list_extraction_results", load)
    monkeypatch.setattr(prep, "latest_assistant_trace_for_session", lambda **kwargs: "preview-turn")
    monkeypatch.setattr(prep, "summarize_curation_prep_scope", lambda selected: SimpleNamespace(
        candidate_count=sum(r.candidate_count for r in selected), adapter_keys=["allele"],
    ))
    run = AsyncMock(return_value=SimpleNamespace(
        review_row_count=14, run_metadata=SimpleNamespace(warnings=[], processing_notes=[]),
    ))
    monkeypatch.setattr(prep, "run_curation_prep", run)
    return SimpleNamespace(redis=redis, records=records, run=run, captured=captured)


async def call(action="preview", **overrides):
    args: dict[str, Any] = dict(
        action=action, candidate_scope="all_candidates_in_results",
        result_refs=["extraction-result:r1"], expected_candidate_count=14,
        authoritative_user_request="Prepare the saved findings", session_id="session",
        user_id="user", trace_id="preview-turn" if action == "preview" else "confirm-turn",
        document_id="doc",
    )
    args.update(overrides)
    return json.loads(await prep.prepare_from_chat(**args))


@pytest.mark.asyncio
async def test_four_of_fourteen_is_never_prepared(context):
    result = await call(candidate_scope="selected_candidates", expected_candidate_count=4)
    assert result["status"] == "unsupported_candidate_scope"
    result = await call(expected_candidate_count=4)
    assert result["status"] == "scope_count_mismatch"
    assert result["candidate_count"] == 14
    context.run.assert_not_called()
    assert not context.redis.values


@pytest.mark.asyncio
async def test_whole_result_preview_confirms_once_without_assistant_phrase(context):
    preview = await call()
    assert preview["status"] == "confirmation_required"
    assert "ALL 14" in preview["message"]
    context.run.assert_not_called()
    result = await call("confirm", authoritative_user_request="Yes please")
    assert result["status"] == "prepared"
    assert context.captured["scope"]["user_id"] == "user"
    assert context.captured["scope"]["origin_session_id"] == "session"
    assert context.captured["scope"]["document_id"] == "doc"
    assert context.captured["scope"]["source_kind"].value == "chat"
    args, kwargs = context.run.call_args
    assert args[0] == context.records
    assert kwargs["scope_confirmation"].expected_review_row_count == 14
    assert kwargs["persistence_context"].trace_id == "confirm-turn"
    assert (await call("confirm", authoritative_user_request="Yes"))["status"] == "confirmation_required"
    assert context.run.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, "No", "not ready", "Yes, but only four", "Yes, exclude the unvalidated ones", "Maybe", "I said yes to exporting"])
async def test_rejection_ambiguity_and_scope_changes_in_actual_user_turn_do_not_confirm(context, answer):
    await call()
    assert (await call("confirm", authoritative_user_request=answer))["status"] == "confirmation_required"
    context.run.assert_not_called()
    assert not context.redis.values


@pytest.mark.asyncio
async def test_same_turn_cannot_consume_preview(context):
    await call()
    assert (await call("confirm", trace_id="preview-turn", authoritative_user_request="Yes"))["status"] == "confirmation_required"
    context.run.assert_not_called()


@pytest.mark.asyncio
async def test_unrelated_preceding_turn_invalidates_confirmation(context, monkeypatch):
    await call()
    monkeypatch.setattr(prep, "latest_assistant_trace_for_session", lambda **kwargs: "unrelated-turn")
    assert (await call("confirm", authoritative_user_request="Yes"))["status"] == "confirmation_required"
    context.run.assert_not_called()


@pytest.mark.asyncio
async def test_changed_saved_payload_requires_new_preview(context):
    await call()
    context.records[0].payload_json = {"objects": ["changed"]}
    assert (await call("confirm", authoritative_user_request="Yes"))["status"] == "confirmation_required"
    context.run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"user_id": "other"}, {"session_id": "other"}, {"document_id": "other"},
    {"result_refs": ["extraction-result:unknown"]}, {"result_refs": []},
    {"result_refs": ["extraction-result:r1", "extraction-result:r1"]},
])
async def test_scope_is_bound_to_user_session_document_and_exact_results(context, change):
    await call()
    result = await call("confirm", authoritative_user_request="Yes", **change)
    assert result["status"] != "prepared"
    context.run.assert_not_called()


@pytest.mark.asyncio
async def test_missing_adapter_and_multiple_documents_are_blocked(context):
    context.records[0].adapter_key = None
    assert (await call())["status"] == "scope_confirmation_required"
    context.records[0].adapter_key = "allele"
    context.records.append(context.records[0].model_copy(update={"document_id": "doc2", "extraction_result_id": "r2"}))
    assert (await call(result_refs=["extraction-result:r1", "extraction-result:r2"], expected_candidate_count=28, document_id=None))["status"] == "scope_confirmation_required"
    context.run.assert_not_called()


@pytest.mark.asyncio
async def test_expired_or_unavailable_confirmation_store_fails_closed(context, monkeypatch):
    await call()
    context.redis.values.clear()
    assert (await call("confirm", authoritative_user_request="Yes"))["status"] == "confirmation_required"
    monkeypatch.setattr(prep, "get_redis", AsyncMock(side_effect=ConnectionError("unavailable")))
    assert (await call())["status"] == "unavailable"
    context.run.assert_not_called()
