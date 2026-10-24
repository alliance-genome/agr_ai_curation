"""Recognize expected review stops through the SDK's transparent tool wrappers."""

import asyncio

from .admission import ReviewStopped


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
