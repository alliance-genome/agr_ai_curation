"""
Schema Loader for Structured LLM Outputs

This module provides utilities to load schemas from Pydantic models.
Schemas are generated on-the-fly from Python models using explicit registration.
"""

import json
from typing import Dict, Any, Optional, Type
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


def _get_schema_models() -> Dict[str, Type[BaseModel]]:
    """
    Get explicit registry of schema models.

    This uses explicit registration instead of auto-discovery to:
    - Avoid accidentally registering helper classes (StructuredMessageEnvelope, etc.)
    - Make it obvious when new schemas need to be registered
    - Keep the registry deterministic and maintainable
    """
    from src.schemas.models import SCHEMA_REGISTRY

    logger.info(
        "Loaded %d schemas: %s",
        len(SCHEMA_REGISTRY),
        sorted(SCHEMA_REGISTRY.keys())
    )
    return SCHEMA_REGISTRY


# Load schemas from explicit registry at module import time
SCHEMA_REGISTRY = _get_schema_models()


class SchemaLoader:
    """Load and manage schemas for structured output.

    Schemas are generated on-the-fly from Pydantic models.
    The explicit SCHEMA_REGISTRY ensures all schemas are properly registered.
    """

    def __init__(self):
        """Initialize the schema loader."""
        self._schema_cache: Dict[str, Dict[str, Any]] = {}

    def load_schema(self, schema_name: str) -> Dict[str, Any]:
        """
        Load a schema by name, generating JSON schema from Pydantic model.

        Args:
            schema_name: Name of the schema (e.g., 'supervisor', 'pdf_extraction_plan')

        Returns:
            JSON schema dictionary

        Raises:
            ValueError: If schema_name is not found in SCHEMA_REGISTRY
        """
        # Check cache first
        if schema_name in self._schema_cache:
            return self._schema_cache[schema_name]

        # Get model class from explicit registry
        model_class = SCHEMA_REGISTRY.get(schema_name)
        if not model_class:
            raise ValueError(
                f"Unknown schema: {schema_name}. "
                f"Available schemas: {sorted(SCHEMA_REGISTRY.keys())}"
            )

        # Generate JSON schema from model
        schema = model_class.model_json_schema()
        logger.info('Generated schema for: %s', schema_name)

        # Cache it
        self._schema_cache[schema_name] = schema
        return schema

    def get_model_class(self, schema_name: str) -> Optional[Type[BaseModel]]:
        """
        Get the Pydantic model class for a schema.

        Args:
            schema_name: Name of the schema

        Returns:
            Pydantic model class or None if not found
        """
        return SCHEMA_REGISTRY.get(schema_name)

    def list_available_schemas(self) -> Dict[str, str]:
        """
        List all available schemas with descriptions.

        Returns:
            Dictionary of schema name to description
        """
        descriptions = {}
        for schema_name, model_class in SCHEMA_REGISTRY.items():
            # Extract description from class docstring or generate a default
            doc = model_class.__doc__ or ""
            first_line = doc.strip().split('\n')[0] if doc else ""
            descriptions[schema_name] = first_line or f"Schema for {schema_name}"

        return descriptions

    def get_schema_instruction(self, schema_name: str) -> str:
        """
        Get the instruction to append to tasks for schema conformance.

        Args:
            schema_name: Name of the schema

        Returns:
            Instruction string with the JSON schema
        """
        schema = self.load_schema(schema_name)
        if not schema:
            return ""

        return f"\n\nYou MUST respond with JSON that conforms to this schema:\n```json\n{json.dumps(schema, indent=2)}\n```"


# Global instance for convenience
default_loader = SchemaLoader()
