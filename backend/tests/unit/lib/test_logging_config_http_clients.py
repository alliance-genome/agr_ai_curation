"""Per-request HTTP client logs stay below INFO for both HTTP stacks."""

import logging

import pytest

from src.lib.logging_config import configure_logging


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    names = ("httpx", "httpcore", "httpx2", "httpcore2")
    levels = {name: logging.getLogger(name).level for name in names}
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, previous in levels.items():
        logging.getLogger(name).setLevel(previous)


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore", "httpx2", "httpcore2"])
def test_http_client_request_logs_are_warning_and_above(restore_logging, monkeypatch, logger_name):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    configure_logging()
    logger = logging.getLogger(logger_name)
    assert logger.level == logging.WARNING
    assert not logger.isEnabledFor(logging.INFO)
