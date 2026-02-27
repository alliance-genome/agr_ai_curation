"""Additional tests for auth + EC2 runtime configuration helpers."""

import sys
from types import SimpleNamespace

import pytest

import src.config as conf


class _FakeResponse:
    def __init__(self, text: str):
        self._text = text

    def read(self):
        return self._text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _reset_config_caches(monkeypatch):
    monkeypatch.setattr(conf, "_ec2_detection_cache", None)
    monkeypatch.setattr(conf, "_dev_mode_allowed_cache", None)


def test_get_auth_provider_paths(monkeypatch):
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="AUTH_PROVIDER must be set explicitly"):
        conf.get_auth_provider()

    monkeypatch.setenv("AUTH_PROVIDER", "bad")
    with pytest.raises(ValueError, match="Invalid AUTH_PROVIDER"):
        conf.get_auth_provider()

    monkeypatch.setenv("AUTH_PROVIDER", " COGNITO ")
    assert conf.get_auth_provider() == "cognito"


def test_is_auth_configured_paths(monkeypatch):
    monkeypatch.setattr(conf, "is_dev_mode", lambda: True)
    assert conf.is_auth_configured() is True

    monkeypatch.setattr(conf, "is_dev_mode", lambda: False)
    monkeypatch.setattr(conf, "get_auth_provider", lambda: (_ for _ in ()).throw(ValueError("missing")))
    assert conf.is_auth_configured() is False

    monkeypatch.setattr(conf, "get_auth_provider", lambda: "dev")
    assert conf.is_auth_configured() is False

    monkeypatch.setattr(conf, "get_auth_provider", lambda: "cognito")
    monkeypatch.setattr(conf, "is_cognito_configured", lambda: True)
    assert conf.is_auth_configured() is True

    monkeypatch.setattr(conf, "get_auth_provider", lambda: "oidc")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app/callback")
    assert conf.is_auth_configured() is True
    monkeypatch.delenv("OIDC_CLIENT_ID", raising=False)
    assert conf.is_auth_configured() is False


def test_is_running_on_ec2_via_env_and_hostname(monkeypatch):
    monkeypatch.setenv("RUNNING_ON_EC2", "true")
    assert conf.is_running_on_ec2() is True

    monkeypatch.delenv("RUNNING_ON_EC2", raising=False)
    monkeypatch.setattr(conf.socket, "gethostname", lambda: "ip-172-31-1-2")
    assert conf.is_running_on_ec2() is True


def test_is_running_on_ec2_via_imds(monkeypatch):
    monkeypatch.delenv("RUNNING_ON_EC2", raising=False)
    monkeypatch.setattr(conf.socket, "gethostname", lambda: "local-dev")

    def _fake_urlopen(req, timeout=1):
        _ = timeout
        url = req.full_url
        if url.endswith("/latest/api/token"):
            return _FakeResponse("token-1")
        if url.endswith("/latest/meta-data/instance-id"):
            return _FakeResponse("i-abc123")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(conf.urllib.request, "urlopen", _fake_urlopen)
    assert conf.is_running_on_ec2() is True


def test_is_running_on_ec2_returns_false_when_detection_fails(monkeypatch):
    monkeypatch.delenv("RUNNING_ON_EC2", raising=False)
    monkeypatch.setattr(conf.socket, "gethostname", lambda: (_ for _ in ()).throw(RuntimeError("no host")))
    monkeypatch.setattr(
        conf.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no imds")),
    )
    assert conf.is_running_on_ec2() is False


def test_get_ec2_instance_metadata_paths(monkeypatch):
    def _fake_urlopen(req, timeout=1):
        _ = timeout
        url = req.full_url
        if url.endswith("/latest/api/token"):
            return _FakeResponse("token-2")
        if url.endswith("/latest/meta-data/instance-id"):
            return _FakeResponse("i-xyz789")
        if url.endswith("/latest/meta-data/placement/availability-zone"):
            return _FakeResponse("us-east-1a")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(conf.urllib.request, "urlopen", _fake_urlopen)
    assert conf._get_ec2_instance_metadata() == ("i-xyz789", "us-east-1")

    monkeypatch.setattr(
        conf.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("metadata down")),
    )
    assert conf._get_ec2_instance_metadata() == (None, None)


def test_check_ec2_tag_paths(monkeypatch):
    monkeypatch.setattr(conf, "_get_ec2_instance_metadata", lambda: (None, None))
    assert conf._check_ec2_tag("AllowDevMode", "true") is False

    monkeypatch.setattr(conf, "_get_ec2_instance_metadata", lambda: ("i-1", "us-east-1"))
    fake_boto3 = SimpleNamespace(
        client=lambda service, region_name: SimpleNamespace(
            describe_tags=lambda Filters: {
                "Tags": [{"Key": "AllowDevMode", "Value": "true"}]
            }
        )
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    assert conf._check_ec2_tag("AllowDevMode", "true") is True

    fake_boto3_error = SimpleNamespace(
        client=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("aws down"))
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3_error)
    assert conf._check_ec2_tag("AllowDevMode", "true") is False


def test_ec2_status_and_dev_mode_allowed_are_cached(monkeypatch):
    calls = {"ec2": 0, "allow": 0}

    def _is_running_on_ec2():
        calls["ec2"] += 1
        return True

    def _check_tag(_k, _v):
        calls["allow"] += 1
        return True

    monkeypatch.setattr(conf, "is_running_on_ec2", _is_running_on_ec2)
    monkeypatch.setattr(conf, "_check_ec2_tag", _check_tag)

    assert conf._get_ec2_status() is True
    assert conf._get_ec2_status() is True
    assert calls["ec2"] == 1

    assert conf._is_dev_mode_allowed_on_ec2() is True
    assert conf._is_dev_mode_allowed_on_ec2() is True
    assert calls["allow"] == 1


def test_is_dev_mode_security_paths(monkeypatch):
    monkeypatch.setattr(conf, "_get_ec2_status", lambda: True)
    monkeypatch.setattr(conf, "_is_dev_mode_allowed_on_ec2", lambda: False)
    monkeypatch.setenv("DEV_MODE", "true")
    assert conf.is_dev_mode() is False

    monkeypatch.setattr(conf, "_is_dev_mode_allowed_on_ec2", lambda: True)
    assert conf.is_dev_mode() is True

    monkeypatch.setattr(conf, "_get_ec2_status", lambda: False)
    monkeypatch.setenv("DEV_MODE", "TRUE")
    assert conf.is_dev_mode() is True


def test_secure_cookies_and_env_source(monkeypatch):
    monkeypatch.setenv("SECURE_COOKIES", "true")
    assert conf.get_secure_cookies() is True
    monkeypatch.setenv("SECURE_COOKIES", "false")
    assert conf.get_secure_cookies() is False

    monkeypatch.setattr(conf, "_env_loaded_from", "/home/test/.agr_ai_curation/.env")
    assert conf.get_env_source() == "/home/test/.agr_ai_curation/.env"


def test_print_configuration_masks_secret_values(monkeypatch, capsys):
    monkeypatch.setattr(
        conf,
        "get_typed_config",
        lambda: {
            "openai_api_key": "not_a_real_key_value",
            "log_level": "DEBUG",
        },
    )
    monkeypatch.setattr(conf, "get_env_source", lambda: None)

    conf.print_configuration(mask_secrets=True)
    captured = capsys.readouterr().out
    assert "openai_api_key: not_a_re..." in captured
    assert "log_level: DEBUG" in captured
    assert "env_source: (no .env file loaded)" in captured

    conf.print_configuration(mask_secrets=False)
    captured2 = capsys.readouterr().out
    assert "openai_api_key: not_a_real_key_value" in captured2
