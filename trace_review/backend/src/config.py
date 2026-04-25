"""
Configuration module for trace_review backend

Loads environment variables and provides config helpers.
"""
import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

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

TRACE_SOURCES = ("remote", "local")

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


def sanitize_url_for_diagnostics(url: Optional[str]) -> str:
    """Return a URL that is safe to include in health responses."""
    if not url:
        return ""

    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname or ""
    except ValueError:
        return "[unparseable-url]"

    if not parts.netloc or "@" not in parts.netloc:
        return url

    port_text = f":{port}" if port else ""
    redacted_netloc = f"[redacted]@{hostname}{port_text}"
    return urlunsplit((parts.scheme, redacted_netloc, parts.path, parts.query, parts.fragment))


def validate_trace_source(source: str) -> str:
    """Validate a TraceReview Langfuse source selection."""
    if source in TRACE_SOURCES:
        return source

    expected = ", ".join(TRACE_SOURCES)
    raise ValueError(f"Unsupported trace source '{source}'. Expected one of: {expected}")


def get_trace_source_runtime_config(source: str) -> Dict[str, Optional[str]]:
    """Return raw runtime connection settings for a valid trace source."""
    validate_trace_source(source)

    if source == "local":
        return {
            "source": source,
            "host": get_langfuse_local_host(),
            "public_key": get_langfuse_local_public_key(),
            "secret_key": get_langfuse_local_secret_key(),
        }

    return {
        "source": source,
        "host": get_langfuse_host(),
        "public_key": get_langfuse_public_key(),
        "secret_key": get_langfuse_secret_key(),
    }


def get_trace_source_diagnostic(source: str) -> Dict[str, Any]:
    """Return non-secret diagnostic details for a TraceReview source."""
    runtime_config = get_trace_source_runtime_config(source)
    public_key = runtime_config.get("public_key")
    secret_key = runtime_config.get("secret_key")
    host = runtime_config.get("host") or ""

    if source == "local":
        required_env = [
            "LANGFUSE_LOCAL_HOST",
            "LANGFUSE_LOCAL_PUBLIC_KEY",
            "LANGFUSE_LOCAL_SECRET_KEY",
        ]
        description = "Local Langfuse source"
    else:
        required_env = [
            "LANGFUSE_HOST",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
        ]
        description = "Remote Langfuse source"

    missing = []
    if not host:
        missing.append(required_env[0])
    if not public_key:
        missing.append(required_env[1])
    if not secret_key:
        missing.append(required_env[2])

    safe_host = sanitize_url_for_diagnostics(host)

    return {
        "source": source,
        "description": description,
        "host": safe_host,
        "health_url": f"{safe_host.rstrip('/')}/api/public/health" if safe_host else "",
        "credentials": {
            "public_key_present": bool(public_key),
            "secret_key_present": bool(secret_key),
        },
        "ready": not missing,
        "missing_env": missing,
        "required_env": required_env,
    }


def get_trace_review_preflight_diagnostics(selected_source: str = "remote") -> Dict[str, Any]:
    """Return report-only TraceReview preflight configuration diagnostics."""
    source_error = None
    try:
        selected = validate_trace_source(selected_source)
    except ValueError as exc:
        selected = selected_source
        source_error = str(exc)

    source_diagnostics: Dict[str, Any] = {}
    for source in TRACE_SOURCES:
        source_diagnostics[source] = get_trace_source_diagnostic(source)

    selected_ready = False
    if source_error is None:
        selected_ready = bool(source_diagnostics[selected]["ready"])

    ssh_key_file = os.getenv("TRACE_REVIEW_PRODUCTION_SSH_KEY_FILE")
    return {
        "env_file_loaded": get_env_source(),
        "source_selection": {
            "selected": selected,
            "valid": source_error is None,
            "error": source_error,
            "valid_sources": list(TRACE_SOURCES),
            "selected_ready": selected_ready,
        },
        "langfuse_sources": source_diagnostics,
        "production_readiness": {
            "mode": "report_only",
            "vpn_route_hint": {
                "remote_langfuse_host": source_diagnostics["remote"]["host"],
                "note": "Use scripts/testing/trace_review_preflight.sh for host route and TCP checks.",
            },
            "ssh_tcp": {
                "host_configured": bool(os.getenv("TRACE_REVIEW_PRODUCTION_SSH_HOST")),
                "port": os.getenv("TRACE_REVIEW_PRODUCTION_SSH_PORT", "22"),
                "user_configured": bool(os.getenv("TRACE_REVIEW_PRODUCTION_SSH_USER")),
                "key_file_configured": bool(ssh_key_file),
                "key_file_readable": bool(ssh_key_file and Path(ssh_key_file).is_file()),
            },
            "environment": {
                "curation_db_url_present": bool(os.getenv("CURATION_DB_URL")),
                "curation_db_credentials_source": os.getenv("CURATION_DB_CREDENTIALS_SOURCE", ""),
                "curation_db_aws_secret_id_present": bool(os.getenv("CURATION_DB_AWS_SECRET_ID")),
                "aws_region_present": bool(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")),
                "aws_profile_present": bool(os.getenv("AWS_PROFILE")),
            },
        },
    }


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
    """Setup logging configuration.

    Deprecated: Use ``from src.logging_config import configure_logging`` instead.
    Kept for backwards compatibility.
    """
    from .logging_config import configure_logging
    configure_logging()


def get_env_source() -> Optional[str]:
    """Get the path from which .env was loaded.

    Returns:
        Path string if .env was loaded, None if no .env file was found.

    Useful for debugging configuration issues.
    """
    return _env_loaded_from
