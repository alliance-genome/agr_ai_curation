"""Unit tests for durable chat title generation helpers."""

from src.lib.chat_title_generator import (
    ChatTitleSource,
    TITLE_MAX_LENGTH,
    generate_chat_title,
    normalize_generated_chat_title,
)


def test_normalize_generated_chat_title_collapses_whitespace_and_truncates():
    title = normalize_generated_chat_title(
        "  \"How   does TP53 evidence differ across durable chat history sessions when the prompt keeps running far past the maximum title length?\"  "
    )

    assert title is not None
    assert title.startswith("How does TP53 evidence differ across durable chat history sessions")
    assert len(title) <= TITLE_MAX_LENGTH


def test_generate_chat_title_prefers_user_message():
    title = generate_chat_title(
        [
            ChatTitleSource(role="assistant", content="Assistant fallback title"),
            ChatTitleSource(role="user", content="Compare TP53 and EGFR evidence trails"),
        ]
    )

    assert title == "Compare TP53 and EGFR evidence trails"


def test_generate_chat_title_selects_assistant_when_user_prompt_is_low_signal():
    title = generate_chat_title(
        [
            ChatTitleSource(role="user", content="Hi"),
            ChatTitleSource(role="assistant", content="TP53 evidence summary for durable chat history"),
        ]
    )

    assert title == "TP53 evidence summary for durable chat history"


def test_generate_chat_title_returns_none_when_every_candidate_is_low_signal():
    title = generate_chat_title(
        [
            ChatTitleSource(role="user", content="Hello"),
            ChatTitleSource(role="assistant", content="Thanks"),
            ChatTitleSource(role="flow", content="OK"),
        ]
    )

    assert title is None
