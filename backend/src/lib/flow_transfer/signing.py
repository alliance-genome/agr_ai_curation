"""Ed25519 signatures that bind one exported flow version to one curator for a short time
(FLOW_TRANSFER_SIGNATURE_LIFETIME_SECONDS, ten minutes by default).

Main AI Curation signs; the benchmark resolver holds only the public key. The portal
relays the bundle and cannot change it or hand it to another curator.
"""

from datetime import datetime
from typing import Any

import jwt

from src.lib.openai_agents.config import (get_flow_transfer_signature_leeway_seconds,
                                          get_flow_transfer_signature_lifetime_seconds)

from .bundle import CheckedBundle, FlowBundle, bundle_sha256
from .config import FlowExportConfig, FlowImportConfig

AUDIENCE = "agr-ai-curation-flow-import"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp", "curator_iss", "source_flow_id",
                   "source_version", "bundle_sha256")


class UntrustedBundle(ValueError):
    """The signature or its claims don't match. Never shown to curators."""


def sign_bundle(bundle_json: dict[str, Any], *, config: FlowExportConfig, now: datetime) -> str:
    bundle = FlowBundle.model_validate(bundle_json)
    issued = int(now.timestamp())
    return jwt.encode({
        "iss": config.issuer, "aud": AUDIENCE,
        "sub": bundle.exported_for.sub, "curator_iss": bundle.exported_for.iss,
        "source_flow_id": str(bundle.flow.source_flow_id),
        "source_version": bundle.flow.source_version,
        "bundle_sha256": bundle_sha256(bundle_json),
        "iat": issued, "exp": issued + get_flow_transfer_signature_lifetime_seconds(),
    }, config.signer, algorithm="EdDSA")


def verify_bundle(token: str, checked: CheckedBundle, *, config: FlowImportConfig,
                  subject: str, issuer: str | None) -> None:
    """The bundle was signed by our AI Curation, for this curator, and is unchanged."""
    try:
        claims = jwt.decode(
            token, config.public_key, algorithms=["EdDSA"], audience=AUDIENCE,
            issuer=config.issuer, leeway=get_flow_transfer_signature_leeway_seconds(),
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.InvalidTokenError:
        raise UntrustedBundle("signature") from None
    bundle = checked.bundle
    if (issuer is None or claims["sub"] != subject or claims["curator_iss"] != issuer
            or bundle.exported_for.sub != subject or bundle.exported_for.iss != issuer
            or bundle.source.issuer != config.issuer):
        raise UntrustedBundle("curator")
    if (claims["bundle_sha256"] != checked.sha256
            or claims["source_flow_id"] != str(bundle.flow.source_flow_id)
            or claims["source_version"] != bundle.flow.source_version):
        raise UntrustedBundle("content")
