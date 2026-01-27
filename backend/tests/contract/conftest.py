"""Shared configuration for contract tests.

Contract tests validate API endpoint behavior against specifications.
These tests require proper authentication enforcement (no DEV_MODE bypass).
"""

import pytest
import os


@pytest.fixture(scope="session", autouse=True)
def disable_dev_mode():
    """Disable DEV_MODE for all contract tests.

    Contract tests validate authentication requirements. DEV_MODE bypasses
    authentication, which would make all auth tests pass incorrectly.

    This fixture runs once before any contract test and sets DEV_MODE=false.
    """
    # Save original value
    original_dev_mode = os.environ.get("DEV_MODE")

    # Disable DEV_MODE for contract tests
    os.environ["DEV_MODE"] = "false"

    yield

    # Restore original value after all tests
    if original_dev_mode is not None:
        os.environ["DEV_MODE"] = original_dev_mode
    else:
        del os.environ["DEV_MODE"]
