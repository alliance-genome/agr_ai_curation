"""
CurationConnectionResolver — sole entry point for curation DB connection config.

All curation DB consumers MUST use this resolver. Do not read CURATION_DB_URL
directly or call get_aws_credentials() from individual modules.

Credential resolution priority:
    1. CURATION_DB_URL env var (explicit override)
    2. credentials.source from connections.yaml
       - "env": use CURATION_DB_URL (already checked above, so no-op)
       - "aws_secrets": fetch from AWS Secrets Manager
       - "url": use url field from connections.yaml directly
"""

import json
import logging
import os
import threading
from typing import Optional, Dict, Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


class CurationConnectionResolver:
    """Resolves curation database connection details from config/connections.yaml.

    This is the single source of truth for curation DB connectivity. All modules
    that need curation DB access should use get_curation_resolver() instead of
    reading environment variables directly.
    """

    def __init__(self):
        self._connection_url: Optional[str] = None
        self._db_client = None
        self._resolved = False
        self._lock = threading.Lock()

    def _resolve(self) -> None:
        """Resolve the connection URL using the priority chain."""
        if self._resolved:
            return

        with self._lock:
            if self._resolved:
                return

            url = self._try_resolve()
            self._connection_url = url
            self._resolved = True

            if url:
                # Log with redacted credentials
                from src.lib.config.connections_loader import _redact_url_credentials
                logger.info(
                    "Curation DB connection resolved: %s",
                    _redact_url_credentials(url),
                )
            else:
                logger.info("Curation DB not configured — agents will operate without it")

    def _try_resolve(self) -> Optional[str]:
        """Try each resolution strategy in priority order.

        Returns:
            PostgreSQL connection URL, or None if not configured.
        """
        # Priority 1: CURATION_DB_URL env var (explicit override)
        url = os.getenv("CURATION_DB_URL")
        if url:
            logger.debug("Using CURATION_DB_URL environment variable")
            return url

        # Priority 2: credentials.source from connections.yaml
        url = self._try_connections_config()
        if url:
            return url

        return None

    def _try_connections_config(self) -> Optional[str]:
        """Resolve from connections.yaml credentials config."""
        try:
            from src.lib.config.connections_loader import get_connection
        except ImportError:
            logger.debug("connections_loader not available")
            return None

        conn = get_connection("curation_db")
        if not conn:
            return None

        # If URL is set in the connection config, use it directly
        if conn.url:
            return conn.url

        # Check credentials config
        if not conn.credentials:
            return None

        source = conn.credentials.source

        if source == "url":
            # URL should have been set in the connection config
            return conn.url if conn.url else None

        if source == "aws_secrets":
            return self._fetch_aws_credentials(conn.credentials)

        if source == "env":
            # Explicit env mode requires CURATION_DB_URL.
            return None

        raise ValueError(
            f"Invalid curation_db credentials.source '{source}'. "
            "Expected one of: env, aws_secrets, url"
        )

    def _fetch_aws_credentials(self, credentials) -> Optional[str]:
        """Fetch credentials from AWS Secrets Manager and build connection URL."""
        try:
            import boto3
        except ImportError:
            logger.warning("boto3 not installed — cannot use AWS Secrets Manager")
            return None

        try:
            if credentials.aws_profile:
                session = boto3.Session(profile_name=credentials.aws_profile)
            else:
                session = boto3.Session()

            client = session.client(
                "secretsmanager", region_name=credentials.aws_region
            )
            response = client.get_secret_value(SecretId=credentials.aws_secret_id)
            secret = json.loads(response["SecretString"])

            required_keys = ("username", "password", "host", "port", "dbname")
            missing = [k for k in required_keys if not secret.get(k)]
            if missing:
                raise ValueError(
                    "AWS Secrets Manager secret is missing required keys: "
                    + ", ".join(sorted(missing))
                )

            username = secret["username"]
            password = quote(secret["password"], safe="")
            dbname = str(secret["dbname"])
            host = str(secret["host"])
            port = str(secret["port"])

            logger.info(
                "Retrieved curation DB credentials from AWS Secrets Manager: %s",
                credentials.aws_secret_id,
            )
            return f"postgresql://{username}:{password}@{host}:{port}/{dbname}"

        except Exception as e:
            logger.error("Failed to retrieve AWS Secrets Manager credentials: %s", e)
            raise ValueError(
                "Failed to resolve curation DB credentials from AWS Secrets Manager"
            ) from e

    def get_connection_url(self) -> Optional[str]:
        """Returns the resolved PostgreSQL connection URL, or None if not configured."""
        self._resolve()
        return self._connection_url

    def get_db_client(self) -> Optional[Any]:
        """Returns a DatabaseMethods instance, or None if curation DB unavailable.

        Uses lazy initialization and caches the client instance.
        """
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

            self._db_client = DatabaseMethods(config)
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
            self._probe_connectivity(client)
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

            self._probe_connectivity(client)
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
        self._connection_url = None
        self._resolved = False


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
