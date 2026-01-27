"""
Configuration module for trace_review backend

Loads environment variables and provides config helpers.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env file from secure home directory location ONLY.
# This prevents accidental commits of secrets to the repository.
#
# REQUIRED location: ~/.agr_ai_curation/trace_review/.env
#
# Setup:
#   mkdir -p ~/.agr_ai_curation/trace_review
#   cp .env.example ~/.agr_ai_curation/trace_review/.env
#   chmod 600 ~/.agr_ai_curation/trace_review/.env

_env_loaded_from: Optional[str] = None

def _load_env_file() -> Optional[str]:
    """Load .env from secure home directory location only."""
    home = Path.home()
    env_path = home / '.agr_ai_curation' / 'trace_review' / '.env'

    if env_path.exists():
        load_dotenv(env_path)
        return str(env_path)

    return None

_env_loaded_from = _load_env_file()

logger = logging.getLogger(__name__)

# Warn if .env not found in required location
if not _env_loaded_from:
    print("[trace_review] WARNING: No .env file found at ~/.agr_ai_curation/trace_review/.env")
    print("[trace_review] Copy .env.example to ~/.agr_ai_curation/trace_review/.env")


# ===========================
# AWS Cognito Configuration
# ===========================

def get_cognito_region() -> str:
    """Get AWS Cognito region from environment."""
    region = os.getenv("COGNITO_REGION", "us-east-1")
    return region


def get_cognito_user_pool_id() -> Optional[str]:
    """Get Cognito User Pool ID from environment."""
    pool_id = os.getenv("COGNITO_USER_POOL_ID")
    # Only warn if not in dev mode (expected in dev mode)
    if not pool_id and os.getenv("DEV_MODE", "false").lower() != "true":
        logger.warning("COGNITO_USER_POOL_ID not set - Cognito authentication disabled")
    return pool_id


def get_cognito_client_id() -> Optional[str]:
    """Get Cognito Client ID from environment."""
    client_id = os.getenv("COGNITO_CLIENT_ID")
    # Only warn if not in dev mode (expected in dev mode)
    if not client_id and os.getenv("DEV_MODE", "false").lower() != "true":
        logger.warning("COGNITO_CLIENT_ID not set - Cognito authentication disabled")
    return client_id


def get_cognito_client_secret() -> Optional[str]:
    """Get Cognito Client Secret from environment."""
    client_secret = os.getenv("COGNITO_CLIENT_SECRET")
    # Only warn if not in dev mode (expected in dev mode)
    if not client_secret and os.getenv("DEV_MODE", "false").lower() != "true":
        logger.warning("COGNITO_CLIENT_SECRET not set - Cognito authentication disabled")
    return client_secret


def get_cognito_domain() -> Optional[str]:
    """Get Cognito Domain from environment."""
    domain = os.getenv("COGNITO_DOMAIN")
    # Only warn if not in dev mode (expected in dev mode)
    if not domain and os.getenv("DEV_MODE", "false").lower() != "true":
        logger.warning("COGNITO_DOMAIN not set - Cognito authentication disabled")
    return domain


def get_cognito_redirect_uri() -> str:
    """Get Cognito Redirect URI from environment."""
    # Default to localhost for development
    redirect_uri = os.getenv("COGNITO_REDIRECT_URI", "http://localhost:3001/api/auth/callback")
    return redirect_uri


def is_cognito_configured() -> bool:
    """Check if all required Cognito config is present."""
    return all([
        get_cognito_user_pool_id(),
        get_cognito_client_id(),
        get_cognito_client_secret(),
        get_cognito_domain()
    ])


# ===========================
# Development Mode
# ===========================

def is_dev_mode() -> bool:
    """Check if development mode is enabled (bypasses Cognito)."""
    return os.getenv("DEV_MODE", "false").lower() == "true"


def get_secure_cookies() -> bool:
    """
    Check if secure cookies should be used.

    Returns:
        True for production (HTTPS), False for development (HTTP)
    """
    return os.getenv("SECURE_COOKIES", "false").lower() == "true"


# ===========================
# Langfuse Configuration
# ===========================

def get_langfuse_host() -> str:
    """Get Langfuse host URL from environment."""
    return os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")


def get_langfuse_public_key() -> Optional[str]:
    """Get Langfuse public key from environment."""
    return os.getenv("LANGFUSE_PUBLIC_KEY")


def get_langfuse_secret_key() -> Optional[str]:
    """Get Langfuse secret key from environment."""
    return os.getenv("LANGFUSE_SECRET_KEY")


def get_langfuse_local_host() -> str:
    """Get Local Langfuse host URL from environment."""
    # Default to host.docker.internal for Docker-to-host communication
    return os.getenv("LANGFUSE_LOCAL_HOST", "http://host.docker.internal:3000")


def get_langfuse_local_public_key() -> Optional[str]:
    """Get Local Langfuse public key from environment."""
    return os.getenv("LANGFUSE_LOCAL_PUBLIC_KEY")


def get_langfuse_local_secret_key() -> Optional[str]:
    """Get Local Langfuse secret key from environment."""
    return os.getenv("LANGFUSE_LOCAL_SECRET_KEY")


# ===========================
# Server Configuration
# ===========================

def get_backend_host() -> str:
    """Get backend host from environment."""
    return os.getenv("BACKEND_HOST", "localhost")


def get_backend_port() -> int:
    """Get backend port from environment."""
    return int(os.getenv("BACKEND_PORT", "8001"))


def get_frontend_url() -> str:
    """Get frontend URL from environment."""
    return os.getenv("FRONTEND_URL", "http://localhost:3001")


# ===========================
# Logging
# ===========================

def setup_logging(level: str = "INFO") -> None:
    """
    Setup logging configuration.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def get_env_source() -> Optional[str]:
    """Get the path from which .env was loaded.

    Returns:
        Path string if .env was loaded, None if no .env file was found.

    Useful for debugging configuration issues.
    """
    return _env_loaded_from
