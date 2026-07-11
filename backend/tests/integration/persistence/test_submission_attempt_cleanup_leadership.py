"""Persistence tests for submission-attempt cleanup leader election."""

from src.lib.curation_workspace.submission_attempt_cleanup import (
    _release_cleanup_leadership,
    _try_acquire_cleanup_leadership,
)
from src.models.sql.database import engine


def test_cleanup_leadership_is_exclusive_and_recoverable():
    with (
        engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as first_connection,
        engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as second_connection,
    ):
        assert _try_acquire_cleanup_leadership(first_connection) is True
        try:
            assert _try_acquire_cleanup_leadership(second_connection) is False
        finally:
            _release_cleanup_leadership(first_connection)

        assert _try_acquire_cleanup_leadership(second_connection) is True
        _release_cleanup_leadership(second_connection)
