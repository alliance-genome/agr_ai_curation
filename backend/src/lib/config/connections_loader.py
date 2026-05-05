"""
Connections Loader for Config-Driven Architecture.

This module loads external service connection definitions from runtime config or
config/connections.yaml.
It provides health check capabilities and connection status tracking.

Usage:
    from src.lib.config import load_connections, get_connection, check_service_health

    # Load all connections at startup
    connections = load_connections()

    # Get a specific connection
    weaviate = get_connection("weaviate")

    # Check health of a service
    is_healthy = check_service_health("weaviate")
"""

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml
from src.lib.config.package_default_sources import resolve_runtime_config_path

logger = logging.getLogger(__name__)


def _get_default_connections_path() -> Path:
    """Get the default connections.yaml path, preferring runtime config.

    Order of precedence:
    1. CONNECTIONS_CONFIG_PATH environment variable
    2. Runtime config directory (`AGR_RUNTIME_CONFIG_DIR` or `/runtime/config`)
    3. Project root fallback for repository-backed development

    Returns:
        Path to connections.yaml file
    """
    resolved_path, _ = resolve_runtime_config_path(
        explicit_path=None,
        env_var="CONNECTIONS_CONFIG_PATH",
        filename="connections.yaml",
    )
    return resolved_path


def _substitute_env_vars(value: str) -> str:
    """Substitute ${VAR:-default} patterns with environment variable values.

    Args:
        value: String potentially containing ${VAR:-default} patterns

    Returns:
        String with environment variables substituted
    """
    if not isinstance(value, str):
        return value

    # Pattern: ${VAR:-default} or ${VAR}
    pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'

    def replacer(match):
        var_name = match.group(1)
        default_value = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(var_name, default_value)

    return re.sub(pattern, replacer, value)


def _parse_boolean_value(value: Any, field_name: str) -> bool:
    """Parse a YAML boolean field, allowing env-substituted string values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = _substitute_env_vars(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field_name} must be a boolean value")


def _required_when_matches(data: Dict[str, Any]) -> Optional[bool]:
    """Return whether a conditional service predicate matches, if present."""
    required_when = data.get("required_when")
    if required_when is None:
        return None

    if not isinstance(required_when, dict):
        raise ValueError("required_when must be a mapping")

    env_name = str(required_when.get("env", "")).strip()
    if not env_name:
        raise ValueError("required_when.env must not be empty")

    expected_value = str(_substitute_env_vars(required_when.get("equals", ""))).strip().lower()
    default_value = str(_substitute_env_vars(required_when.get("default", ""))).strip()
    actual_value = os.getenv(env_name, default_value).strip().lower()

    return actual_value == expected_value


def _resolve_required_flag(data: Dict[str, Any]) -> bool:
    """Resolve whether a connection is required for startup."""
    required = _parse_boolean_value(data.get("required", False), "required")
    required_when_matches = _required_when_matches(data)
    return required or required_when_matches is True


def _resolve_active_flag(data: Dict[str, Any]) -> bool:
    """Resolve whether a connection should be actively health-checked."""
    required = _parse_boolean_value(data.get("required", False), "required")
    required_when_matches = _required_when_matches(data)

    if required:
        return True
    if required_when_matches is None:
        return True
    return required_when_matches


# Default path for connections configuration
DEFAULT_CONNECTIONS_PATH = _get_default_connections_path()

# Thread safety lock for initialization
_init_lock = threading.Lock()


@dataclass
class CredentialsConfig:
    """Credentials configuration for database services.

    Supports multiple credential sources:
    - "env": Credentials from environment variables (CURATION_DB_URL, etc.)
    - "aws_secrets": Credentials from AWS Secrets Manager
    - "url": Credentials embedded in the service URL
    """

    source: str = "env"
    aws_secret_id: str = ""
    aws_profile: str = ""
    aws_region: str = "us-east-1"

    @classmethod
    def from_yaml(cls, data: Optional[Dict[str, Any]]) -> Optional["CredentialsConfig"]:
        """Create a CredentialsConfig from parsed YAML data, or None if not present."""
        if not data:
            return None

        return cls(
            source=_substitute_env_vars(data.get("source", "env")),
            aws_secret_id=_substitute_env_vars(data.get("aws_secret_id", "")),
            aws_profile=_substitute_env_vars(data.get("aws_profile", "")),
            aws_region=_substitute_env_vars(data.get("aws_region", "us-east-1")),
        )


@dataclass
class HealthCheck:
    """Health check configuration for a service."""

    endpoint: Optional[str] = None
    method: str = "GET"
    expected_status: Any = 200  # Can be int or string (e.g., "PONG" for Redis)
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, data: Optional[Dict[str, Any]]) -> "HealthCheck":
        """Create a HealthCheck from parsed YAML data."""
        if not data:
            return cls()

        # Substitute env vars in headers
        headers = {}
        for key, value in data.get("headers", {}).items():
            headers[key] = _substitute_env_vars(value)

        return cls(
            endpoint=data.get("endpoint"),
            method=data.get("method", "GET"),
            expected_status=data.get("expected_status", 200),
            headers=headers,
        )


def _redact_url_credentials(url: str) -> str:
    """Redact credentials from a URL for safe display/logging.

    Replaces username and password in URL with '***' to prevent credential exposure.

    Examples:
        scheme://user:PASS@host:port/db -> scheme://***:***@host:port/db
        http://host:8080/path -> http://host:8080/path (unchanged, no auth)

    Args:
        url: URL that may contain credentials

    Returns:
        URL with credentials redacted (or original URL if no credentials present)
    """
    from urllib.parse import urlparse, urlunparse

    if not url:
        return url

    try:
        parsed = urlparse(url)

        # If there's a password in the URL, redact both username and password
        if parsed.password:
            # Reconstruct netloc with redacted credentials
            redacted_netloc = f"***:***@{parsed.hostname}"

            # Add port if present
            if parsed.port:
                redacted_netloc += f":{parsed.port}"

            # Rebuild URL with redacted netloc
            return urlunparse((
                parsed.scheme,
                redacted_netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))

        return url
    except Exception:
        # If parsing fails, return a safe generic message
        return "[URL parsing failed - redacted for safety]"


def redact_url_credentials(url: str) -> str:
    """Public wrapper for URL credential redaction."""
    return _redact_url_credentials(url)


def sanitize_error_message(error: Optional[str], max_length: int = 500) -> Optional[str]:
    """Sanitize error messages for safe display on public endpoints.

    Applies credential redaction to any URLs found in the error message
    and truncates overly long messages to prevent information leakage.

    Args:
        error: Error message that may contain sensitive information
        max_length: Maximum length before truncation (default 500 chars)

    Returns:
        Sanitized error message, or None if input is None
    """
    if not error:
        return error

    import re

    # Pattern to match URLs in error messages
    # Matches common URL schemes that might contain credentials
    url_pattern = r'((?:postgresql|postgres|redis|mysql|mongodb|http|https)://[^\s\'"<>]+)'

    def redact_match(match):
        return _redact_url_credentials(match.group(1))

    # Redact any URLs found in the error message
    sanitized = re.sub(url_pattern, redact_match, error, flags=re.IGNORECASE)

    # Truncate if too long
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "... [truncated]"

    return sanitized


@dataclass
class ConnectionDefinition:
    """
    Connection definition loaded from connections.yaml.

    Attributes:
        service_id: Unique identifier (e.g., "weaviate", "openai")
        description: Human-readable description
        url: Service URL (env vars already substituted) - INTERNAL USE ONLY
        health_check: Health check configuration
        required: Whether this service is required for startup
        timeout_seconds: Timeout for health check requests
        is_healthy: Current health status (set after health check)
        last_error: Last error message if health check failed

    Security Note:
        Always use display_url for logging and API responses to prevent
        credential exposure. The url field may contain credentials.
    """

    service_id: str
    description: str = ""
    url: str = ""
    health_check: HealthCheck = field(default_factory=HealthCheck)
    required: bool = False
    active: bool = True
    timeout_seconds: int = 10
    credentials: Optional[CredentialsConfig] = None
    is_healthy: Optional[bool] = None
    last_error: Optional[str] = None

    @property
    def display_url(self) -> str:
        """Return URL with credentials redacted for safe display/logging."""
        return _redact_url_credentials(self.url)

    @classmethod
    def from_yaml(cls, service_id: str, data: Dict[str, Any]) -> "ConnectionDefinition":
        """
        Create a ConnectionDefinition from parsed YAML data.

        Args:
            service_id: The service ID (e.g., "weaviate")
            data: Parsed YAML dictionary for this service

        Returns:
            ConnectionDefinition instance
        """
        # Substitute environment variables in URL
        url = _substitute_env_vars(data.get("url", ""))

        return cls(
            service_id=service_id,
            description=data.get("description", "").strip(),
            url=url,
            health_check=HealthCheck.from_yaml(data.get("health_check")),
            required=_resolve_required_flag(data),
            active=_resolve_active_flag(data),
            timeout_seconds=data.get("timeout_seconds", 10),
            credentials=CredentialsConfig.from_yaml(data.get("credentials")),
        )


# Module-level cache for loaded connections
_connection_registry: Dict[str, ConnectionDefinition] = {}
_initialized: bool = False


def load_connections(
    connections_path: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, ConnectionDefinition]:
    """
    Load connection definitions from connections.yaml.

    This function is thread-safe; concurrent calls will block until
    initialization is complete.

    Args:
        connections_path: Path to connections.yaml (default: config/connections.yaml)
        force_reload: Force reload even if already initialized

    Returns:
        Dictionary mapping service_id to ConnectionDefinition

    Raises:
        FileNotFoundError: If connections_path doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    global _connection_registry, _initialized

    # Thread-safe initialization
    with _init_lock:
        if _initialized and not force_reload:
            return _connection_registry

        if connections_path is None:
            connections_path = _get_default_connections_path()

        if not connections_path.exists():
            raise FileNotFoundError(f"Connections configuration not found: {connections_path}")

        logger.info('Loading connection definitions from: %s', connections_path)

        with open(connections_path, "r") as f:
            data = yaml.safe_load(f)

        if not data or "services" not in data:
            logger.warning('No services defined in %s', connections_path)
            _connection_registry = {}
            _initialized = True
            return _connection_registry

        _connection_registry = {}

        services_data = data.get("services", {})
        for service_id, service_data in services_data.items():
            try:
                connection = ConnectionDefinition.from_yaml(service_id, service_data)
                _connection_registry[service_id] = connection

                logger.info(
                    f"Loaded connection: {service_id} "
                    f"(url={connection.display_url[:50]}..., required={connection.required})"
                )

            except Exception as e:
                logger.error('Failed to load connection %s: %s', service_id, e)
                raise

        _initialized = True
        logger.info('Loaded %s connection definitions', len(_connection_registry))

        return _connection_registry


def get_connection(service_id: str) -> Optional[ConnectionDefinition]:
    """
    Get a connection definition by its service ID.

    Args:
        service_id: The service identifier (e.g., "weaviate", "openai")

    Returns:
        ConnectionDefinition or None if not found
    """
    if not _initialized:
        load_connections()

    return _connection_registry.get(service_id)


def list_connections() -> List[ConnectionDefinition]:
    """
    List all loaded connection definitions.

    Returns:
        List of ConnectionDefinition objects
    """
    if not _initialized:
        load_connections()

    return list(_connection_registry.values())


def get_required_connections() -> List[ConnectionDefinition]:
    """
    Get all connections marked as required.

    Returns:
        List of required ConnectionDefinition objects
    """
    if not _initialized:
        load_connections()

    return [c for c in _connection_registry.values() if c.required]


def get_optional_connections() -> List[ConnectionDefinition]:
    """
    Get all connections marked as optional.

    Returns:
        List of optional ConnectionDefinition objects
    """
    if not _initialized:
        load_connections()

    return [c for c in _connection_registry.values() if not c.required]


def get_connection_status() -> Dict[str, Dict[str, Any]]:
    """
    Get health status of all connections.

    Returns:
        Dictionary with service_id keys and status info values.
        Note: URL and last_error are sanitized to prevent information exposure.
    """
    if not _initialized:
        load_connections()

    status = {}
    for service_id, conn in _connection_registry.items():
        status[service_id] = {
            "service_id": service_id,
            "description": conn.description,
            "url": conn.display_url,  # Use display_url to prevent credential exposure
            "required": conn.required,
            "is_healthy": conn.is_healthy,
            "last_error": sanitize_error_message(conn.last_error),  # Sanitize error messages
        }

    return status


def update_health_status(
    service_id: str,
    is_healthy: Optional[bool],
    error_message: Optional[str] = None
) -> None:
    """
    Update the health status of a connection.

    Args:
        service_id: The service identifier
        is_healthy: Whether the service is healthy. None means intentionally
            not configured.
        error_message: Error message if not healthy
    """
    if not _initialized:
        load_connections()

    conn = _connection_registry.get(service_id)
    if conn:
        conn.is_healthy = is_healthy
        conn.last_error = error_message


def is_initialized() -> bool:
    """Check if connections have been loaded."""
    return _initialized


def reset_cache() -> None:
    """Reset the connections cache (for testing)."""
    global _connection_registry, _initialized
    _connection_registry = {}
    _initialized = False


async def check_service_health(service_id: str) -> Optional[bool]:
    """
    Check health of a specific service by its service ID.

    Performs the appropriate health check based on the service's health_check configuration:
    - HTTP endpoints: Makes HTTP request and checks response
    - Redis (PING method): Sends PING command
    - Postgres (CONNECT method): Tests database connection

    Args:
        service_id: The service identifier (e.g., "weaviate", "redis")

    Returns:
        True if service is healthy, False if unhealthy, None if intentionally
        not configured.

    Side effects:
        Updates the connection's is_healthy and last_error fields
    """
    if not _initialized:
        load_connections()

    conn = _connection_registry.get(service_id)
    if not conn:
        logger.warning('Unknown service: %s', service_id)
        return False

    if not conn.active:
        update_health_status(service_id, None, None)
        return None

    health = conn.health_check
    is_healthy: Optional[bool] = False
    error_message = None

    try:
        # Handle different health check methods
        if health.method == "PING":
            # Redis PING check
            is_healthy, error_message = await _check_redis_health(conn)
        elif health.method == "CONNECT":
            # Database connection check
            is_healthy, error_message = await _check_postgres_health(conn)
        elif health.method == "BEDROCK_RERANKER":
            # Bedrock reranker provider config and credential readiness check
            is_healthy, error_message = await _check_bedrock_reranker_health(conn)
        elif health.endpoint:
            # HTTP endpoint check
            is_healthy, error_message = await _check_http_health(conn)
        else:
            # No health check configured - assume healthy if URL is set
            is_healthy = bool(conn.url)
            if not is_healthy:
                error_message = "No URL configured"

    except Exception as e:
        is_healthy = False
        error_message = str(e)
        logger.error('Health check failed for %s: %s', service_id, e)

    # Update cached status
    update_health_status(service_id, is_healthy, error_message)

    return is_healthy


async def _check_http_health(conn: ConnectionDefinition) -> tuple[bool, Optional[str]]:
    """Check HTTP endpoint health."""
    import httpx

    health = conn.health_check
    url = f"{conn.url.rstrip('/')}{health.endpoint}"

    try:
        async with httpx.AsyncClient(timeout=conn.timeout_seconds) as client:
            response = await client.request(
                method=health.method,
                url=url,
                headers=health.headers
            )

            if response.status_code == health.expected_status:
                return True, None
            else:
                return False, f"Expected status {health.expected_status}, got {response.status_code}"

    except httpx.TimeoutException:
        return False, f"Connection timeout after {conn.timeout_seconds}s"
    except httpx.ConnectError as e:
        return False, f"Connection failed: {e}"
    except Exception as e:
        return False, str(e)


async def _check_redis_health(conn: ConnectionDefinition) -> tuple[bool, Optional[str]]:
    """Check Redis health via PING command.

    Uses from_url() to automatically handle authentication from redis:// URLs.
    Supports URLs like: redis://username:password@host:port/db
    """
    # Step 1: Check if redis package is available (separate from connection logic)
    try:
        import redis.asyncio as aioredis
    except ImportError:
        return False, "redis package not installed"

    # Step 2: Attempt connection and health check
    # Use from_url() to automatically handle auth from URL (KANBAN-1019)
    client = None
    try:
        client = aioredis.from_url(
            conn.url,
            socket_timeout=conn.timeout_seconds,
            socket_connect_timeout=conn.timeout_seconds,
        )

        result = await client.ping()
        if result:
            return True, None
        return False, "PING returned False"

    except ConnectionError as e:
        return False, f"Connection failed: {e}"
    except TimeoutError as e:
        return False, f"Connection timeout: {e}"
    except Exception as e:
        return False, str(e)
    finally:
        if client:
            await client.aclose()


async def _check_postgres_health(conn: ConnectionDefinition) -> tuple[Optional[bool], Optional[str]]:
    """Check Postgres health via connection test.

    For services with credentials config but no URL (e.g., curation_db using AWS
    Secrets Manager), resolves the effective URL via CurationConnectionResolver.
    """
    url = conn.url

    # If no URL but credentials are configured, try the curation resolver
    if not url and conn.credentials:
        try:
            from src.lib.database.curation_resolver import get_curation_resolver
            url = get_curation_resolver().get_connection_url()
        except ImportError:
            pass

    if not url:
        if conn.required:
            return False, "No connection URL configured"
        return None, None

    try:
        import asyncpg

        # Parse connection string or use URL directly
        # asyncpg can handle postgres:// URLs
        url = url.replace("postgresql://", "postgres://")

        try:
            conn_pg = await asyncpg.connect(url, timeout=conn.timeout_seconds)
            await conn_pg.execute("SELECT 1")
            await conn_pg.close()
            return True, None
        except Exception as e:
            return False, str(e)

    except ImportError:
        # Fall back to psycopg2 sync check if asyncpg not available
        try:
            import psycopg2
            from urllib.parse import urlparse

            parsed = urlparse(url)
            pg_conn = psycopg2.connect(
                host=parsed.hostname,
                port=parsed.port or 5432,
                user=parsed.username,
                password=parsed.password,
                dbname=parsed.path.lstrip('/'),
                connect_timeout=conn.timeout_seconds,
            )
            pg_conn.close()
            return True, None
        except ImportError:
            return False, "Neither asyncpg nor psycopg2 installed"
        except Exception as e:
            return False, str(e)


async def _check_bedrock_reranker_health(
    _conn: ConnectionDefinition,
) -> tuple[Optional[bool], Optional[str]]:
    """Check Bedrock reranker provider configuration readiness."""
    from src.lib.bedrock_reranker import get_bedrock_reranker_status

    status = get_bedrock_reranker_status(check_credentials=True)
    provider = str(status["provider"]).strip().lower()
    if provider in {"", "none", "local_transformers"}:
        return None, None

    if status.get("is_healthy") is True:
        return True, None

    reason = status["reason"]
    assert isinstance(reason, str)
    return False, reason


async def check_all_health() -> Dict[str, Dict[str, Any]]:
    """
    Check health of all configured services.

    Returns:
        Dictionary with service_id keys and health status info:
        {
            "weaviate": {
                "service_id": "weaviate",
                "description": "Vector database",
                "required": True,
                "is_healthy": True,
                "last_error": None,
                "url": "http://***@..."  # URL is redacted for security
            },
            ...
        }

    Note:
        The URL in the response is redacted (credentials replaced with ***)
        to prevent credential exposure in API responses and logs.
    """
    if not _initialized:
        load_connections()

    # Check all services concurrently
    import asyncio
    service_ids = list(_connection_registry.keys())

    await asyncio.gather(*[check_service_health(sid) for sid in service_ids])

    return get_connection_status()


async def check_required_services_healthy() -> tuple[bool, List[str]]:
    """
    Check if all required services are healthy.

    This is intended for startup gating - if required services are unhealthy,
    the application should not start.

    Returns:
        Tuple of (all_healthy, list_of_failed_service_ids)
    """
    if not _initialized:
        load_connections()

    required = get_required_connections()
    failed = []

    import asyncio
    await asyncio.gather(*[check_service_health(c.service_id) for c in required])

    for conn in required:
        if not conn.is_healthy:
            failed.append(conn.service_id)

    return len(failed) == 0, failed
