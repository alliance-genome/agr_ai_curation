"""Tests for environment-configurable batch recovery lease limits."""

from src.lib.openai_agents.config import (
    get_batch_worker_heartbeat_seconds,
    get_batch_worker_lease_seconds,
)


def test_batch_recovery_lease_defaults(monkeypatch):
    monkeypatch.delenv("BATCH_WORKER_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("BATCH_WORKER_HEARTBEAT_SECONDS", raising=False)

    assert get_batch_worker_lease_seconds() == 120
    assert get_batch_worker_heartbeat_seconds() == 30


def test_batch_recovery_lease_limits_are_env_configurable(monkeypatch):
    monkeypatch.setenv("BATCH_WORKER_LEASE_SECONDS", "240")
    monkeypatch.setenv("BATCH_WORKER_HEARTBEAT_SECONDS", "45")

    assert get_batch_worker_lease_seconds() == 240
    assert get_batch_worker_heartbeat_seconds() == 45
