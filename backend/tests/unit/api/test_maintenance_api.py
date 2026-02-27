"""Unit tests for maintenance message API."""

from pathlib import Path

import pytest

from src.api import maintenance as maintenance_api


def test_read_maintenance_message_returns_none_when_file_missing(monkeypatch):
    monkeypatch.setattr(maintenance_api, "MAINTENANCE_MESSAGE_FILE", "/nonexistent/maintenance_message.txt")
    assert maintenance_api.read_maintenance_message() is None


def test_read_maintenance_message_returns_first_non_comment_line(tmp_path, monkeypatch):
    message_file = tmp_path / "maintenance_message.txt"
    message_file.write_text("# No maintenance\n\nScheduled maintenance at 8 PM UTC\nSecond line ignored\n")
    monkeypatch.setattr(maintenance_api, "MAINTENANCE_MESSAGE_FILE", str(message_file))
    assert maintenance_api.read_maintenance_message() == "Scheduled maintenance at 8 PM UTC"


def test_read_maintenance_message_returns_none_for_comment_only_file(tmp_path, monkeypatch):
    message_file = tmp_path / "maintenance_message.txt"
    message_file.write_text("# comment one\n# comment two\n\n")
    monkeypatch.setattr(maintenance_api, "MAINTENANCE_MESSAGE_FILE", str(message_file))
    assert maintenance_api.read_maintenance_message() is None


def test_read_maintenance_message_returns_none_on_read_error(tmp_path, monkeypatch):
    message_path = tmp_path / "maintenance_message.txt"
    message_path.write_text("Scheduled\n")
    monkeypatch.setattr(maintenance_api, "MAINTENANCE_MESSAGE_FILE", str(message_path))
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")))
    assert maintenance_api.read_maintenance_message() is None


@pytest.mark.asyncio
async def test_get_maintenance_message_endpoint_active(monkeypatch):
    monkeypatch.setattr(maintenance_api, "read_maintenance_message", lambda: "Maintenance in progress")
    payload = await maintenance_api.get_maintenance_message()
    assert payload == {"message": "Maintenance in progress", "active": True}


@pytest.mark.asyncio
async def test_get_maintenance_message_endpoint_inactive(monkeypatch):
    monkeypatch.setattr(maintenance_api, "read_maintenance_message", lambda: None)
    payload = await maintenance_api.get_maintenance_message()
    assert payload == {"message": None, "active": False}
