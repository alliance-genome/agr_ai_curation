from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


def _load_smoke_module():
    repo_root = Path(__file__).resolve().parents[4]
    module_path = repo_root / "scripts" / "testing" / "abc_literature_live_smoke.py"
    spec = importlib.util.spec_from_file_location("abc_literature_live_smoke", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_args_defaults_to_stage_fixture(monkeypatch):
    smoke = _load_smoke_module()
    for name in (
        "ABC_LITERATURE_SMOKE_AWS_PROFILE",
        "ABC_LITERATURE_LIVE_KNOWN_MD5",
        "ABC_LITERATURE_LIVE_REFERENCE",
        "ABC_LITERATURE_SMOKE_CONVERTED_REFERENCEFILE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_PROFILE", "unit-profile")

    args = smoke.parse_args([])

    assert args.aws_profile == "unit-profile"
    assert args.base_url == smoke.DEFAULT_BASE_URL
    assert args.known_md5 == smoke.DEFAULT_KNOWN_MD5
    assert args.reference == smoke.DEFAULT_REFERENCE
    assert args.converted_referencefile_id == smoke.DEFAULT_CONVERTED_REFERENCEFILE_ID


def test_build_pytest_env_sets_live_harness_contract(tmp_path):
    smoke = _load_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--aws-profile",
                "unit-profile",
                "--known-md5",
                "abc123",
                "--converted-referencefile-id",
                "42",
            ]
        )
    )

    env = smoke.build_pytest_env(
        config,
        authorized_token="authorized-token",
        unauthorized_token="unauthorized-token",
    )

    assert env["ABC_LITERATURE_LIVE_ENABLE"] == "1"
    assert env["ABC_LITERATURE_LIVE_KNOWN_MD5"] == "abc123"
    assert env["ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID"] == "42"
    assert env["ABC_LITERATURE_LIVE_RESTRICTED_REFERENCEFILE_ID"] == "42"
    assert env["ABC_LITERATURE_LIVE_BEARER_TOKEN"] == "authorized-token"
    assert env["ABC_LITERATURE_LIVE_UNAUTHORIZED_BEARER_TOKEN"] == "unauthorized-token"


def test_run_smoke_creates_ephemeral_users_runs_pytest_and_deletes(tmp_path):
    smoke = _load_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--aws-profile",
                "unit-profile",
                "--pytest-timeout-seconds",
                "12",
            ]
        )
    )
    calls: list[tuple[str, str]] = []
    captured_pytest: dict[str, Any] = {}

    class FakeAwsClient:
        def caller_identity(self):
            return {
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/unit",
                "UserId": "unit",
            }

        def discover_client_secret(self):
            return "unit-client-secret"

        def create_user(self, *, username, email):
            calls.append(("create_user", username))
            assert username == email

        def set_user_password(self, *, username, password):
            calls.append(("set_user_password", username))
            assert password

        def add_user_to_group(self, *, username, group):
            calls.append(("add_user_to_group", f"{username}:{group}"))

        def initiate_auth(self, *, username, auth_parameters):
            calls.append(("initiate_auth", username))
            assert auth_parameters["PASSWORD"]
            assert auth_parameters["SECRET_HASH"]
            return {"AuthenticationResult": {"IdToken": f"token-for-{username}"}}

        def delete_user(self, *, username):
            calls.append(("delete_user", username))

    def fake_pytest_runner(config_arg, env, command):
        captured_pytest["config"] = config_arg
        captured_pytest["env"] = dict(env)
        captured_pytest["command"] = tuple(command)
        return smoke.CommandResult(0, "6 passed in 1.23s\n", "")

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        pytest_runner=fake_pytest_runner,
        now=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
    )

    assert result.exit_code == 0
    assert result.evidence_path.exists()
    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence["overall_status"] == "pass"
    assert evidence["smoke_users"]["authorized"]["deleted"] is True
    assert evidence["smoke_users"]["unauthorized"]["deleted"] is True
    assert "token-for-" not in evidence_text
    assert "unit-client-secret" not in evidence_text
    assert evidence["aws"]["client_secret_source"] == "describe-user-pool-client"
    assert "6 passed" in evidence["pytest"]["stdout_tail"]

    env = captured_pytest["env"]
    assert isinstance(env, dict)
    assert str(env["ABC_LITERATURE_LIVE_BEARER_TOKEN"]).startswith("token-for-")
    assert str(env["ABC_LITERATURE_LIVE_UNAUTHORIZED_BEARER_TOKEN"]).startswith(
        "token-for-"
    )
    assert captured_pytest["command"][:4] == (
        config.python_executable,
        "-m",
        "pytest",
        "tests/live_integration/test_abc_literature_live_smoke.py",
    )

    deleted_usernames = [value for action, value in calls if action == "delete_user"]
    assert len(deleted_usernames) == 2
    assert deleted_usernames[0].endswith("-unauthorized@example.invalid")
    assert deleted_usernames[1].endswith("-authorized@example.invalid")


def test_create_smoke_user_deletes_partial_user_when_setup_fails(tmp_path):
    smoke = _load_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--aws-profile",
                "unit-profile",
            ]
        )
    )
    calls: list[tuple[str, str]] = []

    class FakeAwsClient:
        def create_user(self, *, username, email):
            calls.append(("create_user", username))

        def set_user_password(self, *, username, password):
            raise smoke.SmokeFailure("password setup failed")

        def delete_user(self, *, username):
            calls.append(("delete_user", username))

    with pytest.raises(smoke.SmokeFailure, match="password setup failed"):
        smoke.create_smoke_user(
            config,
            username="partial@example.invalid",
            groups=("FBStaff",),
            aws_client=FakeAwsClient(),
        )

    assert [value for action, value in calls if action == "delete_user"] == [
        "partial@example.invalid"
    ]


def test_run_smoke_redacts_tokens_from_failed_pytest_evidence(tmp_path):
    smoke = _load_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--aws-profile",
                "unit-profile",
            ]
        )
    )

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def create_user(self, *, username, email):
            return None

        def set_user_password(self, *, username, password):
            return None

        def add_user_to_group(self, *, username, group):
            return None

        def initiate_auth(self, *, username, auth_parameters):
            return {"AuthenticationResult": {"IdToken": f"secret-token-for-{username}"}}

        def delete_user(self, *, username):
            return None

    def failing_pytest_runner(_config_arg, env, _command):
        return smoke.CommandResult(
            1,
            f"stdout leaked {env['ABC_LITERATURE_LIVE_BEARER_TOKEN']}",
            f"stderr leaked {env['ABC_LITERATURE_LIVE_UNAUTHORIZED_BEARER_TOKEN']}",
        )

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        pytest_runner=failing_pytest_runner,
        now=datetime(2026, 6, 25, 12, 5, tzinfo=timezone.utc),
    )

    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    assert result.exit_code == 1
    assert "secret-token-for-" not in evidence_text
    assert "unit-client-secret" not in evidence_text
    assert "<redacted>" in evidence_text
