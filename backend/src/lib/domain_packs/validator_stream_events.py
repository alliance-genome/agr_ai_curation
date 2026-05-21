"""Stream event helpers for package-scoped validator agent runs."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol


LOGGER = logging.getLogger(__name__)

_OUTPUT_PREVIEW_LIMIT = 300


class ValidatorStreamEventEmitter(Protocol):
    """Callable used to surface validator-internal stream events."""

    def __call__(self, event: dict[str, Any]) -> None:
        """Emit one validator stream event."""


async def emit_validator_agent_stream_events(
    stream_events: AsyncIterator[Any],
    *,
    event_emitter: ValidatorStreamEventEmitter | None,
    validator_binding_id: str,
    validator_agent: dict[str, Any],
    validator_request_id: str | None = None,
    validator_request_ids: list[str] | None = None,
    validator_batch_family: str | None = None,
) -> None:
    """Translate OpenAI SDK stream items into validator-owned progress events."""

    if event_emitter is None:
        async for _event in stream_events:
            pass
        return

    pending_tool_calls: deque[dict[str, Any]] = deque()
    async for event in stream_events:
        if getattr(event, "type", None) != "run_item_stream_event":
            continue

        item = getattr(event, "item", None)
        if item is None:
            continue

        item_type = getattr(item, "type", None)
        if item_type == "tool_call_item":
            tool_started_at = datetime.now(timezone.utc)
            tool_name = _stream_tool_name(item)
            tool_args = _stream_tool_args(item)
            pending_tool_calls.append(
                {
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "tool_id": _stream_tool_call_tracking_id(item),
                    "started_at": tool_started_at,
                }
            )
            _emit_validator_stream_event(
                event_emitter,
                {
                    "event": "validator_tool_start",
                    "timestamp": tool_started_at.isoformat(),
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "validator_binding_id": validator_binding_id,
                    "validator_agent": validator_agent,
                    "validator_request_id": validator_request_id,
                    "validator_request_ids": list(validator_request_ids or []),
                    "validator_batch_family": validator_batch_family,
                    "tool_call_id": _stream_tool_call_tracking_id(item),
                },
            )
            continue

        if item_type != "tool_call_output_item":
            continue

        completed_tool = _pop_matching_pending_tool_call(
            pending_tool_calls,
            output_item=item,
        )
        if completed_tool is None:
            continue

        output = getattr(item, "output", "")
        duration_ms = _duration_ms(completed_tool.get("started_at"))
        _emit_validator_stream_event(
            event_emitter,
            {
                "event": "validator_tool_complete",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_name": completed_tool.get("tool_name") or "unknown_tool",
                "validator_binding_id": validator_binding_id,
                "validator_agent": validator_agent,
                "validator_request_id": validator_request_id,
                "validator_request_ids": list(validator_request_ids or []),
                "validator_batch_family": validator_batch_family,
                "tool_call_id": completed_tool.get("tool_id")
                or _stream_tool_call_tracking_id(item),
                "duration_ms": duration_ms,
                "success": True,
                "output_preview": _preview_output(output),
            },
        )


def _stream_tool_name(item: Any) -> str:
    raw_item = getattr(item, "raw_item", None)
    return str(
        getattr(item, "name", None)
        or getattr(item, "tool_name", None)
        or getattr(raw_item, "name", None)
        or "unknown_tool"
    )


def _stream_tool_args(item: Any) -> dict[str, Any] | None:
    raw_item = getattr(item, "raw_item", None)
    arguments = getattr(raw_item, "arguments", None) or getattr(item, "arguments", None)
    if not arguments:
        return None
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return None
    try:
        parsed = json.loads(arguments)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _stream_tool_call_tracking_id(item: Any) -> str | None:
    raw_item = getattr(item, "raw_item", None)
    candidates = (
        getattr(item, "id", None),
        getattr(item, "tool_id", None),
        getattr(item, "tool_call_id", None),
        getattr(item, "call_id", None),
        getattr(raw_item, "id", None),
        getattr(raw_item, "tool_id", None),
        getattr(raw_item, "tool_call_id", None),
        getattr(raw_item, "call_id", None),
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _pop_matching_pending_tool_call(
    pending_tool_calls: deque[dict[str, Any]],
    *,
    output_item: Any,
) -> dict[str, Any] | None:
    if not pending_tool_calls:
        return None

    output_tool_id = _stream_tool_call_tracking_id(output_item)
    if output_tool_id:
        for candidate in list(pending_tool_calls):
            if str(candidate.get("tool_id") or "").strip() == output_tool_id:
                pending_tool_calls.remove(candidate)
                return candidate

    if len(pending_tool_calls) > 1:
        LOGGER.warning(
            "Ambiguous validator tool output without matching call_id; "
            "falling back to oldest pending tool call",
            extra={"output_tool_id": output_tool_id, "pending_count": len(pending_tool_calls)},
        )
    return pending_tool_calls.popleft()


def _duration_ms(started_at: Any) -> int | None:
    if not isinstance(started_at, datetime):
        return None
    elapsed = datetime.now(timezone.utc) - started_at
    return int(elapsed.total_seconds() * 1000)


def _preview_output(output: Any) -> str:
    text = str(output)
    if len(text) <= _OUTPUT_PREVIEW_LIMIT:
        return text
    return f"{text[:_OUTPUT_PREVIEW_LIMIT]}..."


def _emit_validator_stream_event(
    event_emitter: ValidatorStreamEventEmitter,
    event: dict[str, Any],
) -> None:
    try:
        event_emitter(event)
    except Exception:
        LOGGER.debug("Validator stream event emitter failed", exc_info=True)
