"""Chunking strategy configurations for different document types."""

from typing import Dict, Any, List, Optional

# Research-only chunking strategy configuration
CHUNKING_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "research": {
        "method": "by_title",
        "max_chars": 2200,
        "overlap": 440,  # 20% overlap (440/2200) - improved from 16% for better context preservation
        "exclude_types": ["Footer", "Header"],
        "description": "Optimized for research papers - chunks by section titles",
        "use_cases": [
            "Scientific papers",
            "Academic articles",
            "Research reports",
            "Technical documentation with clear sections"
        ]
    }
}

# Default strategy - always research
DEFAULT_STRATEGY = "research"


def get_strategy(strategy_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Get a chunking strategy configuration by name.

    Args:
        strategy_name: Name of the strategy. If None, returns default strategy.

    Returns:
        Strategy configuration dictionary

    Raises:
        ValueError: If strategy name is not found
    """
    if strategy_name is None:
        strategy_name = DEFAULT_STRATEGY

    if strategy_name not in CHUNKING_STRATEGIES:
        available = ", ".join(CHUNKING_STRATEGIES.keys())
        raise ValueError(
            f"Unknown strategy: {strategy_name}. "
            f"Available strategies: {available}"
        )

    return CHUNKING_STRATEGIES[strategy_name].copy()


def list_strategies() -> List[Dict[str, Any]]:
    """
    List all available chunking strategies.

    Returns:
        List of strategy information dictionaries
    """
    strategies = []
    for name, config in CHUNKING_STRATEGIES.items():
        strategies.append({
            "name": name,
            "method": config["method"],
            "max_characters": config.get("max_characters", config.get("max_chars", 2200)),
            "overlap_characters": config["overlap_characters"],
            "description": config["description"],
            "is_default": name == DEFAULT_STRATEGY
        })
    return strategies


def validate_strategy_config(config: Dict[str, Any]) -> bool:
    """
    Validate a strategy configuration.

    Args:
        config: Strategy configuration to validate

    Returns:
        True if valid

    Raises:
        ValueError: If configuration is invalid
    """
    required_fields = ["method", "max_characters", "overlap_characters"]

    # Check required fields
    for field in required_fields:
        if field not in config:
            raise ValueError(f"Missing required field: {field}")

    # Validate method
    valid_methods = ["by_title", "by_paragraph", "by_character"]
    if config["method"] not in valid_methods:
        raise ValueError(
            f"Invalid method: {config['method']}. "
            f"Must be one of: {valid_methods}"
        )

    # Validate character limits
    max_chars = config["max_characters"]
    overlap = config["overlap_characters"]

    if not isinstance(max_chars, int) or max_chars < 100:
        raise ValueError("max_characters must be an integer >= 100")

    if not isinstance(overlap, int) or overlap < 0:
        raise ValueError("overlap_characters must be a non-negative integer")

    if overlap >= max_chars:
        raise ValueError("overlap_characters must be less than max_characters")

    # Validate exclude_element_types if present
    if "exclude_element_types" in config:
        if not isinstance(config["exclude_element_types"], list):
            raise ValueError("exclude_element_types must be a list")

        valid_element_types = [
            "Title", "NarrativeText", "ListItem", "Table", "Image",
            "Header", "Footer", "PageBreak"
        ]
        for element_type in config["exclude_element_types"]:
            if element_type not in valid_element_types:
                raise ValueError(f"Invalid element type: {element_type}")

    return True


def create_custom_strategy(
    method: str,
    max_characters: int,
    overlap_characters: int,
    exclude_element_types: Optional[List[str]] = None,
    description: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a custom chunking strategy configuration.

    Args:
        method: Chunking method ("by_title", "by_paragraph", or "by_character")
        max_characters: Maximum characters per chunk
        overlap_characters: Character overlap between chunks
        exclude_element_types: Optional list of element types to exclude
        description: Optional description of the strategy

    Returns:
        Custom strategy configuration

    Raises:
        ValueError: If parameters are invalid
    """
    config = {
        "method": method,
        "max_characters": max_characters,
        "overlap_characters": overlap_characters,
        "exclude_element_types": exclude_element_types or ["Header", "Footer", "PageBreak"],
        "description": description or "Custom chunking strategy"
    }

    # Validate the configuration
    validate_strategy_config(config)

    return config


def recommend_strategy(_document_type: str = "research") -> str:
    """
    Recommend a chunking strategy based on document type.

    Always returns 'research' since we only process research documents.

    Args:
        _document_type: Type of document (unused - always research)

    Returns:
        Always returns "research"
    """
    # Always return research strategy
    return "research"