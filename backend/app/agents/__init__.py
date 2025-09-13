"""
PydanticAI Agent System for AGR AI Curation

This module provides intelligent agents for biocuration tasks using PydanticAI.
Replaces the traditional chat interface with structured, type-safe agents.
"""

from .biocuration_agent import BioCurationAgent, BioCurationDependencies
from .models import (
    BioCurationOutput,
    EntityExtractionOutput,
    AnnotationSuggestion,
    CurationContext,
)
from .factory import AgentFactory

__all__ = [
    "BioCurationAgent",
    "BioCurationDependencies",
    "BioCurationOutput",
    "EntityExtractionOutput",
    "AnnotationSuggestion",
    "CurationContext",
    "AgentFactory",
]
