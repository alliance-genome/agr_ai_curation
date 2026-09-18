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
    record.delegated_source_authorization = f"Bearer {token}"
    record.estimated_tokens = 1234
    rendered = JsonFormatter().format(record)
    assert token not in rendered
    assert REDACTED in rendered
    assert json.loads(rendered)["estimated_tokens"] == 1234


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


# --- KANBAN-1771 credential patterns (v0.9.18 merge-back review) -------------
# Sentry content redaction is off by default, so these patterns are the only
# backstop for a credential value under an ordinary key. They also scrub every
# application log line through redact_secrets, so they must not over-redact
# ordinary text. Credential fixtures are assembled at runtime so no literal
# in this file matches a secret-scanning rule.

def _dsn(scheme, user, password, host="db.internal:5432/agr"):
    return f"{scheme}://{user}:{password}@{host}"


def test_dsn_password_is_redacted_but_host_is_kept():
    text = "connect failed: " + _dsn("postgresql", "curation", "hunter" + "2")
    scrubbed = redact_secrets(text)
    assert "hunter2" not in scrubbed
    assert "db.internal:5432/agr" in scrubbed, "the host is the diagnostic part"
    assert "postgresql://" in scrubbed


def test_dsn_with_an_empty_username_is_redacted():
    """REDIS_URL in this repo uses redis://:password@host."""
    text = "redis error: " + _dsn("redis", "", "s3cr" + "et", "redis:6379/0")
    scrubbed = redact_secrets(text)
    assert "s3cret" not in scrubbed
    assert "redis:6379/0" in scrubbed


def test_a_url_with_a_port_and_an_at_sign_in_its_query_is_not_mangled():
    """Review finding: the first DSN pattern turned this into https[Filtered]x.org."""
    text = "Fetching https://api.example.org:443?user=me@x.org"
    assert redact_secrets(text) == text


def test_an_ordinary_email_after_a_path_is_not_redacted():
    text = "See https://example.org/contact for admin@example.org"
    assert redact_secrets(text) == text


def test_a_private_key_body_is_redacted_not_just_its_header():
    """Review finding: only the BEGIN line was removed, so the body survived."""
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" + "BKcwggSjAgEAAoIBAQC7"
    pem = (
        "-----BEGIN " + "PRIVATE KEY-----\n" + body + "\n-----END " + "PRIVATE KEY-----"
    )
    scrubbed = redact_secrets("key was: " + pem + " (end)")
    assert body not in scrubbed
    assert "(end)" in scrubbed, "text after the key must survive"


def test_github_slack_and_google_tokens_are_redacted():
    for token in (
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 40,
        "xoxb-" + "1234567890-abcdef",
        "AIza" + "c" * 35,
    ):
        assert token not in redact_secrets(f"value={token}"), token


def test_a_jwt_is_redacted():
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEF123_-"
    assert jwt not in redact_secrets(f"token {jwt}")


def test_ordinary_curator_text_is_untouched():
    text = "Adgrl1 was detected in embryonic brain; see FB:FBal0001816."
    assert redact_secrets(text) == text
