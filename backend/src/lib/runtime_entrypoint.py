"""Production startup entrypoint for the modular backend container."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2 import sql

from src.lib.packages import (
    AgentBundleRegistrationError,
    ExportKind,
    PackageEnvironmentManager,
    PackageRegistry,
    build_package_health_report,
    get_file_output_dir,
    get_identifier_prefix_file_path,
    get_identifier_prefix_state_dir,
    get_package_runner_state_dir,
    get_pdf_storage_dir,
    get_pdfx_json_storage_dir,
    get_processed_json_storage_dir,
    get_runtime_config_dir,
    get_runtime_packages_dir,
    get_runtime_state_dir,
    load_package_registry,
    load_tool_registry,
    validate_agent_bundle_directory_registration,
)

logger = logging.getLogger(__name__)

DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = "8000"
DEFAULT_SERVER_WORKERS = "1"
PACKAGE_ENV_BOOTSTRAP_ENV_VAR = "AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START"
PACKAGE_ENV_BOOTSTRAP_DONE_ENV_VAR = "AGR_PACKAGE_ENVS_PREPARED"
_PREFIX_QUERIES = (
    "SELECT DISTINCT split_part(referencedcurie, ':', 1) AS prefix "
    "FROM crossreference WHERE referencedcurie LIKE '%:%' AND referencedcurie IS NOT NULL;",
    "SELECT DISTINCT split_part(curie, ':', 1) AS prefix "
    "FROM ontologyterm WHERE curie LIKE '%:%' AND curie IS NOT NULL;",
    "SELECT DISTINCT split_part(primaryexternalid, ':', 1) AS prefix "
    "FROM biologicalentity WHERE primaryexternalid LIKE '%:%' AND primaryexternalid IS NOT NULL;",
)


def configure_startup_logging() -> None:
    """Configure a simple logger for container startup work before app import."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="[backend-startup] %(levelname)s %(message)s",
    )


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"{name} must be one of true/false/1/0/yes/no/on/off; got {raw!r}"
    )


def ensure_runtime_layout() -> None:
    """Create the runtime directories used by the modular container contract."""
    directories = (
        get_runtime_config_dir(),
        get_runtime_packages_dir(),
        get_runtime_state_dir(),
        get_pdf_storage_dir(),
        get_pdfx_json_storage_dir(),
        get_processed_json_storage_dir(),
        get_file_output_dir(),
        get_identifier_prefix_state_dir(),
        get_package_runner_state_dir(),
    )
    for path in directories:
        path.mkdir(parents=True, exist_ok=True)


def validate_runtime_packages() -> PackageRegistry:
    """Validate package and tool registries for the mounted runtime packages."""
    packages_dir = get_runtime_packages_dir()
    report = build_package_health_report(packages_dir)

    if report["status"] == "unhealthy":
        details = "; ".join(report["validation_errors"]) or "package registry is unhealthy"
        raise RuntimeError(
            f"Runtime package validation failed for {packages_dir}: {details}"
        )

    registry = load_package_registry(
        packages_dir,
        fail_on_validation_error=True,
    )
    if not registry.loaded_packages:
        raise RuntimeError(
            "No compatible runtime packages were discovered in "
            f"{packages_dir}. Mount deployment packages at AGR_RUNTIME_PACKAGES_DIR "
            "or /runtime/packages."
        )

    _warn_undeclared_agent_bundle_directories(registry)

    tool_registry = load_tool_registry(
        packages_dir,
        fail_on_validation_error=True,
    )
    logger.info(
        "Validated runtime packages: loaded=%s failed=%s status=%s tool_bindings=%s",
        len(registry.loaded_packages),
        len(registry.failed_packages),
        report["status"],
        len(tool_registry.bindings),
    )
    if registry.failed_packages:
        for failure in registry.failed_packages:
            logger.warning(
                "Skipping runtime package %s at %s: %s",
                failure.package_id,
                failure.manifest_path,
                failure.reason,
            )

    return registry


def _warn_undeclared_agent_bundle_directories(registry: PackageRegistry) -> None:
    """Warn when on-disk agent bundles are undeclared but do not fail package loading."""
    for package in registry.loaded_packages:
        try:
            validate_agent_bundle_directory_registration(
                package.manifest_path,
                package.manifest,
            )
        except AgentBundleRegistrationError as exc:
            logger.warning(
                "Ignoring undeclared agent bundle directories for package '%s': %s",
                package.package_id,
                exc,
            )


def bootstrap_package_environments(registry: PackageRegistry) -> None:
    """Eagerly create per-package virtual environments when requested."""
    if not _parse_bool_env(PACKAGE_ENV_BOOTSTRAP_ENV_VAR, False):
        logger.info(
            "Skipping eager package environment bootstrap (%s=false)",
            PACKAGE_ENV_BOOTSTRAP_ENV_VAR,
        )
        return

    _bootstrap_package_environments(registry)


def _bootstrap_package_environments(registry: PackageRegistry) -> None:
    """Create per-package virtual environments for tool-bearing packages."""
    env_manager = PackageEnvironmentManager()
    bootstrapped = 0
    for package in registry.loaded_packages:
        if not any(
            export.kind == ExportKind.TOOL_BINDING
            for export in package.manifest.exports
        ):
            continue

        environment = env_manager.ensure_environment(package)
        bootstrapped += 1
        logger.info(
            "Package environment ready for %s (reused=%s)",
            package.package_id,
            environment.reused,
        )

    os.environ[PACKAGE_ENV_BOOTSTRAP_DONE_ENV_VAR] = "1"
    logger.info("Eager package environment bootstrap complete for %s package(s)", bootstrapped)


def maybe_prepare_package_tool_environments_on_start() -> bool:
    """Optionally validate runtime packages and prewarm tool environments on app startup."""
    if os.getenv(PACKAGE_ENV_BOOTSTRAP_DONE_ENV_VAR) == "1":
        logger.info(
            "Skipping eager package environment bootstrap (%s already set)",
            PACKAGE_ENV_BOOTSTRAP_DONE_ENV_VAR,
        )
        return False

    if not _parse_bool_env(PACKAGE_ENV_BOOTSTRAP_ENV_VAR, False):
        logger.info(
            "Skipping eager package environment bootstrap (%s=false)",
            PACKAGE_ENV_BOOTSTRAP_ENV_VAR,
        )
        return False

    ensure_runtime_layout()
    registry = validate_runtime_packages()
    _bootstrap_package_environments(registry)
    return True


def maybe_bootstrap_database() -> None:
    """Create the local compose database on demand when configured."""
    if not _parse_bool_env("RUN_DB_BOOTSTRAP_ON_START", False):
        logger.info("Skipping database bootstrap (RUN_DB_BOOTSTRAP_ON_START=false)")
        return

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        logger.info("DATABASE_URL is not set; skipping auto-create.")
        return

    parsed = urlparse(database_url)
    target_db = (parsed.path or "").lstrip("/")
    if not target_db:
        logger.info("DATABASE_URL has no database name; skipping auto-create.")
        return

    host = (parsed.hostname or "").strip()
    local_hosts = {"postgres", "localhost", "127.0.0.1"}
    # Docker Compose project-scoped Postgres containers are typically named
    # <project>-postgres-1, for example "all57-postgres-1".
    is_local_host = host in local_hosts or host.endswith("-postgres-1")
    if not is_local_host:
        logger.info(
            "Host %r is not a local compose Postgres target; skipping auto-create.",
            host or "<empty>",
        )
        return

    conn_kwargs = {
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "host": host or "postgres",
        "port": parsed.port or 5432,
        "connect_timeout": 5,
    }

    try:
        with psycopg2.connect(dbname=target_db, **conn_kwargs):
            logger.info("Database %r already exists.", target_db)
            return
    except psycopg2.OperationalError as exc:
        if "does not exist" not in str(exc):
            logger.error("Could not connect to target database %r: %s", target_db, exc)
            raise

    maintenance_db = os.getenv("POSTGRES_MAINTENANCE_DB", "postgres")
    logger.info(
        "Database %r missing; creating via maintenance db %r.",
        target_db,
        maintenance_db,
    )
    with psycopg2.connect(dbname=maintenance_db, **conn_kwargs) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            exists = cur.fetchone() is not None
            if exists:
                logger.info("Database %r already exists after race-safe check.", target_db)
            else:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
                logger.info("Created database %r.", target_db)


def maybe_run_database_migrations() -> None:
    """Run Alembic migrations when configured."""
    if not _parse_bool_env("RUN_DB_MIGRATIONS_ON_START", False):
        logger.info("Skipping database migrations (RUN_DB_MIGRATIONS_ON_START=false)")
        return

    logger.info("Running Alembic migrations...")
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        cwd=_backend_root(),
    )
    logger.info("Alembic migrations complete.")


def _redact_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    if not parsed.password:
        return database_url
    netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@", 1)
    return parsed._replace(netloc=netloc).geturl()


def refresh_identifier_prefixes() -> bool:
    """Refresh runtime identifier prefixes using the modular runtime path contract."""
    database_url = os.getenv("CURATION_DB_URL") or os.getenv("DATABASE_URL") or ""
    database_url = database_url.strip()
    if not database_url:
        logger.info("No CURATION_DB_URL/DATABASE_URL set; skipping prefix refresh.")
        return False

    prefix_file = get_identifier_prefix_file_path()
    prefix_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Refreshing identifier prefixes from %s into %s",
        _redact_database_url(database_url),
        prefix_file,
    )

    prefixes: set[str] = set()
    try:
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                for query in _PREFIX_QUERIES:
                    cur.execute(query)
                    for row in cur.fetchall():
                        prefix = str((row[0] if row else "") or "").strip()
                        if prefix:
                            prefixes.add(prefix)
    except Exception as exc:
        logger.warning("Prefix refresh failed; leaving existing prefixes in place: %s", exc)
        return False

    payload = {"prefixes": sorted(prefixes)}
    _write_json_atomically(prefix_file, payload)
    logger.info("Identifier prefix refresh complete with %s prefix(es).", len(prefixes))
    return True


def _write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    """Persist JSON via an atomic replace so concurrent readers never see partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def build_default_server_command() -> list[str]:
    """Build the default ASGI server command for the production container."""
    host = os.getenv("BACKEND_HOST", DEFAULT_SERVER_HOST).strip() or DEFAULT_SERVER_HOST
    port = os.getenv("BACKEND_PORT", DEFAULT_SERVER_PORT).strip() or DEFAULT_SERVER_PORT
    workers = os.getenv("BACKEND_WORKERS", DEFAULT_SERVER_WORKERS).strip() or DEFAULT_SERVER_WORKERS

    command = [
        "uvicorn",
        "main:app",
        "--host",
        host,
        "--port",
        port,
    ]
    if workers != "1":
        command.extend(["--workers", workers])
    return command


def exec_server_command(command: Sequence[str]) -> None:
    """Replace the current process with the configured server command."""
    if not command:
        raise ValueError("Server command must not be empty")

    argv = list(command)
    logger.info("Starting backend server: %s", " ".join(argv))
    os.execvpe(argv[0], argv, os.environ.copy())


def run_startup(server_command: Sequence[str] | None = None) -> None:
    """Run production startup steps, then hand off to the server process."""
    ensure_runtime_layout()
    registry = validate_runtime_packages()
    bootstrap_package_environments(registry)
    maybe_bootstrap_database()
    maybe_run_database_migrations()
    refresh_identifier_prefixes()
    exec_server_command(server_command or build_default_server_command())


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint used by the production container."""
    configure_startup_logging()
    run_startup(list(argv if argv is not None else sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
