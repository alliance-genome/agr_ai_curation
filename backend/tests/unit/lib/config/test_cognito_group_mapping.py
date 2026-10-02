"""Real Cognito group names must map to the right MOD group in every shipped groups.yaml."""

from pathlib import Path

import pytest
import yaml

from src.lib.config.groups_loader import (
    get_group_for_provider_group,
    get_groups_for_provider_groups,
    load_groups,
    reset_cache,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
GROUPS_FILES = [
    REPO_ROOT / "config/groups.yaml",
    REPO_ROOT / "alliance_config/groups.yaml",
]

COGNITO_MOD_GROUPS = {
    "FlyBaseCurator": "FB",
    "FBStaff": "FB",
    "FlyBaseDeveloper": "FB",
    "WormBaseCurator": "WB",
    "WBStaff": "WB",
    "WormBaseDeveloper": "WB",
    "MGICurator": "MGI",
    "MGIStaff": "MGI",
    "MGIDeveloper": "MGI",
    "ZFINCurator": "ZFIN",
    "ZFINStaff": "ZFIN",
    "ZFINDeveloper": "ZFIN",
    "zfin-curators": "ZFIN",
    "RGDCurator": "RGD",
    "RGDStaff": "RGD",
    "RGDDeveloper": "RGD",
    "SGDCurator": "SGD",
    "SGDStaff": "SGD",
    "SGDDeveloper": "SGD",
    "XenbaseCurator": "XB",
    "XBStaff": "XB",
}

COGNITO_NON_MOD_GROUPS = [
    "FlyBaseObserver",
    "GOStaff",
    "POTester",
    "Tester",
    "Developers",
    "admins",
    "SuperAdmin",
    "benchmark-dev-admin",
    "benchmark-dev-curator",
    "benchmark-prod-admin",
    "benchmark-prod-curator",
]


@pytest.fixture(params=GROUPS_FILES, ids=lambda path: path.parent.name)
def groups_file(request):
    reset_cache()
    load_groups(request.param, force_reload=True)
    yield request.param
    reset_cache()


@pytest.mark.parametrize("provider_group, group_id", sorted(COGNITO_MOD_GROUPS.items()))
def test_real_cognito_mod_group_maps_to_group(groups_file, provider_group, group_id):
    assert get_group_for_provider_group(provider_group) == group_id


@pytest.mark.parametrize("provider_group", COGNITO_NON_MOD_GROUPS)
def test_non_mod_cognito_group_maps_to_nothing(groups_file, provider_group):
    assert get_group_for_provider_group(provider_group) is None


@pytest.mark.parametrize(
    "provider_group", ["flybasecurator", "FLYBASECURATOR", "xbstaff", " FBStaff"]
)
def test_provider_group_matching_is_exact(groups_file, provider_group):
    assert get_group_for_provider_group(provider_group) is None


def test_mixed_membership_resolves_only_mod_groups(groups_file):
    assert get_groups_for_provider_groups(
        ["Developers", "ZFINCurator", "SuperAdmin", "XBStaff", "benchmark-prod-curator"]
    ) == ["ZFIN", "XB"]


def test_shipped_groups_files_define_the_same_groups():
    contents = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in GROUPS_FILES]
    assert contents[0] == contents[1]
