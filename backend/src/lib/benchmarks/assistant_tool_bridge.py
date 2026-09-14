"""One live turn's named-tool requests, relayed by the authenticated portal.

No destinations, owner claims, code or provider credentials cross this bridge.
The API supplies the verified owner; the portal independently authorizes every
resource read. This object owns no jobs or model runs and is discarded when its
existing executable-run producer terminates. Durable proposals are separate.
"""

import asyncio
import hashlib
import json
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.lib.agent_studio.openai_runtime import ToolExecutionResult
from src.lib.openai_agents.config import (
    get_benchmark_assistant_max_tool_calls,
    get_benchmark_assistant_tool_result_max_bytes,
    get_benchmark_assistant_tool_timeout_seconds,
)


class PaperDraftSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID


class PaperDraftProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID
    expected_revision: StrictInt = Field(ge=1)
    base_draft_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate: dict[str, Any]
    change_summary: str = Field(min_length=1)


PAPER_DRAFT_TOOL = {
    "name": "read_paper_reference_draft",
    "description": "Read your saved paper-reference draft and its source. This does not edit or publish it.",
    "input_schema": PaperDraftSelection.model_json_schema(),
}
PAPER_PROPOSAL_TOOL = {
    "name": "propose_paper_reference_draft",
    "description": (
        "Prepare a candidate for curator review, never apply or publish. Read the current draft first. "
        "Use its revision and draft_fingerprint; candidate must preserve the content object shape "
        "(entities and correction_evidence). Preserve unrelated fields and explain every removal. "
        "The portal validates the scientific entity schema. Stop after a valid pending proposal."
    ),
    "input_schema": PaperDraftProposal.model_json_schema(),
}


class AssistantToolReplyError(ValueError):
    """Missing, stale, conflicting or invalid tool reply; contains no payload."""


class AssistantToolBridge:
    def __init__(self, *, owner: str, cancel_event: asyncio.Event) -> None:
        if not owner.strip():
            raise ValueError("Verified owner required")
        self.owner = owner
        self.cancel_event = cancel_event
        self.max_calls = get_benchmark_assistant_max_tool_calls()
        self.max_bytes = get_benchmark_assistant_tool_result_max_bytes()
        self.timeout = get_benchmark_assistant_tool_timeout_seconds()
        self.requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.max_calls)
        self._pending: dict[str, asyncio.Future[ToolExecutionResult]] = {}
        self._replies: dict[str, str] = {}
        self._calls = 0
        self._closed = False

    async def execute(self, name: str, arguments: dict[str, Any], call_id: str | None) -> ToolExecutionResult:
        if self._closed or self.cancel_event.is_set():
            raise asyncio.CancelledError()
        if name == PAPER_DRAFT_TOOL["name"]:
            selected = PaperDraftSelection.model_validate(arguments)
        elif name == PAPER_PROPOSAL_TOOL["name"]:
            selected = PaperDraftProposal.model_validate(arguments)
        else:
            raise PermissionError("Assistant tool is unavailable")
        if self._calls >= self.max_calls:
            raise AssistantToolReplyError("Assistant tool budget exhausted")
        self._calls += 1
        request_id = str(uuid4())
        future: asyncio.Future[ToolExecutionResult] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        stopped = asyncio.create_task(self.cancel_event.wait())
        try:
            await self.requests.put({
                "type": "TOOL_REQUEST", "tool_request_id": request_id,
                "tool_name": name, "arguments": selected.model_dump(mode="json"),
                "call_id": call_id,
            })
            done, _ = await asyncio.wait(
                {future, stopped}, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED,
            )
            if stopped in done or self._closed:
                raise asyncio.CancelledError()
            if future not in done:
                raise AssistantToolReplyError("Portal tool reply timed out")
            return future.result()
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            stopped.cancel()
            with suppress(asyncio.CancelledError):
                await stopped

    def reply(self, *, owner: str, request_id: UUID, output: dict[str, Any]) -> None:
        if owner != self.owner:
            raise PermissionError("Assistant tool reply is owned by another user")
        if self._closed or self.cancel_event.is_set():
            raise AssistantToolReplyError("Assistant turn is no longer accepting replies")
        try:
            encoded = json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                                 sort_keys=True)
        except (TypeError, ValueError):
            raise AssistantToolReplyError("Invalid portal tool reply") from None
        if len(encoded.encode("utf-8")) > self.max_bytes:
            raise AssistantToolReplyError("Portal tool reply exceeds configured limit")
        key = str(request_id)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        previous = self._replies.get(key)
        if previous is not None:
            if previous != fingerprint:
                raise AssistantToolReplyError("Portal tool reply changed")
            return
        future = self._pending.get(key)
        if future is None or future.done():
            raise AssistantToolReplyError("Portal tool request not found")
        self._replies[key] = fingerprint
        # Snapshot caller data through canonical JSON: subsequent mutation of the
        # HTTP payload cannot alter the accepted result or its audit fingerprint.
        future.set_result(ToolExecutionResult(full_output=json.loads(encoded), provider_output=encoded))

    def close(self) -> None:
        self._closed = True
        for future in self._pending.values():
            future.cancel()
        self._replies.clear()
