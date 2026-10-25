"""Validator role restrictions use package capability metadata, not agent names."""
import pytest
from src.lib.packages import tool_roles


@pytest.fixture
def roles(monkeypatch):
    monkeypatch.setattr(tool_roles, "tool_metadata_by_name", lambda: {
        "custom_paper_reader": {"paper_source": True},
        "custom_evidence_writer": {"paper_source": True},
        "stage_observation": {"builder_run_state": True},
        "lookup": {"identity_lookup": True},
    })
    monkeypatch.setattr(tool_roles, "is_validator_output_schema", lambda key: key == "validation")
    tool_roles.builder_finalization_tool_names.cache_clear()
    yield
    tool_roles.builder_finalization_tool_names.cache_clear()


@pytest.mark.parametrize("name", ["custom_paper_reader", "custom_evidence_writer", "stage_observation"])
@pytest.mark.parametrize("explicit", [False, True])
def test_forbidden_metadata_is_enforced(roles, name, explicit):
    with pytest.raises(ValueError, match="cannot read papers"):
        tool_roles.require_validator_tool_boundary(
            ["lookup", name], output_schema_key=None if explicit else "validation",
            validator_role=explicit, agent_label="Custom validator",
        )


def test_lookup_finalization_and_extractor_source_access_are_not_removed(roles):
    tool_roles.require_validator_tool_boundary(["lookup", "finalize_validator_result"],
                                               validator_role=True, agent_label="Validator")
    tool_roles.require_validator_tool_boundary(["custom_paper_reader"],
                                               output_schema_key="extraction", agent_label="Extractor")
