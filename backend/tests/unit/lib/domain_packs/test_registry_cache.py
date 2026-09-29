"""Domain-pack parsing is cached per resolved packs directory and uses LibYAML."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from src.lib.domain_packs import loader as loader_module
from src.lib.domain_packs.loader import DomainPackContractError, _load_yaml_mapping
from src.lib.domain_packs.registry import (
    clear_domain_pack_registry_cache,
    load_domain_pack_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
PACKAGES_ROOT = REPO_ROOT / "packages"
ALLIANCE_PYTHON_SRC = PACKAGES_ROOT / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

SHIPPED_PACK_YAML_FILES = sorted(PACKAGES_ROOT.glob("*/domain_packs/**/*.yaml"))

pytestmark = pytest.mark.provider_agnostic_domain_pack


@pytest.fixture
def yaml_load_counter(monkeypatch):
    calls: list[Path] = []
    original = loader_module._load_yaml_mapping

    def _counting_load(path: Path) -> dict:
        calls.append(Path(path))
        return original(path)

    monkeypatch.setattr(loader_module, "_load_yaml_mapping", _counting_load)
    clear_domain_pack_registry_cache()
    yield calls
    clear_domain_pack_registry_cache()


def _write_pack(root: Path, pack_id: str, display_name: str = "Cached pack") -> Path:
    pack_dir = root / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "domain_pack.yaml").write_text(
        "\n".join(
            [
                f"pack_id: {pack_id}",
                f"display_name: {display_name}",
                "version: 0.1.0",
                "metadata_api_version: 1.0.0",
                "status: active",
            ]
        ),
        encoding="utf-8",
    )
    return pack_dir


def test_shipped_pack_files_exist():
    assert len(SHIPPED_PACK_YAML_FILES) >= 10


@pytest.mark.parametrize(
    "path",
    SHIPPED_PACK_YAML_FILES,
    ids=[str(path.relative_to(PACKAGES_ROOT)) for path in SHIPPED_PACK_YAML_FILES],
)
def test_libyaml_loader_matches_pure_python_safe_loader_for_shipped_pack(path: Path):
    text = path.read_text(encoding="utf-8")
    assert _load_yaml_mapping(path) == yaml.load(text, Loader=yaml.SafeLoader)


@pytest.mark.parametrize(
    "source",
    [
        "value: !!python/object/apply:builtins.str [unsafe]",
        "value: [unfinished",
    ],
)
def test_pack_yaml_loader_rejects_unsafe_tags_and_malformed_yaml(tmp_path, source):
    path = tmp_path / "domain_pack.yaml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(DomainPackContractError, match="Invalid YAML"):
        _load_yaml_mapping(path)


def test_registry_parses_each_pack_directory_once(tmp_path, yaml_load_counter):
    packs_dir = tmp_path / "domain_packs"
    _write_pack(packs_dir, "pack.alpha")
    _write_pack(packs_dir, "pack.beta")

    first = load_domain_pack_registry(packs_dir)
    second = load_domain_pack_registry(packs_dir)
    labelled = load_domain_pack_registry(
        packs_dir,
        package_id="example",
        package_display_name="Example package",
        package_version="1.0.0",
    )

    assert len(yaml_load_counter) == 2
    assert [pack.pack_id for pack in second.loaded_packs] == ["pack.alpha", "pack.beta"]
    assert first.get_pack("pack.alpha").metadata is second.get_pack("pack.alpha").metadata
    assert first.get_pack("pack.alpha").package_id is None
    assert labelled.get_pack("pack.alpha").package_id == "example"
    assert labelled.get_pack("pack.alpha").package_version == "1.0.0"


def test_shared_cached_pack_metadata_rejects_attribute_assignment(tmp_path, yaml_load_counter):
    from pydantic import ValidationError

    packs_dir = tmp_path / "domain_packs"
    _write_pack(packs_dir, "pack.alpha")
    metadata = load_domain_pack_registry(packs_dir).get_pack("pack.alpha").metadata

    with pytest.raises(ValidationError, match="frozen"):
        metadata.display_name = "Changed by a caller"
    assert load_domain_pack_registry(packs_dir).get_pack("pack.alpha").display_name == "Cached pack"


def test_registry_cache_is_keyed_by_resolved_directory(tmp_path, yaml_load_counter):
    packs_dir = tmp_path / "domain_packs"
    _write_pack(packs_dir, "pack.alpha")
    (tmp_path / "nested").mkdir()

    load_domain_pack_registry(packs_dir)
    load_domain_pack_registry(tmp_path / "nested" / ".." / "domain_packs")

    assert len(yaml_load_counter) == 1


def test_clear_domain_pack_registry_cache_rereads_pack_files(tmp_path, yaml_load_counter):
    packs_dir = tmp_path / "domain_packs"
    _write_pack(packs_dir, "pack.alpha", display_name="Before")
    assert load_domain_pack_registry(packs_dir).get_pack("pack.alpha").display_name == "Before"

    _write_pack(packs_dir, "pack.alpha", display_name="After")
    assert load_domain_pack_registry(packs_dir).get_pack("pack.alpha").display_name == "Before"

    clear_domain_pack_registry_cache()
    assert load_domain_pack_registry(packs_dir).get_pack("pack.alpha").display_name == "After"


def test_invalid_pack_still_fails_loudly_on_every_cached_call(tmp_path, yaml_load_counter):
    from src.lib.domain_packs.registry import DomainPackRegistryValidationError

    packs_dir = tmp_path / "domain_packs"
    broken_dir = packs_dir / "broken"
    broken_dir.mkdir(parents=True)
    (broken_dir / "domain_pack.yaml").write_text("pack_id: [unfinished", encoding="utf-8")

    for _ in range(2):
        with pytest.raises(DomainPackRegistryValidationError, match="broken"):
            load_domain_pack_registry(packs_dir)
    registry = load_domain_pack_registry(packs_dir, fail_on_validation_error=False)

    assert [failure.pack_id for failure in registry.failed_packs] == ["broken"]
    assert len(yaml_load_counter) == 1


def test_alliance_pack_registry_is_parsed_once_across_repeated_lookups(yaml_load_counter):
    from agr_ai_curation_alliance.domain_packs import (
        get_alliance_domain_pack,
        load_alliance_domain_pack_registry,
        load_alliance_domain_packs,
    )
    from agr_ai_curation_alliance.domain_packs.paths import get_alliance_domain_packs_dir

    pack_count = sum(
        1 for path in get_alliance_domain_packs_dir().iterdir() if (path / "domain_pack.yaml").is_file()
    )
    assert pack_count >= 5

    for _ in range(7):
        get_alliance_domain_pack()
    load_alliance_domain_pack_registry()
    load_alliance_domain_packs()

    parsed_metadata_files = [path for path in yaml_load_counter if path.name == "domain_pack.yaml"]
    assert len(parsed_metadata_files) == pack_count
    assert len(set(parsed_metadata_files)) == pack_count
