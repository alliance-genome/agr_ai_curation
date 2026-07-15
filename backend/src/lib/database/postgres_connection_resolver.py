"""Generic PostgreSQL URL resolution for config-defined external databases.

This resolver is intentionally limited to connection configuration. Domain-
specific database clients, such as the AGR curation client, remain separate.
"""

import json
import logging
import threading
from typing import Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


class PostgresConnectionResolver:
    """Resolve one PostgreSQL service from ``config/connections.yaml``."""

    def __init__(self, service_id: str):
        if not service_id.strip():
            raise ValueError("service_id must not be empty")
        self.service_id = service_id
        self._connection_url: Optional[str] = None
        self._resolved = False
        self._lock = threading.Lock()

    def _resolve(self) -> None:
        if self._resolved:
            return

        with self._lock:
            if self._resolved:
                return

            self._connection_url = self._try_connections_config()
            self._resolved = True

            if self._connection_url:
                from src.lib.config.connections_loader import redact_url_credentials

                logger.info(
                    "PostgreSQL connection resolved for %s: %s",
                    self.service_id,
                    redact_url_credentials(self._connection_url),
                )
            else:
                logger.info("PostgreSQL service %s is not configured", self.service_id)

    def _try_connections_config(self) -> Optional[str]:
        try:
            from src.lib.config.connections_loader import get_connection
        except ImportError:
            logger.debug("connections_loader not available")
            return None

        connection = get_connection(self.service_id)
        if not connection:
            return None
        if connection.url:
            return connection.url
        if not connection.credentials:
            return None

        source = connection.credentials.source
        if source == "aws_secrets":
            return self._fetch_aws_credentials(connection.credentials)
        if source == "env":
            return None
        if source == "url":
            raise ValueError(
                f"{self.service_id} credentials.source is 'url' but "
                f"services.{self.service_id}.url is empty"
            )
        raise ValueError(
            f"Invalid {self.service_id} credentials.source '{source}'. "
            "Expected one of: env, aws_secrets, url"
        )

    def _fetch_aws_credentials(self, credentials) -> Optional[str]:
        try:
            import boto3
        except ImportError:
            logger.warning("boto3 not installed — cannot use AWS Secrets Manager")
            return None

        try:
            session = (
                boto3.Session(profile_name=credentials.aws_profile)
                if credentials.aws_profile
                else boto3.Session()
            )
            client = session.client(
                "secretsmanager",
                region_name=credentials.aws_region,
            )
            response = client.get_secret_value(SecretId=credentials.aws_secret_id)
            secret = json.loads(response["SecretString"])

            required_keys = ("username", "password", "host", "port", "dbname")
            missing = [key for key in required_keys if not secret.get(key)]
            if missing:
                raise ValueError(
                    "AWS Secrets Manager secret is missing required keys: "
                    + ", ".join(sorted(missing))
                )

            username = quote(str(secret["username"]), safe="")
            password = quote(str(secret["password"]), safe="")
            host = str(secret["host"])
            port = str(secret["port"])
            dbname = quote(str(secret["dbname"]), safe="")

            logger.info(
                "Retrieved %s credentials from AWS Secrets Manager: %s",
                self.service_id,
                credentials.aws_secret_id,
            )
            return f"postgresql://{username}:{password}@{host}:{port}/{dbname}"
        except Exception as exc:
            logger.error(
                "Failed to retrieve AWS Secrets Manager credentials for %s: %s",
                self.service_id,
                exc,
            )
            raise ValueError(
                f"Failed to resolve {self.service_id} credentials from AWS Secrets Manager"
            ) from exc

    def get_connection_url(self) -> Optional[str]:
        """Return the resolved URL, or ``None`` when unconfigured."""
        self._resolve()
        return self._connection_url

    def reset(self) -> None:
        """Discard cached resolution state."""
        self._connection_url = None
        self._resolved = False


_resolvers: Dict[str, PostgresConnectionResolver] = {}
_resolvers_lock = threading.Lock()


def get_postgres_connection_resolver(service_id: str) -> PostgresConnectionResolver:
    """Return the process-wide resolver for a configured PostgreSQL service."""
    resolver = _resolvers.get(service_id)
    if resolver is None:
        with _resolvers_lock:
            resolver = _resolvers.get(service_id)
            if resolver is None:
                resolver = PostgresConnectionResolver(service_id)
                _resolvers[service_id] = resolver
    return resolver


def reset_postgres_connection_resolvers() -> None:
    """Reset all generic resolver singletons (primarily for tests)."""
    global _resolvers
    for resolver in _resolvers.values():
        resolver.reset()
    _resolvers = {}
