"""Flow export (main AI Curation) and flow import (benchmark resolver) settings.

Each side's keys are all set or all unset. Unset turns that side's routes off (503);
a partial or malformed set stops startup. Error messages name keys, never values.
"""

import base64
import binascii
import os
import re
from dataclasses import dataclass

from auth_runtime.factory import create_auth_provider, create_cognito_provider, get_auth_provider
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.lib.config.groups_loader import get_group_claim_key

EXPORT_KEYS = ("FLOW_EXPORT_SIGNING_KEY", "FLOW_EXPORT_ISSUER", "FLOW_EXPORT_BEARER_CLIENT_IDS")
IMPORT_KEYS = ("FLOW_IMPORT_EXPORT_ISSUER", "FLOW_IMPORT_EXPORT_PUBLIC_KEY")
_ORIGIN = re.compile(r"https://[a-z0-9.-]+(:[0-9]{1,5})?")


class FlowTransferConfigError(RuntimeError):
    """A flow export/import setting is partial or malformed."""


@dataclass(frozen=True)
class FlowExportConfig:
    signer: Ed25519PrivateKey
    issuer: str
    bearer_client_ids: tuple[str, ...]


@dataclass(frozen=True)
class FlowExportSignIn:
    """Where export bearer ID tokens come from: the platform's own sign-in provider."""
    provider: str  # "cognito" or "oidc", exactly as AUTH_PROVIDER selects
    issuer_url: str
    group_claim: str


@dataclass(frozen=True)
class FlowImportConfig:
    public_key: Ed25519PublicKey
    issuer: str


def _values(keys: tuple[str, ...]) -> dict[str, str] | None:
    values = {key: os.getenv(key, "").strip() for key in keys}
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise FlowTransferConfigError(
            f"Set all or none of {', '.join(keys)}; missing {', '.join(missing)}"
        )
    return values


def _key_bytes(name: str, value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32:
        raise FlowTransferConfigError(f"{name} must be base64 of exactly 32 bytes")
    return raw


def _origin(name: str, value: str) -> str:
    if not _ORIGIN.fullmatch(value):
        raise FlowTransferConfigError(
            f"{name} must be an https origin, such as https://ai-curation.alliancegenome.org"
        )
    return value


def flow_export_config() -> FlowExportConfig | None:
    values = _values(EXPORT_KEYS)
    if values is None:
        return None
    clients = tuple(
        item.strip() for item in values["FLOW_EXPORT_BEARER_CLIENT_IDS"].split(",") if item.strip()
    )
    if not clients:
        raise FlowTransferConfigError("FLOW_EXPORT_BEARER_CLIENT_IDS must list at least one client")
    return FlowExportConfig(
        signer=Ed25519PrivateKey.from_private_bytes(
            _key_bytes("FLOW_EXPORT_SIGNING_KEY", values["FLOW_EXPORT_SIGNING_KEY"])
        ),
        issuer=_origin("FLOW_EXPORT_ISSUER", values["FLOW_EXPORT_ISSUER"]),
        bearer_client_ids=clients,
    )


def flow_export_sign_in() -> FlowExportSignIn:
    """The issuer and group claim of the provider AUTH_PROVIDER selects, read through the
    same factory the sign-in cookie uses. No other provider is ever tried."""
    try:
        provider = get_auth_provider()
        if provider == "cognito":
            platform = create_cognito_provider()
        elif provider == "oidc":
            platform = create_auth_provider(dev_mode=False, group_claim=get_group_claim_key())
        else:
            raise ValueError(provider)
    except ValueError:
        raise FlowTransferConfigError(
            "FLOW_EXPORT_* needs the sign-in issuer: set AUTH_PROVIDER=cognito with its COGNITO_* "
            "settings, or AUTH_PROVIDER=oidc with OIDC_ISSUER_URL, OIDC_CLIENT_ID and "
            "OIDC_REDIRECT_URI"
        ) from None
    return FlowExportSignIn(provider=provider, issuer_url=platform.issuer_url,
                            group_claim=platform.group_claim)


def flow_import_config() -> FlowImportConfig | None:
    values = _values(IMPORT_KEYS)
    if values is None:
        return None
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _key_bytes("FLOW_IMPORT_EXPORT_PUBLIC_KEY", values["FLOW_IMPORT_EXPORT_PUBLIC_KEY"])
        )
    except ValueError:
        raise FlowTransferConfigError("FLOW_IMPORT_EXPORT_PUBLIC_KEY is not an Ed25519 public key") from None
    return FlowImportConfig(
        public_key=public_key,
        issuer=_origin("FLOW_IMPORT_EXPORT_ISSUER", values["FLOW_IMPORT_EXPORT_ISSUER"]),
    )


def validate_flow_transfer_config() -> None:
    """Startup check: raise for a partial or malformed set on either side, or for an
    export set whose sign-in issuer can't be resolved from the auth settings."""
    if flow_export_config() is not None:
        flow_export_sign_in()
    flow_import_config()
