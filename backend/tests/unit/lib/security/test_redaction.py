"""Shared credential redaction coverage."""

import json
import logging

from src.lib.logging_config import JsonFormatter
from src.lib.security.redaction import (
    REDACTED,
    active_secret_redaction,
    redact_secrets,
)


def test_delegated_header_and_bearer_are_redacted_in_nested_values():
    token = "distinctive-opaque-delegated-token"
    scrubbed = redact_secrets(
        {
            "headers": {
                "X-Benchmark-Delegated-Source-Authorization": f"Bearer {token}"
            },
            "detail": f"upstream rejected Bearer {token}",
        }
    )
    serialized = json.dumps(scrubbed)
    assert token not in serialized
    assert scrubbed["headers"]["X-Benchmark-Delegated-Source-Authorization"] == REDACTED


def test_json_logging_redacts_message_and_secret_extra():
    token = "distinctive-opaque-delegated-token"
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        f"upstream rejected Bearer {token}",
        (),
        None,
    )
    record.delegated_source_authorization = token
    rendered = JsonFormatter().format(record)
    assert token not in rendered
    assert REDACTED in rendered


def test_json_logging_redacts_exception_detail():
    token = "distinctive-opaque-delegated-token"
    try:
        raise RuntimeError(f"upstream rejected Bearer {token}")
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "source failed", (), exc_info
    )
    rendered = JsonFormatter().format(record)
    assert token not in rendered
    assert REDACTED in rendered


def test_request_local_bare_secret_is_redacted_from_messages_and_exceptions():
    token = "distinctive-opaque-delegated-token"
    with active_secret_redaction(token):
        try:
            raise RuntimeError(f"upstream echoed {token} without a scheme")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, f"bare value: {token}", (), exc_info
        )
        rendered = JsonFormatter().format(record)
        assert token not in rendered
        assert REDACTED in rendered

    assert redact_secrets(f"after boundary: {token}") == f"after boundary: {token}"
