"""Curation database client lifecycle backed by canonical PostgreSQL config."""

import logging
import os
import threading
from typing import Optional, Dict, Any

from src.lib.database.postgres_connection_resolver import (
    PostgresConnectionResolver,
    get_postgres_connection_resolver,
)

logger = logging.getLogger(__name__)


class CurationDbClient:
    """Public adapter around a published curation DB client."""

    def __init__(self, delegate: Any):
        self.__delegate = delegate

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.__delegate, name)

    def create_session(self) -> Any:
        """Return a SQLAlchemy session from the wrapped client."""
        return self.__delegate._create_session()

    def close(self) -> None:
        close = getattr(self.__delegate, "close", None)
        if callable(close):
            close()


class CurationConnectionResolver:
    """Manage the curation client while delegating PostgreSQL URL resolution.

    All curation consumers should use ``get_curation_resolver()`` for the domain
    client. ``PostgresConnectionResolver`` is the canonical URL and credential
    resolver shared with connection health checks.
    """

    def __init__(
        self,
        connection_resolver: Optional[PostgresConnectionResolver] = None,
    ):
        self._connection_resolver = (
            connection_resolver
            if connection_resolver is not None
            else get_postgres_connection_resolver("curation_db")
        )
        self._db_client = None
        self._db_client_lock = threading.Lock()

    def get_connection_url(self) -> Optional[str]:
        """Returns the resolved PostgreSQL connection URL, or None if not configured."""
        return self._connection_resolver.get_connection_url()

    def get_db_client(self) -> Optional[Any]:
        """Returns a DatabaseMethods instance, or None if curation DB unavailable.

        Uses lazy initialization and caches the client instance.
        """
        if self._db_client is not None:
            return self._db_client

        with self._db_client_lock:
            if self._db_client is not None:
                return self._db_client

            url = self.get_connection_url()
            if not url:
                return None

            try:
                # Import here to avoid circular imports and handle missing package
                import tempfile
                if "TMP_PATH" not in os.environ:
                    os.environ["TMP_PATH"] = tempfile.mkdtemp()

                from agr_curation_api.db_methods import DatabaseConfig, DatabaseMethods
                from urllib.parse import urlparse

                parsed = urlparse(url)
                config = DatabaseConfig()
                config.username = parsed.username
                config.password = parsed.password
                config.database = parsed.path.lstrip("/")
                config.host = parsed.hostname
                config.port = str(parsed.port) if parsed.port else "5432"

                self._db_client = CurationDbClient(DatabaseMethods(config))
                logger.info("Created curation DB client instance")
                return self._db_client

            except ImportError:
                logger.warning(
                    "agr_curation_api package not installed — curation DB client unavailable"
                )
                return None
            except Exception as e:
                logger.error("Failed to create curation DB client: %s", e)
                return None

    def is_configured(self) -> bool:
        """Whether curation DB connection is configured (not necessarily available)."""
        return self.get_connection_url() is not None

    def is_available(self) -> bool:
        """Whether curation DB is currently reachable."""
        client = self.get_db_client()
        if client is None:
            return False

        try:
            self._probe_connectivity_with_refresh(client)
            return True
        except Exception:
            return False

    def _probe_connectivity(self, client: Any) -> None:
        """Run a lightweight connectivity probe against the curation DB client.

        Uses provider metadata lookup instead of species-specific taxon queries to
        avoid hardcoded taxon fallbacks in health checks.
        """
        providers = client.get_data_providers()
        if providers is None:
            raise RuntimeError("Curation DB connectivity probe returned no provider data")

    def _probe_connectivity_with_refresh(self, client: Any) -> None:
        """Probe connectivity, retrying once with a fresh client on failure."""
        try:
            self._probe_connectivity(client)
            return
        except Exception as initial_error:
            logger.warning(
                "Curation DB connectivity probe failed; retrying with a fresh client: %s",
                initial_error,
            )

        self.close()
        refreshed_client = self.get_db_client()
        if refreshed_client is None:
            raise RuntimeError("Failed to recreate curation DB client")

        self._probe_connectivity(refreshed_client)

    def get_health_status(self) -> Dict[str, Any]:
        """Returns health check result for use in /health endpoint."""
        if not self.is_configured():
            return {
                "status": "not_configured",
                "message": "Curation database is not configured",
            }

        try:
            client = self.get_db_client()
            if client is None:
                return {
                    "status": "error",
                    "message": "Failed to create database client",
                }

            self._probe_connectivity_with_refresh(client)
            return {"status": "connected"}

        except Exception as e:
            return {
                "status": "disconnected",
                "message": f"Connection failed: {e}",
            }

    def close(self) -> None:
        """Close the database client connection."""
        if self._db_client is not None:
            try:
                self._db_client.close()
                logger.info("Closed curation DB client")
            except Exception as e:
                logger.warning("Error closing curation DB client: %s", e)
            finally:
                self._db_client = None

    def reset(self) -> None:
        """Reset resolver state (for testing)."""
        self.close()
        self._connection_resolver.reset()


# Module-level singleton
_resolver: Optional[CurationConnectionResolver] = None
_resolver_lock = threading.Lock()


def get_curation_resolver() -> CurationConnectionResolver:
    """Get the singleton CurationConnectionResolver instance."""
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = CurationConnectionResolver()
    return _resolver


def reset_curation_resolver() -> None:
    """Reset the singleton resolver (for testing)."""
    global _resolver
    if _resolver is not None:
        _resolver.reset()
    _resolver = None
