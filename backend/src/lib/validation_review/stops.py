"""Recognize expected review stops through the SDK's transparent tool wrappers."""

import asyncio

from .admission import ReviewStopped


class ReviewInterrupted(asyncio.CancelledError):
    """An explicit curator Stop, distinct from an unexpected transport cancellation."""


def review_stop(error):
    from agents import UserError, MaxTurnsExceeded
    from src.lib.openai_agents.model_context_view import ContextRecoveryError

    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(
            error,
            (
                ReviewStopped,
                ContextRecoveryError,
                MaxTurnsExceeded,
                asyncio.TimeoutError,
                asyncio.CancelledError,
            ),
        ):
            return error
        # Do not hide a genuine database/runtime failure merely because it
        # occurred while handling a stop. Only SDK tool wrappers are transparent.
        if not isinstance(error, UserError):
            return None
        error = error.__cause__ or error.__context__
    return None


def stop_reason(error):
    """Classify a recognized stop consistently across chat and flow review."""
    from agents import MaxTurnsExceeded
    from src.lib.openai_agents.model_context_view import ContextRecoveryError

    if isinstance(error, ReviewStopped):
        return error.reason
    if isinstance(error, asyncio.TimeoutError):
        return "expired"
    if isinstance(error, ContextRecoveryError):
        return "unsupported_exact_context_admission"
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, MaxTurnsExceeded):
        return "reviewer_turns_exhausted"
    raise TypeError("Not a recognized review stop")


def has_review_terminal(error, pending):
    """SDK extraction failures without a saved source remain operational failures."""
    return (isinstance(error, ReviewStopped) and (
        pending is not None or error.reason == "flow_step_already_submitted"
    )) or (
        error is not None and pending is not None and pending.record is not None
    )
