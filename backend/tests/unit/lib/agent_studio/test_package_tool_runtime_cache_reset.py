"""Clearing the package tool runtime caches also clears every cache derived from them.

The tool-role maps (``src.lib.packages.tool_roles``) are cached from the package
tool registry. ``catalog_service.clear_package_tool_runtime_caches`` used to leave
them in place, so a process that switched package directories (tests that start
a core-only runtime) kept the old roles: later, the installed fleet's finalize
tools were not recognised.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.lib.agent_studio import catalog_service
from src.lib.config import tool_loading_loader
from src.lib.packages import tool_roles
from src.lib.packages.tool_registry import load_tool_registry

REPO_ROOT = Path(__file__).resolve().parents[5]


def _finalize_tools_from_a_fresh_registry() -> frozenset[str]:
    return frozenset(
        binding.tool_id
        for binding in load_tool_registry().bindings
        if isinstance(binding.metadata, dict)
        and binding.metadata.get(tool_roles.BUILDER_FINALIZATION_METADATA_KEY) is True
    )


@pytest.fixture(autouse=True)
def _clean_caches():
    catalog_service.clear_package_tool_runtime_caches()
    yield
    catalog_service.clear_package_tool_runtime_caches()


def test_tool_roles_follow_the_packages_after_the_runtime_caches_are_cleared(tmp_path):
    installed = _finalize_tools_from_a_fresh_registry()
    assert installed, "the installed packages declare builder finalize tools"

    with pytest.MonkeyPatch.context() as core_only:
        runtime_root = tmp_path / "runtime"
        packages_dir = runtime_root / "packages"
        shutil.copytree(REPO_ROOT / "packages" / "core", packages_dir / "agr.core")
        core_only.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
        core_only.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
        catalog_service.clear_package_tool_runtime_caches()
        core_only_finalizers = tool_roles.builder_finalization_tool_names()
        assert core_only_finalizers != installed

    catalog_service.clear_package_tool_runtime_caches()

    assert tool_roles.builder_finalization_tool_names() == installed
    assert {
        tool_id for tool_id, metadata in tool_roles.tool_metadata_by_name().items()
        if metadata.get(tool_roles.BUILDER_FINALIZATION_METADATA_KEY) is True
    } == installed


def test_every_cache_in_tool_roles_is_cleared_by_its_reset():
    """A cache added to the module later cannot be left out of the reset."""

    cached = [
        value for value in vars(tool_roles).values()
        if callable(getattr(value, "cache_clear", None)) and getattr(value, "__module__", None) == tool_roles.__name__
    ]
    assert cached
    for function in cached:
        function()
        assert function.cache_info().currsize > 0, function.__name__

    catalog_service.clear_package_tool_runtime_caches()

    assert {function.__name__: function.cache_info().currsize for function in cached} == {
        function.__name__: 0 for function in cached
    }


def test_the_package_tool_loading_cache_is_cleared_too():
    tool_loading_loader.get_tool_namespaces()
    tool_loading_loader.get_tool_loading_policies()

    catalog_service.clear_package_tool_runtime_caches()

    assert tool_loading_loader._namespaces_cache is None
    assert tool_loading_loader._policies_cache is None
