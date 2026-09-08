"""Provider-agnostic authentication contracts and models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class AuthPrincipal:
    """Normalized authenticated user identity."""

    subject: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    groups: List[str] = field(default_factory=list)
    raw_claims: Dict[str, Any] = field(default_factory=dict)
    provider: str = "unknown"


@dataclass(frozen=True)
class PrincipalLookupIdentity:
    """Verified identity locator; contains no tokens or historical memberships."""

    subject: str
    auth_provider: str
    auth_issuer: str | None
    provider_username: str | None


class CurrentPrincipalDenied(PermissionError):
    """Authoritative account absence, disablement, or identity mismatch.

    Infrastructure, permission-to-read, and malformed-response failures must
    raise other exceptions, not this explicit authorization-denial signal.
    """


class CurrentPrincipalResolver(Protocol):
    """Synchronous, token-free administrative lookup run in a worker thread.

    Verify the configured issuer and stable identity, require an enabled
    account, and return AuthPrincipal with complete current memberships.
    Never reconstruct authorization from historical claims. Use bounded I/O
    and close resources; unavailable or incomplete reads must raise.
    """

    def __call__(self, identity: PrincipalLookupIdentity, /) -> AuthPrincipal:
        """Resolve one authoritative current principal or raise."""
        ...


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
