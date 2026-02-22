"""Development authentication provider."""

from typing import Any, Dict, Optional
from urllib.parse import quote

from src.auth.base import AuthPrincipal, AuthProvider, TokenSet


class DevAuthProvider(AuthProvider):
    """Simple provider used when development mode bypass is enabled."""

    def get_login_url(
        self,
        state: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
    ) -> str:
        _ = code_challenge
        _ = code_challenge_method
        encoded_state = quote(state, safe="")
        return f"/api/auth/callback?code=dev-code&state={encoded_state}"

    async def handle_callback(self, code: str, code_verifier: str) -> TokenSet:
        return TokenSet(id_token="dev-token")

    async def validate_token(self, token: str) -> Dict[str, Any]:
        return {
            "sub": "dev-user-123",
            "email": "dev@localhost",
            "name": "Dev User",
            "groups": ["developers"],
        }

    def extract_principal(self, claims: Dict[str, Any]) -> AuthPrincipal:
        groups = claims.get("groups", [])
        if not isinstance(groups, list):
            groups = [str(groups)] if groups else []
        return AuthPrincipal(
            subject=claims.get("sub", "dev-user-123"),
            email=claims.get("email", "dev@localhost"),
            display_name=claims.get("name", "Dev User"),
            groups=groups,
            raw_claims=claims,
            provider=self.provider_name,
        )

    def get_logout_url(self, redirect_uri: Optional[str] = None) -> Optional[str]:
        return redirect_uri or "/"

    @property
    def provider_name(self) -> str:
        return "dev"
