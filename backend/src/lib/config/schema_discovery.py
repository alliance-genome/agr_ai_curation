"""
Schema Discovery for Config-Driven Architecture.

This module discovers and registers Pydantic schemas from resolved agent
configuration sources. Each agent bundle may contain a schema.py file with
envelope classes.

Envelope classes are identified by:
- Class name ending in "Envelope" (e.g., GeneResultEnvelope)
- Or having __envelope_class__ = True attribute

Usage:
    from src.lib.config import discover_agent_schemas, get_agent_schema

    # Discover all schemas at startup
    schemas = discover_agent_schemas()

    # Get a specific schema
    GeneEnvelope = get_agent_schema("GeneResultEnvelope")
"""

import importlib.util
import logging
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Type, Any

import yaml
from pydantic import BaseModel

from .agent_sources import get_default_agent_search_path, resolve_agent_config_sources

logger = logging.getLogger(__name__)

# Thread safety lock for initialization
_init_lock = threading.Lock()

# Module-level cache for discovered schemas
_schema_registry: Dict[str, Type[BaseModel]] = {}
_schema_by_agent: Dict[str, Type[BaseModel]] = {}  # agent_id -> envelope class
_registered_modules: List[str] = []  # Track modules for cleanup
_initialized: bool = False


def _builtin_output_schemas() -> Dict[str, Type[BaseModel]]:
    """Return backend-owned schemas referenced by first-party agent definitions."""

    from src.lib.openai_agents.models import PdfExtractionResultEnvelope
    from src.schemas.curation_prep import CurationPrepAgentOutput

    return {
        "CurationPrepAgentOutput": CurationPrepAgentOutput,
        "PdfExtractionResultEnvelope": PdfExtractionResultEnvelope,
    }


def _is_envelope_class(cls: Any) -> bool:
    """
    Check if a class is an envelope class that should be registered.

    Envelope classes are identified by:
    1. Having __envelope_class__ = True attribute, OR
    2. Class name ending in "Envelope" AND being a Pydantic BaseModel
    """
    if not isinstance(cls, type):
        return False

    if not issubclass(cls, BaseModel):
        return False

    # Skip the BaseModel itself
    if cls is BaseModel:
        return False

    # Check for explicit marker
    if getattr(cls, "__envelope_class__", False):
        return True

    # Check naming convention
    if cls.__name__.endswith("Envelope"):
        return True

    return False


def _is_configured_schema_class(cls: Any, configured_schema: str | None) -> bool:
    if configured_schema is None:
        return False
    if not isinstance(cls, type):
        return False
    if not issubclass(cls, BaseModel):
        return False
    return cls is not BaseModel and cls.__name__ == configured_schema


def _load_schema_module(
    schema_path: Path,
    folder_name: str,
    package_id: str | None = None,
    configured_schema: str | None = None,
) -> Dict[str, Type[BaseModel]]:
    """
    Dynamically load a schema.py file and extract envelope classes.

    Args:
        schema_path: Path to the schema.py file
        folder_name: Name of the agent folder (for module naming)
        package_id: Owning package ID when loading from package exports

    Returns:
        Dictionary of class_name -> Pydantic model class
    """
    global _registered_modules

    # Create a unique module name to avoid conflicts
    module_suffix = folder_name.replace("-", "_").replace(".", "_")
    if package_id:
        package_suffix = package_id.replace("-", "_").replace(".", "_")
        module_name = f"agent_schemas.{package_suffix}.{module_suffix}"
    else:
        module_name = f"agent_schemas.{module_suffix}"

    # Load the module
    spec = importlib.util.spec_from_file_location(module_name, schema_path)
    if spec is None or spec.loader is None:
        logger.warning('Could not load spec for %s', schema_path)
        return {}

    module = importlib.util.module_from_spec(spec)

    # Add to sys.modules so imports within the module work
    sys.modules[module_name] = module
    _registered_modules.append(module_name)  # Track for cleanup

    try:
        original_dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error('Failed to execute module %s: %s', schema_path, e)
        # Clean up
        sys.modules.pop(module_name, None)
        _registered_modules.remove(module_name)
        raise
    finally:
        sys.dont_write_bytecode = original_dont_write_bytecode

    # Find all envelope classes DEFINED in the module (not imported)
    envelope_classes: Dict[str, Type[BaseModel]] = {}
    for name in dir(module):
        if name.startswith("_"):
            continue

        obj = getattr(module, name)
        if _is_envelope_class(obj) or _is_configured_schema_class(obj, configured_schema):
            # Only include classes defined in this module, not imported ones
            # This prevents StructuredMessageEnvelope (imported base class) from being registered
            if getattr(obj, "__module__", None) == module_name:
                envelope_classes[name] = obj
                logger.debug('Found envelope class: %s in %s/schema.py', name, folder_name)
            else:
                logger.debug('Skipping imported class: %s (from %s)', name, getattr(obj, '__module__', 'unknown'))

    return envelope_classes


def _configured_output_schema_name(agent_yaml: Path | None) -> str | None:
    if agent_yaml is None or not agent_yaml.exists():
        return None
    data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return None
    schema_name = str(data.get("output_schema") or "").strip()
    return schema_name or None


def _discover_schema_indexes(
    agents_path: Optional[Path] = None,
) -> tuple[Dict[str, Type[BaseModel]], Dict[str, Type[BaseModel]]]:
    schema_registry: Dict[str, Type[BaseModel]] = {}
    schema_by_agent: Dict[str, Type[BaseModel]] = {}

    for source in resolve_agent_config_sources(agents_path):
        schema_py = source.schema_py
        if schema_py is None or not schema_py.exists():
            logger.debug('No schema.py in %s', source.folder_name)
            continue

        try:
            configured_schema = _configured_output_schema_name(source.agent_yaml)
            envelope_classes = _load_schema_module(
                schema_py,
                source.folder_name,
                source.package_id,
                configured_schema,
            )

            for class_name, cls in envelope_classes.items():
                if class_name in schema_registry:
                    logger.warning(
                        f"Duplicate schema class name: {class_name} "
                        f"(already registered, skipping from {source.folder_name})"
                    )
                    continue

                schema_registry[class_name] = cls
                logger.info('Registered schema: %s from %s/schema.py', class_name, source.folder_name)

            # Also map by agent folder for convenience. Prefer the schema
            # named in agent.yaml when the bundle defines multiple schemas.
            if envelope_classes:
                if configured_schema and configured_schema in envelope_classes:
                    primary_class = envelope_classes[configured_schema]
                elif configured_schema:
                    # Package schemas are the canonical owner when a bundle
                    # ships schema.py; the configured output class must be
                    # defined there instead of relying on a backend shadow.
                    available_schemas = ", ".join(sorted(envelope_classes))
                    raise ValueError(
                        f"agent.yaml output_schema '{configured_schema}' not found "
                        f"in {source.folder_name}/schema.py; available schemas: "
                        f"{available_schemas or '(none)'}"
                    )
                else:
                    primary_class = list(envelope_classes.values())[0]
                schema_by_agent[source.folder_name] = primary_class

        except Exception as e:
            logger.error(
                'Failed to load schemas from %s%s: %s',
                source.folder_name,
                f" in package {source.package_id}" if source.package_id else "",
                e,
            )
            raise

    return schema_registry, schema_by_agent


def discover_agent_schemas(
    agents_path: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, Type[BaseModel]]:
    """
    Discover and register all schema envelope classes from agent folders.

    Scans each agent folder for schema.py files and extracts envelope classes.
    Folders starting with underscore (_) are skipped.

    This function is thread-safe; concurrent calls will block until
    initialization is complete.

    Args:
        agents_path: Optional search path. When omitted, scan installed packages.
        force_reload: Force reload even if already initialized

    Returns:
        Dictionary mapping class_name to Pydantic model class

    Raises:
        FileNotFoundError: If agents_path doesn't exist
    """
    global _schema_registry, _schema_by_agent, _initialized

    # Thread-safe initialization
    with _init_lock:
        if _initialized and not force_reload:
            return _schema_registry

        resolved_agents_path = (
            agents_path.expanduser().resolve(strict=False)
            if agents_path is not None
            else get_default_agent_search_path().expanduser().resolve(strict=False)
        )
        logger.info('Discovering agent schemas from: %s', resolved_agents_path)

        discovered_registry, _schema_by_agent = _discover_schema_indexes(agents_path)
        _schema_registry = {
            **_builtin_output_schemas(),
            **discovered_registry,
        }

        _initialized = True
        logger.info('Discovered %s schema envelope classes', len(_schema_registry))

        return _schema_registry


def get_agent_schema(class_name: str) -> Optional[Type[BaseModel]]:
    """
    Get a schema class by its class name.

    Args:
        class_name: The class name (e.g., "GeneResultEnvelope")

    Returns:
        Pydantic model class or None if not found
    """
    if not _initialized:
        discover_agent_schemas()

    return _schema_registry.get(class_name)


def resolve_output_schema(schema_key: str) -> Optional[Type[BaseModel]]:
    """Resolve a runtime output schema from canonical schema registration."""

    if not _initialized:
        discover_agent_schemas()

    return _schema_registry.get(schema_key)


def build_package_scoped_output_schema_resolver(
    agents_path: Optional[Path] = None,
) -> Any:
    """Build an output-schema resolver without replacing the shared schema cache."""

    local_registry: Dict[str, Type[BaseModel]] | None = None
    resolver_lock = threading.Lock()

    def resolve(schema_key: str) -> Optional[Type[BaseModel]]:
        nonlocal local_registry
        if local_registry is None:
            with resolver_lock:
                if local_registry is None:
                    discovered_registry, _schema_by_folder = _discover_schema_indexes(
                        agents_path
                    )
                    local_registry = {
                        **_builtin_output_schemas(),
                        **discovered_registry,
                    }
        return local_registry.get(schema_key)

    return resolve


def get_schema_for_agent(folder_name: str) -> Optional[Type[BaseModel]]:
    """
    Get the primary schema for an agent by folder name.

    Args:
        folder_name: The agent folder name (e.g., "gene")

    Returns:
        Primary envelope class for that agent, or None
    """
    if not _initialized:
        discover_agent_schemas()

    return _schema_by_agent.get(folder_name)


def list_agent_schemas() -> Dict[str, str]:
    """
    List all discovered schemas with their descriptions.

    Returns:
        Dictionary of class_name to description (from docstring)
    """
    if not _initialized:
        discover_agent_schemas()

    descriptions = {}
    for class_name, cls in _schema_registry.items():
        doc = cls.__doc__ or ""
        first_line = doc.strip().split("\n")[0] if doc else ""
        descriptions[class_name] = first_line or f"Schema: {class_name}"

    return descriptions


def get_schema_json(class_name: str) -> Optional[Dict[str, Any]]:
    """
    Get the JSON schema for a registered envelope class.

    Args:
        class_name: The class name (e.g., "GeneResultEnvelope")

    Returns:
        JSON schema dictionary or None if not found
    """
    cls = get_agent_schema(class_name)
    if cls is None:
        return None

    return cls.model_json_schema()


def is_initialized() -> bool:
    """Check if schemas have been discovered."""
    return _initialized


def reset_cache() -> None:
    """Reset the schema cache (for testing).

    Also cleans up dynamically loaded modules from sys.modules to prevent
    stale references during testing or hot-reload scenarios.
    """
    global _schema_registry, _schema_by_agent, _registered_modules, _initialized

    # Clean up dynamically loaded schema modules
    for module_name in _registered_modules:
        sys.modules.pop(module_name, None)

    _schema_registry = {}
    _schema_by_agent = {}
    _registered_modules = []
    _initialized = False
