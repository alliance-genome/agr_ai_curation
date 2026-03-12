"""
Schema Discovery for Config-Driven Architecture.

This module discovers and registers Pydantic schemas from resolved agent
configuration sources. Each agent bundle may contain a schema.py file with
envelope classes.

Envelope classes are identified by:
- Class name ending in "Envelope" (e.g., GeneValidationEnvelope)
- Or having __envelope_class__ = True attribute

Usage:
    from src.lib.config import discover_agent_schemas, get_agent_schema

    # Discover all schemas at startup
    schemas = discover_agent_schemas()

    # Get a specific schema
    GeneEnvelope = get_agent_schema("GeneValidationEnvelope")
"""

import importlib.util
import logging
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Type, Any

from pydantic import BaseModel

from .agent_sources import resolve_agent_config_sources

logger = logging.getLogger(__name__)

# Thread safety lock for initialization
_init_lock = threading.Lock()

# Module-level cache for discovered schemas
_schema_registry: Dict[str, Type[BaseModel]] = {}
_schema_by_agent: Dict[str, Type[BaseModel]] = {}  # agent_id -> envelope class
_registered_modules: List[str] = []  # Track modules for cleanup
_initialized: bool = False


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


def _load_schema_module(
    schema_path: Path,
    folder_name: str,
    package_id: str | None = None,
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
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error('Failed to execute module %s: %s', schema_path, e)
        # Clean up
        sys.modules.pop(module_name, None)
        _registered_modules.remove(module_name)
        raise

    # Find all envelope classes DEFINED in the module (not imported)
    envelope_classes: Dict[str, Type[BaseModel]] = {}
    for name in dir(module):
        if name.startswith("_"):
            continue

        obj = getattr(module, name)
        if _is_envelope_class(obj):
            # Only include classes defined in this module, not imported ones
            # This prevents StructuredMessageEnvelope (imported base class) from being registered
            if getattr(obj, "__module__", None) == module_name:
                envelope_classes[name] = obj
                logger.debug('Found envelope class: %s in %s/schema.py', name, folder_name)
            else:
                logger.debug('Skipping imported class: %s (from %s)', name, getattr(obj, '__module__', 'unknown'))

    return envelope_classes


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

        logger.info('Discovering agent schemas from: %s', agents_path)

        _schema_registry = {}
        _schema_by_agent = {}

        for source in resolve_agent_config_sources(agents_path):
            schema_py = source.schema_py
            if schema_py is None or not schema_py.exists():
                logger.debug('No schema.py in %s', source.folder_name)
                continue

            try:
                envelope_classes = _load_schema_module(
                    schema_py,
                    source.folder_name,
                    source.package_id,
                )

                for class_name, cls in envelope_classes.items():
                    if class_name in _schema_registry:
                        logger.warning(
                            f"Duplicate schema class name: {class_name} "
                            f"(already registered, skipping from {source.folder_name})"
                        )
                        continue

                    _schema_registry[class_name] = cls
                    logger.info('Registered schema: %s from %s/schema.py', class_name, source.folder_name)

                # Also map by agent folder for convenience
                # Use the first envelope class found as the "primary" schema
                if envelope_classes:
                    primary_class = list(envelope_classes.values())[0]
                    _schema_by_agent[source.folder_name] = primary_class

            except Exception as e:
                logger.error(
                    'Failed to load schemas from %s%s: %s',
                    source.folder_name,
                    f" in package {source.package_id}" if source.package_id else "",
                    e,
                )
                raise

        _initialized = True
        logger.info('Discovered %s schema envelope classes', len(_schema_registry))

        return _schema_registry


def get_agent_schema(class_name: str) -> Optional[Type[BaseModel]]:
    """
    Get a schema class by its class name.

    Args:
        class_name: The class name (e.g., "GeneValidationEnvelope")

    Returns:
        Pydantic model class or None if not found
    """
    if not _initialized:
        discover_agent_schemas()

    return _schema_registry.get(class_name)


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
        class_name: The class name (e.g., "GeneValidationEnvelope")

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
