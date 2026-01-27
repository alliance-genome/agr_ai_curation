"""
Pytest configuration and fixtures for backend tests.
"""
import sys
from pathlib import Path


# Add the project-level scripts directory to Python path
# This allows tests to import from scripts like: from scripts.validate_current_agents import ...
# The scripts directory is mounted at /app/scripts in the Docker container
scripts_path = Path("/app/scripts")
if scripts_path.exists() and str(scripts_path) not in sys.path:
    sys.path.insert(0, str(scripts_path.parent))  # Add /app to path
