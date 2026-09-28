"""Shared credential redaction coverage."""

import base64
import json
import logging

import pytest

from src.lib.logging_config import JsonFormatter
from src.lib.observability import sentry
from src.lib.security.redaction import (
    REDACTED,
    active_secret_redaction,
    redact_secrets,
)


@pytest.mark.parametrize("text", [
    "Basic phenotype information was recorded for the allele",
    "Bearer of the mutation showed no phenotype",
    "task-3f2a9c1e-8b7d-4c2a-9f1e-123456789abc failed",
    "risk-assessment-template-v2 loaded",
    "desk-reference-manual-2024-edition",
    "Basic cellular processes",
    "Basic information",
    "Bearer characterization",
    "Bearer Characterization",
])
@pytest.mark.parametrize("suffix", ["", ".", "...", ",", ";", "!", "?"])
def test_ordinary_identifiers_and_auth_scheme_words_survive(text, suffix):
    text += suffix
    assert redact_secrets(text) == text
    record = logging.LogRecord("test", logging.INFO, __file__, 1, text, (), None)
    assert json.loads(JsonFormatter().format(record))["message"] == text
    assert sentry._scrub_string(text) == text


@pytest.mark.parametrize("prefix", ["sk-", "pk-"])
@pytest.mark.parametrize("context", ["{}", "value='{}'", "({})"])
def test_api_keys_at_word_boundaries_are_redacted(prefix, context):
    key = prefix + "aB3_" * 8
    assert redact_secrets(context.format(key)) == context.format(REDACTED)


@pytest.mark.parametrize("prefix", ["sk-", "pk-"])
def test_api_key_prefixes_inside_identifiers_are_preserved(prefix):
    identifier = "identifier_" + prefix + "aB3_" * 8
    assert redact_secrets(identifier) == identifier


@pytest.mark.parametrize("scheme", ["Bearer", "Basic", "bEaReR", "bAsIc"])
@pytest.mark.parametrize("header", ["Authorization: {}", "authorization:\t{}", '"Authorization": "{}"'])
def test_authorization_headers_redact_even_short_values(scheme, header):
    value = header.format(f"{scheme} abc")
    assert "abc" not in redact_secrets(value)
    assert "abc" not in sentry._scrub_string(value)


@pytest.mark.parametrize("credential", [
    "Bearer " + "aB3_" * 8,
    "Bearer " + "aBcD" * 8,
    "Bearer distinctive-opaque-delegated-token",
    "Bearer " + "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123_-",
    "Basic " + base64.b64encode(b"example:password").decode(),
    "Basic " + base64.b64encode(b"a:b").decode(),
])
@pytest.mark.parametrize("suffix", [", retry", ".", "...", ";", "!", "?"])
def test_standalone_credential_shapes_are_redacted(credential, suffix):
    text = f"rejected {credential}{suffix}"
    expected = f"rejected {REDACTED}{suffix}"
    assert redact_secrets(text) == expected
    assert sentry._scrub_string(text) == expected
    assert credential not in redact_secrets(f"Authorization: {credential}")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, text, (), None)
    assert json.loads(JsonFormatter().format(record))["message"] == expected


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
    text = "Gene7 was detected in embryonic brain; see identifier EXAMPLE:0001816."
    assert redact_secrets(text) == text
