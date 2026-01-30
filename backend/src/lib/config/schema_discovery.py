"""
Schema Discovery for Config-Driven Architecture.

This module discovers and registers Pydantic schemas from agent folders.
Each agent folder may contain a schema.py file with envelope classes.

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
from pathlib import Path
from typing import Dict, Optional, Type, Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# Default path for agent configurations
DEFAULT_AGENTS_PATH = Path(__file__).parent.parent.parent.parent.parent / "config" / "agents"


# Module-level cache for discovered schemas
_schema_registry: Dict[str, Type[BaseModel]] = {}
_schema_by_agent: Dict[str, Type[BaseModel]] = {}  # agent_id -> envelope class
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


def _load_schema_module(schema_path: Path, folder_name: str) -> Dict[str, Type[BaseModel]]:
    """
    Dynamically load a schema.py file and extract envelope classes.

    Args:
        schema_path: Path to the schema.py file
        folder_name: Name of the agent folder (for module naming)

    Returns:
        Dictionary of class_name -> Pydantic model class
    """
    # Create a unique module name to avoid conflicts
    module_name = f"agent_schemas.{folder_name}"

    # Load the module
    spec = importlib.util.spec_from_file_location(module_name, schema_path)
    if spec is None or spec.loader is None:
        logger.warning(f"Could not load spec for {schema_path}")
        return {}

    module = importlib.util.module_from_spec(spec)

    # Add to sys.modules so imports within the module work
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error(f"Failed to execute module {schema_path}: {e}")
        # Clean up
        sys.modules.pop(module_name, None)
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
                logger.debug(f"Found envelope class: {name} in {folder_name}/schema.py")
            else:
                logger.debug(f"Skipping imported class: {name} (from {getattr(obj, '__module__', 'unknown')})")

    return envelope_classes


def discover_agent_schemas(
    agents_path: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, Type[BaseModel]]:
    """
    Discover and register all schema envelope classes from agent folders.

    Scans each agent folder for schema.py files and extracts envelope classes.
    Folders starting with underscore (_) are skipped.

    Args:
        agents_path: Path to agents directory (default: config/agents/)
        force_reload: Force reload even if already initialized

    Returns:
        Dictionary mapping class_name to Pydantic model class

    Raises:
        FileNotFoundError: If agents_path doesn't exist
    """
    global _schema_registry, _schema_by_agent, _initialized

    if _initialized and not force_reload:
        return _schema_registry

    if agents_path is None:
        agents_path = DEFAULT_AGENTS_PATH

    if not agents_path.exists():
        raise FileNotFoundError(f"Agents directory not found: {agents_path}")

    logger.info(f"Discovering agent schemas from: {agents_path}")

    _schema_registry = {}
    _schema_by_agent = {}

    # Scan for agent folders
    for folder in sorted(agents_path.iterdir()):
        # Skip non-directories and underscore-prefixed folders
        if not folder.is_dir() or folder.name.startswith("_"):
            continue

        schema_py = folder / "schema.py"
        if not schema_py.exists():
            logger.debug(f"No schema.py in {folder.name}")
            continue

        try:
            envelope_classes = _load_schema_module(schema_py, folder.name)

            for class_name, cls in envelope_classes.items():
                if class_name in _schema_registry:
                    logger.warning(
                        f"Duplicate schema class name: {class_name} "
                        f"(already registered, skipping from {folder.name})"
                    )
                    continue

                _schema_registry[class_name] = cls
                logger.info(f"Registered schema: {class_name} from {folder.name}/schema.py")

            # Also map by agent folder for convenience
            # Use the first envelope class found as the "primary" schema
            if envelope_classes:
                primary_class = list(envelope_classes.values())[0]
                _schema_by_agent[folder.name] = primary_class

        except Exception as e:
            logger.error(f"Failed to load schemas from {folder.name}: {e}")
            raise

    _initialized = True
    logger.info(f"Discovered {len(_schema_registry)} schema envelope classes")

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
    """Reset the schema cache (for testing)."""
    global _schema_registry, _schema_by_agent, _initialized
    _schema_registry = {}
    _schema_by_agent = {}
    _initialized = False
