"""Lifecycle helpers for OpenAI Agents SDK streamed runs."""

from agents.result import RunResultStreaming


async def await_streamed_run_completion(result: RunResultStreaming) -> None:
    """Wait for and retrieve the SDK run-loop task before transport teardown.

    Consuming ``stream_events()`` is the event-facing lifecycle boundary. Provider
    owners also need the SDK's ``start_streaming`` task to be settled before closing a
    cached Responses WebSocket; otherwise a late connection-close failure can escape as
    an unretrieved task exception after an otherwise successful flow.
    """

    if result.run_loop_task is not None:
        await result.run_loop_task
