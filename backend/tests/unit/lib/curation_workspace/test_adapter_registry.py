"""Unit tests for package-driven curation adapter resolution."""

from pathlib import Path

import src.lib.curation_workspace.adapter_registry as adapter_registry_module


def _write_package_with_generic_domain_pack(packages_dir: Path) -> None:
    package_dir = packages_dir / "demo-core"
    module_dir = package_dir / "python" / "src" / "demo_core"
    module_dir.mkdir(parents=True)
    (package_dir / "package.yaml").write_text(
        """package_id: demo.core
display_name: Demo Core
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/demo_core
requirements_file: requirements/runtime.txt
exports:
  - kind: curation_adapter
    name: default
    path: python/src/demo_core/curation_adapters.py
    description: Demo curation adapters
""",
        encoding="utf-8",
    )
    (module_dir / "curation_adapters.py").write_text(
        """from types import SimpleNamespace


class DemoNormalizer:
    pass


def register_curation_adapters(registry) -> None:
    registry.register_adapter(
        adapter_key="demo",
        candidate_normalizer=DemoNormalizer(),
        domain_pack=SimpleNamespace(pack_id="generic", package_id="demo.core"),
    )
""",
        encoding="utf-8",
    )


def test_resolve_generic_domain_pack_uses_registered_package_export(
    tmp_path,
    monkeypatch,
):
    packages_dir = tmp_path / "packages"
    _write_package_with_generic_domain_pack(packages_dir)

    monkeypatch.setenv("AGR_DOMAIN_PACKS_DIR", str(tmp_path / "domain-packs"))
    monkeypatch.setattr(
        adapter_registry_module,
        "_default_packages_dir",
        lambda: packages_dir,
    )
    adapter_registry_module.load_curation_adapter_registry.cache_clear()

    try:
        domain_pack = adapter_registry_module.resolve_curation_domain_pack_by_id(
            "generic"
        )
    finally:
        adapter_registry_module.load_curation_adapter_registry.cache_clear()

    assert domain_pack is not None
    assert domain_pack.pack_id == "generic"
    assert domain_pack.package_id == "demo.core"
