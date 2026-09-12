"""Two-turn, scope-bound chat preparation with cross-worker one-time approval."""

import asyncio
import hashlib
import json
import re
from typing import Literal

from redis.exceptions import RedisError

from src.lib.chat_transcript import latest_assistant_trace_for_session
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.curation_prep_service import (
    CurationPrepPersistenceContext, run_curation_prep, summarize_curation_prep_scope,
)
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.openai_agents.config import get_chat_curation_confirmation_ttl_seconds
from src.lib.redis_client import get_redis
from src.schemas.curation_prep import CurationPrepScopeConfirmation
from src.schemas.curation_workspace import CurationExtractionSourceKind


def _reply(status: str, message: str, **extra) -> str:
    return json.dumps({"status": status, "message": message, **extra})


def _affirmative(text: str | None) -> bool:
    # Only an unqualified affirmative authorizes the *already previewed* scope.
    # A response adding exclusions/conditions must be clarified, not broadened.
    return re.fullmatch(
        r"(?:yes(?: please)?|yes[, ]+prepare (?:these|them)(?: for curation)?|"
        r"confirm(?:ed)?|i confirm|go ahead|proceed|please do|do it)[.!\s]*",
        (text or "").strip(), re.IGNORECASE,
    ) is not None


async def prepare_from_chat(
    *, action: Literal["preview", "confirm"],
    candidate_scope: Literal["all_candidates_in_results", "selected_candidates"],
    result_refs: list[str], expected_candidate_count: int,
    authoritative_user_request: str | None, session_id: str | None,
    user_id: str | None, trace_id: str | None, document_id: str | None,
) -> str:
    """Preview exact saved results, then consume a later user confirmation.

    A subset within an envelope is explicitly unsupported until the materializer
    can enforce canonical object selection. No prose scope is accepted as a filter.
    """
    if not session_id or not user_id or not trace_id:
        return _reply("unavailable", "Preparation requires an authenticated traced chat turn.")
    key = "chat:curation-prep:" + hashlib.sha256(
        json.dumps([user_id, session_id]).encode()
    ).hexdigest()
    try:
        client = await get_redis()
        if candidate_scope != "all_candidates_in_results":
            await client.delete(key)
            return _reply("unsupported_candidate_scope", "Preparation cannot select individual candidates within a saved extraction yet. Nothing was prepared. Do not substitute all findings for the requested subset.")
        if action not in {"preview", "confirm"}:
            return _reply("invalid_request", "Choose preview or confirm.")
        if not result_refs or len(set(result_refs)) != len(result_refs) or expected_candidate_count < 1:
            await client.delete(key)
            return _reply("scope_confirmation_required", "Select distinct saved result references and the expected candidate count first.")
        records = await asyncio.to_thread(
            list_extraction_results, origin_session_id=session_id, user_id=user_id,
            source_kind=CurationExtractionSourceKind.CHAT, document_id=document_id,
            exclude_agent_keys=(CURATION_PREP_AGENT_ID,),
        )
        available = {f"extraction-result:{record.extraction_result_id}": record for record in records}
        if any(ref not in available for ref in result_refs):
            await client.delete(key)
            return _reply("scope_confirmation_required", "A selected result is unavailable in this session/document. Inspect results again; nothing was prepared.")
        selected = [available[ref] for ref in sorted(result_refs)]
        documents = {record.document_id for record in selected}
        adapters = {record.adapter_key for record in selected}
        if len(documents) != 1 or len(adapters) != 1 or not all(adapters):
            await client.delete(key)
            return _reply("scope_confirmation_required", "Select one document and one explicit adapter per preparation.")
        summary = await asyncio.to_thread(summarize_curation_prep_scope, selected)
        if summary.candidate_count != expected_candidate_count:
            await client.delete(key)
            return _reply("scope_count_mismatch", "The saved results do not match the requested candidate count. Nothing was prepared; do not broaden the curator's selection.", candidate_count=summary.candidate_count)
        snapshot = {
            "document_id": document_id, "result_refs": sorted(result_refs),
            "expected_candidate_count": expected_candidate_count,
            "payloads": [record.model_dump(mode="json") for record in selected],
        }
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        if action == "preview":
            pending = json.dumps({"scope": digest, "trace_id": trace_id}, sort_keys=True)
            await client.set(key, pending, ex=get_chat_curation_confirmation_ttl_seconds())
            return _reply("confirmation_required", f"Ask the curator to confirm preparing ALL {summary.candidate_count} eligible candidates in these exact saved results. Individual-candidate subsets are not supported. Wait for their next response; nothing has been prepared.", candidate_count=summary.candidate_count, result_refs=sorted(result_refs), adapter_keys=summary.adapter_keys)

        pending = await client.get(key)
        if not pending:
            return _reply("confirmation_required", "Preview the intended saved results and ask for a new confirmation first.")
        approval = json.loads(pending)
        previous_trace = await asyncio.to_thread(
            latest_assistant_trace_for_session, session_id=session_id, user_id=user_id,
        )
        if approval["trace_id"] == trace_id:
            return _reply("confirmation_required", "Wait for the curator's next turn before confirming this preview.")
        if approval["scope"] != digest or previous_trace != approval["trace_id"] or not _affirmative(authoritative_user_request):
            await client.delete(key)
            return _reply("confirmation_required", "The scope, preceding turn, or user response does not confirm this preview. Nothing was prepared; clarify and preview again.")
        # Compare-and-delete prevents another worker from consuming the same
        # approval, or a stale request from consuming a newer preview.
        consumed = await client.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
            1, key, pending,
        )
        if not consumed:
            return _reply("confirmation_required", "The preview was replaced or already consumed. Preview again.")
    except (RedisError, ValueError, KeyError):
        return _reply("unavailable", "The preparation scope could not be verified. Nothing was prepared; retry with a fresh preview.")

    try:
        result = await run_curation_prep(
            selected,
            scope_confirmation=CurationPrepScopeConfirmation(
                confirmed=True, adapter_keys=summary.adapter_keys,
                expected_review_row_count=expected_candidate_count,
                notes=["Confirmed exact saved-result scope from a later authenticated user turn."],
            ),
            persistence_context=CurationPrepPersistenceContext(
                document_id=selected[0].document_id, user_id=user_id,
                source_kind=CurationExtractionSourceKind.CHAT,
                origin_session_id=session_id, trace_id=trace_id,
            ),
        )
    except ValueError as exc:
        return _reply("unable_to_prepare", str(exc))
    return _reply("prepared", f"Prepared {result.review_row_count} candidates for curation review.", candidate_count=result.review_row_count, document_id=selected[0].document_id, adapter_keys=summary.adapter_keys, warnings=list(result.run_metadata.warnings), processing_notes=list(result.run_metadata.processing_notes))
