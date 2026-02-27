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
    # Save original values
    original_dev_mode = os.environ.get("DEV_MODE")
    original_testing_api_key = os.environ.get("TESTING_API_KEY")

    # Disable DEV_MODE for contract tests
    os.environ["DEV_MODE"] = "false"
    # Provide deterministic API-key auth path for tests that send explicit auth headers.
    os.environ["TESTING_API_KEY"] = "contract-test-key"

    yield

    # Restore original values after all tests
    if original_dev_mode is not None:
        os.environ["DEV_MODE"] = original_dev_mode
    else:
        del os.environ["DEV_MODE"]

    if original_testing_api_key is not None:
        os.environ["TESTING_API_KEY"] = original_testing_api_key
    else:
        del os.environ["TESTING_API_KEY"]
