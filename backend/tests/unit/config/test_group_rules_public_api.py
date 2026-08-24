"""Public API checks for the group-rules package."""

import config.group_rules as group_rules


def test_group_rules_exports_only_provider_neutral_mapper():
    assert callable(group_rules.get_groups_from_provider_groups)
    assert "get_groups_from_provider_groups" in group_rules.__all__
    assert not hasattr(group_rules, "get_groups_from_cognito")
    assert "get_groups_from_cognito" not in group_rules.__all__
