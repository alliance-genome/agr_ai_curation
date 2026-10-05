import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.lib.flow_transfer.bundle import check_bundle
from src.lib.flow_transfer.config import FlowExportConfig, FlowImportConfig
from src.lib.flow_transfer.signing import AUDIENCE, UntrustedBundle, sign_bundle, verify_bundle

from .support import CURATOR_ISS, CURATOR_SUB, ISSUER, make_bundle

NOW = datetime.now(timezone.utc)


def _configs(issuer=ISSUER):
    key = Ed25519PrivateKey.generate()
    return (FlowExportConfig(signer=key, issuer=issuer, bearer_client_ids=("c",)),
            FlowImportConfig(public_key=key.public_key(), issuer=issuer))


def test_a_signed_bundle_verifies_for_its_curator():
    export, imported = _configs()
    raw = make_bundle()
    token = sign_bundle(raw, config=export, now=NOW)
    verify_bundle(token, check_bundle(raw), config=imported, subject=CURATOR_SUB, issuer=CURATOR_ISS)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["aud"] == AUDIENCE and claims["exp"] - claims["iat"] == 600
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"


def _refused(token, raw, imported, subject=CURATOR_SUB, issuer=CURATOR_ISS):
    with pytest.raises(UntrustedBundle):
        verify_bundle(token, check_bundle(raw), config=imported, subject=subject, issuer=issuer)


def test_another_curator_or_issuer_is_refused():
    export, imported = _configs()
    raw = make_bundle()
    token = sign_bundle(raw, config=export, now=NOW)
    _refused(token, raw, imported, subject="someone-else")
    _refused(token, raw, imported, issuer="https://cognito-idp.us-east-1.amazonaws.com/other")
    _refused(token, raw, imported, issuer=None)


def test_wrong_key_wrong_issuer_and_expiry_are_refused():
    export, imported = _configs()
    _, other_key = _configs()
    raw = make_bundle()
    token = sign_bundle(raw, config=export, now=NOW)
    _refused(token, raw, other_key)
    _refused(token, raw, FlowImportConfig(public_key=imported.public_key,
                                          issuer="https://ai-curation.example.org"))
    _refused(sign_bundle(raw, config=export, now=NOW - timedelta(minutes=11)), raw, imported)


def test_changed_content_is_refused():
    export, imported = _configs()
    raw = make_bundle()
    token = sign_bundle(raw, config=export, now=NOW)
    raw["flow"]["name"] = "Renamed after signing"
    _refused(token, raw, imported)


def test_only_eddsa_is_accepted():
    export, imported = _configs()
    raw = make_bundle()
    good = jwt.decode(sign_bundle(raw, config=export, now=NOW), options={"verify_signature": False})
    secret = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    _refused(jwt.encode(good, secret, algorithm="HS256"), raw, imported)


def test_lifetime_and_leeway_follow_their_env_settings(monkeypatch):
    export, imported = _configs()
    raw = make_bundle()
    monkeypatch.setenv("FLOW_TRANSFER_SIGNATURE_LIFETIME_SECONDS", "120")
    monkeypatch.setenv("FLOW_TRANSFER_SIGNATURE_LEEWAY_SECONDS", "0")
    token = sign_bundle(raw, config=export, now=NOW)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 120
    # Expired five seconds ago: refused with no leeway, accepted with ten seconds.
    stale = sign_bundle(raw, config=export,
                        now=datetime.now(timezone.utc) - timedelta(seconds=125))
    _refused(stale, raw, imported)
    monkeypatch.setenv("FLOW_TRANSFER_SIGNATURE_LEEWAY_SECONDS", "10")
    verify_bundle(stale, check_bundle(raw), config=imported, subject=CURATOR_SUB, issuer=CURATOR_ISS)
