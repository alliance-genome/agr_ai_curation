"""Unit tests for shared chat history configuration."""

from src.lib.chat_config import ChatHistoryConfig, load_chat_history_config


def test_load_chat_history_config_reads_environment_values():
    config = load_chat_history_config(
        {
            "CHAT_HISTORY_ENABLED": "false",
            "CHAT_MAX_HISTORY_EXCHANGES": "42",
            "CHAT_HISTORY_IN_ROUTING": "false",
            "CHAT_HISTORY_IN_RESPONSE": "true",
            "CHAT_MAX_SESSIONS_PER_USER": "9",
        }
    )

    assert config == ChatHistoryConfig(
        history_enabled=False,
        max_exchanges=42,
        include_in_routing=False,
        include_in_response=True,
        max_sessions_per_user=9,
    )


def test_chat_history_config_as_history_dict_matches_api_shape():
    config = ChatHistoryConfig(
        history_enabled=True,
        max_exchanges=20,
        include_in_routing=True,
        include_in_response=False,
        max_sessions_per_user=50,
    )

    assert config.as_history_dict() == {
        "enabled": True,
        "max_exchanges": 20,
        "include_in_routing": True,
        "include_in_response": False,
        "max_sessions_per_user": 50,
    }
