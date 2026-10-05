import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.lib.flow_transfer.config import (EXPORT_KEYS, IMPORT_KEYS, FlowTransferConfigError,
                                          flow_export_config, flow_import_config,
                                          validate_flow_transfer_config)

from .support import ISSUER


def _seed_and_public() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(seed).decode(), base64.b64encode(public).decode()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in (*EXPORT_KEYS, *IMPORT_KEYS):
        monkeypatch.delenv(key, raising=False)


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
