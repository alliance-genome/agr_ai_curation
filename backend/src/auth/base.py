"""Provider-agnostic authentication contracts and models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuthPrincipal:
    """Normalized authenticated user identity."""

    subject: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    raw_claims: Dict[str, Any] = field(default_factory=dict)
    provider: str = "unknown"


@dataclass
class TokenSet:
    """Token payload returned by an auth provider callback."""

    id_token: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None


class AuthProvider(ABC):
    """Base interface for pluggable auth providers."""

    @abstractmethod
    def get_login_url(
        self,
        state: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
    ) -> str:
        """Build the provider authorization URL."""

    @abstractmethod
    async def handle_callback(self, code: str, code_verifier: str) -> TokenSet:
        """Exchange auth code for provider tokens."""

    @abstractmethod
    async def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate token and return token claims."""

    @abstractmethod
    def extract_principal(self, claims: Dict[str, Any]) -> AuthPrincipal:
        """Normalize provider claims into AuthPrincipal."""

    @abstractmethod
    def get_logout_url(self, redirect_uri: Optional[str] = None) -> Optional[str]:
        """Build provider logout URL if available."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name."""
