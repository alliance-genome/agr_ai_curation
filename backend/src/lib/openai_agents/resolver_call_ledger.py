"""Run-scoped ledger for authoritative resolver tool selections."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic_core import PydanticSerializationError  # type: ignore[import-not-found]

from .extraction_trace_events import write_extraction_trace_event

logger = logging.getLogger(__name__)

RESOLVER_TOOL_NAME = "resolve_domain_field_term"
LEDGER_EVENT_RECORDED = "resolver_call_ledger.recorded"
LEDGER_EVENT_REJECTED = "resolver_call_ledger.rejected"
LEDGER_EVENT_LOOKUP_FAILED = "resolver_call_ledger.lookup_failed"

_ACTIVE_RESOLVER_CALL_LEDGER: ContextVar["ResolverCallLedger | None"] = ContextVar(
    "active_resolver_call_ledger",
    default=None,
)


@dataclass(frozen=True)
class ResolverCallLedgerEntry:
    """One validated resolver selection keyed by runtime tool call ID."""

    tool_call_id: str
    tool_name: str
    domain_pack_id: str
    object_type: str
    field_path: str
    source_phrase: str
    selected_value: str
    lookup_status: str
    helper_selection: dict[str, Any]
    payload_field_instructions: dict[str, Any]
    raw_output: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "domain_pack_id": self.domain_pack_id,
            "object_type": self.object_type,
            "field_path": self.field_path,
            "source_phrase": self.source_phrase,
            "selected_value": self.selected_value,
            "lookup_status": self.lookup_status,
        }

    def provenance_selection(self) -> dict[str, Any]:
        selection = deepcopy(self.helper_selection)
        selection["source_tool"] = RESOLVER_TOOL_NAME
        selection["resolver_tool_name"] = self.tool_name
        selection["resolver_call_id"] = self.tool_call_id
        return selection


class ResolverCallLedger:
    """Validated resolver outputs for one active extraction run."""

    def __init__(self, *, trace_id: str | None = None) -> None:
        self.trace_id = _optional_string(trace_id)
        self._entries: dict[str, ResolverCallLedgerEntry] = {}

    def record_tool_output(
        self,
        *,
        tool_call_id: str | None,
        tool_name: str,
        output: Any,
    ) -> ResolverCallLedgerEntry | None:
        normalized_call_id = _optional_string(tool_call_id)
        if not normalized_call_id:
            self._emit_rejection(
                reason="missing_tool_call_id",
                tool_call_id=None,
                tool_name=tool_name,
                output=output,
            )
            return None
        if tool_name != RESOLVER_TOOL_NAME:
            return None

        payload = _coerce_mapping(output)
        if payload is None:
            self._emit_rejection(
                reason="unparseable_output",
                tool_call_id=normalized_call_id,
                tool_name=tool_name,
                output=output,
            )
            return None

        try:
            entry = self._entry_from_payload(
                tool_call_id=normalized_call_id,
                tool_name=tool_name,
                payload=payload,
            )
        except ValueError as exc:
            self._emit_rejection(
                reason=str(exc),
                tool_call_id=normalized_call_id,
                tool_name=tool_name,
                output=payload,
            )
            return None
        if entry is None:
            self._emit_rejection(
                reason="output_not_resolved",
                tool_call_id=normalized_call_id,
                tool_name=tool_name,
                output=payload,
            )
            return None

        self._entries[entry.tool_call_id] = entry
        write_extraction_trace_event(
            event_type=LEDGER_EVENT_RECORDED,
            trace_id=self.trace_id,
            tool_call_id=entry.tool_call_id,
            domain_pack_id=entry.domain_pack_id,
            output_summary=entry.summary(),
            metadata={"tool_name": entry.tool_name, "field_path": entry.field_path},
        )
        return entry

    def get(self, tool_call_id: str) -> ResolverCallLedgerEntry:
        normalized_call_id = _required_string(tool_call_id, "resolver_call_id")
        try:
            return self._entries[normalized_call_id]
        except KeyError as exc:
            write_extraction_trace_event(
                event_type=LEDGER_EVENT_LOOKUP_FAILED,
                trace_id=self.trace_id,
                tool_call_id=normalized_call_id,
                output_summary={"resolver_call_id": normalized_call_id},
                validation={
                    "status": "failed",
                    "reason": "unknown_resolver_call_id",
                },
            )
            raise KeyError(f"Unknown resolver_call_id: {normalized_call_id}") from exc

    def snapshot(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "entry_count": len(self._entries),
            "tool_call_ids": sorted(self._entries),
            "entries": [entry.summary() for entry in self._entries.values()],
        }

    def _entry_from_payload(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> ResolverCallLedgerEntry | None:
        if payload.get("status") != "resolved":
            return None
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return None
        helper_selection = data.get("helper_selection")
        if not isinstance(helper_selection, Mapping):
            return None
        required_helper_fields = (
            "field_path",
            "authority",
            "lookup_status",
            "source_phrase",
            "term_source",
            "selected_value",
        )
        if any(not helper_selection.get(field) for field in required_helper_fields):
            return None
        if helper_selection.get("source_tool") != RESOLVER_TOOL_NAME:
            return None

        payload_instructions = data.get("payload_field_instructions")
        if not isinstance(payload_instructions, Mapping) or not payload_instructions:
            return None

        return ResolverCallLedgerEntry(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            domain_pack_id=_required_string(data.get("domain_pack_id"), "domain_pack_id"),
            object_type=_required_string(data.get("object_type"), "object_type"),
            field_path=_required_string(helper_selection.get("field_path"), "field_path"),
            source_phrase=_required_string(helper_selection.get("source_phrase"), "source_phrase"),
            selected_value=_required_string(helper_selection.get("selected_value"), "selected_value"),
            lookup_status=_required_string(helper_selection.get("lookup_status"), "lookup_status"),
            helper_selection=deepcopy(dict(helper_selection)),
            payload_field_instructions=deepcopy(dict(payload_instructions)),
            raw_output=deepcopy(dict(payload)),
        )

    def _emit_rejection(
        self,
        *,
        reason: str,
        tool_call_id: str | None,
        tool_name: str,
        output: Any,
    ) -> None:
        write_extraction_trace_event(
            event_type=LEDGER_EVENT_REJECTED,
            trace_id=self.trace_id,
            tool_call_id=tool_call_id,
            output_summary={"tool_name": tool_name, "reason": reason, "output": output},
            validation={"status": "failed", "reason": reason},
            metadata={"tool_name": tool_name},
        )


def set_active_resolver_call_ledger(
    ledger: ResolverCallLedger | None,
) -> Token[ResolverCallLedger | None]:
    return _ACTIVE_RESOLVER_CALL_LEDGER.set(ledger)


def reset_active_resolver_call_ledger(token: Token[ResolverCallLedger | None]) -> None:
    _ACTIVE_RESOLVER_CALL_LEDGER.reset(token)


def get_active_resolver_call_ledger() -> ResolverCallLedger:
    ledger = _ACTIVE_RESOLVER_CALL_LEDGER.get()
    if ledger is None:
        raise RuntimeError("No active resolver call ledger is bound to this run")
    return ledger


def _coerce_mapping(output: Any) -> dict[str, Any] | None:
    if isinstance(output, Mapping):
        return dict(output)
    if hasattr(output, "model_dump"):
        try:
            dumped = output.model_dump(mode="json")
            return dict(dumped) if isinstance(dumped, Mapping) else None
        except (TypeError, ValueError, PydanticSerializationError):
            logger.debug("Failed to dump resolver output model", exc_info=True)
            return None
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _required_string(value: Any, field_name: str) -> str:
    text = _optional_string(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


__all__ = [
    "RESOLVER_TOOL_NAME",
    "ResolverCallLedger",
    "ResolverCallLedgerEntry",
    "get_active_resolver_call_ledger",
    "reset_active_resolver_call_ledger",
    "set_active_resolver_call_ledger",
]
