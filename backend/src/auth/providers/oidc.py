"""Generic OIDC authentication provider."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
import requests
from jose import JWTError, jwt
from jwt import PyJWKClient

from src.auth.base import AuthPrincipal, AuthProvider, TokenSet


logger = logging.getLogger(__name__)


class OIDCAuthProvider(AuthProvider):
    """OIDC provider implementation with discovery."""

    def __init__(self, config: Dict[str, Any]):
        self.issuer_url = config["issuer_url"].rstrip("/")
        self.client_id = config["client_id"]
        self.client_secret = config.get("client_secret")
        self.redirect_uri = config.get("redirect_uri", "http://localhost:3002/auth/callback")
        self.scopes = config.get("scopes", "openid profile email")
        self.group_claim = config.get("group_claim", "groups")
        self.logout_url = config.get("logout_url")
        self.logout_redirect_param = config.get(
            "logout_redirect_param", "post_logout_redirect_uri"
        )
        self.timeout_seconds = int(config.get("timeout_seconds", 10))

        self._discovery: Optional[Dict[str, Any]] = None
        self._jwks_client: Optional[PyJWKClient] = None
        self._discovery_lock = threading.Lock()

    def _discover(self) -> Dict[str, Any]:
        if self._discovery is not None:
            return self._discovery

        with self._discovery_lock:
            if self._discovery is not None:
                return self._discovery

            discovery_url = f"{self.issuer_url}/.well-known/openid-configuration"
            response = requests.get(discovery_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            self._discovery = response.json()
        return self._discovery

    async def _discover_async(self) -> Dict[str, Any]:
        """Async wrapper to avoid blocking event loop on discovery HTTP call."""
        return await asyncio.to_thread(self._discover)

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is not None:
            return self._jwks_client

        discovery = self._discover()
        jwks_uri = discovery.get("jwks_uri")
        if not jwks_uri:
            raise ValueError("OIDC discovery missing jwks_uri")

        self._jwks_client = PyJWKClient(jwks_uri)
        return self._jwks_client

    def _extract_groups(self, claims: Dict[str, Any]) -> List[str]:
        """Extract groups from a direct claim or dot-path (e.g. realm_access.roles)."""
        value: Any = claims
        for part in self.group_claim.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break

        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [value]
        return []

    def get_login_url(
        self,
        state: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
    ) -> str:
        discovery = self._discover()
        authorize_endpoint = discovery.get("authorization_endpoint")
        if not authorize_endpoint:
            raise ValueError("OIDC discovery missing authorization_endpoint")

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": self.scopes,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge_method": code_challenge_method,
            "code_challenge": code_challenge,
        }
        return f"{authorize_endpoint}?{urlencode(params)}"

    async def handle_callback(self, code: str, code_verifier: str) -> TokenSet:
        discovery = await self._discover_async()
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("OIDC discovery missing token_endpoint")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }

        headers: Dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.client_secret:
            credentials = f"{self.client_id}:{self.client_secret}"
            credentials_b64 = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {credentials_b64}"
        else:
            data["client_id"] = self.client_id

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                token_endpoint,
                data=data,
                headers=headers,
            )
        response.raise_for_status()
        payload = response.json()

        id_token = payload.get("id_token")
        if not id_token:
            raise ValueError("OIDC token response missing id_token")

        return TokenSet(
            id_token=id_token,
            access_token=payload.get("access_token"),
            refresh_token=payload.get("refresh_token"),
            expires_in=payload.get("expires_in"),
        )

    async def validate_token(self, token: str) -> Dict[str, Any]:
        discovery = await self._discover_async()
        jwks_client = self._get_jwks_client()
        signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)

        issuer = discovery.get("issuer", self.issuer_url)
        try:
            decoded = await asyncio.to_thread(
                jwt.decode,
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=issuer,
                options={"verify_at_hash": False},
            )
        except JWTError as exc:
            logger.error("OIDC token validation failed: %s", exc)
            raise

        return decoded

    def extract_principal(self, claims: Dict[str, Any]) -> AuthPrincipal:
        groups = self._extract_groups(claims)
        return AuthPrincipal(
            subject=claims.get("sub", ""),
            email=claims.get("email"),
            display_name=claims.get("name") or claims.get("preferred_username") or claims.get("email"),
            groups=groups,
            raw_claims=claims,
            provider=self.provider_name,
        )

    def get_logout_url(self, redirect_uri: Optional[str] = None) -> Optional[str]:
        target = redirect_uri or self.redirect_uri

        if self.logout_url:
            params = {"client_id": self.client_id}
            if target:
                params[self.logout_redirect_param] = target
            return f"{self.logout_url}?{urlencode(params)}"

        discovery = self._discover()
        end_session_endpoint = discovery.get("end_session_endpoint")
        if not end_session_endpoint:
            return None

        params = {}
        if target:
            params[self.logout_redirect_param] = target
        if self.client_id:
            params["client_id"] = self.client_id
        if not params:
            return end_session_endpoint
        return f"{end_session_endpoint}?{urlencode(params)}"

    @property
    def provider_name(self) -> str:
        return "oidc"
