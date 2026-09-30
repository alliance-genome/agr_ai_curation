"""Application adapter for the shared authentication runtime."""

from auth_runtime.base import AuthProvider
from auth_runtime.factory import create_auth_provider as create_runtime_auth_provider
from src.config import is_dev_mode
from src.lib.config.groups_loader import get_group_claim_key


def create_auth_provider() -> AuthProvider:
    """Preserve the application's EC2 dev-mode gate and configured group claim."""
    return create_runtime_auth_provider(
        dev_mode=is_dev_mode(), group_claim=get_group_claim_key()
    )
