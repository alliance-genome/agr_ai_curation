"""Identity provider metadata tests for groups_loader."""

from pathlib import Path

from src.lib.config.groups_loader import (
    get_group_claim_key,
    get_identity_provider_type,
    get_groups_for_provider_groups,
    load_groups,
    reset_cache,
)


CONFIG_PATH = Path(__file__).resolve().parents[5] / "config"


def test_get_group_claim_key_for_oidc_groups_yaml(tmp_path):
    groups_yaml = tmp_path / "groups.yaml"
    groups_yaml.write_text(
        "identity_provider:\n"
        "  type: oidc\n"
        "  group_claim: realm_access.roles\n"
        "groups:\n"
        "  FB:\n"
        "    name: FlyBase\n"
        "    provider_groups:\n"
        "      - flybase-curators\n",
        encoding="utf-8",
    )

    reset_cache()
    try:
        load_groups(groups_yaml, force_reload=True)
        assert get_identity_provider_type() == "oidc"
        assert get_group_claim_key() == "realm_access.roles"
    finally:
        reset_cache()


def test_get_group_claim_key_for_dev_groups_yaml(tmp_path):
    groups_yaml = tmp_path / "groups.yaml"
    groups_yaml.write_text(
        "identity_provider:\n"
        "  type: dev\n"
        "  group_claim: groups\n"
        "groups:\n"
        "  TEST:\n"
        "    name: Test Group\n"
        "    provider_groups:\n"
        "      - test-curators\n",
        encoding="utf-8",
    )

    reset_cache()
    try:
        load_groups(groups_yaml, force_reload=True)
        assert get_identity_provider_type() == "dev"
        assert get_group_claim_key() == "groups"
    finally:
        reset_cache()


def test_real_mgi_cognito_provider_groups_map_to_internal_mgi_group():
    reset_cache()
    try:
        load_groups(CONFIG_PATH / "groups.yaml", force_reload=True)

        assert get_groups_for_provider_groups(["MGIStaff"]) == ["MGI"]
        assert get_groups_for_provider_groups(["MGICurator"]) == ["MGI"]
        assert get_groups_for_provider_groups(
            ["MGIStaff", "Tester", "MGICurator"]
        ) == ["MGI"]
    finally:
        reset_cache()
