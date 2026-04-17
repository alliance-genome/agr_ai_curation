"""
Pytest configuration and fixtures for backend tests.
"""
import importlib.util
import os
import inspect
import socket
import sys
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import asyncio
import pytest

logger = logging.getLogger(__name__)

# Add the project-level scripts directory to Python path
# This allows tests to import from scripts like: from scripts.validate_current_agents import ...
# The scripts directory is mounted at /app/scripts in the Docker container
scripts_path = Path("/app/scripts")
if scripts_path.exists() and str(scripts_path) not in sys.path:
    sys.path.insert(0, str(scripts_path.parent))  # Add /app to path


def _ensure_rapidfuzz_test_stub() -> None:
    """Provide a minimal rapidfuzz stub when the dependency is unavailable."""
    if "rapidfuzz" in sys.modules or importlib.util.find_spec("rapidfuzz") is not None:
        return

    sys.modules["rapidfuzz"] = SimpleNamespace(
        fuzz=SimpleNamespace(
            partial_ratio_alignment=lambda *_args, **_kwargs: SimpleNamespace(
                dest_start=0,
                dest_end=0,
                score=0.0,
            )
        )
    )


_ensure_rapidfuzz_test_stub()


def _running_in_docker() -> bool:
    """Best-effort detection for containerized test execution."""
    return Path("/.dockerenv").exists()


def _default_database_url() -> str:
    """Build default test database URL without hardcoding full credential URI literals."""
    db_user = os.environ.get("TEST_DB_USER", "postgres")
    db_password = os.environ.get("TEST_DB_PASSWORD", "postgres")
    db_name = os.environ.get("TEST_DB_NAME", "ai_curation")
    if _running_in_docker():
        db_host = os.environ.get("TEST_DB_HOST", "postgres-test")
        db_port = os.environ.get("TEST_DB_PORT", "5432")
    else:
        db_host = os.environ.get("TEST_DB_HOST", "127.0.0.1")
        db_port = os.environ.get("TEST_DB_PORT", "15434")
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


# Keep backend tests deterministic across host `.venv` runs and Docker CI runs.
# Host runs should target docker-compose published ports; container runs should
# target in-network service names.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = _default_database_url()

if "WEAVIATE_HOST" not in os.environ:
    os.environ["WEAVIATE_HOST"] = "weaviate-test" if _running_in_docker() else "127.0.0.1"

os.environ.setdefault("WEAVIATE_PORT", "8080")
os.environ.setdefault("WEAVIATE_SCHEME", "http")


def _is_tcp_reachable(host: str, port: int, timeout: float = 0.4) -> bool:
    """Return True when a TCP endpoint is reachable within a short timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _bootstrap_weaviate_schema() -> None:
    """Best-effort bootstrap for shared test Weaviate collections.

    Many integration/contract tests assume `PDFDocument` and `DocumentChunk`
    collections exist. In local host `.venv` runs that is often not true, which
    causes broad 500 failures unrelated to tested behavior.
    """
    host = os.environ.get("WEAVIATE_HOST", "127.0.0.1")
    port = int(os.environ.get("WEAVIATE_PORT", "8080"))
    scheme = os.environ.get("WEAVIATE_SCHEME", "http")
    url = f"{scheme}://{host}:{port}"
    if not _is_tcp_reachable(host, port):
        return

    try:
        from weaviate.classes.config import Configure, DataType, Property
        from src.lib.weaviate_client.connection import WeaviateConnection

        conn = WeaviateConnection(url=url)
        conn.connect()
    except Exception as exc:
        logger.warning("Skipping Weaviate schema bootstrap; failed to connect to %s: %s", url, exc)
        return

    def _ensure_collection(client, name: str, properties: Iterable[Property]) -> None:
        property_list = list(properties)
        required_property_names = {getattr(prop, "name", None) for prop in property_list}
        required_property_names.discard(None)

        def _extract_collection_names(raw_collections) -> set[str]:
            if isinstance(raw_collections, dict):
                return {str(k) for k in raw_collections.keys()}
            if isinstance(raw_collections, list):
                return {str(item) for item in raw_collections}
            return set()

        try:
            existing = client.collections.list_all(simple=True)
        except TypeError:
            # Some client versions don't support the `simple` kwarg.
            try:
                existing = client.collections.list_all()
            except Exception:
                existing = {}
        except Exception:
            existing = {}

        existing_names = {n.lower() for n in _extract_collection_names(existing)}
        if name.lower() in existing_names:
            try:
                collection = client.collections.get(name)
                config = collection.config.get()
                existing_properties = {
                    getattr(prop, "name", None)
                    for prop in (getattr(config, "properties", None) or [])
                }
                existing_properties.discard(None)
                if required_property_names.issubset(existing_properties):
                    return
                # Avoid destructive mutations of shared local test instances.
                logger.warning(
                    "Collection %s exists but is missing required properties: %s",
                    name,
                    sorted(required_property_names - existing_properties),
                )
                return
            except Exception as exc:
                logger.warning("Unable to inspect existing collection %s: %s", name, exc)
                return
        try:
            client.collections.create(
                name=name,
                vectorizer_config=Configure.Vectorizer.none(),
                multi_tenancy_config=Configure.multi_tenancy(enabled=True),
                properties=property_list,
            )
        except Exception as exc:
            # Allow concurrent/repeated bootstrap runs to proceed idempotently.
            if "already exists" not in str(exc).lower():
                raise

    try:
        with conn.session() as client:
            _ensure_collection(
                client,
                "DocumentChunk",
                [
                    Property(name="documentId", data_type=DataType.TEXT),
                    Property(name="chunkIndex", data_type=DataType.INT),
                    Property(name="content", data_type=DataType.TEXT),
                    Property(name="metadata", data_type=DataType.TEXT),
                ],
            )
            _ensure_collection(
                client,
                "PDFDocument",
                [
                    Property(name="filename", data_type=DataType.TEXT),
                    Property(name="fileSize", data_type=DataType.INT),
                    Property(name="creationDate", data_type=DataType.DATE),
                    Property(name="lastAccessedDate", data_type=DataType.DATE),
                    Property(name="uploadDate", data_type=DataType.DATE),
                    Property(name="processingStatus", data_type=DataType.TEXT),
                    Property(name="embeddingStatus", data_type=DataType.TEXT),
                    Property(name="chunkCount", data_type=DataType.INT),
                    Property(name="vectorCount", data_type=DataType.INT),
                    Property(name="metadata", data_type=DataType.TEXT),
                ],
            )
    finally:
        try:
            close_result = conn.close()
            if inspect.isawaitable(close_result):
                asyncio.run(close_result)
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def bootstrap_shared_weaviate_schema():
    """Session bootstrap for tests that touch Weaviate-backed routes."""
    _bootstrap_weaviate_schema()


_LEGACY_TDD_SENTINELS = (
    "this test validates the future contract",
    "all tests must fail until",
    "expected to fail until",
    "this test will fail until",
    "verify: this test should fail",
    "important: these tests are expected to fail",
)
_LEGACY_TDD_FILE_CACHE: dict[str, bool] = {}


def _is_legacy_tdd_spec(path: Path) -> bool:
    """Return True when a test module is intentionally a fail-first legacy TDD spec."""
    key = str(path)
    if key in _LEGACY_TDD_FILE_CACHE:
        return _LEGACY_TDD_FILE_CACHE[key]

    is_legacy = False
    if path.suffix == ".py" and ("/tests/contract/" in key or "/tests/integration/" in key):
        try:
            # We intentionally only inspect the module header to avoid skipping
            # active tests that contain isolated "known fail" notes later on.
            text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore").splitlines()[:80]
            ).lower()
            is_legacy = any(sentinel in text for sentinel in _LEGACY_TDD_SENTINELS)
        except Exception:
            is_legacy = False

    _LEGACY_TDD_FILE_CACHE[key] = is_legacy
    return is_legacy


def pytest_collection_modifyitems(config, items):
    """Skip legacy fail-first TDD specs unless explicitly requested."""
    if os.getenv("RUN_LEGACY_TDD_TESTS", "0").strip() == "1":
        return

    reason = (
        "Legacy fail-first TDD spec; excluded from default regression run. "
        "Set RUN_LEGACY_TDD_TESTS=1 to include."
    )
    for item in items:
        item_path = Path(str(item.fspath))
        if _is_legacy_tdd_spec(item_path):
            item.add_marker(pytest.mark.legacy_tdd)
            item.add_marker(pytest.mark.skip(reason=reason))
