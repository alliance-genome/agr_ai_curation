"""Flexible extraction: no new agents or revisions; existing Flexible agents stay editable."""

import pytest

from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio import domain_output_contract
from src.lib.agent_studio.execution_revision_service import (
    append_execution_revision,
    get_execution_revision,
    restore_execution_revision,
)
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.agent_studio.flexible_extraction import FLEXIBLE_COPY_REFUSED
from src.models.sql.custom_agent import CustomAgentVersion
from src.schemas.agent_execution_revision import AgentOutputContract
from .test_agent_execution_revision_persistence import builder_policies, execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401

FLEXIBLE = AgentOutputContract(output_state="structured_extraction", output_mode="unprofiled_generic")
RETIRED = "Flexible extraction is retired"


@pytest.fixture
def studio(execution_db, builder_policies):  # noqa: F811
    db, *_ = execution_db
    CustomAgentVersion.__table__.create(db.connection())
    return db


def existing_flexible(db):
    """A Flexible agent saved before the rule existed (written below the service rule)."""
    agent = service.create_custom_agent(db, 1, "Older finder", model_id="gpt-6-sol",
                                        custom_prompt="Find things", include_group_rules=False)
    snapshot = capture_execution_snapshot(db, agent, FLEXIBLE)
    revision = append_execution_revision(db, agent, snapshot, user_id=1,
                                         expected_revision_id=agent.execution_revision_id)
    return agent, revision


def head_mode(db, agent):
    _, saved = get_execution_revision(db, agent.id, agent.execution_revision_id, 1,
                                      active_group_ids=[])
    return saved.output_contract.output_mode


def test_creating_a_flexible_agent_is_refused(studio):
    with pytest.raises(ValueError, match=RETIRED):
        service.create_custom_agent(studio, 1, "New finder", model_id="gpt-6-sol",
                                    custom_prompt="Find things", include_group_rules=False,
                                    output_contract=FLEXIBLE)


def test_a_template_default_that_resolves_to_flexible_is_refused(studio, monkeypatch):
    # Review Focus 4: the default output path, not only an explicit request.
    monkeypatch.setattr(domain_output_contract, "initial_agent_output_contract",
                        lambda agent: FLEXIBLE)
    with pytest.raises(ValueError, match=RETIRED):
        service.create_custom_agent(studio, 1, "Template finder", model_id="gpt-6-sol",
                                    custom_prompt="Find things", include_group_rules=False)


def test_an_existing_flexible_agent_stays_editable(studio):
    agent, _ = existing_flexible(studio)
    service.update_custom_agent(studio, agent, expected_revision_id=agent.execution_revision_id,
                                model_temperature=0.4)
    assert head_mode(studio, agent) == "unprofiled_generic"


def test_cloning_a_flexible_agent_is_refused_with_the_copy_message(studio):
    agent, _ = existing_flexible(studio)
    with pytest.raises(ValueError) as refused:
        service.clone_saved_custom_agent(studio, 1, agent, name="Copy", active_group_ids=[])
    assert str(refused.value) == FLEXIBLE_COPY_REFUSED
    assert str(refused.value) == (
        "This agent uses retired Flexible extraction, so it can't be copied. "
        "Convert the original agent to Custom Output Structure first, then copy it."
    )
    assert not service.custom_agent_name_exists(studio, 1, "Copy")


def test_a_converted_agent_cannot_be_restored_to_flexible(studio):
    agent, flexible_revision = existing_flexible(studio)
    service.update_custom_agent(
        studio, agent, expected_revision_id=agent.execution_revision_id,
        new_generic_profile={"name": "Things", "semantic_class": "thing", "fields": []})
    converted = agent.execution_revision_id
    with pytest.raises(ValueError, match=RETIRED):
        restore_execution_revision(studio, agent.id, flexible_revision.id, user_id=1,
                                   expected_revision_id=converted, active_group_ids=[])
    assert agent.execution_revision_id == converted


def test_restoring_a_non_flexible_revision_never_reads_the_head(studio, monkeypatch):
    # The head may be broken or restricted to groups the curator is not in now; a restore
    # that cannot create Flexible output must not depend on reading it.
    from src.lib.agent_studio import execution_revision_service as revisions

    agent = service.create_custom_agent(studio, 1, "Plain finder", model_id="gpt-6-sol",
                                        custom_prompt="Find things", include_group_rules=False)
    first = agent.execution_revision_id
    service.update_custom_agent(studio, agent, expected_revision_id=first, model_temperature=0.4)
    head = agent.execution_revision_id
    read = []
    original = revisions.get_execution_revision

    def recording(db, agent_id, revision_id, *args, **kwargs):
        read.append(revision_id)
        return original(db, agent_id, revision_id, *args, **kwargs)

    monkeypatch.setattr(revisions, "get_execution_revision", recording)
    restore_execution_revision(studio, agent.id, first, user_id=1,
                               expected_revision_id=head, active_group_ids=[])
    assert read == [first]
    assert agent.execution_revision_id not in (first, head)


def test_a_flexible_agent_may_restore_an_older_flexible_revision(studio):
    agent, older = existing_flexible(studio)
    service.update_custom_agent(studio, agent, expected_revision_id=agent.execution_revision_id,
                                model_temperature=0.4)
    head = agent.execution_revision_id
    restored = restore_execution_revision(studio, agent.id, older.id, user_id=1,
                                          expected_revision_id=head, active_group_ids=[])
    assert agent.execution_revision_id == restored.id != head
    assert head_mode(studio, agent) == "unprofiled_generic"
