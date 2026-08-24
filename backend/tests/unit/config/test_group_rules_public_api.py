"""Public API checks for the prompt-injection package."""

import config.group_rules as group_rules
import src.lib.group_rules as canonical_group_rules


def test_group_rules_exports_only_prompt_injection():
    assert group_rules.__all__ == ["inject_group_rules"]
    assert callable(group_rules.inject_group_rules)
    assert not hasattr(group_rules, "get_groups_from_provider_groups")
    assert not hasattr(group_rules, "get_groups_from_cognito")


def test_canonical_group_rules_keep_only_live_helpers():
    assert callable(canonical_group_rules.get_groups_from_provider_groups)
    assert callable(canonical_group_rules.normalize_group_id)
    assert not hasattr(canonical_group_rules, "get_available_groups")
    assert not hasattr(canonical_group_rules, "validate_group_rules")
