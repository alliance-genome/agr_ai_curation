"""Application-owned delivery of rendered flow chat output.

The chat-output formatter chooses projection operations; application code
renders every requested row and records the rendered content here. The flow
executor that opened the delivery emits it to the UI and transcript once,
while the formatter model and the flow supervisor receive only a compact
receipt that never contains the table.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class ChatOutputDelivery:
    """Rendered chat content held by the application for one flow chat step."""

    output: str | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    cannot_complete: dict[str, Any] | None = None
    # A finalization failure already reported through the payload contract.
    failure: dict[str, Any] | None = None

    @property
    def delivered(self) -> bool:
        return self.output is not None


_current_delivery: ContextVar[ChatOutputDelivery | None] = ContextVar(
    "flow_chat_output_delivery",
    default=None,
)


@contextmanager
def chat_output_delivery_scope() -> Iterator[ChatOutputDelivery]:
    """Open the delivery slot that one flow chat-output step may fill once."""

    delivery = ChatOutputDelivery()
    token = _current_delivery.set(delivery)
    try:
        yield delivery
    finally:
        _current_delivery.reset(token)


def current_chat_output_delivery() -> ChatOutputDelivery | None:
    return _current_delivery.get()


async def deliver_projected_chat_output(
    content: str,
    receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Hold rendered chat content for the executor; return the compact receipt."""

    delivery = _current_delivery.get()
    if delivery is None:
        raise RuntimeError(
            "Chat output delivery is only available inside a bound flow chat-output step."
        )
    if delivery.delivered:
        raise RuntimeError("This flow chat-output step has already delivered its output.")
    delivery.output = content
    delivery.receipt = {"chat_output_id": str(uuid4()), **dict(receipt)}
    return delivery.receipt


def record_chat_output_cannot_complete(payload: Mapping[str, Any]) -> None:
    """Record a formatter cannot-complete result for the open delivery, if any."""

    delivery = _current_delivery.get()
    if delivery is not None and not delivery.delivered:
        delivery.cannot_complete = dict(payload)


def record_chat_output_failure(payload: Mapping[str, Any]) -> None:
    """Record an already-reported finalization failure for the open delivery, if any."""

    delivery = _current_delivery.get()
    if delivery is not None and not delivery.delivered:
        delivery.failure = dict(payload)


__all__ = [
    "ChatOutputDelivery",
    "chat_output_delivery_scope",
    "current_chat_output_delivery",
    "deliver_projected_chat_output",
    "record_chat_output_cannot_complete",
    "record_chat_output_failure",
]
