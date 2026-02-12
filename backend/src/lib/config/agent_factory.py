"""
Agent Factory for Config-Driven Architecture.

This module provides convention-based agent factory discovery and creation.
Factory functions are discovered dynamically based on folder name conventions,
eliminating the need for hardcoded mappings.

Convention:
- Module: src.lib.openai_agents.agents.{folder_name}_agent
  Fallback: src.lib.openai_agents.{folder_name}_agent (for top-level agents)
- Factory: create_{folder_name}_agent

Usage:
    from src.lib.config.agent_factory import get_agent_factory, create_agent

    # Get factory function
    factory = get_agent_factory("gene")
    agent = factory(active_groups=["MGI"])

    # Or use create_agent directly
    agent = create_agent("gene", active_groups=["MGI"])
"""

import importlib
import logging
from typing import Any, Callable, Dict, Optional, List

from agents import Agent

from .agent_loader import get_agent_by_folder, get_agent_definition

logger = logging.getLogger(__name__)


# Cache for loaded factories
_factory_cache: Dict[str, Callable] = {}


def get_agent_factory(folder_name: str) -> Optional[Callable[..., Agent]]:
    """
    Get the factory function for an agent by its folder name.

    Uses convention-based discovery:
    1. Try src.lib.openai_agents.agents.{folder_name}_agent.create_{folder_name}_agent
    2. Fall back to src.lib.openai_agents.{folder_name}_agent.create_{folder_name}_agent

    Args:
        folder_name: The agent folder name (e.g., "gene", "pdf", "allele")

    Returns:
        Factory function or None if not found

    Example:
        >>> factory = get_agent_factory("gene")
        >>> agent = factory(active_groups=["MGI"])
    """
    # Check cache first
    if folder_name in _factory_cache:
        return _factory_cache[folder_name]

    factory_name = f"create_{folder_name}_agent"

    # Try agents subdirectory first (most common)
    module_paths = [
        f"src.lib.openai_agents.agents.{folder_name}_agent",
        f"src.lib.openai_agents.{folder_name}_agent",
    ]

    for module_path in module_paths:
        try:
            module = importlib.import_module(module_path)
            factory = getattr(module, factory_name, None)

            if factory is not None:
                _factory_cache[folder_name] = factory
                logger.debug(
                    f"[AgentFactory] Found factory for {folder_name}: "
                    f"{module_path}.{factory_name}"
                )
                return factory

        except ImportError:
            continue
        except Exception as e:
            logger.warning(
                '[AgentFactory] Error loading module %s: %s', module_path, e)
            continue

    logger.warning('[AgentFactory] No factory found for agent: %s', folder_name)
    return None


def get_factory_by_agent_id(agent_id: str) -> Optional[Callable[..., Agent]]:
    """
    Get the factory function for an agent by its agent_id.

    Looks up the agent definition to get the folder name, then uses
    convention-based discovery.

    Args:
        agent_id: The agent identifier (e.g., "gene_validation")

    Returns:
        Factory function or None if not found
    """
    agent_def = get_agent_definition(agent_id)
    if agent_def is None:
        logger.warning('[AgentFactory] Unknown agent_id: %s', agent_id)
        return None

    return get_agent_factory(agent_def.folder_name)


def create_agent(
    folder_name: str,
    **kwargs: Any,
) -> Optional[Agent]:
    """
    Create an agent instance using convention-based factory discovery.

    This is a convenience function that combines get_agent_factory() and
    calling the factory with kwargs.

    Args:
        folder_name: The agent folder name (e.g., "gene")
        **kwargs: Arguments to pass to the factory function

    Returns:
        Agent instance or None if factory not found

    Example:
        >>> agent = create_agent("gene", active_groups=["MGI"])
        >>> agent = create_agent("pdf", document_id="123", user_id="456")
    """
    factory = get_agent_factory(folder_name)
    if factory is None:
        return None

    try:
        return factory(**kwargs)
    except Exception as e:
        logger.error('[AgentFactory] Failed to create %s agent: %s', folder_name, e)
        return None


def create_agent_by_id(
    agent_id: str,
    **kwargs: Any,
) -> Optional[Agent]:
    """
    Create an agent instance by agent_id using convention-based discovery.

    Args:
        agent_id: The agent identifier (e.g., "gene_validation")
        **kwargs: Arguments to pass to the factory function

    Returns:
        Agent instance or None if not found
    """
    factory = get_factory_by_agent_id(agent_id)
    if factory is None:
        return None

    try:
        return factory(**kwargs)
    except Exception as e:
        logger.error('[AgentFactory] Failed to create agent %s: %s', agent_id, e)
        return None


def list_available_factories() -> List[str]:
    """
    List all agent folder names that have discoverable factories.

    Scans the agents directories to find modules matching the convention.

    Returns:
        List of folder names with available factories
    """
    from pathlib import Path

    available = []

    # Check agents subdirectory
    agents_dir = Path(__file__).parent.parent / "openai_agents" / "agents"
    if agents_dir.exists():
        for path in agents_dir.glob("*_agent.py"):
            folder_name = path.stem.replace("_agent", "")
            if folder_name and folder_name != "supervisor":
                factory = get_agent_factory(folder_name)
                if factory:
                    available.append(folder_name)

    # Check top-level openai_agents directory
    openai_agents_dir = Path(__file__).parent.parent / "openai_agents"
    if openai_agents_dir.exists():
        for path in openai_agents_dir.glob("*_agent.py"):
            folder_name = path.stem.replace("_agent", "")
            if folder_name not in available:
                factory = get_agent_factory(folder_name)
                if factory:
                    available.append(folder_name)

    return sorted(available)


def clear_factory_cache() -> None:
    """Clear the factory cache (for testing)."""
    global _factory_cache
    _factory_cache = {}
