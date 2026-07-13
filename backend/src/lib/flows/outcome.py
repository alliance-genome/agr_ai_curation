"""Canonical terminal outcome reduction for streamed flow runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FlowRunStatus = Literal["running", "completed", "failed"]
FlowPersistenceStatus = Literal["pending", "succeeded", "failed"]

_TYPED_SUCCESS_OUTPUT_EVENTS = {"FILE_READY", "CHAT_OUTPUT_READY"}


class FlowRunOutcomeNotDurableError(RuntimeError):
    """Signal that no terminal event may publish for the failed outcome."""


@dataclass
class FlowRunOutcome:
    """Reduce runtime events to the durable, user-visible flow outcome.

    Typed success outputs are retained as candidates until the authoritative
    ``FLOW_FINISHED`` event is observed and transcript persistence succeeds.
    A raw ``RUN_FINISHED`` response is only a fallback when no typed output was
    produced. A failed terminal status always discards all success candidates.
    """

    status: FlowRunStatus = "running"
    failure_reason: str | None = None
    final_user_visible_text: str | None = None
    persistence_status: FlowPersistenceStatus = "pending"
    persistence_result: dict[str, Any] = field(default_factory=dict)
    _success_output_events: list[dict[str, Any]] = field(default_factory=list)
    _run_finished_event: dict[str, Any] | None = None
    _run_error_event: dict[str, Any] | None = None
    _flow_finished_event: dict[str, Any] | None = None
    _replacement_failure_events: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, event: dict[str, Any]) -> None:
        """Consume one flattened endpoint event without publishing it."""

        event_type = str(event.get("type") or "")
        if event_type == "RUN_FINISHED":
            self._run_finished_event = dict(event)
            if not self._success_output_events:
                self.final_user_visible_text = self._extract_visible_text(event)
            return

        if event_type in _TYPED_SUCCESS_OUTPUT_EVENTS:
            candidate = dict(event)
            identity = self._success_output_identity(candidate)
            if not any(
                self._success_output_identity(existing) == identity
                for existing in self._success_output_events
            ):
                self._success_output_events.append(candidate)
            self.final_user_visible_text = self._combined_visible_text(
                self._success_output_events
            )
            return

        if event_type == "RUN_ERROR":
            self.status = "failed"
            self._run_error_event = dict(event)
            self.failure_reason = str(event.get("message") or "Flow execution failed.")
            self.final_user_visible_text = None
            self._success_output_events = []
            self._run_finished_event = None
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
            self._success_output_events = []
            self._run_finished_event = None
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

        if self.status == "failed" and self._replacement_failure_events:
            return [dict(event) for event in self._replacement_failure_events]

        events: list[dict[str, Any]] = []
        if self.status == "completed" and self._success_output_events:
            events.extend(dict(event) for event in self._success_output_events)
        elif self.status == "completed" and self._run_finished_event is not None:
            events.append(dict(self._run_finished_event))
        elif self.status == "failed" and self._run_error_event is not None:
            events.append(dict(self._run_error_event))
        if self._flow_finished_event is not None:
            events.append(dict(self._flow_finished_event))
        return events

    def mark_persisted(self, **result: Any) -> None:
        self.persistence_status = "succeeded"
        self.persistence_result = dict(result)

    def replace_with_persistence_failure(
        self,
        reason: str,
        *,
        terminal_events: list[dict[str, Any]],
    ) -> None:
        """Replace a non-durable candidate with its recoverable failed truth."""

        self.persistence_status = "failed"
        self.persistence_result = {"reason": reason}
        self.status = "failed"
        self.failure_reason = reason
        self.final_user_visible_text = None
        self._success_output_events = []
        self._run_finished_event = None
        self._run_error_event = None
        self._flow_finished_event = None
        self._replacement_failure_events = [dict(event) for event in terminal_events]

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

    @classmethod
    def _combined_visible_text(cls, events: list[dict[str, Any]]) -> str | None:
        visible = [
            text
            for event in events
            if (text := cls._extract_visible_text(event)) is not None
        ]
        return "\n\n".join(visible) or None

    @staticmethod
    def _success_output_identity(event: dict[str, Any]) -> tuple[str, str, str]:
        event_type = str(event.get("type") or "")
        details = event.get("details")
        details = details if isinstance(details, dict) else {}
        formatter_node_id = str(details.get("formatter_node_id") or "")
        if event_type == "FILE_READY":
            output_identity = str(
                details.get("file_id")
                or f"{details.get('format') or details.get('file_type') or ''}:"
                f"{details.get('filename') or ''}"
            )
        else:
            output_identity = str(details.get("output") or event.get("output") or "")
        return event_type, formatter_node_id, output_identity
