"""Canonical terminal outcome reduction for streamed flow runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FlowRunStatus = Literal["running", "completed", "failed"]
FlowPersistenceStatus = Literal["pending", "succeeded", "failed"]

_SUCCESS_OUTPUT_EVENT_PRIORITY = {
    "RUN_FINISHED": 1,
    "FILE_READY": 2,
    "CHAT_OUTPUT_READY": 3,
}


@dataclass
class FlowRunOutcome:
    """Reduce runtime events to the one durable, user-visible flow outcome.

    Success output is retained as a candidate until the authoritative
    ``FLOW_FINISHED`` event is observed and transcript persistence succeeds.
    A failed terminal status always discards that candidate.
    """

    status: FlowRunStatus = "running"
    failure_reason: str | None = None
    final_user_visible_text: str | None = None
    persistence_status: FlowPersistenceStatus = "pending"
    persistence_result: dict[str, Any] = field(default_factory=dict)
    _success_output_event: dict[str, Any] | None = None
    _run_error_event: dict[str, Any] | None = None
    _flow_finished_event: dict[str, Any] | None = None

    def observe(self, event: dict[str, Any]) -> None:
        """Consume one flattened endpoint event without publishing it."""

        event_type = str(event.get("type") or "")
        if event_type in _SUCCESS_OUTPUT_EVENT_PRIORITY:
            current_type = str((self._success_output_event or {}).get("type") or "")
            if _SUCCESS_OUTPUT_EVENT_PRIORITY[event_type] >= _SUCCESS_OUTPUT_EVENT_PRIORITY.get(
                current_type, 0
            ):
                self._success_output_event = dict(event)
                self.final_user_visible_text = self._extract_visible_text(event)
            return

        if event_type == "RUN_ERROR":
            self.status = "failed"
            self._run_error_event = dict(event)
            self.failure_reason = str(event.get("message") or "Flow execution failed.")
            self.final_user_visible_text = None
            self._success_output_event = None
            return

        if event_type != "FLOW_FINISHED":
            return

        self._flow_finished_event = dict(event)
        terminal_status = str(event.get("status") or "failed")
        if terminal_status != "completed" or self.status == "failed":
            self.status = "failed"
            self.failure_reason = str(
                event.get("failure_reason") or self.failure_reason or "Flow execution failed."
            )
            self.final_user_visible_text = None
            self._success_output_event = None
            self._flow_finished_event["status"] = "failed"
            self._flow_finished_event["failure_reason"] = self.failure_reason
        else:
            self.status = "completed"
            self.failure_reason = None

    @property
    def terminal(self) -> bool:
        return self._flow_finished_event is not None or self._run_error_event is not None

    def events_for_persistence(self) -> list[dict[str, Any]]:
        """Return the canonical terminal order to store for durable replay."""

        events: list[dict[str, Any]] = []
        if self.status == "completed" and self._success_output_event is not None:
            events.append(dict(self._success_output_event))
        elif self.status == "failed" and self._run_error_event is not None:
            events.append(dict(self._run_error_event))
        if self._flow_finished_event is not None:
            events.append(dict(self._flow_finished_event))
        return events

    def mark_persisted(self, **result: Any) -> None:
        self.persistence_status = "succeeded"
        self.persistence_result = dict(result)

    def mark_persistence_failed(self, reason: str) -> None:
        self.persistence_status = "failed"
        self.persistence_result = {"reason": reason}
        self.status = "failed"
        self.failure_reason = reason
        self.final_user_visible_text = None
        self._success_output_event = None

    def publishable_terminal_events(self) -> list[dict[str, Any]]:
        """Release terminal events only after their durable transcript commit."""

        if self.persistence_status != "succeeded":
            return []
        return self.events_for_persistence()

    @staticmethod
    def _extract_visible_text(event: dict[str, Any]) -> str | None:
        event_type = str(event.get("type") or "")
        if event_type == "RUN_FINISHED":
            value = event.get("response")
        elif event_type == "CHAT_OUTPUT_READY":
            details = event.get("details")
            value = details.get("output") if isinstance(details, dict) else event.get("output")
        else:
            value = None
        normalized = str(value or "").strip()
        return normalized or None
