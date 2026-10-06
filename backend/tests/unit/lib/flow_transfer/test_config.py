import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.lib.flow_transfer import config as config_module
from src.lib.flow_transfer.config import (EXPORT_KEYS, IMPORT_KEYS, FlowTransferConfigError,
                                          flow_export_config, flow_export_sign_in,
                                          flow_import_config, validate_flow_transfer_config)
from src.lib.openai_agents import config as limits

from .support import ISSUER


def _seed_and_public() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(seed).decode(), base64.b64encode(public).decode()


AUTH_KEYS = ("AUTH_PROVIDER", "COGNITO_REGION", "COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID",
             "COGNITO_DOMAIN", "COGNITO_REDIRECT_URI", "OIDC_ISSUER_URL", "OIDC_CLIENT_ID",
             "OIDC_REDIRECT_URI", "OIDC_GROUP_CLAIM")
POOL = "us-east-1_synthetic"
OIDC_ISSUER = "https://sign-in.example.org/realms/synthetic"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in (*EXPORT_KEYS, *IMPORT_KEYS, *AUTH_KEYS):
        monkeypatch.delenv(key, raising=False)


def _cognito(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("COGNITO_CLIENT_ID", "synthetic-main-client")
    monkeypatch.setenv("COGNITO_DOMAIN", "https://auth.example.org")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://app.example.org/auth/callback")


def _oidc(monkeypatch, issuer=OIDC_ISSUER):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    if issuer:
        monkeypatch.setenv("OIDC_ISSUER_URL", issuer)
    monkeypatch.setenv("OIDC_CLIENT_ID", "synthetic-main-client")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.org/auth/callback")


def _export_keys(monkeypatch):
    seed, _ = _seed_and_public()
    monkeypatch.setenv("FLOW_EXPORT_SIGNING_KEY", seed)
    monkeypatch.setenv("FLOW_EXPORT_ISSUER", ISSUER)
    monkeypatch.setenv("FLOW_EXPORT_BEARER_CLIENT_IDS", "target-client")
    return seed


def test_nothing_set_means_both_sides_are_off():
    assert flow_export_config() is None and flow_import_config() is None
    validate_flow_transfer_config()


def test_all_export_keys_make_a_config(monkeypatch):
    seed, _ = _seed_and_public()
    monkeypatch.setenv("FLOW_EXPORT_SIGNING_KEY", seed)
    monkeypatch.setenv("FLOW_EXPORT_ISSUER", ISSUER)
    monkeypatch.setenv("FLOW_EXPORT_BEARER_CLIENT_IDS", " target-client , other-client ")
    config = flow_export_config()
    assert config.issuer == ISSUER and config.bearer_client_ids == ("target-client", "other-client")


def test_both_import_keys_make_a_config(monkeypatch):
    _, public = _seed_and_public()
    monkeypatch.setenv("FLOW_IMPORT_EXPORT_ISSUER", ISSUER)
    monkeypatch.setenv("FLOW_IMPORT_EXPORT_PUBLIC_KEY", public)
    assert flow_import_config().issuer == ISSUER


@pytest.mark.parametrize("values", [
    {"FLOW_EXPORT_ISSUER": ISSUER},
    {"FLOW_EXPORT_SIGNING_KEY": "AAAA", "FLOW_EXPORT_ISSUER": ISSUER, "FLOW_EXPORT_BEARER_CLIENT_IDS": "c"},
    {"FLOW_EXPORT_SIGNING_KEY": None, "FLOW_EXPORT_ISSUER": "http://plain.example", "FLOW_EXPORT_BEARER_CLIENT_IDS": "c"},
    {"FLOW_EXPORT_SIGNING_KEY": None, "FLOW_EXPORT_ISSUER": ISSUER + "/path", "FLOW_EXPORT_BEARER_CLIENT_IDS": "c"},
    {"FLOW_EXPORT_SIGNING_KEY": None, "FLOW_EXPORT_ISSUER": ISSUER, "FLOW_EXPORT_BEARER_CLIENT_IDS": " , "},
    {"FLOW_IMPORT_EXPORT_PUBLIC_KEY": "not base64!"},
], ids=["partial", "short-seed", "http", "path", "no-clients", "import-partial"])
def test_partial_or_malformed_config_stops_startup(monkeypatch, values):
    seed, _ = _seed_and_public()
    for key, value in values.items():
        monkeypatch.setenv(key, seed if value is None else value)
    with pytest.raises(FlowTransferConfigError) as caught:
        validate_flow_transfer_config()
    assert seed not in str(caught.value)


def test_cognito_sign_in_is_the_pool_issuer_and_cognito_groups(monkeypatch):
    _cognito(monkeypatch)
    monkeypatch.setattr(config_module, "get_group_claim_key", lambda: "not-used-for-cognito")
    sign_in = flow_export_sign_in()
    assert sign_in.provider == "cognito"
    assert sign_in.issuer_url == f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
    assert sign_in.group_claim == "cognito:groups"


def test_oidc_sign_in_is_the_oidc_issuer_and_its_configured_group_claim(monkeypatch):
    _oidc(monkeypatch)
    monkeypatch.setattr(config_module, "get_group_claim_key", lambda: "realm_access.roles")
    sign_in = flow_export_sign_in()
    assert (sign_in.provider, sign_in.issuer_url, sign_in.group_claim) == (
        "oidc", OIDC_ISSUER, "realm_access.roles")
    monkeypatch.setenv("OIDC_GROUP_CLAIM", "groups")
    assert flow_export_sign_in().group_claim == "groups"


def test_export_keys_with_resolvable_sign_in_pass_startup(monkeypatch):
    _export_keys(monkeypatch)
    _oidc(monkeypatch)
    monkeypatch.setattr(config_module, "get_group_claim_key", lambda: "groups")
    validate_flow_transfer_config()


@pytest.mark.parametrize("auth", [
    lambda m: _oidc(m, issuer=None),
    lambda m: (_cognito(m), m.delenv("COGNITO_USER_POOL_ID")),
    lambda m: m.setenv("AUTH_PROVIDER", "dev"),
    lambda m: None,
], ids=["oidc-without-issuer", "cognito-without-pool", "dev", "no-auth-provider"])
def test_export_keys_without_a_sign_in_issuer_stop_startup(monkeypatch, auth):
    seed = _export_keys(monkeypatch)
    auth(monkeypatch)
    monkeypatch.setattr(config_module, "get_group_claim_key", lambda: "groups")
    with pytest.raises(FlowTransferConfigError) as caught:
        validate_flow_transfer_config()
    message = str(caught.value)
    assert "AUTH_PROVIDER" in message and "OIDC_ISSUER_URL" in message
    for value in (seed, "target-client", "synthetic-main-client", POOL, ISSUER):
        assert value not in message


def test_sign_in_is_not_needed_when_export_is_off(monkeypatch):
    _oidc(monkeypatch, issuer=None)
    validate_flow_transfer_config()


LIMITS = {
    "FLOW_TRANSFER_BUNDLE_MAX_BYTES": (limits.get_flow_transfer_bundle_max_bytes, 8_388_608),
    "FLOW_TRANSFER_MAX_AGENTS": (limits.get_flow_transfer_max_agents, 64),
    "FLOW_TRANSFER_MAX_REVISIONS_PER_AGENT": (limits.get_flow_transfer_max_revisions_per_agent, 64),
    "FLOW_TRANSFER_MAX_OUTPUT_STRUCTURE_REVISIONS": (
        limits.get_flow_transfer_max_output_structure_revisions, 64),
    "FLOW_TRANSFER_SIGNATURE_LIFETIME_SECONDS": (
        limits.get_flow_transfer_signature_lifetime_seconds, 600),
    "FLOW_TRANSFER_SIGNATURE_LEEWAY_SECONDS": (limits.get_flow_transfer_signature_leeway_seconds, 30),
    "FLOW_TRANSFER_EXPORT_PAGE_SIZE": (limits.get_flow_transfer_export_page_size, 50),
    "FLOW_TRANSFER_IMPORT_LIST_MAX_FLOWS": (limits.get_flow_transfer_import_list_max_flows, 50),
}


@pytest.mark.parametrize("key", sorted(LIMITS))
def test_flow_transfer_limits_default_to_the_spec_and_are_env_tunable(monkeypatch, key):
    getter, default = LIMITS[key]
    monkeypatch.delenv(key, raising=False)
    assert getter() == default
    monkeypatch.setenv(key, "7")
    assert getter() == 7
    root = Path("/workspace")
    if not (root / ".env.example").exists():
        root = Path(__file__).resolve().parents[5]
    assert f"\n{key}={default}\n" in (root / ".env.example").read_text(encoding="utf-8")
