"""Contract tests for schema loader validation.

This test ensures that all schema_name references in domain orchestrators
have corresponding Python models in the SCHEMA_REGISTRY to prevent runtime errors.

NOTE: After schema refactoring, schemas are now Python Pydantic models in
src/schemas/models/, not JSON files. This test has been updated accordingly.
"""

import pytest
import re
from pathlib import Path
from typing import Set


class TestSchemaLoaderValidation:
    """Validate that schema models exist for all orchestrator schema_name references."""

    @pytest.fixture
    def orchestrator_files(self):
        """Get all domain orchestrator Python files."""
        orchestrator_dir = Path(__file__).parent.parent.parent / "src" / "lib" / "chat" / "flows" / "domain_orchestrators"
        return list(orchestrator_dir.glob("*_orchestrator.py"))

    @pytest.fixture
    def schema_models_dir(self):
        """Get the schema models directory path."""
        return Path(__file__).parent.parent.parent / "src" / "schemas" / "models"

    def extract_schema_names(self, file_path: Path) -> Set[str]:
        """
        Extract schema_name values from orchestrator files.

        Args:
            file_path: Path to orchestrator Python file

        Returns:
            Set of schema names referenced in the file
        """
        schema_names = set()
        with open(file_path, 'r') as f:
            content = f.read()

        # Find all schema_name="..." patterns
        pattern = r'schema_name\s*=\s*["\']([^"\']+)["\']'
        matches = re.findall(pattern, content)
        schema_names.update(matches)

        return schema_names

    def test_all_orchestrator_schema_names_have_models(self, orchestrator_files):
        """
        Test that every schema_name reference in orchestrators has a corresponding model in SCHEMA_REGISTRY.

        This prevents the issue where missing schemas caused runtime validation errors.
        """
        # Import SCHEMA_REGISTRY
        import sys
        import os
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from src.schemas.models import SCHEMA_REGISTRY

        missing_schemas = []

        for orchestrator_file in orchestrator_files:
            schema_names = self.extract_schema_names(orchestrator_file)

            for schema_name in schema_names:
                if schema_name not in SCHEMA_REGISTRY:
                    missing_schemas.append({
                        "orchestrator": orchestrator_file.name,
                        "schema_name": schema_name,
                        "available_schemas": sorted(SCHEMA_REGISTRY.keys())
                    })

        assert not missing_schemas, (
            f"Missing schema models for orchestrator references:\n"
            + "\n".join(
                f"  - {item['orchestrator']}: schema_name='{item['schema_name']}' "
                f"not found in SCHEMA_REGISTRY\n    Available: {', '.join(item['available_schemas'])}"
                for item in missing_schemas
            )
        )

    def test_schema_models_are_valid_pydantic(self):
        """Test that all models in SCHEMA_REGISTRY are valid Pydantic models."""
        import sys
        import os
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from src.schemas.models import SCHEMA_REGISTRY
        from pydantic import BaseModel

        invalid_models = []

        for schema_name, model_class in SCHEMA_REGISTRY.items():
            # Check if it's a Pydantic model
            if not issubclass(model_class, BaseModel):
                invalid_models.append({
                    "schema_name": schema_name,
                    "issue": f"Not a Pydantic BaseModel subclass: {type(model_class)}"
                })
                continue

            # Try to generate JSON schema
            try:
                schema = model_class.model_json_schema()
                # Verify it has basic fields
                if "type" not in schema or "properties" not in schema:
                    invalid_models.append({
                        "schema_name": schema_name,
                        "issue": "Generated schema missing 'type' or 'properties'"
                    })
            except Exception as e:
                invalid_models.append({
                    "schema_name": schema_name,
                    "issue": f"Failed to generate JSON schema: {e}"
                })

        assert not invalid_models, (
            f"Invalid schema models in SCHEMA_REGISTRY:\n"
            + "\n".join(
                f"  - {item['schema_name']}: {item['issue']}"
                for item in invalid_models
            )
        )

    def test_schema_models_have_docstrings(self):
        """Test that schema models have descriptive docstrings."""
        import sys
        import os
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from src.schemas.models import SCHEMA_REGISTRY

        missing_docs = []

        for schema_name, model_class in SCHEMA_REGISTRY.items():
            doc = model_class.__doc__
            if not doc or not doc.strip():
                missing_docs.append(schema_name)

        # This is a warning, not a hard failure
        if missing_docs:
            import warnings
            warnings.warn(
                f"Schema models without docstrings: {', '.join(missing_docs)}",
                UserWarning
            )

    def test_schema_loader_fails_fast_on_missing_schemas(self):
        """
        Test that schema loader raises ValueError when schema is not in SCHEMA_REGISTRY.

        This validates that we fail fast on missing schemas - no fallbacks.
        """
        import sys
        import os
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from src.lib.models.schema_loader import SchemaLoader

        loader = SchemaLoader()

        # Try to load a non-existent schema
        with pytest.raises(ValueError) as exc_info:
            loader.load_schema("nonexistent_schema_that_does_not_exist")

        # Verify the error message is helpful
        error_msg = str(exc_info.value)
        assert "Unknown schema" in error_msg or "not found" in error_msg.lower()
        assert "nonexistent_schema_that_does_not_exist" in error_msg
        assert "Available schemas" in error_msg

    def test_all_expected_orchestrator_schemas_exist(self):
        """
        Test that the three known orchestrator schemas exist in SCHEMA_REGISTRY.

        This is a hardcoded check for the specific schemas we know should exist.
        """
        import sys
        import os
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from src.schemas.models import SCHEMA_REGISTRY

        expected_schemas = [
            "pdf_extraction_plan",
            "database_query_plan",
            "external_api_plan"
        ]

        missing = []
        for schema_name in expected_schemas:
            if schema_name not in SCHEMA_REGISTRY:
                missing.append(schema_name)

        assert not missing, (
            f"Expected orchestrator schema models are missing from SCHEMA_REGISTRY: {', '.join(missing)}\n"
            f"These schemas are required by domain orchestrators.\n"
            f"Available schemas: {sorted(SCHEMA_REGISTRY.keys())}"
        )
