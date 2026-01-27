"""Weaviate connection management library."""

import logging
from typing import Optional, Dict, Any
from contextlib import contextmanager
import asyncio
from urllib.parse import urlparse

import weaviate
from weaviate import WeaviateClient
from weaviate.auth import Auth

logger = logging.getLogger(__name__)


class WeaviateConnection:
    """Manages Weaviate client connection lifecycle."""

    _instance = None

    def __new__(cls, *_args, **_kwargs):
        """Singleton pattern implementation."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, url: str = "http://localhost:8080", api_key: Optional[str] = None):
        """Initialize Weaviate connection parameters.

        Args:
            url: Weaviate instance URL
            api_key: Optional API key for authentication
        """
        if not hasattr(self, 'initialized'):
            self.url = url
            self.api_key = api_key
            self._client: Optional[WeaviateClient] = None
            self.initialized = True

    def connect(self) -> WeaviateClient:
        """Establish connection to Weaviate.

        Returns:
            Weaviate client instance

        Raises:
            Exception: If connection fails
        """
        if self._client is not None and self._client.is_ready():
            return self._client

        try:
            # Parse URL to get host and port
            parsed = urlparse(self.url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 8080)

            # For local connections, use the simpler connect_to_local method
            if host in ["localhost", "127.0.0.1", "weaviate"] and port == 8080:
                if host == "weaviate":
                    # Docker container hostname
                    self._client = weaviate.connect_to_local(host="weaviate", port=8080)
                else:
                    self._client = weaviate.connect_to_local()
            else:
                # For remote connections, use custom connection
                secure = parsed.scheme == "https"
                auth_config = Auth.api_key(self.api_key) if self.api_key else None

                self._client = weaviate.connect_to_custom(
                    http_host=host,
                    http_port=port,
                    http_secure=secure,
                    grpc_host=host,
                    grpc_port=50051,  # Standard gRPC port
                    grpc_secure=secure,
                    auth_credentials=auth_config,
                    skip_init_checks=False
                )

            logger.info(f"Connected to Weaviate at {self.url}")
            return self._client

        except Exception as e:
            logger.error(f"Failed to connect to Weaviate: {e}")
            raise Exception(f"Connection failed: {e}")

    def disconnect(self) -> None:
        """Close connection to Weaviate."""
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.error(f"Error closing Weaviate connection: {e}")
            finally:
                self._client = None
                logger.info("Disconnected from Weaviate")

    def is_connected(self) -> bool:
        """Check if connection is active.

        Returns:
            True if connected, False otherwise
        """
        if not self._client:
            return False

        try:
            return self._client.is_ready()
        except Exception:
            return False

    async def connect_to_weaviate(self) -> None:
        """Async method to connect to Weaviate."""
        self.connect()

    async def close(self) -> None:
        """Async method to close connection."""
        self.disconnect()

    @contextmanager
    def session(self):
        """Context manager for Weaviate session.

        Yields:
            Weaviate client instance
        """
        client = self.connect()
        try:
            yield client
        finally:
            # Keep connection open for reuse
            pass

    async def connect_to_weaviate(self) -> None:
        """Async wrapper for establishing connection."""
        await asyncio.get_event_loop().run_in_executor(None, self.connect)

    async def close(self) -> None:
        """Async wrapper for closing connection."""
        await asyncio.get_event_loop().run_in_executor(None, self.disconnect)

    async def health_check(self) -> Dict[str, Any]:
        """Async health check for Weaviate cluster.

        Returns:
            Dictionary with health status information
        """
        def _health_check():
            try:
                if not self._client:
                    self.connect()

                # Check if client is ready
                if not self._client.is_ready():
                    return {
                        "status": "unhealthy",
                        "message": "Client is not ready"
                    }

                # Get cluster info
                cluster_info = self._client.cluster.nodes()

                # Get collections count
                collections = self._client.collections.list_all()

                return {
                    "status": "healthy",
                    "nodes": len(cluster_info) if cluster_info else 1,
                    "version": "v4",  # Version info not directly available in v4
                    "collections": len(collections) if collections else 0
                }
            except Exception as e:
                logger.error(f"Health check failed: {e}")
                return {
                    "status": "unhealthy",
                    "message": str(e)
                }

        return await asyncio.get_event_loop().run_in_executor(None, _health_check)


# Module-level singleton connection
_connection: Optional[WeaviateConnection] = None


def get_connection() -> WeaviateConnection:
    """Get the singleton Weaviate connection instance.

    Returns:
        The global WeaviateConnection instance

    Raises:
        RuntimeError: If connection not initialized
    """
    global _connection
    if _connection is None:
        # Create and connect to default Weaviate instance
        import os
        weaviate_host = os.getenv("WEAVIATE_HOST", "localhost")
        weaviate_port = os.getenv("WEAVIATE_PORT", "8080")
        weaviate_scheme = os.getenv("WEAVIATE_SCHEME", "http")
        weaviate_url = f"{weaviate_scheme}://{weaviate_host}:{weaviate_port}"

        _connection = WeaviateConnection(url=weaviate_url)
        # Actually connect to Weaviate
        _connection.connect()
    return _connection


def set_connection(connection: WeaviateConnection) -> None:
    """Set the global Weaviate connection instance.

    Args:
        connection: WeaviateConnection instance to use globally
    """
    global _connection
    _connection = connection


def connect_to_weaviate(url: str = "http://localhost:8080",
                        api_key: Optional[str] = None) -> WeaviateClient:
    """Establish a global Weaviate connection.

    Args:
        url: Weaviate instance URL
        api_key: Optional API key for authentication

    Returns:
        Weaviate client instance
    """
    global _connection
    _connection = WeaviateConnection(url, api_key)
    return _connection.connect()


def close_connection() -> None:
    """Close the global Weaviate connection."""
    global _connection
    if _connection:
        _connection.disconnect()
        _connection = None


def health_check() -> Dict[str, Any]:
    """Check Weaviate cluster health.

    Returns:
        Dictionary with health status information

    Raises:
        RuntimeError: If no connection established
    """
    global _connection
    if not _connection:
        raise RuntimeError("No Weaviate connection established")

    with _connection.session() as client:
        try:
            # Check if client is ready
            if not client.is_ready():
                return {
                    "healthy": False,
                    "error": "Client is not ready"
                }

            # Get cluster info
            nodes = client.cluster.nodes()

            return {
                "healthy": True,
                "nodes": nodes if nodes else [],
                "version": "v4",  # Version info not directly available in v4
                "modules": {}  # Module info would need separate queries in v4
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "healthy": False,
                "error": str(e)
            }


def get_collection_info(collection_name: str) -> Dict[str, Any]:
    """Get information about a Weaviate collection.

    Args:
        collection_name: Name of the collection

    Returns:
        Dictionary with collection information

    Raises:
        RuntimeError: If no connection established
    """
    global _connection
    if not _connection:
        raise RuntimeError("No Weaviate connection established")

    with _connection.session() as client:
        try:
            # Get collection from client
            collection = client.collections.get(collection_name)

            # Get collection configuration
            config = collection.config.get()

            # Get object count using aggregation
            aggregate_result = collection.aggregate.over_all(total_count=True)
            count = aggregate_result.total_count if aggregate_result else 0

            return {
                "name": collection_name,
                "properties": config.properties if hasattr(config, 'properties') else [],
                "vectorizer": config.vectorizer if hasattr(config, 'vectorizer') else "none",
                "object_count": count,
                "schema": {
                    "class": collection_name,
                    "properties": config.properties if hasattr(config, 'properties') else []
                }
            }
        except Exception as e:
            logger.error(f"Failed to get collection info: {e}")
            return {
                "error": str(e)
            }