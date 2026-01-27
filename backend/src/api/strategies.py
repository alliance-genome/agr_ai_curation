"""Chunking strategies API endpoints."""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
import logging

from ..lib.pdf_processing.strategies import CHUNKING_STRATEGIES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")


@router.get("/chunking-strategies")
async def get_chunking_strategies_endpoint() -> Dict[str, Any]:
    """
    Get all available chunking strategies.

    Returns the configured strategies with their parameters and
    identifies the default strategy.
    """
    try:
        strategies_list = []

        for name, config in CHUNKING_STRATEGIES.items():
            strategy_info = {
                "name": name,
                "method": config["method"],
                "max_characters": config["max_chars"],
                "overlap": config["overlap"],
                "exclude_types": config.get("exclude_types", []),
                "is_default": name == "research",
                "description": _get_strategy_description(name)
            }
            strategies_list.append(strategy_info)

        return {
            "strategies": strategies_list,
            "default": "research",
            "total": len(strategies_list)
        }

    except Exception as e:
        logger.error(f"Error retrieving chunking strategies: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve chunking strategies: {str(e)}"
        )


def _get_strategy_description(strategy_name: str) -> str:
    """Get human-readable description for the strategy."""
    if strategy_name == "research":
        return "Optimized for academic and research papers - chunks by title sections"
    return "Research chunking strategy"