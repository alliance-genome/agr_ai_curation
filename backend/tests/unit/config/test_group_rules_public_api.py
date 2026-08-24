"""Public API checks for the group-rules package."""

import config.group_rules as group_rules
import src.lib.group_rules as canonical_group_rules


def test_group_rules_exports_only_provider_neutral_mapper():
    assert callable(group_rules.get_groups_from_provider_groups)
    assert "get_groups_from_provider_groups" in group_rules.__all__
    assert not hasattr(group_rules, "get_groups_from_cognito")
    assert "get_groups_from_cognito" not in group_rules.__all__


def test_group_rules_reexports_canonical_helpers():
    assert group_rules.get_groups_from_provider_groups is (
        canonical_group_rules.get_groups_from_provider_groups
    )
    assert group_rules.normalize_group_id is canonical_group_rules.normalize_group_id
    assert group_rules.get_available_groups is canonical_group_rules.get_available_groups
    assert group_rules.validate_group_rules is canonical_group_rules.validate_group_rules
