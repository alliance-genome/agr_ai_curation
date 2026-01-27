"""Tests for create_agent CLI validation."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts"))

from create_agent import (
    check_agent_exists,
    check_tools_exist,
)


def test_check_agent_exists_returns_false_for_new():
    """New agent ID should return False."""
    assert check_agent_exists("totally_new_agent_12345") is False


def test_check_agent_exists_returns_true_for_existing():
    """Existing agent ID should return True."""
    assert check_agent_exists("gene") is True


def test_check_tools_exist_returns_errors_for_invalid():
    """Invalid tool names should return error list."""
    errors = check_tools_exist(["fake_tool_xyz"])
    assert len(errors) > 0


def test_check_tools_exist_returns_empty_for_valid():
    """Valid tool names should return empty list."""
    errors = check_tools_exist(["agr_curation_query"])
    assert len(errors) == 0
