"""
Connections Loader for Config-Driven Architecture.

This module loads external service connection definitions from config/connections.yaml.
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

logger = logging.getLogger(__name__)


def _find_project_root() -> Optional[Path]:
    """Find project root by looking for pyproject.toml or docker-compose.yml.

    Returns:
        Path to project root directory, or None if not found
    """
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists() or (parent / "docker-compose.yml").exists():
            return parent
    return None


def _get_default_connections_path() -> Path:
    """Get the default connections.yaml path, trying multiple strategies.

    Order of precedence:
    1. CONNECTIONS_CONFIG_PATH environment variable
    2. Project root detection (pyproject.toml or docker-compose.yml)
    3. Relative path from this module (fallback for Docker)

    Returns:
        Path to connections.yaml file
    """
    # Strategy 1: Environment variable
    env_path = os.environ.get("CONNECTIONS_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # Strategy 2: Project root detection
    project_root = _find_project_root()
    if project_root:
        return project_root / "config" / "connections.yaml"

    # Strategy 3: Relative path fallback (for Docker where backend is at /app/backend)
    return Path(__file__).parent.parent.parent.parent.parent / "config" / "connections.yaml"


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


# Default path for connections configuration
DEFAULT_CONNECTIONS_PATH = _get_default_connections_path()

# Thread safety lock for initialization
_init_lock = threading.Lock()


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


@dataclass
class ConnectionDefinition:
    """
    Connection definition loaded from connections.yaml.

    Attributes:
        service_id: Unique identifier (e.g., "weaviate", "openai")
        description: Human-readable description
        url: Service URL (env vars already substituted)
        health_check: Health check configuration
        required: Whether this service is required for startup
        timeout_seconds: Timeout for health check requests
        is_healthy: Current health status (set after health check)
        last_error: Last error message if health check failed
    """

    service_id: str
    description: str = ""
    url: str = ""
    health_check: HealthCheck = field(default_factory=HealthCheck)
    required: bool = False
    timeout_seconds: int = 10
    is_healthy: Optional[bool] = None
    last_error: Optional[str] = None

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
            required=data.get("required", False),
            timeout_seconds=data.get("timeout_seconds", 10),
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
            connections_path = DEFAULT_CONNECTIONS_PATH

        if not connections_path.exists():
            raise FileNotFoundError(f"Connections configuration not found: {connections_path}")

        logger.info(f"Loading connection definitions from: {connections_path}")

        with open(connections_path, "r") as f:
            data = yaml.safe_load(f)

        if not data or "services" not in data:
            logger.warning(f"No services defined in {connections_path}")
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
                    f"(url={connection.url[:50]}..., required={connection.required})"
                )

            except Exception as e:
                logger.error(f"Failed to load connection {service_id}: {e}")
                raise

        _initialized = True
        logger.info(f"Loaded {len(_connection_registry)} connection definitions")

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
        Dictionary with service_id keys and status info values
    """
    if not _initialized:
        load_connections()

    status = {}
    for service_id, conn in _connection_registry.items():
        status[service_id] = {
            "service_id": service_id,
            "description": conn.description,
            "url": conn.url,
            "required": conn.required,
            "is_healthy": conn.is_healthy,
            "last_error": conn.last_error,
        }

    return status


def update_health_status(
    service_id: str,
    is_healthy: bool,
    error_message: Optional[str] = None
) -> None:
    """
    Update the health status of a connection.

    Args:
        service_id: The service identifier
        is_healthy: Whether the service is healthy
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
